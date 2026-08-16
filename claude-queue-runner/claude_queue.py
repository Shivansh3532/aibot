#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATE_VERSION = 1
DEFAULT_STATE_DIR = ".claude-queue"
RATE_LIMIT_PATTERNS = [
    r"rate\s*limit",
    r"usage\s*limit",
    r"limit\s*(?:has\s*been\s*)?reached",
    r"you(?:'ve| have)\s+(?:hit|reached)\s+(?:your\s+)?limit",
    r"too many requests",
    r"\b429\b",
    r"resets?\s+(?:at|in)",
    r"try again (?:after|later)",
]
TRANSIENT_PATTERNS = [
    r"overloaded",
    r"temporar(?:y|ily) unavailable",
    r"service unavailable",
    r"gateway timeout",
    r"\b502\b",
    r"\b503\b",
    r"\b504\b",
    r"connection (?:reset|timed out)",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class RunnerLock:
    def __init__(self, path):
        self.path = Path(path)
        self.owned = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                info = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                info = {}
            pid = info.get("pid")
            if process_alive(pid):
                raise RuntimeError(f"another runner is already active (pid {pid})")
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": now_iso()}, f)
        self.owned = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.owned:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.state_path = self.root / "state.json"
        self.logs_dir = self.root / "logs"
        self.lock_path = self.root / "runner.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def blank(self):
        return {
            "version": STATE_VERSION,
            "session_id": None,
            "session_started": False,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "items": [],
        }

    def load(self):
        state = load_json(self.state_path, self.blank())
        if state.get("version") != STATE_VERSION:
            raise RuntimeError("unsupported state version")
        changed = False
        for item in state.get("items", []):
            if item.get("status") == "running":
                item["status"] = "queued"
                item["recovered_after_interrupt"] = True
                changed = True
        if changed:
            self.save(state)
        return state

    def save(self, state):
        state["updated_at"] = now_iso()
        atomic_write_json(self.state_path, state)

    def log_attempt(self, item_id, payload):
        path = self.logs_dir / f"{item_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return str(path)


def new_item(prompt):
    return {
        "id": "q-" + uuid.uuid4().hex[:10],
        "prompt": prompt,
        "status": "queued",
        "attempts": 0,
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "last_error": None,
        "result": None,
        "recovered_after_interrupt": False,
    }


def classify_failure(text):
    low = (text or "").lower()
    for pattern in RATE_LIMIT_PATTERNS:
        if re.search(pattern, low, re.I):
            return "rate_limit"
    for pattern in TRANSIENT_PATTERNS:
        if re.search(pattern, low, re.I):
            return "transient"
    return "error"


def parse_json_output(stdout):
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def find_claude(explicit=None):
    if explicit:
        p = shutil.which(explicit) or explicit
        return str(p)
    env_bin = os.environ.get("CLAUDE_QUEUE_BIN")
    if env_bin:
        return str(shutil.which(env_bin) or env_bin)
    found = shutil.which("claude")
    if not found and os.name == "nt":
        found = shutil.which("claude.exe") or shutil.which("claude.cmd")
    if not found:
        raise RuntimeError("Claude Code CLI was not found. Install/update Claude Code and make sure `claude` is on PATH.")
    return found


def build_command(claude_bin, state, prompt, model, permission_mode, max_turns, extra_args):
    cmd = [claude_bin, "-p", prompt, "--output-format", "json"]
    session_id = state.get("session_id")
    if session_id and state.get("session_started"):
        cmd += ["--resume", session_id]
    elif session_id:
        cmd += ["--session-id", session_id]
    if model:
        cmd += ["--model", model]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if extra_args:
        cmd += extra_args
    return cmd


def run_claude(cmd, cwd, timeout):
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout if timeout and timeout > 0 else None,
        )
        return cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return 124, stdout, (stderr + "\nqueue runner timeout").strip()


def update_session_from_output(state, parsed):
    if not isinstance(parsed, dict):
        return
    sid = parsed.get("session_id")
    if sid:
        state["session_id"] = sid
        state["session_started"] = True


def command_init(args):
    store = Store(args.state_dir)
    state = store.load()
    if not state.get("session_id"):
        state["session_id"] = str(uuid.uuid4())
    store.save(state)
    print(f"Initialized {store.root}")
    print(f"Session: {state['session_id']}")


def command_add(args):
    store = Store(args.state_dir)
    state = store.load()
    if not state.get("session_id"):
        state["session_id"] = str(uuid.uuid4())
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise RuntimeError("prompt cannot be empty")
    item = new_item(prompt)
    state["items"].append(item)
    store.save(state)
    print(f"Added {item['id']}: {prompt[:100]}")


def parse_prompt_file(path):
    text = Path(path).read_text(encoding="utf-8")
    chunks = [chunk.strip() for chunk in re.split(r"^\s*---+\s*$", text, flags=re.M)]
    return [c for c in chunks if c and not c.startswith("#")]


def command_add_file(args):
    prompts = parse_prompt_file(args.file)
    if not prompts:
        raise RuntimeError("no prompts found; separate prompts with a line containing ---")
    store = Store(args.state_dir)
    state = store.load()
    if not state.get("session_id"):
        state["session_id"] = str(uuid.uuid4())
    for prompt in prompts:
        state["items"].append(new_item(prompt))
    store.save(state)
    print(f"Added {len(prompts)} prompts")


def pending_counts(state):
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for item in state.get("items", []):
        status = item.get("status", "queued")
        out[status] = out.get(status, 0) + 1
    return out


def command_status(args):
    store = Store(args.state_dir)
    state = store.load()
    counts = pending_counts(state)
    print(f"Session: {state.get('session_id') or '-'}")
    print("  ".join(f"{k}={v}" for k, v in counts.items()))
    for item in state.get("items", []):
        marker = {"queued": "[ ]", "running": "[..]", "done": "[x]", "failed": "[!]"}.get(item.get("status"), "[?]")
        print(f"{marker} {item['id']} attempts={item.get('attempts', 0)}  {item['prompt'][:100]}")
        if item.get("last_error") and item.get("status") == "failed":
            print(f"    {item['last_error'][:180]}")


def command_retry(args):
    store = Store(args.state_dir)
    state = store.load()
    matched = 0
    for item in state.get("items", []):
        if args.id == "all" or item.get("id") == args.id:
            if item.get("status") == "failed":
                item["status"] = "queued"
                item["last_error"] = None
                matched += 1
    store.save(state)
    print(f"Requeued {matched} item(s)")


def command_clear_done(args):
    store = Store(args.state_dir)
    state = store.load()
    before = len(state["items"])
    state["items"] = [i for i in state["items"] if i.get("status") != "done"]
    store.save(state)
    print(f"Removed {before - len(state['items'])} completed item(s)")


def command_reset_session(args):
    store = Store(args.state_dir)
    state = store.load()
    if any(i.get("status") in ("queued", "running") for i in state["items"]) and not args.force:
        raise RuntimeError("queued work exists; use --force if you intentionally want a fresh Claude session")
    state["session_id"] = str(uuid.uuid4())
    state["session_started"] = False
    store.save(state)
    print(f"New session: {state['session_id']}")


def command_run(args):
    store = Store(args.state_dir)
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise RuntimeError(f"project directory does not exist: {project}")
    claude_bin = find_claude(args.claude_bin)
    state = store.load()
    if not state.get("session_id"):
        state["session_id"] = str(uuid.uuid4())
        store.save(state)

    stopped = False

    def on_signal(signum, frame):
        nonlocal stopped
        stopped = True
        print("\nStop requested; finishing the current Claude process before exiting...", file=sys.stderr)

    old_int = signal.signal(signal.SIGINT, on_signal)
    old_term = signal.signal(signal.SIGTERM, on_signal) if hasattr(signal, "SIGTERM") else None

    try:
        with RunnerLock(store.lock_path):
            while True:
                state = store.load()
                item = next((i for i in state["items"] if i.get("status") == "queued"), None)
                if not item:
                    print("Queue empty.")
                    return 0
                if stopped:
                    return 130

                item["status"] = "running"
                item["started_at"] = item.get("started_at") or now_iso()
                store.save(state)
                generic_failures = 0
                transient_failures = 0

                while True:
                    if stopped:
                        item["status"] = "queued"
                        store.save(state)
                        return 130

                    item["attempts"] = int(item.get("attempts", 0)) + 1
                    store.save(state)
                    cmd = build_command(
                        claude_bin,
                        state,
                        item["prompt"],
                        args.model,
                        args.permission_mode,
                        args.max_turns,
                        args.extra_arg,
                    )
                    print(f"\n→ {item['id']} attempt {item['attempts']}: {item['prompt'][:120]}")
                    rc, stdout, stderr = run_claude(cmd, project, args.timeout)
                    parsed = parse_json_output(stdout)
                    update_session_from_output(state, parsed)
                    store.save(state)

                    combined = "\n".join(x for x in (stdout, stderr) if x)
                    store.log_attempt(item["id"], {
                        "at": now_iso(),
                        "attempt": item["attempts"],
                        "returncode": rc,
                        "stdout": stdout,
                        "stderr": stderr,
                        "session_id": state.get("session_id"),
                    })

                    is_error_payload = isinstance(parsed, dict) and bool(parsed.get("is_error"))
                    if rc == 0 and not is_error_payload:
                        item["status"] = "done"
                        item["finished_at"] = now_iso()
                        item["last_error"] = None
                        if isinstance(parsed, dict):
                            item["result"] = parsed.get("result")
                        elif stdout.strip():
                            item["result"] = stdout.strip()
                        store.save(state)
                        result_preview = (item.get("result") or "completed").replace("\n", " ")[:180]
                        print(f"✓ {item['id']} done: {result_preview}")
                        break

                    kind = classify_failure(combined)
                    error_text = (stderr.strip() or stdout.strip() or f"Claude exited with code {rc}")[-2000:]
                    item["last_error"] = error_text
                    store.save(state)

                    if kind == "rate_limit":
                        wait = max(1, args.rate_limit_wait)
                        print(f"⏸ Claude usage/rate limit detected. Keeping {item['id']} queued in-place and retrying in {wait}s.")
                        time.sleep(wait)
                        continue

                    if kind == "transient":
                        transient_failures += 1
                        wait = min(args.transient_max_wait, args.transient_wait * (2 ** (transient_failures - 1)))
                        print(f"↻ Temporary Claude/service error. Retry {transient_failures} in {wait}s.")
                        time.sleep(max(1, wait))
                        continue

                    generic_failures += 1
                    if args.forever or generic_failures <= args.error_retries:
                        wait = min(args.error_max_wait, args.error_wait * (2 ** (generic_failures - 1)))
                        print(f"↻ Error. Retry {generic_failures}/{args.error_retries if not args.forever else '∞'} in {wait}s.")
                        time.sleep(max(1, wait))
                        continue

                    item["status"] = "failed"
                    item["finished_at"] = now_iso()
                    store.save(state)
                    print(f"✗ {item['id']} failed after {generic_failures} non-limit errors.", file=sys.stderr)
                    if not args.keep_going:
                        return 1
                    break
    finally:
        signal.signal(signal.SIGINT, old_int)
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)


def command_remote(args):
    store = Store(args.state_dir)
    state = store.load()
    project = Path(args.project).resolve()
    claude_bin = find_claude(args.claude_bin)
    if not state.get("session_id") or not state.get("session_started"):
        raise RuntimeError("no started queue session yet; run at least one queued prompt first")
    cmd = [claude_bin, "--resume", state["session_id"], "--remote-control", args.name]
    print("Opening the queue session interactively with Claude Remote Control...")
    print("Stop the queue runner first; the same session should not be open in two processes at once.")
    return subprocess.call(cmd, cwd=str(project))


def build_parser():
    p = argparse.ArgumentParser(
        prog="claude-queue",
        description="Persistent prompt queue for Claude Code. Continues across restarts and waits through normal usage/rate-limit resets.",
    )
    p.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="queue state directory (default: .claude-queue)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="initialize queue state")
    s.set_defaults(func=command_init)

    s = sub.add_parser("add", help="add one prompt")
    s.add_argument("prompt", nargs="+")
    s.set_defaults(func=command_add)

    s = sub.add_parser("add-file", help="add prompts from a file separated by --- lines")
    s.add_argument("file")
    s.set_defaults(func=command_add_file)

    s = sub.add_parser("status", help="show queue status")
    s.set_defaults(func=command_status)

    s = sub.add_parser("retry", help="retry one failed item or all failed items")
    s.add_argument("id", help="queue id or 'all'")
    s.set_defaults(func=command_retry)

    s = sub.add_parser("clear-done", help="remove completed items")
    s.set_defaults(func=command_clear_done)

    s = sub.add_parser("reset-session", help="start a fresh Claude conversation")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=command_reset_session)

    s = sub.add_parser("run", help="process prompts until the queue is empty")
    s.add_argument("--project", default=".", help="directory Claude Code should work in")
    s.add_argument("--claude-bin", default=None, help="path/name of Claude Code executable")
    s.add_argument("--model", default=None, help="optional Claude model/alias")
    s.add_argument("--permission-mode", choices=["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"], default="acceptEdits")
    s.add_argument("--max-turns", type=int, default=None)
    s.add_argument("--timeout", type=int, default=0, help="per-prompt timeout seconds; 0 means no timeout")
    s.add_argument("--rate-limit-wait", type=int, default=300, help="seconds between limit probes; default 300")
    s.add_argument("--transient-wait", type=int, default=30)
    s.add_argument("--transient-max-wait", type=int, default=300)
    s.add_argument("--error-retries", type=int, default=3)
    s.add_argument("--error-wait", type=int, default=10)
    s.add_argument("--error-max-wait", type=int, default=120)
    s.add_argument("--forever", action="store_true", help="retry non-limit errors forever too")
    s.add_argument("--keep-going", action="store_true", help="move to later prompts after one permanently fails")
    s.add_argument("--extra-arg", action="append", default=[], help="extra Claude CLI arg; repeat for multiple args")
    s.set_defaults(func=command_run)

    s = sub.add_parser("remote", help="resume the queue's Claude session with Remote Control")
    s.add_argument("--project", default=".")
    s.add_argument("--claude-bin", default=None)
    s.add_argument("--name", default="Claude Queue")
    s.set_defaults(func=command_remote)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        return int(result or 0)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

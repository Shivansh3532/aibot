#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
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
SESSION_EXISTS_PATTERNS = [
    r"session(?: id)? .*already (?:exists|in use)",
    r"session .*already exists",
    r"already in use.*session",
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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read queue state {path}: {exc}") from exc


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


def session_exists_failure(text):
    low = (text or "").lower()
    return any(re.search(pattern, low, re.I) for pattern in SESSION_EXISTS_PATTERNS)


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


def _resolve_executable(candidate):
    found = shutil.which(candidate)
    if found:
        return found
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    return None


def find_claude(explicit=None):
    if explicit:
        found = _resolve_executable(explicit)
        if not found:
            raise RuntimeError(f"Claude Code CLI was not found at: {explicit}")
        return found
    env_bin = os.environ.get("CLAUDE_QUEUE_BIN")
    if env_bin:
        found = _resolve_executable(env_bin)
        if not found:
            raise RuntimeError(f"CLAUDE_QUEUE_BIN does not point to an executable: {env_bin}")
        return found
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
    except OSError as exc:
        return 127, "", f"could not start Claude Code: {exc}"


def update_session_from_output(state, parsed):
    if not isinstance(parsed, dict):
        return
    sid = parsed.get("session_id")
    if sid:
        state["session_id"] = sid
        state["session_started"] = True

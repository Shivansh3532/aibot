import re
import subprocess
import sys
import uuid
from pathlib import Path

from .core import Store, find_claude, new_item


def command_doctor(args):
    project = Path(args.project).resolve()
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9 or newer is required")
    if not project.is_dir():
        raise RuntimeError(f"project directory does not exist: {project}")
    claude_bin = find_claude(args.claude_bin)
    try:
        cp = subprocess.run([claude_bin, "--version"], cwd=str(project), text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not run Claude Code: {exc}") from exc
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "unknown error").strip()
        raise RuntimeError(f"`claude --version` failed: {detail}")
    store = Store(args.state_dir)
    state = store.load()
    version = (cp.stdout or cp.stderr).strip().splitlines()[0]
    print(f"Python: {sys.version.split()[0]} [ok]")
    print(f"Claude: {version} [ok]")
    print(f"Project: {project} [ok]")
    print(f"State: {store.root.resolve()} [ok]")
    print(f"Queue items: {len(state.get('items', []))}")
    print("Ready. Authentication/workspace trust are checked by Claude when a real prompt starts.")
    return 0


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
    return [c for c in chunks if c]


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

import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .core import (
    RunnerLock, Store, build_command, classify_failure, find_claude, now_iso,
    parse_json_output, run_claude, session_exists_failure, update_session_from_output,
)


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

    def sleep_or_stop(seconds):
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            if stopped:
                return False
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return not stopped

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
                    if stopped and (rc != 0 or is_error_payload):
                        item["status"] = "queued"
                        store.save(state)
                        return 130

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

                    if not state.get("session_started") and state.get("session_id") and session_exists_failure(combined):
                        state["session_started"] = True
                        store.save(state)
                        print("↻ Claude reports this session already exists; retrying with --resume.")
                        continue

                    kind = classify_failure(combined)
                    error_text = (stderr.strip() or stdout.strip() or f"Claude exited with code {rc}")[-2000:]
                    item["last_error"] = error_text
                    store.save(state)

                    if kind == "rate_limit":
                        wait = max(1, args.rate_limit_wait)
                        print(f"⏸ Claude usage/rate limit detected. Keeping {item['id']} queued in-place and retrying in {wait}s.")
                        if not sleep_or_stop(wait):
                            item["status"] = "queued"
                            store.save(state)
                            return 130
                        continue

                    if kind == "transient":
                        transient_failures += 1
                        wait = min(args.transient_max_wait, args.transient_wait * (2 ** (transient_failures - 1)))
                        print(f"↻ Temporary Claude/service error. Retry {transient_failures} in {wait}s.")
                        if not sleep_or_stop(max(1, wait)):
                            item["status"] = "queued"
                            store.save(state)
                            return 130
                        continue

                    generic_failures += 1
                    if args.forever or generic_failures <= args.error_retries:
                        wait = min(args.error_max_wait, args.error_wait * (2 ** (generic_failures - 1)))
                        print(f"↻ Error. Retry {generic_failures}/{args.error_retries if not args.forever else '∞'} in {wait}s.")
                        if not sleep_or_stop(max(1, wait)):
                            item["status"] = "queued"
                            store.save(state)
                            return 130
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
    if not project.is_dir():
        raise RuntimeError(f"project directory does not exist: {project}")
    claude_bin = find_claude(args.claude_bin)
    if not state.get("session_id") or not state.get("session_started"):
        raise RuntimeError("no started queue session yet; run at least one queued prompt first")
    cmd = [claude_bin, "--resume", state["session_id"], "--remote-control", args.name]
    print("Opening the queue session interactively with Claude Remote Control...")
    print("Stop the queue runner first; the same session should not be open in two processes at once.")
    return subprocess.call(cmd, cwd=str(project))

import argparse
import sys

from . import __version__
from .core import DEFAULT_STATE_DIR
from .commands import (
    command_add, command_add_file, command_clear_done, command_doctor, command_init,
    command_reset_session, command_retry, command_status,
)
from .runner import command_remote, command_run


def build_parser():
    p = argparse.ArgumentParser(
        prog="claude-queue",
        description="Persistent prompt queue for Claude Code. Continues across restarts and waits through normal usage/rate-limit resets.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="queue state directory (default: .claude-queue)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("doctor", help="check Python, Claude Code, project, and queue state")
    s.add_argument("--project", default=".")
    s.add_argument("--claude-bin", default=None)
    s.set_defaults(func=command_doctor)

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

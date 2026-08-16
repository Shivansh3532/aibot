import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd, env=None):
    child_env = os.environ.copy() if env is None else env.copy()
    child_env["PYTHONPATH"] = str(ROOT) + os.pathsep + child_env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "claude_queue", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=child_env,
        timeout=20,
    )


def make_fake(td, code):
    td = Path(td)
    fake = td / "fake_claude.py"
    fake.write_text(code, encoding="utf-8")
    launcher = td / ("fake.cmd" if os.name == "nt" else "fake")
    if os.name == "nt":
        launcher.write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


SUCCESS_FAKE = r'''import json, sys
args=sys.argv[1:]
sid='22222222-2222-4222-8222-222222222222'
if '--session-id' in args: sid=args[args.index('--session-id')+1]
if '--resume' in args: sid=args[args.index('--resume')+1]
if '--version' in args:
    print('2.1.999 (Claude Code)')
    raise SystemExit(0)
print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':'ok'}))
'''


class QueueBehaviorTests(unittest.TestCase):
    def test_add_file_preserves_markdown_heading_prompts(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prompts = td / "p.txt"
            prompts.write_text("# First task\nDo one\n---\n# Second task\nDo two\n", encoding="utf-8")
            r = run(["add-file", str(prompts)], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            state = json.loads((td / ".claude-queue" / "state.json").read_text())
            self.assertEqual(
                [x["prompt"] for x in state["items"]],
                ["# First task\nDo one", "# Second task\nDo two"],
            )
            r = run(["status"], td)
            self.assertIn("queued=2", r.stdout)

    def test_rate_limit_then_success(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            counter = td / "counter.txt"
            fake_code = f'''import json, pathlib, sys
counter=pathlib.Path({str(counter)!r})
n=int(counter.read_text() or '0') if counter.exists() else 0
counter.write_text(str(n+1))
args=sys.argv[1:]
sid='11111111-1111-4111-8111-111111111111'
if '--session-id' in args: sid=args[args.index('--session-id')+1]
if '--resume' in args: sid=args[args.index('--resume')+1]
if n==0:
    print('usage limit reached; try again later', file=sys.stderr)
    sys.exit(1)
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':'ok'}}))
'''
            launcher = make_fake(td, fake_code)
            self.assertEqual(run(["add", "do", "work"], td).returncode, 0)
            r = run(["run", "--project", str(td), "--claude-bin", str(launcher), "--rate-limit-wait", "1"], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            state = json.loads((td / ".claude-queue" / "state.json").read_text())
            self.assertEqual(state["items"][0]["status"], "done")
            self.assertGreaterEqual(state["items"][0]["attempts"], 2)
            self.assertTrue(state["session_started"])

    def test_crash_recovery_requeues_running_item(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self.assertEqual(run(["add", "hello"], td).returncode, 0)
            path = td / ".claude-queue" / "state.json"
            state = json.loads(path.read_text())
            state["items"][0]["status"] = "running"
            path.write_text(json.dumps(state), encoding="utf-8")
            r = run(["status"], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            state = json.loads(path.read_text())
            self.assertEqual(state["items"][0]["status"], "queued")
            self.assertTrue(state["items"][0]["recovered_after_interrupt"])

    def test_second_prompt_uses_resume_with_same_session(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            args_log = td / "args.jsonl"
            fake_code = f'''import json, pathlib, sys
log=pathlib.Path({str(args_log)!r})
args=sys.argv[1:]
with log.open('a', encoding='utf-8') as f: f.write(json.dumps(args)+'\\n')
sid='22222222-2222-4222-8222-222222222222'
if '--session-id' in args: sid=args[args.index('--session-id')+1]
if '--resume' in args: sid=args[args.index('--resume')+1]
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':'ok'}}))
'''
            launcher = make_fake(td, fake_code)
            self.assertEqual(run(["add", "first"], td).returncode, 0)
            self.assertEqual(run(["add", "second"], td).returncode, 0)
            r = run(["run", "--project", str(td), "--claude-bin", str(launcher)], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = [json.loads(line) for line in args_log.read_text().splitlines()]
            self.assertIn("--session-id", calls[0])
            self.assertIn("--resume", calls[1])
            first_sid = calls[0][calls[0].index("--session-id") + 1]
            second_sid = calls[1][calls[1].index("--resume") + 1]
            self.assertEqual(first_sid, second_sid)

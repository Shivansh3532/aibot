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


class RecoveryAndDiagnosticsTests(unittest.TestCase):
    def test_existing_first_session_recovers_with_resume(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            counter = td / "counter.txt"
            args_log = td / "args.jsonl"
            fake_code = f'''import json, pathlib, sys
counter=pathlib.Path({str(counter)!r})
log=pathlib.Path({str(args_log)!r})
n=int(counter.read_text() or '0') if counter.exists() else 0
counter.write_text(str(n+1))
args=sys.argv[1:]
with log.open('a', encoding='utf-8') as f: f.write(json.dumps(args)+'\\n')
if n==0:
    print('session id already exists', file=sys.stderr)
    sys.exit(1)
sid=args[args.index('--resume')+1]
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':'ok'}}))
'''
            launcher = make_fake(td, fake_code)
            self.assertEqual(run(["add", "recover me"], td).returncode, 0)
            r = run(["run", "--project", str(td), "--claude-bin", str(launcher)], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = [json.loads(line) for line in args_log.read_text().splitlines()]
            self.assertIn("--session-id", calls[0])
            self.assertIn("--resume", calls[1])

    def test_doctor_and_version(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            launcher = make_fake(td, SUCCESS_FAKE)
            r = run(["--version"], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("1.0.0", r.stdout)
            r = run(["doctor", "--project", str(td), "--claude-bin", str(launcher)], td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Ready.", r.stdout)
            self.assertIn("2.1.999", r.stdout)

    def test_missing_claude_binary_is_friendly_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(["doctor", "--project", td, "--claude-bin", "definitely-not-a-real-claude-binary"], td)
            self.assertEqual(r.returncode, 2)
            self.assertIn("Claude Code CLI was not found", r.stderr)

    def test_corrupt_state_is_friendly_error(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            state_dir = td / ".claude-queue"
            state_dir.mkdir()
            (state_dir / "state.json").write_text("{bad json", encoding="utf-8")
            r = run(["status"], td)
            self.assertEqual(r.returncode, 2)
            self.assertIn("could not read queue state", r.stderr)

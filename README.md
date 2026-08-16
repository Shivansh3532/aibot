# Claude Queue

A small, dependency-free prompt queue for **Claude Code**.

Add work once, start the runner, and leave it running. Claude Queue sends prompts one at a time in a single resumable Claude Code session, saves progress after every step, recovers after restarts, and waits through normal Claude usage/rate limits before continuing.

> Claude Queue does **not** bypass Claude usage limits, rotate accounts, or evade plan restrictions. It only automates the normal wait-and-retry path using your existing authenticated Claude Code installation.

## Why this exists

Claude Code already supports non-interactive prompts and resumable sessions. Claude Queue adds the missing durable queue around those primitives:

- persistent prompt list
- one shared Claude session across prompts
- crash/restart recovery
- usage-limit retry loop
- transient-error backoff
- per-attempt logs
- a simple health check
- an easy handoff back to Remote Control

## Requirements

- Python 3.9+
- Claude Code installed and available as `claude`
- Claude Code authenticated with the account you intend to use
- Workspace trust completed for the target project

For Remote Control, use a Claude Code version that supports it and authenticate through claude.ai rather than an API-key-only session.

## Install

From a clone of this repository:

```bash
python -m pip install .
```

That installs the command:

```bash
claude-queue --version
claude-queue doctor --project .
```

You can also run it directly from a clone without installing anything:

```bash
python -m claude_queue --version
```

## 60-second start

From the project you want Claude to work on:

```bash
claude-queue doctor --project .
claude-queue add "Inspect this repository and identify the highest-priority unfinished work."
claude-queue add "Implement the work you identified and run the relevant tests."
claude-queue add "Review everything you changed, fix regressions, and run the full test suite."
claude-queue run --project .
```

The queue state is stored locally in `.claude-queue/`.

## Prompt files

For longer queues, create a text file and separate prompts with a line containing `---`:

```text
Inspect the repository and summarize the current failures.
---
Fix the failures and run the relevant tests.
---
Review the changes for regressions and fix anything you find.
---
Run the full test suite and resolve any remaining failures.
```

Then load and run it:

```bash
claude-queue add-file prompts.txt
claude-queue status
claude-queue run --project .
```

Markdown headings inside prompts are preserved.

## Commands

```text
claude-queue doctor --project PATH
claude-queue init
claude-queue add "prompt text"
claude-queue add-file prompts.txt
claude-queue status
claude-queue run --project PATH
claude-queue retry all
claude-queue clear-done
claude-queue reset-session
claude-queue remote --project PATH
```

### Important `run` options

```text
--rate-limit-wait 300      seconds between usage-limit probes
--error-retries 3          retries for ordinary errors before failing an item
--forever                  retry ordinary errors indefinitely too
--keep-going               continue after a permanently failed queue item
--model MODEL              optional Claude model/alias
--permission-mode MODE     default: acceptEdits
--max-turns N              Claude Code turn limit for each prompt
--timeout N                per-prompt timeout; 0 means no timeout
--extra-arg ARG            pass another Claude CLI argument; repeat as needed
```

Supported permission modes are `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, and `bypassPermissions`. Claude Queue defaults to `acceptEdits`; it deliberately does not default to `bypassPermissions`.

## What happens at a usage limit?

If Claude returns a response that looks like a normal usage/rate limit, Claude Queue keeps the current item in progress and retries after `--rate-limit-wait` seconds. The default is five minutes.

It does not change credentials or attempt to get around the limit.

Pressing `Ctrl+C` during a wait exits promptly and leaves the current item queued for the next run.

## Session continuity

The queue uses one UUID-backed Claude Code session. The first prompt starts it with `--session-id`; later prompts use `--resume` with the same ID. If the runner was interrupted after Claude created the first session but before Claude Queue recorded that fact, it recognizes the existing-session error and safely switches to `--resume`.

This keeps later prompts in the same conversation context.

## Remote Control handoff

Stop the queue runner first, then run:

```bash
claude-queue remote --project . --name "My queued project"
```

Claude Queue resumes the saved session with Claude Code Remote Control enabled so you can continue it from claude.ai/code or the Claude mobile app.

Do not run the queue worker and an interactive Remote Control process against the same session at the same time; two writers to one session can interleave messages.

## State and logs

```text
.claude-queue/
├── state.json
├── runner.lock
└── logs/
    └── q-XXXXXXXXXX.jsonl
```

Queue item states:

- `queued`
- `running`
- `done`
- `failed`

A leftover `running` item is automatically returned to `queued` when the state is loaded after an interruption.

Every Claude attempt is appended to a JSONL log containing the timestamp, exit code, stdout, stderr, attempt number, and session ID.

## Reliability behavior

Claude Queue distinguishes three broad failure classes:

1. **Usage/rate limit** — retry indefinitely at a fixed interval.
2. **Transient service/network error** — retry with exponential backoff.
3. **Other error** — retry a limited number of times, then mark the item failed unless `--forever` is set.

The queue state is written atomically and a lock prevents two queue workers from processing the same local state directory at once.

## Testing

Run the full suite:

```bash
python -m unittest discover -s tests -v
python -m py_compile claude_queue/*.py tests/test_claude_queue.py
```

The test suite uses fake Claude executables, so it does not consume Claude usage. It covers:

- prompt-file parsing, including Markdown-heading prompts
- same-session continuation with `--resume`
- usage-limit wait/retry behavior
- interrupted-state recovery
- pre-existing first-session recovery
- friendly missing-binary errors
- corrupted-state handling
- `doctor` and version reporting

GitHub Actions runs the suite on Windows and Linux across supported Python versions.

## Documentation

- [`docs/USAGE.md`](docs/USAGE.md) — full command guide and practical workflows
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — state model, retry behavior, and session lifecycle
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — common installation, authentication, and recovery issues
- [`SECURITY.md`](SECURITY.md) — unattended-agent and permission guidance
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development and test workflow

## Project status

**v1.0.0** — stable initial release of the durable queue runner.

See [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT. See [`LICENSE`](LICENSE).

## Disclaimer

Claude Queue is an independent utility and is not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of their respective owner.

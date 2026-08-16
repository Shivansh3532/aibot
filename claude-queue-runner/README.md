# Claude Queue Runner

A tiny, dependency-free prompt queue for **Claude Code**. Add a list of prompts, start the runner, and walk away. The queue is persisted to disk, uses one resumable Claude Code session for context, survives restarts, and waits/retries when Claude reports a normal usage/rate limit.

It does **not** bypass Claude limits. It waits for access to become available again and continues using your authenticated Claude Code account.

## What it does

- Sends prompts to `claude -p` one at a time.
- Keeps all prompts in the same Claude Code session using `--session-id` / `--resume`.
- Saves queue state after every step in `.claude-queue/state.json`.
- If the runner or computer restarts, an in-progress item is safely returned to the queue.
- Retries usage/rate-limit errors indefinitely with a configurable delay.
- Retries temporary service/network errors with exponential backoff.
- Stops after repeated ordinary errors by default so a bad command does not spin forever; `--forever` changes that.
- Stores every attempt under `.claude-queue/logs/` for debugging.
- Can reopen the same completed/headless session with Claude Remote Control for manual steering.

## Requirements

1. Python 3.9+.
2. Current Claude Code installed and on `PATH`.
3. Sign into Claude Code with the Claude account you intend to use.
4. Run Claude Code in the target project at least once so workspace trust/initial setup is complete.

Check:

```bash
claude --version
claude
```

Claude Remote Control requires a current Claude Code version and Claude.ai authentication. API-key-only authentication is not supported for Remote Control.

## Fast start

Put `claude_queue.py` in any folder, then from the project Claude should work on:

```bash
python claude_queue.py add "Inspect the repo and identify the highest-priority problem."
python claude_queue.py add "Fix it and run the relevant tests."
python claude_queue.py add "Review your work, fix regressions, and run the full test suite."
python claude_queue.py run --project .
```

Or create a prompt file with prompts separated by a line containing `---`:

```text
Inspect the repository and summarize the current failures.
---
Fix the failures and run tests.
---
Review the changes for regressions and fix anything you find.
```

Then:

```bash
python claude_queue.py add-file prompts.txt
python claude_queue.py status
python claude_queue.py run --project .
```

## Windows example

PowerShell:

```powershell
cd C:\path\to\your\project
python C:\path\to\claude_queue.py add-file C:\path\to\prompts.txt
python C:\path\to\claude_queue.py run --project .
```

Leave that terminal running. If Claude hits your plan's normal usage limit, the current queue item stays in place and the runner probes again every 5 minutes by default.

## Commands

```text
python claude_queue.py init
python claude_queue.py add "prompt text"
python claude_queue.py add-file prompts.txt
python claude_queue.py status
python claude_queue.py run --project PATH
python claude_queue.py retry all
python claude_queue.py clear-done
python claude_queue.py reset-session
python claude_queue.py remote --project PATH
```

### Useful run options

```text
--rate-limit-wait 300      seconds between usage-limit retries
--error-retries 3          ordinary error retries before failing the item
--forever                  retry ordinary errors forever too
--keep-going               continue later queue items after a permanent failure
--model sonnet             optional model alias
--permission-mode MODE     default is acceptEdits
--max-turns N              pass a Claude Code max-turn limit
--timeout N                per-prompt process timeout; 0 disables it
--extra-arg ARG            pass an additional Claude CLI argument; repeat as needed
```

Example for an isolated machine where you explicitly choose broader Claude permissions:

```bash
python claude_queue.py run --project . --permission-mode auto
```

The tool deliberately does not default to `bypassPermissions`.

## Remote Control

Claude Code Remote Control is for controlling a **live local session** from claude.ai/code or the Claude mobile app. The queue uses documented non-interactive `-p` calls instead of trying to inject fake terminal keystrokes into Remote Control.

After the queue has started at least one prompt, you can stop the queue runner and reopen its saved session interactively with Remote Control:

```bash
python claude_queue.py remote --project .
```

Do not run the queue process and Remote Control on the same session at the same time. Claude's session documentation warns that opening the same session from multiple processes can interleave messages.

If you only want ordinary Remote Control without the queue, Claude already provides:

```bash
claude remote-control --name "My Project"
```

## Behavior at usage limits

The runner looks for common Claude limit responses such as `usage limit`, `rate limit`, HTTP 429, and reset/try-again messages. It does not change accounts, rotate credentials, or attempt to evade plan limits. The same prompt remains current and is retried after the configured wait.

For ordinary Pro/Max subscription usage, Claude Code and Claude share plan usage. When the plan limit is reached, Anthropic's documented options include waiting for the limit to reset or explicitly choosing paid API usage if you have configured it. This project only automates the **wait and retry** path.

## State and recovery

The queue lives in:

```text
.claude-queue/
  state.json
  runner.lock
  logs/
```

The state folder is intended to stay local and is ignored by the included `.gitignore`.

A queue item has one of four states: `queued`, `running`, `done`, or `failed`. On startup, a leftover `running` item is changed back to `queued`, which makes a power loss or process crash recoverable.

## Tests

No third-party packages are needed:

```bash
python -m unittest -v
```

The tests use a fake Claude executable and verify queue parsing, rate-limit retry, session persistence, and crash recovery without making real Claude requests.

## Notes

- Prompts can cause Claude to edit files or execute commands according to your Claude Code permission configuration. Review the project and permissions before leaving it unattended.
- Use Git/version control for important repositories.
- Very long-running unattended work is safer in a dedicated branch or worktree.

# Troubleshooting

## `Claude Code CLI was not found`

Confirm:

```bash
claude --version
```

If Claude is installed somewhere unusual, pass its path:

```bash
claude-queue doctor --claude-bin /path/to/claude
claude-queue run --claude-bin /path/to/claude --project .
```

You can also set `CLAUDE_QUEUE_BIN`.

## Authentication or workspace trust blocks a run

Open Claude normally in the target project first:

```bash
cd /path/to/project
claude
```

Complete login and workspace-trust prompts, then exit and start Claude Queue again.

## Queue says another runner is active

Only one process should consume a given state directory. If another worker is genuinely running, stop one of them. If the previous process crashed, Claude Queue normally detects the stale PID and removes the lock automatically.

## A prompt is permanently failed

Inspect its error preview:

```bash
claude-queue status
```

Then inspect the JSONL attempt log under `.claude-queue/logs/`.

After fixing the underlying issue:

```bash
claude-queue retry all
claude-queue run --project .
```

## `state.json` is corrupt

Claude Queue intentionally stops rather than guessing at damaged state. Back up `.claude-queue/`, inspect the file, and restore a known-good copy. If the queue can be discarded, remove the state directory and initialize a new queue.

## Remote Control does not open

Run Claude Code directly and confirm your installed version and claude.ai authentication support Remote Control. API-key-only authentication is not sufficient for Remote Control.

Also ensure the queue worker is stopped before opening the same saved session interactively.

## Auto mode is unavailable

`auto` is controlled by Claude Code account, model, version, provider, and organization policy requirements. Use `acceptEdits`, `dontAsk` with explicit allow rules, or another supported mode when `auto` is unavailable.

# Usage Guide

## 1. Check the environment

```bash
claude-queue doctor --project /path/to/project
```

`doctor` verifies Python, the target directory, the Claude Code executable, and readable queue state. A real queued prompt is still responsible for surfacing Claude authentication or workspace-trust problems.

## 2. Add work

One prompt:

```bash
claude-queue add "Fix the failing authentication tests and verify the fix."
```

Several prompts from a file:

```bash
claude-queue add-file prompts.txt
```

Separate prompts with a line containing `---`. The rest of each chunk is passed to Claude exactly as prompt text after leading/trailing whitespace is trimmed.

## 3. Inspect the queue

```bash
claude-queue status
```

The output shows the session ID, item counts, queue IDs, attempts, and failed-item error previews.

## 4. Run

```bash
claude-queue run --project /path/to/project
```

The worker processes one queued item at a time until there is no queued work or an unrecoverable item stops the run.

### Usage-limit behavior

```bash
claude-queue run --project . --rate-limit-wait 300
```

A detected normal usage/rate limit does not fail the item. The runner waits and sends the same prompt again using the same session lifecycle.

### Keep processing after a failed prompt

```bash
claude-queue run --project . --keep-going
```

### Retry ordinary failures indefinitely

```bash
claude-queue run --project . --forever
```

Use this carefully: a deterministic bad prompt or invalid project command can otherwise loop forever.

## 5. Recover or clean up

Retry all failed items:

```bash
claude-queue retry all
```

Retry one failed queue ID:

```bash
claude-queue retry q-1234567890
```

Remove completed items:

```bash
claude-queue clear-done
```

Start a fresh Claude conversation after clearing/finishing queued work:

```bash
claude-queue reset-session
```

Force a new conversation while queued work exists:

```bash
claude-queue reset-session --force
```

## 6. Continue manually with Remote Control

Stop the worker, then:

```bash
claude-queue remote --project . --name "Project follow-up"
```

This opens the stored Claude session interactively with Remote Control enabled.

## State location

By default the queue is local to the directory where you invoke Claude Queue:

```text
.claude-queue/
```

Use a different state directory when needed:

```bash
claude-queue --state-dir /path/to/state add "Prompt"
claude-queue --state-dir /path/to/state run --project .
```

Use the same `--state-dir` value for every command that should operate on that queue.

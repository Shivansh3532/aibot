# Architecture

Claude Queue is intentionally a small Python package with no third-party runtime dependencies. The CLI, queue commands, runner loop, and persistence primitives are separated into focused modules.

## Components

### `Store`

Owns `.claude-queue/state.json`, the lock file, and per-item JSONL logs. State writes use a temporary file plus `os.replace` so a process interruption does not leave a partially-written JSON document.

### `RunnerLock`

Prevents two local Claude Queue workers from consuming the same state directory. A stale lock is removed when its recorded process no longer exists.

### Queue items

Each item contains:

- queue ID
- prompt text
- state (`queued`, `running`, `done`, `failed`)
- attempt count
- timestamps
- latest error
- final result preview data
- interruption-recovery marker

On load, any leftover `running` item becomes `queued` again.

## Session lifecycle

A queue receives a UUID before the first prompt.

1. First execution: `claude -p ... --session-id <uuid>`
2. Claude reports the session ID in JSON output.
3. State records `session_started=true`.
4. Later prompts: `claude -p ... --resume <uuid>`

There is an additional recovery path for a narrow interruption window: if Claude reports that the preselected first session already exists while local state still says it has not started, Claude Queue marks it started and retries with `--resume`.

## Retry classification

Output is classified from Claude stdout/stderr only after a non-success exit or error payload.

### Rate limit

Common usage-limit, rate-limit, HTTP 429, reset, and try-again messages are treated as a normal pause. They retry indefinitely at `--rate-limit-wait`.

### Transient

Overload, temporary unavailability, selected 5xx errors, resets, and timeouts use exponential backoff capped by `--transient-max-wait`.

### Generic

Everything else uses a bounded exponential retry loop. After the configured attempts, the item becomes `failed` unless `--forever` is enabled.

## Signals

`SIGINT`/`SIGTERM` set a stop flag. Retry sleeps wake at short intervals so a stop request does not have to wait for the full rate-limit delay. If a Claude child process has returned unsuccessfully after a stop request, the item is returned to `queued` before exit.

## Logs

Each attempt appends one JSON object to:

```text
.claude-queue/logs/<queue-id>.jsonl
```

Logs are append-only and intentionally separate from `state.json`, keeping the state file small and making retry history inspectable.

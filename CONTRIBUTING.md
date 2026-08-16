# Contributing

## Development setup

Claude Queue has no runtime dependencies.

```bash
python -m venv .venv
# activate the environment for your shell
python -m pip install -e .
```

## Required checks

Before proposing a change:

```bash
python -m compileall -q claude_queue tests
python -m unittest discover -s tests -v
claude-queue --version
```

Tests must not require a real Claude account or consume Claude usage. Use fake launcher scripts to model CLI output and exit codes.

## Design rules

- Keep the runtime dependency-free unless there is a strong reason not to.
- Preserve queue state compatibility or explicitly bump `STATE_VERSION`.
- Never implement account rotation or rate-limit bypass behavior.
- Prefer deterministic tests for recovery, retry, and session behavior.
- Keep destructive permission modes opt-in.

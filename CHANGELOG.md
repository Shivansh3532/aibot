# Changelog

All notable changes to Claude Queue are documented here.

## 1.0.0 - 2026-08-15

### Added

- Installable `claude-queue` command with zero runtime dependencies.
- Persistent prompt queue backed by atomic JSON state writes.
- Single-session continuity using Claude Code `--session-id` and `--resume`.
- Usage/rate-limit detection with indefinite wait-and-retry behavior.
- Exponential backoff for transient service and network failures.
- Crash recovery for items left in `running` state.
- Recovery when the first Claude session already exists after an interrupted launch.
- Process lock preventing two workers from consuming one queue simultaneously.
- Per-attempt JSONL logs.
- `doctor`, `status`, `retry`, `clear-done`, `reset-session`, and `remote` commands.
- Remote Control handoff for the saved Claude Code session.
- Cross-platform test suite and GitHub Actions CI.
- Usage, architecture, troubleshooting, security, and contribution documentation.

### Fixed

- Prompt files no longer drop valid prompts that begin with Markdown headings.
- Missing Claude executable and corrupt queue state now fail with clear user-facing errors.
- Waiting loops now react promptly to interruption instead of sleeping until the entire retry delay expires.

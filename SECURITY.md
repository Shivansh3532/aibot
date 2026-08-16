# Security

Claude Queue launches Claude Code against a local project and can therefore cause file edits and command execution according to your Claude Code permission configuration.

## Safe defaults

Claude Queue defaults to `acceptEdits`. It does not default to `bypassPermissions`, does not rotate credentials, and does not attempt to evade Claude plan or rate limits.

For unattended runs:

- use Git and start from a clean working tree;
- prefer a dedicated branch or worktree;
- review Claude Code permission settings before starting;
- avoid `bypassPermissions` on a normal host machine;
- keep secrets out of prompts and logs;
- remember that `.claude-queue/logs/` records Claude stdout and stderr locally.

## Reporting a vulnerability

Do not post secrets, tokens, private prompts, or sensitive logs in a public issue. Open a minimal issue describing the affected version and behavior without confidential data, or contact the repository owner privately when possible.

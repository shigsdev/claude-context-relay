# claude-context-relay

Claude Code hooks that hand off to a fresh session instead of auto-compacting.
See README.md for the user-facing flow.

- `context_relay.py` -- the single hook script (stdlib only; must run on Windows + POSIX).
- `install.py` -- merges hooks into `~/.claude/settings.json`; must stay idempotent and
  never touch entries it didn't create.
- `commands/handoff.md` -- the `/handoff` slash command installed to `~/.claude/commands/`.

Rules:
- A hook must never break a session: every handler failure exits 0 (see `main()`).
- Stop hook must never loop -- respect `stop_hook_active`.
- SessionStart `additionalContext` is capped at ~10k chars by Claude Code.

Before committing: `ruff check . && ruff format . && pytest`.

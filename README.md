# Context relay — clear and restart instead of compacting

Auto-compaction replaces your conversation with a lossy summary written at
the worst moment (the window is nearly full). Context relay hands off
*deliberately*, while there's still room:

```
 turn ends ──► Stop hook reads transcript usage
                 │ < threshold → nothing happens
                 ▼ ≥ threshold (default 60%)
 Claude is told to write a structured handoff → ~/.claude/handoffs/<project>/latest.md
 hook archives the handoff + a full copy of the transcript (session history)
 desktop app: Claude clears the session itself (clear_session tool)
 CLI:         Claude: "Handoff saved -- type /clear to continue in a fresh session."
                 │
                 ▼
 SessionStart hook (source=clear) injects the handoff → Claude picks up at "Next steps"
```

In the desktop app (Code tab), Claude calls the app's `clear_session` tool,
so the relay is fully automatic. The app refuses that for a pinned session
or one serving Remote Control. In those cases, and in the CLI (where hooks and
the model can't run `/clear`), Claude asks you to type `/clear` instead.

## Install (once — applies to every project)

```bash
python install.py --statusline
```

This copies the hook to `~/.claude/hooks/`, adds a `/handoff` command, and
merges `Stop` / `SessionStart` / `PreCompact` hooks into
`~/.claude/settings.json`. Your other settings are kept, and a `.bak` copy is
written first. `--statusline` shows `ctx 43% (86k/200k)` at the bottom if you
don't already have a status line. Restart Claude Code afterwards.

**Desktop app with a 1M model:** the app doesn't run status line commands, so
the hook can't detect the window size there and assumes 200k. Install with
`python install.py --window 1000000` to set `CONTEXT_RELAY_WINDOW`. It's
global, so leave it out if you also use 200k-window sessions. Without it, a
handoff triggered on a guessed size tells you the size was assumed and doesn't
clear on its own.

Then turn **auto-compact off** in `/config`. The PreCompact hook still saves
the full transcript if compaction ever fires anyway.

Uninstall: `python install.py --uninstall`

## Use

- **Automatic:** keep working. When the threshold is crossed, Claude writes
  the handoff and clears (or asks you to `/clear`).
- **Manual:** run `/handoff` at a natural breakpoint (e.g. after a PR
  merges). It clears the same way.
- **Check:** `python ~/.claude/hooks/context_relay.py status`

A handoff is restored once. On `/clear` it's always used; on a fresh
`claude` launch it's used only if it is under 24 h old. Resumed sessions
(`--resume`) skip it because they already have their history. If you keep
working in the session after a handoff, it's requested again once the
context grows another 50k tokens, so the pending handoff doesn't go stale.

## Tuning

Set these in `~/.claude/settings.json` → `"env": {...}`:

| Variable | Default | Meaning |
|---|---|---|
| `CONTEXT_RELAY_THRESHOLD` | `0.60` | Fraction of the window that triggers a handoff |
| `CONTEXT_RELAY_WINDOW` | auto | Force the context window size in tokens (needed in the desktop app for a 1M window, see below) |
| `CONTEXT_RELAY_MAX_AGE_H` | `24` | Ignore older handoffs on a plain startup |

### Window size detection

Hooks aren't told the context window size, so it's resolved in this order:

1. `CONTEXT_RELAY_WINDOW`, if set.
2. The size Claude Code reports to the status line for that session. The status line (`--statusline`)
   records it, so with it installed 200k and 1M sessions are told apart exactly, even side by side.
3. A `[1m]` model in `ANTHROPIC_MODEL` or in `~/.claude/settings.json` / the project's
   `.claude/settings*.json`.
4. More than 200k tokens in use, which only an extended window can hold.
5. Otherwise 200k. This is a guess, so the handoff says so and leaves the clear to you. `status` marks
   it as assumed. The desktop app ends up here (no status line, and transcripts record a plain model
   id with no `[1m]`), so set `CONTEXT_RELAY_WINDOW` there.

On a 1M window the default 60% threshold means ~600k tokens. If you'd rather hand off sooner,
lower `CONTEXT_RELAY_THRESHOLD` (e.g. `0.3` = ~300k).

## Files

```
~/.claude/handoffs/<project>/
  latest.md        pending handoff (moved to archive/ when restored)
  archive/         every handoff written / restored
  history/         full transcript copies (.jsonl) at each handoff & compaction
  state.json       per-session bookkeeping
~/.claude/handoffs/windows.json   window size per session, recorded by the status line
```

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
 Claude: "Handoff saved -- type /clear to continue in a fresh session."
                 │  you type /clear
                 ▼
 SessionStart hook (source=clear) injects the handoff → Claude picks up at "Next steps"
```

Claude Code doesn't let a hook, skill or the model run `/clear`, so typing
`/clear` is the one manual step. Everything else is automatic.

## Install (once — applies to every project)

```bash
python install.py --statusline
```

This copies the hook to `~/.claude/hooks/`, adds a `/handoff` command, and
merges `Stop` / `SessionStart` / `PreCompact` hooks into
`~/.claude/settings.json`. Your other settings are kept, and a `.bak` copy is
written first. `--statusline` shows `ctx 43% (86k/200k)` at the bottom if you
don't already have a status line. Restart Claude Code afterwards.

Then turn **auto-compact off** in `/config`. The PreCompact hook still saves
the full transcript if compaction ever fires anyway.

Uninstall: `python install.py --uninstall`

## Use

- **Automatic:** keep working. When the threshold is crossed, Claude writes
  the handoff and asks you to `/clear`.
- **Manual:** run `/handoff` at a natural breakpoint (e.g. after a PR
  merges), then `/clear`.
- **Check:** `python ~/.claude/hooks/context_relay.py status`

A handoff is restored once. On `/clear` it's always used; on a fresh
`claude` launch it's used only if it is under 24 h old. Resumed sessions
(`--resume`) skip it because they already have their history.

## Tuning

Set these in `~/.claude/settings.json` → `"env": {...}`:

| Variable | Default | Meaning |
|---|---|---|
| `CONTEXT_RELAY_THRESHOLD` | `0.60` | Fraction of the window that triggers a handoff |
| `CONTEXT_RELAY_WINDOW` | auto | Force the context window size in tokens (normally not needed, see below) |
| `CONTEXT_RELAY_MAX_AGE_H` | `24` | Ignore older handoffs on a plain startup |

### Window size detection

Hooks aren't told the context window size, so it's resolved in this order:

1. `CONTEXT_RELAY_WINDOW`, if set.
2. The size Claude Code reports to the status line for that session. The status line (`--statusline`)
   records it, so with it installed 200k and 1M sessions are told apart exactly, even side by side.
3. A `[1m]` model in `ANTHROPIC_MODEL` or in `~/.claude/settings.json` / the project's
   `.claude/settings*.json`.
4. More than 200k tokens in use, which only an extended window can hold.
5. Otherwise 200k.

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

# Manual smoke checks

`tests/test_smoke.py` (part of `pytest`) installs the hooks into a temp `~/.claude` and runs
them as subprocesses for each host profile. It can't drive a real Claude Code host, so run
these by hand after changing hook output, Claude's instructions, `/handoff`, or `install.py`.
Only the hosts whose behavior you changed need checking.

Setup: `python install.py` (no `--window`: the window is detected from the model), then set
`"CONTEXT_RELAY_THRESHOLD": "0.01"` in `~/.claude/settings.json` → `"env"` so the first
turn triggers. Remove it afterwards.

## Desktop app (Code tab)

Use a session that is **unpinned, with Remote Control off, in bypass permissions mode**.

- [ ] After one turn, Claude writes `~/.claude/handoffs/<project>/latest.md`.
- [ ] Claude calls `clear_session("self")` and ends with "Handoff saved -- clearing to a fresh session."
- [ ] The session clears by itself, and the fresh one opens with Claude confirming the "CONTEXT RELAY" handoff.
- [ ] No permission prompt appears anywhere in the cycle (bypass mode). If one does, note the tool or path it names.
- [ ] `/handoff` does the same: writes the file, clears, restores.

Pinned session (or Remote Control on):

- [ ] Claude says why it couldn't clear and ends with "Handoff saved -- type /clear to continue in a fresh session."
- [ ] Typing `/clear` restores the handoff.

Window size (no status line runs in the app, so it comes from the transcript's model id):

- [ ] On a current model (e.g. Opus 5.5), `python ~/.claude/hooks/context_relay.py status` shows
      `of 1,000,000` and does not say "assumed".
- [ ] Unit/smoke tests cover an unrecognized model (assumed 200k, no auto-clear); no manual check needed.

## CLI

- [ ] With `--statusline`: the status line shows the right window (`ctx N% (Xk/1000k)` on a 1M model).
- [ ] Claude writes the handoff and ends with the "type /clear" line (no `clear_session` tool in the CLI).
- [ ] `/clear` restores the handoff. `claude --resume` does not.

## After any check

- [ ] Remove `CONTEXT_RELAY_THRESHOLD` from settings.
- [ ] `~/.claude/handoffs/<project>/archive/` and `history/` hold the handoff and transcript copies.

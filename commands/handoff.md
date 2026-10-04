---
description: Write a context-relay handoff now, then clear to continue in a fresh session (no compaction).
allowed-tools: Bash, Read, Write, ToolSearch, mcp__ccd_session_mgmt__clear_session
---

Write a handoff for the current work so a fresh session can pick it up
after the session is cleared. The SessionStart hook from context relay injects it
automatically into the next session.

1. Get the handoff path. Pass the directory this session was started in
   (your primary working directory), not wherever the shell has cd'd to:

   ```bash
   python ~/.claude/hooks/context_relay.py path "<primary working directory>"
   ```

2. Write that file (overwrite it) with these sections, specific enough
   that a stranger could resume from it alone: file paths, commands,
   branch, exact error messages. Keep it under ~1,200 words.

   - `# Handoff -- <one-line goal>`
   - `## Goal`
   - `## Done so far`
   - `## Current state` (branch, uncommitted changes, test status)
   - `## Next steps` (numbered; the first startable immediately)
   - `## Decisions & constraints`
   - `## Open questions / gotchas`

3. Clear the session. If the `mcp__ccd_session_mgmt__clear_session` tool
   exists (desktop app; load it via ToolSearch if it is deferred), call it
   with `session_id: "self"`. The clear runs when this turn ends, and the
   handoff is restored into the fresh session. End with exactly one line:
   `Handoff saved -- clearing to a fresh session.`

   Only if that tool is missing or refuses (for example, the session is
   pinned or Remote Control is active), say why in a few words, then end
   with: `Handoff saved -- type /clear to continue in a fresh session.`

$ARGUMENTS

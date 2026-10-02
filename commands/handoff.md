---
description: Write a context-relay handoff now so you can /clear and continue in a fresh session (no compaction).
allowed-tools: Bash, Read, Write
---

Write a handoff for the current work so a fresh session can pick it up
after `/clear`. The SessionStart hook from context relay injects it
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

3. End with exactly one line: `Handoff saved -- type /clear to continue in a fresh session.`

$ARGUMENTS

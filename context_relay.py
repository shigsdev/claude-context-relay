"""Context relay -- hand off to a fresh Claude Code session instead of compacting.

One stdlib-only script wired into Claude Code hooks (see install.py):

  stop           Stop hook. After each turn, measures context usage from the
                 transcript. Over the threshold -> blocks the stop once and
                 tells Claude to write a handoff file, then to ask you to
                 type /clear.
  session-start  SessionStart hook. On /clear (or a fresh start / compaction)
                 injects the pending handoff into the new session's context,
                 then archives it so it is only restored once.
  pre-compact    PreCompact hook. Safety net if compaction fires anyway:
                 archives the full transcript before it is summarised.
  statusline     Optional status line: "ctx 43% (86k/200k)".
  path [DIR]     Print the handoff file path for a project (used by /handoff).
  status [DIR]   Print usage for the project's most recent session.

Everything lives under ~/.claude/handoffs/<project-slug>/ so project repos
stay clean:
  latest.md              pending handoff (consumed on next session start)
  archive/<ts>-<sid>.md  every handoff ever written
  history/<ts>-<sid>.jsonl  full transcript copies ("session history")
  state.json             per-session bookkeeping

Tunables (environment variables, e.g. in settings.json "env"):
  CONTEXT_RELAY_THRESHOLD   fraction of the window that triggers a handoff (0.60)
  CONTEXT_RELAY_WINDOW      context window size in tokens (200000)
  CONTEXT_RELAY_MAX_AGE_H   never restore handoffs older than this (24)
  CONTEXT_RELAY_HOME        override ~/.claude/handoffs (used by tests)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_THRESHOLD = 0.60
DEFAULT_WINDOW = 200_000
DEFAULT_MAX_AGE_H = 24.0
# Claude Code caps SessionStart additionalContext at ~10k chars (total, header included).
MAX_INJECT_CHARS = 9_500


# ── config ──────────────────────────────────────────────────────────────────


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def threshold() -> float:
    value = _env_float("CONTEXT_RELAY_THRESHOLD", DEFAULT_THRESHOLD)
    return value if value < 1 else DEFAULT_THRESHOLD


def window() -> int:
    return int(_env_float("CONTEXT_RELAY_WINDOW", DEFAULT_WINDOW))


def max_age_seconds() -> float:
    return _env_float("CONTEXT_RELAY_MAX_AGE_H", DEFAULT_MAX_AGE_H) * 3600


def relay_home() -> Path:
    override = os.environ.get("CONTEXT_RELAY_HOME")
    return Path(override) if override else Path.home() / ".claude" / "handoffs"


def project_slug(cwd: str) -> str:
    """Filesystem-safe, stable name for a project directory."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", cwd or "unknown").strip("-")
    return slug[-80:] or "unknown"


def project_root(payload: dict | None = None) -> str:
    """The directory the session was started in.

    CLAUDE_PROJECT_DIR (set by Claude Code for hooks) wins over the payload's
    cwd, which drifts if Claude cd's into a subdirectory or worktree.
    """
    return os.environ.get("CLAUDE_PROJECT_DIR") or (payload or {}).get("cwd") or os.getcwd()


def project_dir(cwd: str) -> Path:
    path = relay_home() / project_slug(cwd)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ── transcript measurement ──────────────────────────────────────────────────


def context_tokens(transcript_path: str) -> int:
    """Tokens in context as of the last main-thread assistant message.

    input + cache_creation + cache_read is what the model was sent; adding
    output gives what the next request will start from. 0 if unreadable.
    """
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, TypeError):
        return 0
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage") or {}
        total = sum(
            int(usage.get(key) or 0)
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
        )
        if total:
            return total
    return 0


def usage_fraction(transcript_path: str) -> float:
    return context_tokens(transcript_path) / window()


# ── state ───────────────────────────────────────────────────────────────────


def _load_state(pdir: Path) -> dict:
    try:
        data = json.loads((pdir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(pdir: Path, state: dict) -> None:
    # Keep the file small: only the 20 most recent sessions.
    recent = dict(sorted(state.items(), key=lambda kv: kv[1].get("requested_at", 0))[-20:])
    (pdir / "state.json").write_text(json.dumps(recent, indent=2), encoding="utf-8")


def archive_transcript(transcript_path: str, pdir: Path, session_id: str) -> Path | None:
    """Copy the full transcript into history/ -- the raw session history."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    dest = pdir / "history" / f"{_stamp()}-{session_id[:8]}.jsonl"
    dest.parent.mkdir(exist_ok=True)
    shutil.copy2(transcript_path, dest)
    return dest


# ── hook handlers ───────────────────────────────────────────────────────────

HANDOFF_TEMPLATE = """\
# Handoff -- <one-line goal>

## Goal
What the user asked for, in their words where possible.

## Done so far
Bullet list. Name files, functions, commits, PRs.

## Current state
Branch, uncommitted changes, test status, anything running.

## Next steps
Numbered, concrete, in order. The first one should be startable immediately.

## Decisions & constraints
Choices already made (and why), user preferences, things NOT to do.

## Open questions / gotchas
Anything unresolved, flaky, or surprising.
"""


def handle_stop(payload: dict) -> dict | None:
    """Return hook JSON to emit, or None to let Claude stop normally."""
    transcript = payload.get("transcript_path") or ""
    session_id = payload.get("session_id") or "unknown"
    cwd = project_root(payload)

    tokens = context_tokens(transcript)
    frac = tokens / window()
    if frac < threshold():
        return None

    pdir = project_dir(cwd)
    latest = pdir / "latest.md"
    state = _load_state(pdir)
    entry = state.get(session_id, {})
    pct = f"{frac:.0%} ({tokens // 1000}k/{window() // 1000}k tokens)"

    if entry.get("requested_at"):
        written = latest.exists() and latest.stat().st_mtime >= entry["requested_at"]
        if written and not entry.get("archived"):
            archived = pdir / "archive" / f"{_stamp()}-{session_id[:8]}.md"
            archived.parent.mkdir(exist_ok=True)
            shutil.copy2(latest, archived)
            history = archive_transcript(transcript, pdir, session_id)
            entry.update(archived=str(archived), history=str(history) if history else None)
            _save_state(pdir, state)
        if written:
            return {"systemMessage": f"Context relay: handoff saved, context at {pct}. Type /clear to continue fresh."}
        # Asked once already and Claude didn't write it -- never loop.
        if payload.get("stop_hook_active"):
            return None
        return {"systemMessage": f"Context relay: context at {pct}; no handoff written yet. Run /handoff, then /clear."}

    state[session_id] = {"requested_at": time.time(), "tokens": tokens}
    _save_state(pdir, state)
    reason = (
        f"CONTEXT RELAY: context is at {pct}, over the {threshold():.0%} handoff threshold. "
        "Do not start new work. Write a handoff so a fresh session can continue seamlessly:\n"
        f"1. Write the file {latest.as_posix()} (overwrite it) using this structure, filled in "
        "specifically -- file paths, commands, exact error messages; a stranger must be able to "
        "resume from it alone. Keep it under ~1,200 words:\n\n"
        f"{HANDOFF_TEMPLATE}\n"
        "2. Then end your turn with one short line telling the user: "
        "'Handoff saved -- type /clear to continue in a fresh session.'"
    )
    return {"decision": "block", "reason": reason}


def handle_session_start(payload: dict) -> dict | None:
    source = payload.get("source") or "startup"
    if source == "resume":
        return None  # resumed sessions already have their own history
    pdir = project_dir(project_root(payload))
    latest = pdir / "latest.md"
    if not latest.exists():
        return None
    age = time.time() - latest.stat().st_mtime
    if age > max_age_seconds():
        # Stale: archive it so it can never hijack a later, unrelated session.
        (pdir / "archive").mkdir(exist_ok=True)
        shutil.move(str(latest), pdir / "archive" / f"{_stamp()}-stale.md")
        return None
    try:
        text = latest.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    consumed = pdir / "archive" / f"{_stamp()}-restored.md"
    consumed.parent.mkdir(exist_ok=True)
    shutil.move(str(latest), consumed)
    header = (
        "CONTEXT RELAY: this session continues work from a previous session that was cleared "
        f"to free up context (handoff written {int(age // 60)} min ago, archived at "
        f"{consumed.as_posix()}; full transcripts are in {(pdir / 'history').as_posix()}). "
        "Treat the handoff below as your working memory. Briefly confirm to the user what you are "
        "picking up, then continue with the next step unless they redirect you.\n\n"
    )
    # The cap applies to the whole injected string; long (e.g. Windows) paths eat into it.
    budget = MAX_INJECT_CHARS - len(header)
    if len(text) > budget:
        note = f"\n\n[...truncated -- read {consumed.as_posix()} for the rest]"
        text = text[: max(budget - len(note), 0)] + note
    context = header + text
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def handle_pre_compact(payload: dict) -> dict | None:
    pdir = project_dir(project_root(payload))
    saved = archive_transcript(payload.get("transcript_path") or "", pdir, payload.get("session_id") or "unknown")
    if saved:
        return {"systemMessage": f"Context relay: compaction starting -- full transcript saved to {saved.as_posix()}"}
    return None


def handle_statusline(payload: dict) -> str:
    tokens = context_tokens(payload.get("transcript_path") or "")
    frac = tokens / window()
    flag = " -> handoff" if frac >= threshold() else ""
    return f"ctx {frac:.0%} ({tokens // 1000}k/{window() // 1000}k){flag}"


def handle_path(cwd: str) -> str:
    return (project_dir(cwd) / "latest.md").as_posix()


def handle_status(cwd: str) -> str:
    projects = Path.home() / ".claude" / "projects"
    candidates = sorted(projects.glob(f"{re.sub(r'[^A-Za-z0-9]', '-', cwd)}/*.jsonl"), key=os.path.getmtime)
    where = f"handoff file: {handle_path(cwd)}"
    if not candidates:
        return f"No transcripts found for {cwd}; {where}"
    tokens = context_tokens(str(candidates[-1]))
    return (
        f"{candidates[-1].name}: {tokens:,} tokens = {tokens / window():.0%} of {window():,} "
        f"(handoff at {threshold():.0%}); {where}"
    )


HANDLERS = {"stop": handle_stop, "session-start": handle_session_start, "pre-compact": handle_pre_compact}


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command in ("path", "status"):
        cwd = argv[2] if len(argv) > 2 else project_root()
        print((handle_path if command == "path" else handle_status)(cwd))
        return 0
    if command not in (*HANDLERS, "statusline"):
        names = "|".join([*HANDLERS, "statusline", "path", "status"])
        print(f"usage: context_relay.py {{{names}}} [DIR]", file=sys.stderr)
        return 1
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        if command == "statusline":
            print(handle_statusline(payload))
            return 0
        result = HANDLERS[command](payload)
    except Exception as exc:  # a hook must never break the session
        print(f"context_relay {command}: {exc}", file=sys.stderr)
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Install context relay into ~/.claude so it works in every project.

  python install.py             # install / update
  python install.py --statusline # also set the status line
  python install.py --window 1000000  # fix the window size (desktop app + 1M model)
  python install.py --uninstall

Copies context_relay.py to ~/.claude/hooks/, handoff.md to
~/.claude/commands/ (the /handoff command), and merges Stop, SessionStart
and PreCompact hooks into ~/.claude/settings.json. Re-running is safe: our
own entries are replaced, everything else in settings.json is kept, and a
timestamped settings.json.bak-<time> copy is written first.

--window sets "env": {"CONTEXT_RELAY_WINDOW": ...}. The desktop app doesn't
run status line commands, so the hook can't learn the window size there;
without it a 1M session is treated as 200k. It applies to every session.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = "context_relay.py"  # identifies our hook entries in settings.json
WINDOW_ENV = "CONTEXT_RELAY_WINDOW"

# hook event -> (matcher or None, subcommand)
HOOKS = {
    "Stop": (None, "stop"),
    "SessionStart": ("startup|clear|compact", "session-start"),
    "PreCompact": (None, "pre-compact"),
}


def _command(script: Path, sub: str) -> str:
    # Absolute interpreter + forward slashes works in cmd, PowerShell and Git Bash.
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}" {sub}'


def _strip_ours(entries: list) -> list:
    kept = []
    for group in entries:
        hooks = [h for h in group.get("hooks", []) if MARKER not in h.get("command", "")]
        if hooks:
            kept.append({**group, "hooks": hooks})
    return kept


def merge_settings(
    settings: dict, script: Path, *, statusline: bool, uninstall: bool, window: int | None = None
) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event, (matcher, sub) in HOOKS.items():
        entries = _strip_ours(hooks.get(event, []))
        if not uninstall:
            group = {"hooks": [{"type": "command", "command": _command(script, sub), "timeout": 30}]}
            if matcher:
                group = {"matcher": matcher, **group}
            entries.append(group)
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)

    current = settings.get("statusLine", {})
    ours = MARKER in str(current.get("command", ""))
    if uninstall and ours:
        settings.pop("statusLine")
    elif statusline and (ours or not current):
        settings["statusLine"] = {"type": "command", "command": _command(script, "statusline")}

    # Only touched when asked: --window sets it, --uninstall drops it (it means nothing without the hook).
    env = settings.get("env")
    if uninstall and isinstance(env, dict) and WINDOW_ENV in env:
        env.pop(WINDOW_ENV)
        if not env:
            settings.pop("env")
    elif window and not uninstall:
        settings.setdefault("env", {})[WINDOW_ENV] = str(window)
    return settings


def run(
    claude_home: Path, *, statusline: bool = False, uninstall: bool = False, window: int | None = None
) -> list[str]:
    log = []
    script = claude_home / "hooks" / "context_relay.py"
    command = claude_home / "commands" / "handoff.md"
    settings_path = claude_home / "settings.json"

    if uninstall:
        for path in (script, command):
            if path.exists():
                path.unlink()
                log.append(f"removed {path}")
    else:
        script.parent.mkdir(parents=True, exist_ok=True)
        command.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / "context_relay.py", script)
        shutil.copy2(HERE / "commands" / "handoff.md", command)
        log += [f"installed {script}", f"installed {command}"]

    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        # Timestamped so a re-run never overwrites the original pre-install backup.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(settings_path, settings_path.with_name(f"settings.json.bak-{stamp}"))
    merged = merge_settings(settings, script, statusline=statusline, uninstall=uninstall, window=window)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    log.append(f"updated {settings_path}")
    if window and not uninstall:
        log.append(f"set env {WINDOW_ENV}={window}")
    if statusline and not uninstall and MARKER not in str(merged.get("statusLine", {}).get("command", "")):
        log.append("statusLine already set to something else -- left it alone")
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--statusline", action="store_true", help="show context %% in the status line")
    parser.add_argument("--window", type=int, metavar="TOKENS", help="force the context window size (e.g. 1000000)")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    args = parser.parse_args(argv)
    if args.window is not None and args.window <= 0:
        parser.error("--window must be a positive number of tokens")
    for line in run(args.claude_home, statusline=args.statusline, uninstall=args.uninstall, window=args.window):
        print(line)
    if not args.uninstall:
        print("\nRestart Claude Code to load the hooks. Optional: turn off auto-compact in /config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

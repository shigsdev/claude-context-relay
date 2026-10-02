"""Install context relay into ~/.claude so it works in every project.

  python install.py             # install / update
  python install.py --statusline # also set the status line
  python install.py --uninstall

Copies context_relay.py to ~/.claude/hooks/, handoff.md to
~/.claude/commands/ (the /handoff command), and merges Stop, SessionStart
and PreCompact hooks into ~/.claude/settings.json. Re-running is safe: our
own entries are replaced, everything else in settings.json is kept, and a
timestamped settings.json.bak-<time> copy is written first.
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


def merge_settings(settings: dict, script: Path, *, statusline: bool, uninstall: bool) -> dict:
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
    return settings


def run(claude_home: Path, *, statusline: bool = False, uninstall: bool = False) -> list[str]:
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
    merged = merge_settings(settings, script, statusline=statusline, uninstall=uninstall)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    log.append(f"updated {settings_path}")
    if statusline and not uninstall and MARKER not in str(merged.get("statusLine", {}).get("command", "")):
        log.append("statusLine already set to something else -- left it alone")
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--statusline", action="store_true", help="show context %% in the status line")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    args = parser.parse_args(argv)
    for line in run(args.claude_home, statusline=args.statusline, uninstall=args.uninstall):
        print(line)
    if not args.uninstall:
        print("\nRestart Claude Code to load the hooks. Optional: turn off auto-compact in /config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Smoke tests: install into a temp ~/.claude, then drive the hooks the way Claude Code does.

Each hook runs as a subprocess through a shell, using the exact command install.py wrote to
settings.json and with that file's "env" applied. So command quoting (cmd.exe on Windows,
sh on POSIX), stdin/stdout JSON, exit codes and the install -> env wiring run for real
instead of being mocked. One profile per host:

  cli-statusline  CLI installed with --statusline: Claude Code reports the window size there
  desktop         Desktop app: no status line runs, model picked in the UI (no [1m] anywhere)
  desktop-window  Desktop app installed with --window 1000000

What can't run here (the app's clear_session tool, the pinned / Remote Control fallback,
permission prompts) is the manual checklist in SMOKE.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

_RELAY = Path(__file__).resolve().parent.parent
if str(_RELAY) not in sys.path:
    sys.path.insert(0, str(_RELAY))

import install as ci  # noqa: E402


def _assistant(tokens: int, sidechain: bool = False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "role": "assistant",
            "model": "claude-opus-5-5",  # what the desktop app records: no [1m] marker
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 2_000,
                "cache_read_input_tokens": tokens - 2_203,
                "output_tokens": 200,
            },
        },
    }


class Host:
    """A temp home with context relay installed, and one session in one project."""

    def __init__(self, tmp_path: Path, *, statusline: bool = False, window: int | None = None):
        self.home = tmp_path / "home"
        self.claude = self.home / ".claude"
        self.project = tmp_path / "proj"
        self.project.mkdir()
        ci.run(self.claude, statusline=statusline, window=window)
        self.settings = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        self.session_id = "5m0ke000-0000-4000-8000-000000000001"
        # Where Claude Code keeps transcripts, so the `status` command finds it too.
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(self.project))
        self.transcript = self.claude / "projects" / slug / f"{self.session_id}.jsonl"
        self.transcript.parent.mkdir(parents=True)

    def at(self, tokens: int) -> None:
        """Make the transcript look like a session with `tokens` in context."""
        entries = [
            {"type": "summary", "summary": "earlier work"},
            {"type": "user", "message": {"role": "user", "content": "keep going"}},
            _assistant(tokens),
            _assistant(tokens * 2, sidechain=True),  # subagent usage must be ignored
            {"type": "system", "subtype": "info", "content": "hook ran"},
        ]
        self.transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    def command(self, event: str) -> str:
        if event == "statusline":
            return self.settings["statusLine"]["command"]
        groups = self.settings["hooks"][event]
        return next(h["command"] for g in groups for h in g["hooks"] if ci.MARKER in h["command"])

    def env(self) -> dict:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CONTEXT_RELAY_") and k not in ("CLAUDE_PROJECT_DIR", "ANTHROPIC_MODEL")
        }
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_PROJECT_DIR=str(self.project))
        env.update(self.settings.get("env", {}))  # Claude Code applies settings.json env to hooks
        return env

    def shell(self, command: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S602 -- running the installed command line through a shell is the point
            command, shell=True, input=stdin, capture_output=True, text=True, env=self.env(), timeout=60, check=False
        )

    def hook(self, event: str, **fields) -> dict | None:
        payload = {
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            "cwd": str(self.project),
            "hook_event_name": event,
            **fields,
        }
        proc = self.shell(self.command(event), json.dumps(payload))
        assert proc.returncode == 0, proc.stderr
        assert proc.stderr == ""
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def stop(self, stop_hook_active: bool = False) -> dict | None:
        return self.hook("Stop", stop_hook_active=stop_hook_active)

    def statusline(self, window: int) -> str:
        payload = {
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            "cwd": str(self.project),
            "context_window": {"context_window_size": window},
        }
        proc = self.shell(self.command("statusline"), json.dumps(payload))
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def cli(self, *args: str) -> str:
        """The installed script's own subcommands (`path` is what /handoff runs)."""
        script = self.claude / "hooks" / "context_relay.py"
        quoted = " ".join(f'"{a}"' for a in args)
        proc = self.shell(f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}" {quoted}')
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()


def _handoff_path(reason: str) -> Path:
    match = re.search(r"Write the file (.+?) \(overwrite it\)", reason)
    assert match, reason
    return Path(match.group(1))


class TestRelayCycle:
    def test_handoff_clear_restore(self, tmp_path):
        host = Host(tmp_path, window=1_000_000)

        host.at(100_000)
        assert host.stop() is None

        host.at(650_000)
        block = host.stop()
        assert block["decision"] == "block"
        assert "(650k/1000k tokens)" in block["reason"]
        assert "mcp__ccd_session_mgmt__clear_session" in block["reason"]
        latest = _handoff_path(block["reason"])
        assert Path(host.cli("path", str(host.project))) == latest  # /handoff writes the same file

        latest.write_text("# Handoff -- smoke\n## Next steps\n1. carry on", encoding="utf-8")
        future = time.time() + 2  # coarse filesystem clocks must still count it as written
        os.utime(latest, (future, future))
        saved = host.stop(stop_hook_active=True)
        assert "handoff saved and archived" in saved["systemMessage"]
        assert host.stop() is None  # no repeat on later turns
        assert len(list((latest.parent / "history").glob("*.jsonl"))) == 1

        restored = host.hook("SessionStart", source="clear", session_id="5m0ke000-0000-4000-8000-000000000002")
        context = restored["hookSpecificOutput"]["additionalContext"]
        assert context.startswith("CONTEXT RELAY")
        assert "1. carry on" in context
        assert not latest.exists()
        assert host.hook("SessionStart", source="clear") is None  # restored only once

    def test_pre_compact_saves_transcript(self, tmp_path):
        host = Host(tmp_path)
        host.at(190_000)
        result = host.hook("PreCompact", trigger="auto")
        assert "history" in result["systemMessage"]


class TestHostProfiles:
    def test_cli_statusline_reports_window(self, tmp_path):
        host = Host(tmp_path, statusline=True)
        host.at(145_000)
        assert host.statusline(1_000_000) == "ctx 14% (145k/1000k)"
        assert host.stop() is None  # the recorded 1M window is used, not 200k

    def test_desktop_unknown_window_never_auto_clears(self, tmp_path):
        # Regression: a desktop-app 1M session at 145k was handed off as "73% (145k/200k)".
        host = Host(tmp_path)
        host.at(145_000)
        reason = host.stop()["reason"]
        assert "Do NOT clear" in reason
        assert "clear_session" not in reason
        assert "window size unknown -- assumed" in host.cli("status", str(host.project))

    def test_desktop_window_flag(self, tmp_path):
        host = Host(tmp_path, window=1_000_000)
        host.at(145_000)
        assert host.stop() is None
        status = host.cli("status", str(host.project))
        assert "of 1,000,000" in status
        assert "assumed" not in status


class TestNeverBreaksASession:
    @pytest.mark.parametrize("event", [*ci.HOOKS, "statusline"])
    @pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"transcript_path": 42}'])
    def test_bad_payload(self, tmp_path, event, stdin):
        host = Host(tmp_path, statusline=True)
        proc = host.shell(host.command(event), stdin)
        assert proc.returncode == 0, proc.stderr
        if event != "statusline" and proc.stdout.strip():
            json.loads(proc.stdout)  # Claude Code rejects non-JSON hook output

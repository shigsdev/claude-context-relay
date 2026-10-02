"""Tests for context_relay.py and install.py (clear-and-restart instead of compacting).

Covers:
  - context_tokens(): last main-thread assistant usage, skips sidechains/garbage
  - Stop hook: below threshold no-op; over threshold blocks once; handoff
    written -> archived + /clear reminder; never loops on stop_hook_active
  - SessionStart hook: injects + archives on clear, ignores resume, archives
    stale handoffs instead of restoring them, truncates oversized handoffs
  - project_root(): CLAUDE_PROJECT_DIR beats a drifted cwd; path/status CLI
  - PreCompact hook: copies the transcript to history/
  - main(): bad payloads never raise, unknown command exits 1
  - install.py: merges hooks idempotently, keeps foreign hooks, uninstalls
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

_RELAY = Path(__file__).resolve().parent.parent
if str(_RELAY) not in sys.path:
    sys.path.insert(0, str(_RELAY))

import context_relay as cr  # noqa: E402
import install as ci  # noqa: E402


def _assistant(tokens: int, sidechain: bool = False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": tokens - 1102,
                "output_tokens": 100,
            }
        },
    }


@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_RELAY_HOME", str(tmp_path / "handoffs"))
    monkeypatch.setenv("CONTEXT_RELAY_WINDOW", "100000")
    monkeypatch.setenv("CONTEXT_RELAY_THRESHOLD", "0.5")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


def _transcript(tmp_path: Path, *entries) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in entries), encoding="utf-8")
    return str(path)


def _payload(relay: Path, tokens: int, **extra) -> dict:
    return {
        "session_id": "sess-1234abcd",
        "cwd": str(relay / "proj"),
        "transcript_path": _transcript(relay, {"type": "user"}, _assistant(tokens)),
        "stop_hook_active": False,
        **extra,
    }


class TestConfig:
    def test_defaults(self, monkeypatch):
        for name in ("CONTEXT_RELAY_THRESHOLD", "CONTEXT_RELAY_WINDOW"):
            monkeypatch.delenv(name, raising=False)
        assert cr.threshold() == cr.DEFAULT_THRESHOLD
        assert cr.window() == cr.DEFAULT_WINDOW

    @pytest.mark.parametrize("value", ["abc", "-1", "0", "1.5"])
    def test_bad_threshold_falls_back(self, monkeypatch, value):
        monkeypatch.setenv("CONTEXT_RELAY_THRESHOLD", value)
        assert cr.threshold() == cr.DEFAULT_THRESHOLD

    def test_project_root_prefers_claude_project_dir(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/repo")
        assert cr.project_root({"cwd": "/repo/sub"}) == "/repo"
        monkeypatch.delenv("CLAUDE_PROJECT_DIR")
        assert cr.project_root({"cwd": "/repo/sub"}) == "/repo/sub"

    def test_project_slug_is_filesystem_safe(self):
        assert cr.project_slug("C:\\shigsapps\\windesktopmgr") == "C-shigsapps-windesktopmgr"
        assert cr.project_slug("") == "unknown"


class TestContextTokens:
    def test_uses_last_main_thread_assistant(self, tmp_path):
        path = _transcript(tmp_path, _assistant(10_000), _assistant(50_000), _assistant(99_000, sidechain=True))
        assert cr.context_tokens(path) == 50_000

    def test_skips_garbage_lines(self, tmp_path):
        path = _transcript(tmp_path, _assistant(20_000), "not json", "[1, 2]", {"type": "user"})
        assert cr.context_tokens(path) == 20_000

    def test_missing_file_is_zero(self, tmp_path):
        assert cr.context_tokens(str(tmp_path / "nope.jsonl")) == 0
        assert cr.context_tokens(None) == 0


class TestStopHook:
    def test_below_threshold_does_nothing(self, relay):
        assert cr.handle_stop(_payload(relay, 30_000)) is None

    def test_over_threshold_blocks_with_handoff_path(self, relay):
        result = cr.handle_stop(_payload(relay, 60_000))
        assert result["decision"] == "block"
        assert "latest.md" in result["reason"]
        assert "/clear" in result["reason"]

    def test_no_handoff_written_does_not_loop(self, relay):
        cr.handle_stop(_payload(relay, 60_000))
        assert cr.handle_stop(_payload(relay, 61_000, stop_hook_active=True)) is None

    def test_no_handoff_later_turn_reminds_without_blocking(self, relay):
        cr.handle_stop(_payload(relay, 60_000))
        result = cr.handle_stop(_payload(relay, 62_000))
        assert "decision" not in result
        assert "/handoff" in result["systemMessage"]

    def test_written_handoff_is_archived_once(self, relay):
        payload = _payload(relay, 60_000)
        cr.handle_stop(payload)
        pdir = cr.project_dir(payload["cwd"])
        (pdir / "latest.md").write_text("# Handoff -- x", encoding="utf-8")
        future = time.time() + 5
        os.utime(pdir / "latest.md", (future, future))

        first = cr.handle_stop({**payload, "stop_hook_active": True})
        second = cr.handle_stop(payload)
        assert "/clear" in first["systemMessage"]
        assert "/clear" in second["systemMessage"]
        assert len(list((pdir / "archive").glob("*.md"))) == 1
        assert len(list((pdir / "history").glob("*.jsonl"))) == 1


class TestSessionStartHook:
    def _write_handoff(self, relay: Path, text: str = "# Handoff -- goal\n## Next steps\n1. go") -> Path:
        latest = cr.project_dir(str(relay / "proj")) / "latest.md"
        latest.write_text(text, encoding="utf-8")
        return latest

    def test_clear_injects_and_consumes(self, relay):
        latest = self._write_handoff(relay)
        result = cr.handle_session_start({"source": "clear", "cwd": str(relay / "proj")})
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "# Handoff -- goal" in ctx
        assert not latest.exists()
        assert cr.handle_session_start({"source": "clear", "cwd": str(relay / "proj")}) is None

    def test_resume_is_ignored(self, relay):
        self._write_handoff(relay)
        assert cr.handle_session_start({"source": "resume", "cwd": str(relay / "proj")}) is None

    @pytest.mark.parametrize("source", ["startup", "clear", "compact"])
    def test_stale_handoff_archived_not_restored(self, relay, source):
        latest = self._write_handoff(relay)
        old = time.time() - 3 * 86400
        os.utime(latest, (old, old))
        assert cr.handle_session_start({"source": source, "cwd": str(relay / "proj")}) is None
        assert not latest.exists()
        assert list((latest.parent / "archive").glob("*-stale.md"))

    def test_hook_uses_project_dir_not_drifted_cwd(self, relay, monkeypatch):
        self._write_handoff(relay)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(relay / "proj"))
        assert cr.handle_session_start({"source": "clear", "cwd": str(relay / "proj" / "sub")}) is not None

    def test_empty_handoff_ignored(self, relay):
        self._write_handoff(relay, "   ")
        assert cr.handle_session_start({"source": "clear", "cwd": str(relay / "proj")}) is None

    def test_oversized_handoff_truncated(self, relay):
        self._write_handoff(relay, "x" * 20_000)
        ctx = cr.handle_session_start({"source": "clear", "cwd": str(relay / "proj")})["hookSpecificOutput"][
            "additionalContext"
        ]
        assert len(ctx) < 10_000
        assert "truncated" in ctx


class TestPreCompactAndStatusline:
    def test_pre_compact_saves_transcript(self, relay):
        result = cr.handle_pre_compact(_payload(relay, 90_000))
        assert "history" in result["systemMessage"]

    def test_pre_compact_without_transcript(self, relay):
        assert cr.handle_pre_compact({"cwd": str(relay)}) is None

    def test_statusline_flags_over_threshold(self, relay):
        assert cr.handle_statusline(_payload(relay, 30_000)) == "ctx 30% (30k/100k)"
        assert cr.handle_statusline(_payload(relay, 70_000)).endswith("-> handoff")


class TestMain:
    def _run(self, monkeypatch, capsys, args, stdin=""):
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        code = cr.main(["context_relay.py", *args])
        return code, capsys.readouterr()

    def test_unknown_command(self, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, ["bogus"])
        assert code == 1
        assert "usage" in out.err

    @pytest.mark.parametrize("stdin", ["", "garbage", "[1]"])
    def test_bad_payload_is_harmless(self, relay, monkeypatch, capsys, stdin):
        code, out = self._run(monkeypatch, capsys, ["stop"], stdin)
        assert code == 0
        assert out.out == ""

    def test_emits_json(self, relay, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, ["stop"], json.dumps(_payload(relay, 80_000)))
        assert json.loads(out.out)["decision"] == "block"

    def test_path_command(self, relay, monkeypatch, capsys):
        code, out = self._run(monkeypatch, capsys, ["path", str(relay / "proj")])
        assert code == 0
        assert out.out.strip().endswith("-proj/latest.md")

    def test_status_without_transcripts_still_names_handoff_file(self, relay, monkeypatch, capsys):
        monkeypatch.setattr(cr.Path, "home", lambda: relay)
        code, out = self._run(monkeypatch, capsys, ["status", str(relay / "proj")])
        assert "No transcripts" in out.out
        assert "latest.md" in out.out

    def test_status_reports_usage(self, relay, monkeypatch, capsys):
        project = str(relay / "proj")
        tdir = relay / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", project)
        tdir.mkdir(parents=True)
        (tdir / "s.jsonl").write_text(json.dumps(_assistant(40_000)), encoding="utf-8")
        monkeypatch.setattr(cr.Path, "home", lambda: relay)
        _, out = self._run(monkeypatch, capsys, ["status", project])
        assert "40,000 tokens = 40%" in out.out

    def test_handler_crash_never_breaks_session(self, monkeypatch, capsys):
        monkeypatch.setitem(cr.HANDLERS, "stop", lambda _p: 1 / 0)
        code, out = self._run(monkeypatch, capsys, ["stop"], "{}")
        assert code == 0
        assert "division" in out.err


class TestInstall:
    def test_install_merges_and_is_idempotent(self, tmp_path):
        home = tmp_path / ".claude"
        home.mkdir()
        foreign = {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo hi"}]}
        (home / "settings.json").write_text(json.dumps({"model": "x", "hooks": {"Stop": [foreign]}}))

        ci.run(home)
        ci.run(home)
        settings = json.loads((home / "settings.json").read_text())

        assert settings["model"] == "x"
        assert settings["hooks"]["Stop"][0] == foreign
        assert len(settings["hooks"]["Stop"]) == 2
        assert settings["hooks"]["SessionStart"][0]["matcher"] == "startup|clear|compact"
        assert (home / "hooks" / "context_relay.py").exists()
        assert (home / "commands" / "handoff.md").exists()
        assert list(home.glob("settings.json.bak-*"))

    def test_rerun_keeps_original_backup(self, tmp_path, monkeypatch):
        home = tmp_path / ".claude"
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"original": True}))
        stamps = iter(["20260101-000000", "20260101-000001"])

        class FakeDatetime:
            @staticmethod
            def now():
                return FakeDatetime()

            def strftime(self, _fmt):
                return next(stamps)

        monkeypatch.setattr(ci, "datetime", FakeDatetime)
        ci.run(home)
        ci.run(home)
        first = json.loads((home / "settings.json.bak-20260101-000000").read_text())
        assert first == {"original": True}

    def test_main_prints_log(self, tmp_path, capsys):
        assert ci.main(["--claude-home", str(tmp_path / ".claude")]) == 0
        assert "Restart Claude Code" in capsys.readouterr().out

    def test_statusline_not_clobbered(self, tmp_path):
        home = tmp_path / ".claude"
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "mine"}}))
        log = ci.run(home, statusline=True)
        assert json.loads((home / "settings.json").read_text())["statusLine"]["command"] == "mine"
        assert any("left it alone" in line for line in log)

    def test_uninstall_removes_only_ours(self, tmp_path):
        home = tmp_path / ".claude"
        ci.run(home, statusline=True)
        ci.run(home, uninstall=True)
        settings = json.loads((home / "settings.json").read_text())
        assert settings == {}
        assert not (home / "hooks" / "context_relay.py").exists()

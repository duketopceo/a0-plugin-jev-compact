"""Hermes port — register(ctx), JevContextEngine, engine-carried tools.

The ctx seam is a NoopPluginContext under real Hermes context-engine
loading: only register_context_engine does work. These tests stub that
capture surface and assert the engine's ContextEngine-contract behavior —
deferred compaction, non-destructive store, read-only restore tools.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from usr.plugins.jev_compact import hermes_adapter
from usr.plugins.jev_compact.helpers.engine.jev_compact import store

ROOT = Path(__file__).resolve().parents[1]


class _CaptureCtx:
    """Mirrors plugins/context_engine/_EngineCollector: captures
    register_context_engine, forwards register_command, no-ops the rest."""

    def __init__(self):
        self.engine = None

    def register_context_engine(self, engine):
        self.engine = engine

    def register_command(self, *a, **kw):
        pass

    def __getattr__(self, name):  # NoopPluginContext shape
        return lambda *a, **kw: None


def _engine(tmp_path, **settings):
    cfg = {"enabled": True, "scorer": "heuristic", "min_tokens": 200,
           "budget_tokens": 2000, "tail_keep": 2}
    cfg.update(settings)
    return hermes_adapter.JevContextEngine(
        settings=cfg, store_root=tmp_path / "store")


def _openai_messages(pad: str) -> list[dict]:
    """A realistic OpenAI-format conversation — content padded past the
    min_tokens floor so drops occur."""
    return [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "Fix the login bug in auth.py" + pad},
        {"role": "assistant",
         "content": "Reading auth.py — the handler drops the session "
                    "token on refresh." + pad},
        {"role": "tool", "name": "code_execution",
         "content": "pytest: 2 failed" + pad},
        {"role": "assistant",
         "content": "Root cause: missing session.commit() after token "
                    "rotation." + pad},
        {"role": "user", "content": "Ship it" + pad},
        {"role": "assistant", "content": "Committed 9f3ab — green." + pad},
        {"role": "user", "content": "Now update the changelog" + pad},
        {"role": "assistant", "content": "Updated CHANGELOG.md." + pad},
    ]


PAD = " working through the auth flow, rotating tokens, " * 80


# -- registration --------------------------------------------------------------


def test_register_captures_engine():
    ctx = _CaptureCtx()
    hermes_adapter.register(ctx)
    assert isinstance(ctx.engine, hermes_adapter.JevContextEngine)
    assert ctx.engine.name == "jev_compact"


def test_register_noop_ctx_warns_not_raises(caplog):
    class Empty:  # a ctx without register_context_engine at all
        pass

    hermes_adapter.register(Empty())
    assert any("register_context_engine" in r.message
               for r in caplog.records)


def test_root_init_register_loads_adapter():
    """The repo-root __init__.py is the Hermes entrypoint — load it as a
    standalone module (exercises the file-path fallback) and register."""
    spec = importlib.util.spec_from_file_location(
        "jev_compact_root_init", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jev_compact_root_init"] = mod
    try:
        spec.loader.exec_module(mod)
        ctx = _CaptureCtx()
        mod.register(ctx)
        assert ctx.engine is not None
        assert ctx.engine.name == "jev_compact"
    finally:
        sys.modules.pop("jev_compact_root_init", None)


# -- engine contract ------------------------------------------------------------


def test_disabled_engine_is_inert(tmp_path):
    eng = _engine(tmp_path, enabled=False)
    msgs = _openai_messages(PAD)
    assert eng.should_compress(prompt_tokens=999_999) is False
    assert eng.should_compress_preflight(msgs) is False
    out = eng.compress(msgs)
    assert out == msgs  # unchanged — never partial mutation
    assert list(tmp_path.rglob("*.json")) == []  # no store writes


def test_compress_replaces_convo_and_persists_store(tmp_path):
    eng = _engine(tmp_path)
    eng.on_session_start("sess-1")
    msgs = _openai_messages(PAD)
    out = eng.compress(msgs)

    # systems preserved verbatim; convo → single assistant message
    assert out[0]["role"] == "system" and out[0] == msgs[0]
    assert len(out) == 2
    assert out[1]["role"] == "assistant"
    assert "jev_compact" in out[1]["content"]

    # non-destructive: span store + pre-compact backup exist
    sess_dir = tmp_path / "store" / "sess-1"
    assert (sess_dir / "spans.json").exists()
    assert (sess_dir / "highlight.json").exists()
    backup = sess_dir / "pre_compact.json"
    assert backup.exists()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert eng.compression_count == 1


def test_deferred_compact_tool_queues_request(tmp_path):
    eng = _engine(tmp_path)
    assert eng.should_compress(prompt_tokens=1) is False
    resp = json.loads(eng.handle_tool_call("jev_compact", {}))
    assert resp["status"] == "queued"
    assert eng.should_compress(prompt_tokens=1) is True
    # consumed by the next compress — not re-triggered forever
    eng.compress(_openai_messages(PAD))
    assert eng.should_compress(prompt_tokens=1) is False


def test_disabled_tool_reports_error(tmp_path):
    eng = _engine(tmp_path, enabled=False)
    resp = json.loads(eng.handle_tool_call("jev_compact", {}))
    assert "disabled" in resp["error"]


def test_restore_tools_read_the_store(tmp_path):
    eng = _engine(tmp_path)
    eng.on_session_start("sess-1")
    eng.compress(_openai_messages(PAD))

    sessions = json.loads(eng.handle_tool_call("list_sessions", {}))
    assert "sess-1" in sessions["sessions"]

    tombs = json.loads(eng.handle_tool_call("list_tombstones", {}))
    assert tombs["session"] == "sess-1"
    assert tombs["tombstones"]  # dropped spans left receipts
    rng = tombs["tombstones"][0]["range"]

    span = json.loads(eng.handle_tool_call("get_span", {"span": rng}))
    assert span["text"].strip()

    hl = json.loads(eng.handle_tool_call("get_highlight", {}))
    assert hl["highlight"]


def test_restore_tools_error_when_no_sessions(tmp_path):
    eng = _engine(tmp_path)
    resp = json.loads(eng.handle_tool_call("get_span", {"span": "s1"}))
    assert "error" in resp
    resp = json.loads(eng.handle_tool_call("list_tombstones", {}))
    assert "error" in resp


def test_clone_for_agent_is_safe(tmp_path):
    eng = _engine(tmp_path)
    eng.on_session_start("sess-1")
    clone = eng.clone_for_agent()
    assert clone is not eng
    # cloned budget state, shared backend path
    assert clone._store_root == eng._store_root
    clone._compact_requested = True
    assert eng._compact_requested is False


def test_events_mapping():
    evs = hermes_adapter._events_from_messages([
        {"role": "system", "content": "skip me"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi",
         "tool_calls": [{"function": {"name": "shell"}}]},
        {"role": "tool", "name": "shell", "content": "ok"},
    ])
    assert [e["kind"] for e in evs] == ["user_turn", "assistant_text", "tool"]
    assert "shell" in evs[1]["text"]  # tool-call intent preserved
    assert evs[2]["text"].startswith("shell:")


def test_status_surfaces_engine_state(tmp_path):
    eng = _engine(tmp_path)
    eng.update_model("test-model", 128_000)
    assert eng.threshold_tokens == int(128_000 * 0.75)
    eng.update_from_response(
        {"prompt_tokens": 50_000, "completion_tokens": 500,
         "total_tokens": 50_500})
    st = eng.get_status()
    assert st["last_prompt_tokens"] == 50_000
    assert st["enabled"] is True
    assert st["pending_compact"] is False

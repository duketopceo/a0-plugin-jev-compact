"""a0-adapter behavior — history→events bridge, compact_context
end-to-end, deferred tool request, API handler contract."""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from conftest import run

from usr.plugins.jev_compact.helpers import (
    a0_history, compactor, mcp_register, runtime,
)
from usr.plugins.jev_compact.helpers.config import JevCompactConfig


def _cfg(**over):
    base = dict(enabled=True, scorer="heuristic", budget_tokens=2_000,
                tail_keep=3, min_tokens=200, auto_register_mcp=True)
    base.update(over)
    return JevCompactConfig(**base)


def test_events_from_history_maps_kinds(chat_events):
    evs = a0_history.events_from_history(chat_events)
    kinds = [e["kind"] for e in evs]
    assert kinds[0] == "user_turn" and kinds[1] == "assistant_text"
    assert "tool" in kinds  # the dict-content tool_result record
    tool_ev = next(e for e in evs if e["kind"] == "tool")
    assert "code_execution" in tool_ev["text"]
    assert all(e["text"].strip() for e in evs)


def test_events_skips_empty_and_non_dict():
    evs = a0_history.events_from_history(
        [{"ai": True, "content": ""}, {"ai": True, "content": "real"},
         "garbage", {"content": {"preview": "prev text"}}])
    assert [e["text"] for e in evs][:2] == ["real", "prev text"]


def test_stringify_content_variants():
    assert a0_history.stringify_content("x") == "x"
    assert a0_history.stringify_content({"preview": "P"}) == "P"
    assert a0_history.stringify_content({"a": 1}) == '{"a": 1}'
    assert a0_history.stringify_content(None) == ""
    long_raw = {"x": "y" * 40_000}
    assert a0_history.stringify_content(long_raw).endswith("... TRIMMED")


def test_context_stats(make_context, chat_events):
    ctx = make_context(messages=chat_events)
    cfg = _cfg()
    stats = compactor.context_stats(ctx, cfg)
    assert stats["span_count"] >= len(chat_events)
    assert stats["token_count"] > 0
    assert stats["scorer"] == "heuristic"


def test_compact_context_end_to_end(make_context, chat_events, tmp_path,
                                    monkeypatch, saved_chats):
    """History replaced with compacted text; spans + backup persisted;
    tombstones resolve through the store."""
    monkeypatch.setattr(
        mcp_register, "store_root", lambda: tmp_path / "store")
    ctx = make_context("ctx9", messages=chat_events)
    cfg = _cfg()

    result = asyncio.run(compactor.compact_context(ctx, cfg))

    assert result["tombstoned"] >= 1  # padded fixture forces drops
    assert result["tokens_after"] < result["tokens_before"]
    # history replaced with a single compacted message
    assert len(ctx.agent0.history.messages) == 1
    body = ctx.agent0.history.messages[0]["content"]
    assert "CURRENT WORK" in body and "tombstoned" in body
    # store persisted + resolves
    sess = tmp_path / "store" / "ctx9"
    assert (sess / "spans.json").exists()
    assert (sess / "highlight.json").exists()
    assert (sess / "pre_compact.json").exists()
    src = json.loads((sess / "pre_compact.json").read_text())
    assert src["context"] == "ctx9"
    # provider state cleared + chat persisted
    assert "ctx9" in saved_chats


def test_compact_refuses_empty(make_context):
    ctx = make_context(messages=[])
    with pytest.raises(compactor.CompactError):
        asyncio.run(compactor.compact_context(ctx, _cfg()))


def test_compact_refuses_tiny(make_context):
    ctx = make_context(messages=[{"ai": False, "content": "hi"}])
    with pytest.raises(compactor.CompactError, match="not enough spans"):
        asyncio.run(compactor.compact_context(ctx, _cfg()))


def test_jev_scorer_missing_key_fails_loud(make_context, chat_events):
    ctx = make_context(messages=chat_events)
    cfg = _cfg(scorer="jev")
    with pytest.raises(compactor.CompactError, match="scorer=jev"):
        asyncio.run(compactor.compact_context(ctx, cfg))


# --- deferred tool request ---------------------------------------------------


def test_tool_queues_deferred_compact(make_context):
    from usr.plugins.jev_compact.tools.compact_request import JevCompact

    ctx = make_context()
    runtime._reset()
    runtime.configure({"enabled": True, "auto_register_mcp": False})
    tool = JevCompact()
    tool.agent = types.SimpleNamespace(context=ctx)
    resp = run(tool.execute())
    assert "queued" in resp.message.lower()
    assert runtime.pop_pending(ctx.id) is True
    assert runtime.pop_pending(ctx.id) is False  # consumed once


def test_tool_refuses_when_disabled(make_context):
    from usr.plugins.jev_compact.tools.compact_request import JevCompact

    ctx = make_context()
    runtime._reset()
    runtime.configure({"enabled": False, "auto_register_mcp": False})
    tool = JevCompact()
    tool.agent = types.SimpleNamespace(context=ctx)
    resp = run(tool.execute())
    assert "disabled" in resp.message.lower()
    assert runtime.pop_pending(ctx.id) is False


def test_loop_end_extension_runs_pending(make_context, chat_events,
                                         tmp_path, monkeypatch,
                                         saved_chats):
    """message_loop_end ext consumes the deferred flag + compacts."""
    monkeypatch.setattr(mcp_register, "store_root",
                        lambda: tmp_path / "store")
    ctx = make_context(messages=chat_events)
    runtime._reset()
    runtime.configure({"enabled": True, "scorer": "heuristic",
                       "min_tokens": 200, "auto_register_mcp": False})
    runtime.request_compact(ctx.id)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "loop_end_ext",
        mcp_register.__file__.replace("helpers/mcp_register.py",
            "extensions/python/message_loop_end/_75_jev_compact.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ext = mod.JevCompactPending(agent=ctx.agent0)
    asyncio.run(ext.execute())
    assert len(ctx.agent0.history.messages) == 1
    assert runtime.pop_pending(ctx.id) is False  # consumed
    assert any("span-compacted" in str(l.get("heading", ""))
               for l in ctx.log.logs)


def test_loop_end_noop_without_flag(make_context):
    ctx = make_context()
    runtime._reset()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "loop_end_ext2",
        mcp_register.__file__.replace("helpers/mcp_register.py",
            "extensions/python/message_loop_end/_75_jev_compact.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    asyncio.run(mod.JevCompactPending(agent=ctx.agent0).execute())
    assert ctx.log.logs == []


# --- API handler ---------------------------------------------------------------


def _handler():
    from usr.plugins.jev_compact.api.compact import Compact

    return Compact(app=None, thread_lock=None)


def test_api_disabled(make_context):
    runtime._reset()
    runtime.configure({"enabled": False, "auto_register_mcp": False})
    out = run(_handler().process({"context": "ctx1", "action": "compact"},
                                 None))
    assert out == {"ok": False, "message": "jev_compact is disabled"}


def test_api_guards(make_context, chat_events):
    runtime._reset()
    runtime.configure({"enabled": True, "auto_register_mcp": False})
    h = _handler()
    assert run(h.process({"action": "compact"}, None)).status_code == 400
    assert run(h.process({"context": "nope"}, None)).status_code == 404
    ctx = make_context(messages=chat_events, running=True)
    assert run(h.process({"context": ctx.id}, None)).status_code == 409


def test_api_stats_and_compact(make_context, chat_events, tmp_path,
                               monkeypatch, saved_chats):
    monkeypatch.setattr(mcp_register, "store_root",
                        lambda: tmp_path / "store")
    runtime._reset()
    runtime.configure({"enabled": True, "scorer": "heuristic",
                       "min_tokens": 200, "auto_register_mcp": False})
    ctx = make_context(messages=chat_events)
    h = _handler()

    st = run(h.process({"context": ctx.id, "action": "stats"}, None))
    assert st["ok"] and st["stats"]["span_count"] > 0

    out = run(h.process({"context": ctx.id, "action": "compact"}, None))
    assert out["ok"] is True
    # drain the scheduled compaction coroutine (run_task stub captures it)
    assert ctx.task and ctx.task.coro is not None
    asyncio.run(ctx.task.coro)
    assert len(ctx.agent0.history.messages) == 1
    assert ctx.id in saved_chats


def test_status_handler_shape(settings_state):
    runtime._reset()
    runtime.configure({"enabled": True, "auto_register_mcp": False})
    from usr.plugins.jev_compact.api.jev_status import JevStatus

    st = run(JevStatus(None, None).process({}, None))
    assert st["enabled"] is True and st["configured"] is True
    assert st["server_name"] == mcp_register.SERVER_NAME
    # secret VALUES never appear (names like scorer_secret_* are fine)
    assert '"token' not in json.dumps(st).lower()
    assert "sk-" not in json.dumps(st)

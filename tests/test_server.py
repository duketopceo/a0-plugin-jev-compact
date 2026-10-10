"""Restore server tool behavior — module loads standalone (only the
plugin dir + vendored engine on sys.path) and tools hit the store."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_server(store_dir: Path, monkeypatch):
    """Exec server/restore_server.py with JEV_STORE_DIR + a fake FastMCP
    — exercises the real module top-to-bottom without the mcp dep."""
    monkeypatch.setenv("JEV_STORE_DIR", str(store_dir))

    class FakeMCP:
        def __init__(self, name):
            self.name = name
            self.tools = {}

        def tool(self):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

        def run(self):
            pass

    fake_mod = type(sys)("mcp")
    server_mod = type(sys)("mcp.server")
    fastmcp_mod = type(sys)("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FakeMCP
    monkeypatch.setitem(sys.modules, "mcp", fake_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_mod)

    spec = importlib.util.spec_from_file_location(
        "restore_server_under_test", ROOT / "server" / "restore_server.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    finally:
        # the server inserts helpers/engine onto sys.path — don't leak it
        eng = str(ROOT / "helpers" / "engine")
        while eng in sys.path:
            sys.path.remove(eng)
    return mod


def _seed(store_root: Path, session="sess1"):
    from usr.plugins.jev_compact.helpers.engine.jev_compact import (
        compact as eng_compact, highlight as eng_highlight,
        scorer as eng_scorer, spans as eng_spans, store as eng_store,
    )
    evs = [{"kind": "user_turn", "text": f"turn {i} about alpha"} 
           if i % 2 == 0 else {"kind": "assistant_text",
                               "text": f"reply {i} did work on beta.py"}
           for i in range(10)]
    spans = eng_spans.segment(evs)
    hl = eng_highlight.extract(spans)
    res = eng_compact.compact(spans, hl, eng_scorer.HeuristicScorer(10),
                              budget_tokens=40, tail_keep=2)
    eng_store.SpanStore(store_root, session).save(
        spans, hl, res.tombstoned, source=store_root / "t.json")
    return res


def test_server_tools_roundtrip(tmp_path, monkeypatch):
    res = _seed(tmp_path / "st")
    srv = _load_server(tmp_path / "st", monkeypatch)
    for name in ("get_span", "list_tombstones", "get_highlight",
                 "list_sessions"):
        assert name in srv.mcp.tools

    assert "sess1" in srv.mcp.tools["list_sessions"]()
    assert "CURRENT WORK" in srv.mcp.tools["get_highlight"]()
    tb = srv.mcp.tools["list_tombstones"]()
    if res.tombstoned:
        assert "preview:" in tb
        rng = res.tombstoned[0][0]
        out = srv.mcp.tools["get_span"](span=rng)
        assert "error" not in out and out.strip()
    assert "not found" in srv.mcp.tools["get_span"](span="s999",
                                                  session="sess1")


def test_server_empty_store(tmp_path, monkeypatch):
    srv = _load_server(tmp_path / "empty", monkeypatch)
    assert "no sessions" in srv.mcp.tools["get_span"](span="s1")
    assert srv.mcp.tools["list_sessions"]() == "none"


def test_server_is_read_only():
    """The restore surface never mutates the store."""
    src = (ROOT / "server" / "restore_server.py").read_text()
    for banned in ("write_text", "os.remove", "os.unlink", "shutil.rmtree",
                   "open(", ".save("):
        assert banned not in src


def test_a0_root_detects_symlink_install(tmp_path):
    """usr/plugins/jev_compact -> repo checkout: __file__ keeps the
    usr/plugins component; resolve() would follow the link OUT and miss
    the a0 root entirely (store/vault silently fell back to repo-local
    paths before the unresolved-first walk)."""
    from usr.plugins.jev_compact.helpers import secrets_env

    a0 = tmp_path / "a0"
    (a0 / "usr" / "plugins").mkdir(parents=True)
    repo = tmp_path / "a0-plugin-jev-compact"
    (repo / "helpers").mkdir(parents=True)
    (a0 / "usr" / "plugins" / "jev_compact").symlink_to(repo)

    # path as Python imports it — THROUGH the symlink (unresolved)
    imported = (a0 / "usr" / "plugins" / "jev_compact" / "helpers"
                / "secrets_env.py")
    assert secrets_env.a0_root_from(imported) == a0

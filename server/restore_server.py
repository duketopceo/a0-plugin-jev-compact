#!/usr/bin/env python3
"""a0_jev_compact — stdio MCP server exposing tombstone restore tools.

Runs as a spawned subprocess under a0's stdio MCP transport (or any
MCP-capable harness — Hermes registers the identical block). Reads the
span store written by the plugin's compactor; never writes.

  tools:
    get_span(span: "s4" | "s4-s9", session?: str)  -> verbatim span text
    list_tombstones(session?: str)                 -> receipts + previews
    get_highlight(session?: str)                   -> moving-highlight text
    list_sessions()                                -> known session ids

Config: JEV_STORE_DIR points at the store root (baked into the
mcp_servers entry env by helpers/mcp_register).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The spawned process gets only this file's dir on sys.path — add the
# vendored engine root so `jev_compact` resolves standalone.
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_DIR / "helpers" / "engine"))

from jev_compact import __version__, store  # noqa: E402

STORE_ROOT = Path(os.environ.get("JEV_STORE_DIR", ".jev_store")).resolve()

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "mcp package not available — run under the a0 host interpreter "
        f"(sys.executable is pinned in the mcp_servers entry): {exc}"
    )

mcp = FastMCP("a0-jev-compact")


def _store(session: str | None) -> store.SpanStore | None:
    sid = session or store.latest_session(STORE_ROOT)
    return store.SpanStore(STORE_ROOT, sid) if sid else None


@mcp.tool()
def get_span(span: str, session: str = "") -> str:
    """Rehydrate a tombstoned span or range ('s4', 's4-s9') to verbatim
    transcript text. `session` defaults to the most recent."""
    st = _store(session or None)
    if st is None:
        return f"error: no sessions under {STORE_ROOT}"
    text = st.get_range(str(span))
    return text if text else f"error: span not found: {span}"


@mcp.tool()
def list_tombstones(session: str = "") -> str:
    """List tombstone receipts left by compaction: range, receipt text,
    and a preview of the first span."""
    st = _store(session or None)
    if st is None:
        return f"error: no sessions under {STORE_ROOT}"
    rows = st.list_tombstones()
    if not rows:
        return "none"
    return "\n".join(
        f"{t['range']}: {t['receipt']}  preview: {t['preview']}"
        for t in rows
    )


@mcp.tool()
def get_highlight(session: str = "") -> str:
    """Return the moving-highlight spec recorded at last compaction —
    what the conversation was about."""
    st = _store(session or None)
    if st is None:
        return f"error: no sessions under {STORE_ROOT}"
    return st.get_highlight() or "none recorded"


@mcp.tool()
def list_sessions() -> str:
    """List span-store session ids (a0 context ids) that have tombstones
    or saved spans."""
    try:
        if not STORE_ROOT.is_dir():
            return "none"
        ids = sorted(
            d.name for d in STORE_ROOT.iterdir()
            if d.is_dir() and (d / "spans.json").exists()
        )
    except OSError as exc:
        return f"error: {exc}"
    return "\n".join(ids) if ids else "none"


if __name__ == "__main__":
    mcp.run()

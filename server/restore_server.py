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

import json
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


# The store only changes when a compaction runs — memoize parsed files
# keyed by (path, mtime, size) so an agent pulling s4, s7, s9 doesn't
# re-parse the whole spans.json per call.
_span_cache: dict = {}


def _load_json(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime, st.st_size)
    if key not in _span_cache:
        try:
            _span_cache.clear()  # old-mtime entries are stale by definition
            _span_cache[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return _span_cache[key]


def _spans(session_dir: Path) -> list:
    return _load_json(session_dir / "spans.json") or []


def _session(session: str) -> Path | None:
    """Resolve a session arg to a store dir — explicit id, else latest."""
    if session:
        d = STORE_ROOT / Path(session).name  # ids are flat dir names
        return d if (d / "spans.json").exists() else None
    sid = store.latest_session(STORE_ROOT)
    return STORE_ROOT / sid if sid else None


def _require(session: str):
    """(session_dir, error) — one preamble for all three store tools."""
    d = _session(session)
    if d is None:
        return None, f"error: no sessions under {STORE_ROOT}"
    return d, None


@mcp.tool()
def get_span(span: str, session: str = "") -> str:
    """Rehydrate a tombstoned span or range ('s4', 's4-s9') to verbatim
    transcript text. `session` defaults to the most recent."""
    d, err = _require(session)
    if err:
        return err
    lo, hi = _range(str(span))
    if lo is None:
        return f"error: bad span id: {span}"
    picked = [
        s["text"] for s in _spans(d)
        if lo <= _num(s.get("id", "")) <= (hi if hi is not None else lo)
    ]
    return "\n\n".join(picked) if picked else f"error: span not found: {span}"


@mcp.tool()
def list_tombstones(session: str = "") -> str:
    """List tombstone receipts left by compaction: range, receipt text,
    and a preview of the first span."""
    d, err = _require(session)
    if err:
        return err
    hl = _load_json(d / "highlight.json") or {}
    spans = {s.get("id"): s for s in _spans(d)}
    rows = []
    for rng in hl.get("tombstoned", []):
        lo, _ = _range(rng)
        first = next(
            (s for s in spans.values() if _num(s.get("id", "")) == lo),
            None,
        )
        rows.append(
            f"{rng}: {hl.get('receipts', {}).get(rng, '')}  "
            f"preview: {first['preview'] if first else ''}"
        )
    return "\n".join(rows) if rows else "none"


@mcp.tool()
def get_highlight(session: str = "") -> str:
    """Return the moving-highlight spec recorded at last compaction —
    what the conversation was about."""
    d, err = _require(session)
    if err:
        return err
    hl = _load_json(d / "highlight.json") or {}
    return str(hl.get("text", "")) or "none recorded"


@mcp.tool()
def list_sessions() -> str:
    """List span-store session ids (a0 context ids) that have saved
    spans."""
    ids = store.list_sessions(STORE_ROOT)
    return "\n".join(ids) if ids else "none"


def _range(rng: str) -> tuple[int | None, int | None]:
    parts = rng.replace("s", "").split("-")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else int(parts[0])
    except (ValueError, IndexError):
        return None, None


def _num(span_id: str) -> int:
    try:
        return int(span_id.lstrip("s"))
    except (ValueError, AttributeError):
        return -1


if __name__ == "__main__":
    mcp.run()

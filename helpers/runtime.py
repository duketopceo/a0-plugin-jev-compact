"""Runtime facade — configure(), status(), deferred-compact flags.

Module-level state is configured once at startup by the
``startup_migration`` extension. The ``_pending`` set carries agent-tool
compaction requests to the ``message_loop_end`` extension — a tool can
never compact mid-monologue safely, so it defers to the loop boundary.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from . import mcp_register
from .config import JevCompactConfig, get_config

log = logging.getLogger(__name__)

_cfg: JevCompactConfig | None = None
_pending: set[str] = set()
_configure_lock = threading.Lock()


def configure(raw: dict[str, Any] | None = None) -> JevCompactConfig:
    """Resolve config + (un)register the MCP server. Called by the
    startup extension (sync context) — never raises. Serialized: the
    register/unregister decision depends on the _cfg being replaced,
    so two concurrent configures must not interleave."""
    global _cfg
    with _configure_lock:
        _cfg = get_config(raw)
        if _cfg.auto_register_mcp:
            try:
                if _cfg.enabled:
                    if mcp_register.register():
                        log.info("jev_compact: registered %s MCP server",
                                 mcp_register.SERVER_NAME)
                else:
                    # disabled plugin must not leave a live tool surface;
                    # unregister() itself is a no-op when absent/corrupt
                    if mcp_register.unregister():
                        log.info("jev_compact: unregistered %s (disabled)",
                                 mcp_register.SERVER_NAME)
            except Exception:
                log.exception("jev_compact: MCP registration failed")
        return _cfg


def get() -> JevCompactConfig:
    """Current config — defaults when unconfigured (tests, early calls)."""
    return _cfg if _cfg is not None else JevCompactConfig()


def request_compact(context_id: str) -> None:
    _pending.add(str(context_id))


def pop_pending(context_id: str) -> bool:
    """Consume a pending compact request — True iff one was queued."""
    key = str(context_id)
    if key in _pending:
        _pending.discard(key)
        return True
    return False


def status() -> dict[str, Any]:
    """Token-free status snapshot — same key set configured or not."""
    cfg = get()
    registered = False
    try:
        registered = mcp_register.is_registered(mcp_register.current_value())
    except Exception:
        pass
    sessions = 0
    try:
        from .engine.jev_compact import store as engine_store

        sessions = len(engine_store.list_sessions(mcp_register.store_root()))
    except Exception:
        pass
    out: dict[str, Any] = {
        "enabled": cfg.enabled,
        "configured": _cfg is not None,
        "server_name": mcp_register.SERVER_NAME,
        "mcp_registered": registered,
        "store_sessions": sessions,
        "pending_compacts": sorted(_pending),
        "config": cfg.to_dict(),
    }
    return out


def _reset() -> None:
    """Test seam — drop configured state + pending flags."""
    global _cfg
    _cfg = None
    _pending.clear()

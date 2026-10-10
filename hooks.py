"""Plugin hooks — uninstall/update cleanup.

a0 calls ``pre_uninstall``/``pre_update`` when present (any arity); the
MCP entry the startup extension registered must go with the plugin.
Never raises — a hook failure must not block plugin management.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def pre_uninstall(*args, **kwargs) -> None:
    try:
        from usr.plugins.jev_compact.helpers import mcp_register

        if mcp_register.unregister():
            log.info("jev_compact: removed %s MCP entry",
                     mcp_register.SERVER_NAME)
    except Exception:
        log.exception("jev_compact: unregister on uninstall failed")


def pre_update(*args, **kwargs) -> None:
    pre_uninstall()  # registration self-heals at next startup

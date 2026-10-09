"""Register the bundled stdio MCP server in ``settings["mcp_servers"]``.

a0 keeps MCP servers in the ``mcp_servers`` settings key — a JSON string
of shape ``{"mcpServers": {"<name>": {<MCPServerLocal fields>}}}`` — and
re-initializes ``MCPConfig`` on change. The plugin adds (idempotently)
and removes (on uninstall) its own entry while preserving every other
server. The entry carries NO secrets: the server resolves its token
inside the spawned process (see ``secrets_env.py``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SERVER_NAME = "a0_jev_compact"
SERVER_ENTRYPOINT = "restore_server.py"


def store_root() -> Path:
    """Absolute tombstone store root — <a0>/usr/jev_compact_store/<ctxid>/.
    Lives OUTSIDE the plugin dir: uninstall must not silently strand
    receipts a compacted context still references. Falls back to a
    repo-local dir when running outside an installed plugin tree."""
    helpers_dir = Path(__file__).resolve().parent
    plugin_dir = helpers_dir.parent
    if plugin_dir.parent.name == "plugins" and plugin_dir.parent.parent.name == "usr":
        return plugin_dir.parent.parent / "jev_compact_store"
    return plugin_dir / ".jev_store"  # dev/test fallback


def server_path() -> str:
    """Absolute path to the bundled server script."""
    return str(Path(__file__).resolve().parent.parent / "server" / SERVER_ENTRYPOINT)


def build_entry(cfg=None) -> dict[str, Any]:
    """The mcp_servers entry for our stdio server — non-secret config
    only. ``env`` carries the plugin's NON-SECRET config (a0's stdio spawn
    env is a safe-list + this dict — it's the only channel to the child).
    The restore server needs no secrets: it reads the span store."""
    env: dict[str, str] = {"JEV_STORE_DIR": str(store_root())}
    return {
        "name": SERVER_NAME,
        "description": "Jev-compact tombstone restore tools (a0-plugin-jev-compact)",
        "type": "stdio",
        "command": sys.executable,
        "args": [server_path()],
        "env": env,
        "init_timeout": 30,  # FastMCP import + handshake on a cold box
    }


def _unparseable(raw: Any) -> bool:
    """Non-empty + non-dict-raw that fails json.loads. Distinguishes a
    corrupt mcp_servers blob (refuse to write — a rebuild would silently
    delete every other server) from absent/empty (safe to initialize)."""
    if isinstance(raw, dict) or not raw:
        return False
    try:
        json.loads(str(raw))
        return False
    except (json.JSONDecodeError, TypeError):
        return True


def _parse_config(raw: Any) -> dict[str, Any]:
    """Best-effort parse of the mcp_servers settings value. Anything
    malformed returns a fresh shape — a broken config must not wedge
    plugin startup (a0 already surfaces the parse error separately).
    Never mutates the caller's data — copies before writing. a0's own
    apply endpoint also accepts ``mcpServers`` as a list of named server
    dicts (MCPConfig.normalize_config) — keep that form's entries."""
    if isinstance(raw, dict):
        data = dict(raw)  # copy — callers' dicts must not be mutated
    else:
        try:
            data = json.loads(str(raw or ""))
        except (json.JSONDecodeError, TypeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if servers is None and ("command" in data or "url" in data or "serverUrl" in data):
        # bare single-server object — a0's normalize_config accepts this
        # shape too; fold it under mcpServers instead of orphaning it
        name = str(data.get("name") or "unnamed")
        servers = {name: {k: v for k, v in data.items() if k != "mcpServers"}}
        data = {"mcpServers": servers}
    if isinstance(servers, list):  # a0 accepts list form — keep the entries
        servers = {
            s["name"]: s for s in servers
            if isinstance(s, dict) and s.get("name")
        }
    if not isinstance(servers, dict):
        servers = {}
    data["mcpServers"] = dict(servers)
    return data


def is_registered(raw: Any) -> bool:
    return SERVER_NAME in _parse_config(raw)["mcpServers"]


def merged_config(raw: Any, entry: dict[str, Any]) -> str:
    """The mcp_servers JSON with *entry* installed under SERVER_NAME."""
    data = _parse_config(raw)
    data["mcpServers"][SERVER_NAME] = entry
    return json.dumps(data, indent=2)


def without_server(raw: Any) -> str:
    """The mcp_servers JSON with our entry removed (others preserved)."""
    data = _parse_config(raw)
    data["mcpServers"].pop(SERVER_NAME, None)
    return json.dumps(data, indent=2)


def _write(value: str, rollback_to: Any = None) -> None:
    """Persist the mcp_servers value. MUST use set_settings_delta —
    set_settings() is a whole-settings replace that normalize_settings
    fills with defaults and whose sensitive-write path DELETES every
    secret in the vault (helpers/secrets.py merge semantics). a0's own
    api/mcp_servers_apply.py uses delta + the []→value two-step so
    MCPConfig reinitializes and picks up the change — if the second write
    fails, restore the prior value rather than leaving all servers wiped."""
    from helpers import settings as host_settings

    host_settings.set_settings_delta({"mcp_servers": "[]"})
    try:
        host_settings.set_settings_delta({"mcp_servers": value})
    except Exception:
        if rollback_to is not None:
            try:
                host_settings.set_settings_delta({"mcp_servers": rollback_to})
            except Exception:
                pass
        raise


# Operator-owned keys we carry over on re-register — a user who disables
# the server or trims its tools via the MCP UI must not see that erased
# every startup.
_USER_KEYS = ("disabled", "disabled_tools", "init_timeout", "tool_timeout")


def register(cfg=None) -> bool:
    """Write the entry into live settings if missing/stale. Lazy host
    import so this module never aborts a0's sweep."""
    from helpers import settings as host_settings

    current = host_settings.get_settings().get("mcp_servers", "")
    if _unparseable(current):
        return False  # corrupt blob — don't "rebuild" over other servers
    existing = _parse_config(current)["mcpServers"].get(SERVER_NAME)
    entry = build_entry(cfg)
    if isinstance(existing, dict):
        for key in _USER_KEYS:
            if key in existing:
                entry[key] = existing[key]
    if existing == entry:
        return False
    _write(merged_config(current, entry), rollback_to=current)
    return True


def unregister() -> bool:
    """Remove the entry from live settings (hooks.py uninstall)."""
    from helpers import settings as host_settings

    current = host_settings.get_settings().get("mcp_servers", "")
    if _unparseable(current) or not is_registered(current):
        return False
    _write(without_server(current), rollback_to=current)
    return True

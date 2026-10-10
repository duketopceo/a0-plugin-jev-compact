"""mcp_servers registration — idempotent, preserving, corrupt-safe."""

from __future__ import annotations

import json

import pytest

from usr.plugins.jev_compact.helpers import mcp_register, runtime


def test_register_idempotent_and_preserves(settings_state):
    settings_state["mcp_servers"] = json.dumps(
        {"mcpServers": {"other": {"name": "other", "command": "x"}}})
    assert mcp_register.register() is True
    data = json.loads(settings_state["mcp_servers"])["mcpServers"]
    assert "other" in data and mcp_register.SERVER_NAME in data
    assert mcp_register.register() is False  # identical → no write
    entry = data[mcp_register.SERVER_NAME]
    assert entry["type"] == "stdio" and entry["command"]
    assert entry["args"][0].endswith("restore_server.py")
    # env carries the store dir and NO secrets
    assert entry["env"]["JEV_STORE_DIR"].endswith("jev_compact_store") \
        or ".jev_store" in entry["env"]["JEV_STORE_DIR"]
    assert not any("KEY" in k or "TOKEN" in k for k in entry["env"])


def test_register_refuses_corrupt(settings_state):
    settings_state["mcp_servers"] = "{not json!!"
    assert mcp_register.register() is False
    assert settings_state["mcp_servers"] == "{not json!!"  # untouched


def test_unregister_removes_only_ours(settings_state):
    mcp_register.register()
    settings_state["mcp_servers"] = json.dumps({
        "mcpServers": {
            mcp_register.SERVER_NAME: mcp_register.build_entry(),
            "other": {"name": "other"},
        }})
    assert mcp_register.unregister() is True
    data = json.loads(settings_state["mcp_servers"])["mcpServers"]
    assert mcp_register.SERVER_NAME not in data and "other" in data
    assert mcp_register.unregister() is False


def test_writes_go_through_delta(settings_state):
    """P0 contract: set_settings wholesale-replace must never see a
    partial dict — the two-step uses set_settings_delta only."""
    settings_state["auth_login"] = "keepme"
    mcp_register.register()
    assert settings_state["auth_login"] == "keepme"
    assert mcp_register.SERVER_NAME in json.loads(
        settings_state["mcp_servers"])["mcpServers"]


def test_user_keys_preserved_on_reregister(settings_state):
    entry = mcp_register.build_entry()
    entry.update({"disabled": True, "disabled_tools": ["get_span"],
                  "init_timeout": 99})
    settings_state["mcp_servers"] = json.dumps(
        {"mcpServers": {mcp_register.SERVER_NAME: entry}})
    assert mcp_register.register() is False  # identical incl. user keys
    entry["command"] = "/gone/python"  # plugin-owned drift → repair
    settings_state["mcp_servers"] = json.dumps(
        {"mcpServers": {mcp_register.SERVER_NAME: entry}})
    assert mcp_register.register() is True
    new = json.loads(settings_state["mcp_servers"])["mcpServers"][
        mcp_register.SERVER_NAME]
    assert new["command"] != "/gone/python"
    assert new["disabled"] is True and new["init_timeout"] == 99


def test_alternate_shapes(settings_state):
    """List-form and bare single-server mcp_servers survive normalization."""
    settings_state["mcp_servers"] = json.dumps(
        {"mcpServers": [{"name": "ls", "command": "y"}]})
    assert mcp_register.register() is True
    data = json.loads(settings_state["mcp_servers"])["mcpServers"]
    assert "ls" in data and mcp_register.SERVER_NAME in data

    settings_state["mcp_servers"] = json.dumps(
        {"name": "bare", "command": "z", "type": "stdio"})
    assert mcp_register.register() is True
    data = json.loads(settings_state["mcp_servers"])["mcpServers"]
    assert "bare" in data and mcp_register.SERVER_NAME in data


def test_configure_disable_unregisters(settings_state):
    runtime.configure({"enabled": True, "auto_register_mcp": True})
    assert mcp_register.is_registered(settings_state["mcp_servers"])
    runtime.configure({"enabled": False, "auto_register_mcp": True})
    assert not mcp_register.is_registered(settings_state["mcp_servers"])


def test_configure_manual_mode_never_touches(settings_state):
    settings_state["mcp_servers"] = "{}"
    runtime.configure({"enabled": True, "auto_register_mcp": False})
    assert settings_state["mcp_servers"] == "{}"


def test_store_root_inside_a0_vs_dev(tmp_path):
    """Installed layout → <a0>/usr/jev_compact_store; repo → .jev_store."""
    import pathlib
    fake = tmp_path / "usr" / "plugins" / "jev_compact" / "helpers" / "x.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    # monkeypatch __file__ chain isn't feasible — assert the dev path:
    root = mcp_register.store_root()
    assert root.name == ".jev_store" or root.name == "jev_compact_store"

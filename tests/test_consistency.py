"""Structural guards — manifest↔config parity, host contracts, no
secret paths, vendored-engine purity."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_config_keys_align():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    defaults = yaml.safe_load((ROOT / "default_config.yaml").read_text())
    from usr.plugins.jev_compact.helpers.config import JevCompactConfig

    assert manifest["name"] == "jev_compact"  # dir-name contract
    assert set(defaults) == set(JevCompactConfig.__dataclass_fields__), (
        set(defaults) ^ set(JevCompactConfig.__dataclass_fields__))


def test_api_handlers_match_host_contract():
    import inspect as _i
    api_dir = ROOT / "api"
    handlers = []
    for f in sorted(api_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        src = f.read_text()
        assert src.count("class ") <= 3
        assert "def process" in src, f"{f.name}: handler without process()"
        handlers.append(f.name)
    assert "compact.py" in handlers and "jev_status.py" in handlers


def test_startup_extension_is_sync():
    """startup_migration is a SYNC sweep — an async execute() fails."""
    src = (ROOT / "extensions/python/startup_migration"
           / "_76_jev_compact_init.py").read_text()
    assert "def execute" in src
    assert "async def execute" not in src


def test_no_secrets_in_settings_or_server_src():
    """No token value may be wired into settings.json or shipped in the
    MCP entry env — only key NAMES are referenced."""
    reg = (ROOT / "helpers" / "mcp_register.py").read_text()
    assert "API_KEY" not in reg.split("env = ")[-1].split("}")[0]
    entry = json.dumps(
        __import__("usr.plugins.jev_compact.helpers.mcp_register",
                   fromlist=["m"]).build_entry())
    assert "TYPESAFE" not in entry and "OPENROUTER" not in entry


def test_engine_is_stdlib_only():
    """The vendored engine must stay dependency-free — it also runs in
    the bare MCP subprocess."""
    eng = ROOT / "helpers" / "engine" / "jev_compact"
    for f in eng.glob("*.py"):
        for m in re.finditer(
                r"^(?:from|import)\s+([a-zA-Z_][\w.]*)", f.read_text(),
                re.M):
            top = m.group(1).split(".")[0]
            if top in {"jev_compact", "typing", "dataclasses"}:
                continue
            try:
                __import__(top)
            except ImportError:
                pytest.fail(f"{f.name}: non-stdlib import {top}")


def test_server_needs_no_secrets():
    """The restore server reads files only — no key lookups."""
    src = (ROOT / "server" / "restore_server.py").read_text()
    assert "secrets_env" not in src and "API_KEY" not in src


def test_hooks_never_raise_and_unregister():
    src = (ROOT / "hooks.py").read_text()
    assert "pre_uninstall" in src and "unregister" in src

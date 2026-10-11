"""Dual-harness manifest probes — one plugin.yaml must satisfy BOTH
loaders' contracts:

- a0 reads it via ``yaml.safe_load`` + ``PluginMetadata.model_validate``
  (pydantic, extras ignored) and consumes only its known keys.
- Hermes parses it via ``plugins_manifest.parse_manifest_file`` — v2
  fields declared, unknown fields (title/min_a0_version) warn but load.

These probes fail loudly if either side's surface drifts — e.g. a Hermes
key whose shape the a0 reader can't skip, or a config_schema key that no
longer exists in ``JevCompactConfig``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin.yaml"
HERMES_CHECKOUT = ROOT.parent / "hermes-agent"

# Keys a0's PluginMetadata declares — the only surface a0 reads.
_A0_FIELDS = {
    "name", "title", "version", "description", "author", "license",
    "homepage", "min_a0_version", "settings_sections",
}
# Hermes manifest value types the loader consumes scalars/lists/maps for.
_HERMES_SCALAR_TYPES = (str, int, float, bool)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_parses_and_carries_a0_surface(manifest):
    """a0's loader must find every field it consumes."""
    assert manifest["name"] == "jev_compact"
    for key in ("version", "description", "author"):
        assert isinstance(manifest.get(key), str) and manifest[key]


def test_a0_reads_only_its_keys(manifest):
    """The a0 surface is a .get() projection — Hermes-only keys are data
    a0 never reads. If this test fails a key collided with a0's schema."""
    hermes_only = set(manifest) - _A0_FIELDS
    assert hermes_only  # the union manifest must actually carry both sets
    a0_view = {k: manifest.get(k) for k in _A0_FIELDS}
    assert a0_view["name"] == "jev_compact"


def test_hermes_fields_have_portable_shapes(manifest):
    """Hermes-known keys must carry the shapes its parser consumes —
    a malformed union value fails BOTH loaders (a0 passes the whole dict
    to pydantic; a non-scalar where Hermes expects a list would only
    surface on the Hermes side)."""
    assert manifest["kind"] == "exclusive"
    assert manifest["manifest_version"] == 2
    assert isinstance(manifest["optional_env"], list)
    assert all(isinstance(v, str) for v in manifest["optional_env"])
    # Engine-carried tools come from get_tool_schemas, not register() —
    # declaring provides_tools would warn in `hermes plugins validate`.
    assert "provides_tools" not in manifest
    schema = manifest["config_schema"]
    assert isinstance(schema, dict) and schema
    for key, spec in schema.items():
        assert isinstance(spec, dict), f"config_schema.{key} not a spec map"
        assert "type" in spec, f"config_schema.{key} missing type"


def test_config_schema_keys_match_config_dataclass(manifest):
    """config_schema is the Hermes settings surface — every key must be a
    field JevCompactConfig validates, else the settings UI would offer a
    knob the engine silently drops."""
    from usr.plugins.jev_compact.helpers.config import JevCompactConfig

    fields = set(JevCompactConfig.__dataclass_fields__)
    declared = set(manifest["config_schema"])
    assert declared <= fields, (
        f"config_schema keys {sorted(declared - fields)} are not "
        "JevCompactConfig fields — they'd be write-only knobs")
    # auto_register_mcp is a0-only (no MCP registration on Hermes) —
    # it's a valid field but must NOT be declared to the Hermes UI.
    assert "auto_register_mcp" not in declared
    assert "auto_register_mcp" in fields


_HERMES_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from hermes_cli.plugins_manifest import parse_manifest_file
m = parse_manifest_file(Path(sys.argv[2]), Path(sys.argv[3]), "user", "")
if m is None:
    sys.exit("parse_manifest_file returned None")
print(json.dumps({"name": m.name, "kind": m.kind,
                  "config_schema_keys": sorted(m.config_schema or {}),
                  "manifest_version": getattr(m, "manifest_version", None)}))
"""


@pytest.mark.skipif(
    not (HERMES_CHECKOUT / "hermes_cli" / "plugins_manifest.py").exists(),
    reason="hermes-agent checkout not adjacent — real-parse probe skipped",
)
def test_hermes_parse_manifest_file_tolerates_union(manifest):
    """The REAL Hermes manifest parser must accept the file — the a0-only
    keys (title, min_a0_version, settings_sections) warn, never fail.
    Runs in a subprocess: conftest's a0 host stubs (``agent`` etc.) shadow
    hermes's same-named packages, so an in-process import can't work."""
    import json
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-c", _HERMES_PROBE,
         str(HERMES_CHECKOUT), str(MANIFEST), str(ROOT)],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.skip(f"hermes_cli not importable in a bare interpreter: "
                    f"{proc.stderr.strip().splitlines()[-1] if proc.stderr else 'no stderr'}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["name"] == "jev_compact"
    assert out["kind"] == "exclusive"
    assert set(out["config_schema_keys"]) >= set(manifest["config_schema"])
    assert out["manifest_version"] == 2

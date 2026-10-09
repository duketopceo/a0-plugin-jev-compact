"""Secret resolution — env var → a0 secrets vault, never settings.json.

1. The named key in the process environment — covers standalone runs
   and any env the operator wires (e.g. OPENROUTER_API_KEY).
2. ``usr/secrets.env`` — a0's secrets vault is a plain dotenv file;
   ``§§secret(NAME)`` in tools resolves against the same store. The
   server self-locates the a0 root from ``__file__`` by walking ancestors
   for a ``usr/plugins`` dir and parses the file — no host imports
   needed inside a subprocess.
"""

from __future__ import annotations

import os
from pathlib import Path


def parse_env_file(text: str) -> dict[str, str]:
    """Minimal dotenv parse matching a0's python-dotenv semantics —
    keys UPPERCASED, comments, ``export`` prefix, single/double quotes,
    inline comments on unquoted values. Deliberately independent of a0
    helpers so the MCP subprocess needs nothing but stdlib."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().upper()  # dotenv normalizes keys to uppercase
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:  # dotenv strips inline comments on bare values
            value = value.split(" #", 1)[0].rstrip()
        if key:
            out[key] = value
    return out


def a0_root_from(path: str | Path) -> Path | None:
    """Locate the a0 root from a file inside ``usr/plugins/<p>/…``.
    Returns None when the layout doesn't match (e.g. repo checkout in
    tests). Only ``usr/plugins`` counts — a bare ``usr/`` dir would match
    the filesystem root."""
    p = Path(path).resolve()
    for parent in p.parents:
        if (parent / "usr" / "plugins").is_dir():
            return parent
    return None


_VAULT_FILES = ("secrets.env", ".env")  # a0's secret store covers both


def read_vault(secret_name: str, *, a0_root: Path | None = None) -> str:
    """Read *secret_name* from the a0 secret store under *a0_root* (or the
    self-located root) — ``usr/secrets.env`` first, then ``usr/.env``
    (helpers/secrets.py resolves §§secret() over the same files; secrets.env
    wins, matching the store's precedence). Empty when absent — never raises."""
    root = a0_root or a0_root_from(__file__)
    if root is None:
        return ""
    for name in _VAULT_FILES:
        try:
            text = (root / "usr" / name).read_text(encoding="utf-8-sig")
        except OSError:
            continue
        value = parse_env_file(text).get(secret_name.upper())
        if value:
            return value
    return ""


def resolve_token(
    secret_name: str,
    *,
    a0_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """First hit wins: process env → vault file → empty. Secret names are
    looked up uppercased — matching §§secret()/dotenv normalization."""
    env = environ if environ is not None else os.environ
    token = str(env.get(secret_name) or env.get(secret_name.upper()) or "").strip()
    if token:
        return token
    return read_vault(secret_name, a0_root=a0_root).strip()

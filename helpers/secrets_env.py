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


_root_cache: Path | None | bool = False  # False = uncomputed


def a0_root_from(path: str | Path) -> Path | None:
    """Locate the a0 root from a file inside ``usr/plugins/<p>/…``.
    Returns None when the layout doesn't match (e.g. repo checkout in
    tests). Only ``usr/plugins`` counts — a bare ``usr/`` dir would match
    the filesystem root.

    Unresolved path is tried first: under a SYMLINK install
    (usr/plugins/jev_compact -> repo checkout), ``__file__`` keeps the
    usr/plugins component while ``.resolve()`` follows the link OUT of
    the a0 tree — the resolved walk finds no usr/plugins and the store/
    vault silently fall back to repo-local paths."""
    for p in (Path(path).absolute(), Path(path).resolve()):
        for parent in p.parents:
            if (parent / "usr" / "plugins").is_dir():
                return parent
    return None


def _a0_root() -> Path | None:
    """Self-located root, cached — the layout doesn't move at runtime."""
    global _root_cache
    if _root_cache is False:
        _root_cache = a0_root_from(__file__)
    return _root_cache or None


_VAULT_FILES = ("secrets.env", ".env")  # a0's secret store covers both


def load_vault(*, a0_root: Path | None = None) -> dict[str, str]:
    """Parse both vault files ONCE into a merged name→value dict —
    secrets.env wins over .env (the store's precedence). Empty on
    absent/corrupt — never raises. One call should serve every secret a
    request needs; don't re-read per name."""
    root = a0_root or _a0_root()
    if root is None:
        return {}
    out: dict[str, str] = {}
    for name in reversed(_VAULT_FILES):  # .env first, secrets.env over it
        try:
            text = (root / "usr" / name).read_text(encoding="utf-8-sig")
        except OSError:
            continue
        out.update(parse_env_file(text))
    return out


def resolve_token(
    secret_name: str,
    *,
    a0_root: Path | None = None,
    environ: dict[str, str] | None = None,
    vault: dict[str, str] | None = None,
) -> str:
    """First hit wins: process env → vault → empty. Secret names are
    looked up uppercased — matching §§secret()/dotenv normalization.
    Pass a pre-parsed ``vault`` dict when resolving several names."""
    key = secret_name.upper()
    env = environ if environ is not None else os.environ
    token = str(env.get(key) or "").strip()
    if token:
        return token
    v = vault if vault is not None else load_vault(a0_root=a0_root)
    return str(v.get(key, "")).strip()

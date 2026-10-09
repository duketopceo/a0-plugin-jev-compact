"""Plugin configuration — validated dataclass.

Source of truth: ``default_config.yaml`` (a0 merges user/plugin config
on top). ``get_config`` is the single entry — it accepts the host's
``get_plugin_config`` dict (or a raw override dict for tests), applies
``JEV_COMPACT_*``/``JEV_MODEL`` env overrides, then constructs the
dataclass once (``__post_init__`` normalizes every field).

Scorer keys are read under their conventional env/vault names —
``TYPESAFE_API_KEY`` / ``OPENROUTER_API_KEY`` — resolved host-side by
``secrets_env.resolve_token``; the names are not configurable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

_PLUGIN = "jev_compact"

MIN_BUDGET, MAX_BUDGET = 2_000, 200_000
MIN_TAIL, MAX_TAIL = 0, 32
MIN_MIN_TOKENS = 200
SCORER_MODES = ("auto", "heuristic", "jev")

# env var → config field (single normalize pass at construction)
_ENV_MAP = {
    "JEV_COMPACT_ENABLED": "enabled",
    "JEV_COMPACT_AUTO_REGISTER_MCP": "auto_register_mcp",
    "JEV_COMPACT_SCORER": "scorer",
    "JEV_MODEL": "jev_model",
    "JEV_COMPACT_BUDGET_TOKENS": "budget_tokens",
    "JEV_COMPACT_TAIL_KEEP": "tail_keep",
    "JEV_COMPACT_MIN_TOKENS": "min_tokens",
}


def _bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


@dataclass
class JevCompactConfig:
    enabled: bool = False
    auto_register_mcp: bool = True
    scorer: str = "auto"               # auto | heuristic | jev
    jev_model: str = ""                # override scorer model slug
    budget_tokens: int = 24_000        # post-compact retained-token target
    tail_keep: int = 4                 # trailing spans never scored out
    min_tokens: int = 1_000            # refuse compaction below this

    def __post_init__(self) -> None:
        self.enabled = _bool(self.enabled)
        self.auto_register_mcp = _bool(self.auto_register_mcp)
        self.scorer = str(self.scorer).strip().lower()
        if self.scorer not in SCORER_MODES:
            self.scorer = "auto"
        self.jev_model = str(self.jev_model or "").strip()
        self.budget_tokens = _int(
            self.budget_tokens, MIN_BUDGET, MAX_BUDGET, 24_000
        )
        self.tail_keep = _int(self.tail_keep, MIN_TAIL, MAX_TAIL, 4)
        self.min_tokens = _int(self.min_tokens, MIN_MIN_TOKENS, MAX_BUDGET, 1_000)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def get_config(raw: dict[str, Any] | None = None) -> JevCompactConfig:
    """Resolve config once: host get_plugin_config → env overrides →
    validated dataclass."""
    if raw is None:
        raw = {}
        try:
            from helpers import plugins as host_plugins  # noqa: PLC0415

            raw = host_plugins.get_plugin_config(_PLUGIN) or {}
        except Exception:
            raw = {}
    merged = {
        k: v for k, v in raw.items()
        if k in JevCompactConfig.__dataclass_fields__
    }
    for env_name, attr in _ENV_MAP.items():
        v = os.environ.get(env_name)
        if v is not None:
            merged[attr] = v
    return JevCompactConfig(**merged)

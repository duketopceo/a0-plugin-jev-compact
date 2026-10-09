"""Plugin configuration — validated dataclass.

Source of truth: ``default_config.yaml`` (a0 merges user/plugin config
on top). ``get_config`` is the single entry — it accepts the host's
``get_plugin_config`` dict (or a raw override dict for tests) and
normalizes every field with clamps. Env vars override the file for the
secrets and scorer knobs only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_PLUGIN = "jev_compact"

ENV_PREFIX = "JEV_COMPACT_"

MIN_BUDGET, MAX_BUDGET = 2_000, 200_000
MIN_TAIL, MAX_TAIL = 0, 32
MIN_MIN_TOKENS = 200
SCORER_MODES = ("auto", "heuristic", "jev")


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
    scorer_secret_typesafe: str = "TYPESAFE_API_KEY"
    scorer_secret_openrouter: str = "OPENROUTER_API_KEY"
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
        self.scorer_secret_typesafe = str(
            self.scorer_secret_typesafe or "TYPESAFE_API_KEY"
        ).strip().upper()
        self.scorer_secret_openrouter = str(
            self.scorer_secret_openrouter or "OPENROUTER_API_KEY"
        ).strip().upper()
        self.jev_model = str(self.jev_model or "").strip()
        self.budget_tokens = _int(
            self.budget_tokens, MIN_BUDGET, MAX_BUDGET, 24_000
        )
        self.tail_keep = _int(self.tail_keep, MIN_TAIL, MAX_TAIL, 4)
        self.min_tokens = _int(self.min_tokens, MIN_MIN_TOKENS, MAX_BUDGET, 1_000)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def get_config(raw: dict[str, Any] | None = None) -> JevCompactConfig:
    """Resolve config: explicit dict → host get_plugin_config → env
    overrides for the secret/scorer knobs."""
    if raw is None:
        raw = {}
        try:
            from helpers import plugins as host_plugins  # noqa: PLC0415

            raw = host_plugins.get_plugin_config(_PLUGIN) or {}
        except Exception:
            raw = {}
    cfg = JevCompactConfig(**{k: v for k, v in raw.items()
                             if k in JevCompactConfig.__dataclass_fields__})
    env_map = {
        "enabled": "JEV_COMPACT_ENABLED",
        "scorer": "JEV_COMPACT_SCORER",
        "jev_model": "JEV_MODEL",
        "budget_tokens": "JEV_COMPACT_BUDGET_TOKENS",
        "tail_keep": "JEV_COMPACT_TAIL_KEEP",
        "min_tokens": "JEV_COMPACT_MIN_TOKENS",
        "auto_register_mcp": "JEV_COMPACT_AUTO_REGISTER_MCP",
    }
    for attr, env_name in env_map.items():
        v = os.environ.get(env_name)
        if v is not None:
            setattr(cfg, attr, v)
    cfg.__post_init__()
    return cfg

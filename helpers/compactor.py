"""Span-level compaction for a live AgentContext.

Pipeline: history.output() → events → spans → moving highlight → score
(heuristic offline, or Jev/TypeSafe + OpenRouter Decisions when a key is
configured) → knapsack retention → tombstone receipts. The full span set
is persisted to the span store so tombstones resolve via the bundled
restore MCP server — compaction is *non-destructive*.

History replacement mirrors plugins/_chat_compaction/helpers/compactor.py:
a pre-compact JSON backup, a fresh History with one message (highlight
header + kept spans + tombstone receipts), responses-provider state
cleared, ctx-window cache dropped, chat persisted.
"""

from __future__ import annotations

import logging
from typing import Any

from .engine.jev_compact import compact as engine_compact
from .engine.jev_compact import highlight as engine_highlight
from .engine.jev_compact import scorer as engine_scorer
from .engine.jev_compact import spans as engine_spans
from .engine.jev_compact import store as engine_store
from . import a0_history, mcp_register, secrets_env
from .config import JevCompactConfig

log = logging.getLogger(__name__)


class CompactError(RuntimeError):
    pass


def context_stats(context: Any, cfg: JevCompactConfig) -> dict[str, Any]:
    """Stats for the confirmation path — mirrors get_compaction_stats."""
    events = a0_history.events_from_history(context.agent0.history.output())
    spans = engine_spans.segment(events)
    return {
        "message_count": len(events),
        "span_count": len(spans),
        "token_count": sum(s.token_est for s in spans),
        "scorer": _scorer_mode(cfg, len(spans)),
        "budget_tokens": cfg.budget_tokens,
    }


def request_scorer(cfg: JevCompactConfig, total_spans: int) -> engine_scorer.Scorer:
    """Resolve the configured scorer with vault-aware key lookup."""
    name = cfg.scorer
    if name == "heuristic":
        return engine_scorer.HeuristicScorer(total_spans)
    ts_key = secrets_env.resolve_token(cfg.scorer_secret_typesafe)
    or_key = secrets_env.resolve_token(cfg.scorer_secret_openrouter)
    if ts_key:
        return engine_scorer.JevScorer(
            ts_key,
            model=cfg.jev_model or "jev-latest",
            api_base="https://api.typesafe.ai",
        )
    if or_key:
        return engine_scorer.JevScorer(
            or_key,
            model=cfg.jev_model or "~typesafe/jev-latest",
            api_base="https://openrouter.ai",
            via="openrouter",
        )
    if name == "jev":
        raise CompactError(
            f"scorer=jev but neither {cfg.scorer_secret_typesafe} nor "
            f"{cfg.scorer_secret_openrouter} is set (env or secrets vault)"
        )
    return engine_scorer.HeuristicScorer(total_spans)


def _scorer_mode(cfg: JevCompactConfig, total_spans: int) -> str:
    try:
        s = request_scorer(cfg, total_spans)
    except CompactError:
        return "jev(missing-key)"
    return type(s).__name__.replace("Scorer", "").lower() or "unknown"


async def compact_context(
    context: Any, cfg: JevCompactConfig
) -> dict[str, Any]:
    """Span-compact *context*'s history in place. Raises CompactError on
    refusal; never leaves history half-replaced (all mutations happen
    after the compacted text is built)."""
    agent = context.agent0
    events = a0_history.events_from_history(agent.history.output())
    if not events:
        raise CompactError("no conversation content to compact")
    spans = engine_spans.segment(events)
    if len(spans) <= cfg.tail_keep:
        raise CompactError(f"not enough spans ({len(spans)} <= tail_keep)")

    hl = engine_highlight.extract(spans)
    scorer = request_scorer(cfg, len(spans))
    result = engine_compact.compact(
        spans, hl, scorer, cfg.budget_tokens, tail_keep=cfg.tail_keep
    )
    if not result.text.strip():
        raise CompactError("compaction produced empty text")

    # pre-compact backup + span store BEFORE touching history —
    # tombstones must resolve and the transcript must survive
    backup_path = _backup(context)
    st = engine_store.SpanStore(mcp_register.store_root(), str(context.id))
    try:
        st.save(spans, hl, result.tombstoned, source=backup_path)
    except OSError as exc:
        raise CompactError(f"span store write failed: {exc}") from exc

    _replace_history(context, agent, result, str(backup_path))
    return {
        "spans": len(spans),
        "kept": len(result.kept_ids),
        "tombstoned": len(result.tombstoned),
        "tokens_before": result.tokens_before,
        "tokens_after": result.tokens_after,
        "ratio": result.stats.get("ratio"),
        "backup": backup_path,
        "store_session": str(context.id),
    }


def _backup(context: Any) -> "Path":
    """JSON dump of the pre-compact chat — same guarantee as
    _chat_compaction's backup, stored inside the span store so
    source.txt points at the true source transcript."""
    from helpers.persist_chat import export_json_chat  # host, lazy

    st_dir = mcp_register.store_root() / str(context.id)
    st_dir.mkdir(parents=True, exist_ok=True)
    path = st_dir / "pre_compact.json"
    path.write_text(
        export_json_chat(context) or "{}", encoding="utf-8", errors="replace"
    )
    return path


def _replace_history(
    context: Any, agent: Any, result: Any, backup_path: str
) -> None:
    """Swap history for the compacted text — same host operations as
    _chat_compaction.run_compaction, minus the LLM summary."""
    from agent import Agent  # host, lazy
    from helpers.history import (  # noqa: PLC0415
        History, clear_responses_provider_state,
    )
    from helpers.persist_chat import remove_msg_files, save_tmp_chat

    note = (
        f"\n\n---\n*Span-compacted by jev_compact: {result.tokens_before}→"
        f"{result.tokens_after} tokens. Tombstoned spans restore via the "
        f"a0_jev_compact MCP tools (get_span/list_tombstones). "
        f"Backup: {backup_path}*"
    )
    agent.history = History(agent=agent)
    agent.history.add_message(ai=True, content=result.text + note)
    clear_responses_provider_state(agent)
    agent.data.pop(Agent.DATA_NAME_CTX_WINDOW, None)
    agent.data.pop(Agent.DATA_NAME_SUBORDINATE, None)
    context.streaming_agent = None
    save_tmp_chat(context)
    remove_msg_files(context.id)

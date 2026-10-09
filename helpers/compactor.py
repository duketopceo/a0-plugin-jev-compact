"""Span-level compaction for a live AgentContext.

Pipeline: history.output() → events → spans → moving highlight → score
(heuristic offline, or Jev/TypeSafe + OpenRouter Decisions when a key is
configured) → knapsack retention → tombstone receipts. The full span set
is persisted to the span store so tombstones resolve via the bundled
restore MCP server — compaction is *non-destructive*.

History replacement mirrors plugins/_chat_compaction/helpers/compactor.py:
a pre-compact JSON backup, a fresh History with one message (highlight
header + kept spans + tombstone receipts), responses-provider state
cleared, ctx-window cache dropped, log reset, chat persisted.
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

# Secret NAMES — conventional env/vault keys, resolved host-side via
# env → usr/secrets.env → usr/.env. Not configurable; values never
# touch settings.json or the MCP entry.
SECRET_TYPESAFE = "TYPESAFE_API_KEY"
SECRET_OPENROUTER = "OPENROUTER_API_KEY"


class CompactError(RuntimeError):
    pass


def _segment_context(context: Any) -> tuple[list[dict], list]:
    """Shared history→spans prelude — the expensive pass both
    context_stats and compact_context used to run twice per request."""
    agent = context.agent0
    events = a0_history.events_from_history(agent.history.output())
    return events, engine_spans.segment(events)


def context_stats(context: Any, cfg: JevCompactConfig) -> dict[str, Any]:
    """Stats for the confirmation path — mirrors get_compaction_stats."""
    events, spans = _segment_context(context)
    return {
        "message_count": len(events),
        "span_count": len(spans),
        "token_count": sum(s.token_est for s in spans),
        "scorer": request_scorer(cfg, len(spans))[1],
        "budget_tokens": cfg.budget_tokens,
    }


def request_scorer(
    cfg: JevCompactConfig, total_spans: int
) -> tuple[engine_scorer.Scorer, str]:
    """Resolve the configured scorer + its mode name in one pass —
    the vault is parsed once for both key names. Model slugs pin the
    vendored engine's defaults (engine/scorer.py `resolve`)."""
    if cfg.scorer == "heuristic":
        return engine_scorer.HeuristicScorer(total_spans), "heuristic"
    vault = secrets_env.load_vault()
    ts_key = secrets_env.resolve_token(SECRET_TYPESAFE, vault=vault)
    or_key = secrets_env.resolve_token(SECRET_OPENROUTER, vault=vault)
    if ts_key:
        return engine_scorer.JevScorer(
            ts_key, **({"model": cfg.jev_model} if cfg.jev_model else {})
        ), "jev"
    if or_key:
        # TypeSafe decisions models route to /api/alpha/decisions; any
        # other slug falls back to generic chat-completions scoring.
        return engine_scorer.JevScorer(
            or_key,
            model=cfg.jev_model or "~typesafe/jev-latest",
            api_base="https://openrouter.ai",
            via="openrouter",
        ), "openrouter"
    if cfg.scorer == "jev":
        raise CompactError(
            f"scorer=jev but neither {SECRET_TYPESAFE} nor "
            f"{SECRET_OPENROUTER} is set (env or secrets vault)"
        )
    return engine_scorer.HeuristicScorer(total_spans), "heuristic"


def compact_context(
    context: Any, cfg: JevCompactConfig
) -> dict[str, Any]:
    """Span-compact *context*'s history in place. SYNC — scoring can be
    network-bound; callers on a0's event loop must wrap in
    ``asyncio.to_thread`` (``compact_and_report`` does). Raises
    CompactError on refusal; never leaves history half-replaced (all
    mutations happen after the compacted text is built)."""
    agent = context.agent0
    events, spans = _segment_context(context)
    if not events:
        raise CompactError("no conversation content to compact")
    if len(spans) <= cfg.tail_keep:
        raise CompactError(f"not enough spans ({len(spans)} <= tail_keep)")
    tokens = sum(s.token_est for s in spans)
    if tokens < cfg.min_tokens:
        raise CompactError(
            f"not enough content to compact "
            f"(minimum {cfg.min_tokens:,} tokens, have ~{tokens:,})"
        )

    hl = engine_highlight.extract(spans)
    scorer, mode = request_scorer(cfg, len(spans))
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
        "scorer": mode,
        "backup": str(backup_path),
        "store_session": str(context.id),
    }


async def compact_and_report(
    context: Any, cfg: JevCompactConfig
) -> dict[str, Any] | None:
    """Shared driver for the two compaction callers (API task + deferred
    loop-end): offload the sync pipeline, report success/failure on the
    context log the same way either path took."""
    import asyncio

    try:
        result = await asyncio.to_thread(compact_context, context, cfg)
        context.log.log(
            type="response",  # user-visible — same type _chat_compaction uses
            heading="Context span-compacted",
            content=(
                f"{result['tokens_before']}→{result['tokens_after']} "
                f"tokens across {result['spans']} spans "
                f"({result['tombstoned']} tombstone(s), scorer: "
                f"{result['scorer']}). Tombstoned content restores via "
                f"the a0_jev_compact MCP tools."
            ),
            update_progress="none",
        )
        return result
    except Exception as exc:
        log.exception("jev_compact: compaction failed")
        context.log.log(
            type="error", heading="Span Compaction Failed",
            content=str(exc),
        )
        return None


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
    context.log.reset()  # visible log mirrors the replaced history
    save_tmp_chat(context)
    remove_msg_files(context.id)

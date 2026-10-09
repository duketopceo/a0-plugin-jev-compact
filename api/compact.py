"""POST /api/plugins/jev_compact/compact — span-level chat compaction.

Mirrors _chat_compaction's api contract: ``context`` + ``action`` input,
``stats`` returns a preview dict, ``compact`` schedules the work on the
context's own task thread (never inline — HTTP handlers run on a0's
event loop and the surgery is heavy).
"""

from __future__ import annotations

import asyncio
import logging

from helpers.api import ApiHandler, Input, Output, Request, Response

log = logging.getLogger(__name__)


class Compact(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        from agent import AgentContext  # host, lazy

        from usr.plugins.jev_compact.helpers import compactor, runtime

        cfg = runtime.get()
        if not cfg.enabled:
            return {"ok": False, "message": "jev_compact is disabled"}

        ctxid = str(input.get("context", ""))
        action = str(input.get("action", "compact")).strip().lower()
        if not ctxid:
            return Response("Missing context id", 400)
        context = AgentContext.get(ctxid)
        if not context:
            return Response("Context not found", 404)
        if context.is_running():
            return Response("Cannot compact while agent is running", 409)

        if action == "stats":
            try:
                stats = await asyncio.to_thread(
                    compactor.context_stats, context, cfg
                )
            except Exception as exc:
                return {"ok": False, "message": str(exc)}
            if stats["token_count"] < cfg.min_tokens:
                return {
                    "ok": False,
                    "message": (
                        f"Not enough content to compact "
                        f"(minimum {cfg.min_tokens:,} tokens)"
                    ),
                }
            return {"ok": True, "stats": stats}

        if action == "compact":
            stats = await asyncio.to_thread(
                compactor.context_stats, context, cfg
            )
            if stats["token_count"] < cfg.min_tokens:
                return {
                    "ok": False,
                    "message": (
                        f"Not enough content to compact "
                        f"(minimum {cfg.min_tokens:,} tokens)"
                    ),
                }
            context.run_task(_compact_task, context, cfg)
            return {"ok": True, "message": "Span compaction started"}

        return Response(f"Unknown action: {action}", 400)


async def _compact_task(context, cfg) -> None:
    from usr.plugins.jev_compact.helpers import compactor  # lazy host path

    try:
        result = await compactor.compact_context(context, cfg)
        context.log.log(
            type="response",
            heading="Context span-compacted",
            content=(
                f"{result['tokens_before']}→{result['tokens_after']} tokens, "
                f"{result['tombstoned']} tombstone(s). Full spans stored — "
                f"restore via a0_jev_compact MCP tools."
            ),
            update_progress="none",
        )
        try:
            from helpers.state_monitor_integration import mark_dirty_all

            mark_dirty_all(reason="plugins.jev_compact.compact")
        except Exception:
            pass
    except Exception as exc:
        log.exception("jev_compact: compaction failed")
        context.log.log(
            type="error", heading="Span Compaction Failed", content=str(exc)
        )

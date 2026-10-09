"""POST /api/plugins/jev_compact/compact — span-level chat compaction.

Mirrors _chat_compaction's api contract: ``context`` + ``action`` input,
``stats`` returns a preview dict, ``compact`` schedules the work on the
context's own task thread (never inline — HTTP handlers run on a0's
event loop and the surgery is heavy). The min_tokens policy lives inside
``compact_context`` so the API stats path and the deferred agent-tool
path share it.
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
            return {"ok": True, "stats": stats}

        if action == "compact":
            # min_tokens/empty-history refusals surface from the task's
            # error log entry — the same guard the deferred path uses.
            context.run_task(_compact_task, context, cfg)
            return {"ok": True, "message": "Span compaction started"}

        return Response(f"Unknown action: {action}", 400)


async def _compact_task(context, cfg) -> None:
    from usr.plugins.jev_compact.helpers import compactor  # lazy host path

    if await compactor.compact_and_report(context, cfg) is not None:
        try:
            from helpers.state_monitor_integration import mark_dirty_all

            mark_dirty_all(reason="plugins.jev_compact.compact")
        except Exception:
            pass

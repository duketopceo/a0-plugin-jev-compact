"""Agent-facing tool: request a span compaction of this chat's context.

The tool NEVER compacts inline — rewriting history mid-monologue would
corrupt the in-flight loop. It queues a deferred request that the
message_loop_end extension runs at the next safe boundary.
"""

from __future__ import annotations

from helpers.tool import Response, Tool

from usr.plugins.jev_compact.helpers import runtime


class JevCompact(Tool):
    async def execute(self, **kwargs) -> Response:
        if not runtime.get().enabled:
            return Response(
                message="jev_compact is disabled — enable it in plugin "
                        "settings before requesting compaction.",
                break_loop=False,
            )
        runtime.request_compact(self.agent.context.id)
        return Response(
            message=(
                "Span compaction queued — it runs when this turn ends. "
                "Afterwards, tombstoned content is restorable via the "
                "a0_jev_compact MCP tools (get_span, list_tombstones)."
            ),
            break_loop=False,
        )

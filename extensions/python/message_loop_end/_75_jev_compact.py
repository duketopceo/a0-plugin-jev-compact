"""Loop-end: run a deferred jev-compact request.

The agent-facing ``jev_compact`` tool sets a pending flag rather than
rewriting history mid-monologue; this extension (running after
``_10_organize_history``) consumes the flag and compacts between turns.
Never raises into the loop — compact_and_report logs failures.
"""

from __future__ import annotations

import logging

from helpers.extension import Extension

log = logging.getLogger(__name__)


class JevCompactPending(Extension):
    async def execute(self, **kwargs) -> None:
        try:
            context = self.agent.context if self.agent else None
            if context is None:
                return
            from usr.plugins.jev_compact.helpers import compactor, runtime

            if not runtime.pop_pending(context.id):
                return
            cfg = runtime.get()
            if not cfg.enabled:
                return
            # loop-end is the safe boundary — this is where upstream's
            # own history compress() runs (_10_organize_history)
            await compactor.compact_and_report(context, cfg)
        except Exception as exc:
            log.warning("jev_compact: deferred compaction failed: %s", exc)

"""GET/POST /api/plugins/jev_compact/jev_status — token-free status."""

from __future__ import annotations

import asyncio

from helpers.api import ApiHandler, Input, Output, Request


class JevStatus(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        from usr.plugins.jev_compact.helpers import runtime

        # status() touches settings + the store dir — off the event loop
        return await asyncio.to_thread(runtime.status)

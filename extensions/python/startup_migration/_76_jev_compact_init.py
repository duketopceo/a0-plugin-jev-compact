"""a0-plugin-jev-compact — startup configuration.

Sync ``execute`` is required: a0's startup_migration sweep is synchronous
(call_extensions_sync) and an awaitable here would fail the call.

The plugin import stays inside execute() — upstream's import sweep is
unguarded, so a module-level import that ever failed would abort the
extension sweep (and a0 boot). ``_76_`` lands late so siblings can
pre-seed config/env first.
"""

from helpers.extension import Extension


class JevCompactInit(Extension):
    def execute(self, **kwargs) -> None:
        del kwargs  # a0 passes no payload at this extension point
        try:
            from usr.plugins.jev_compact.helpers import runtime

            runtime.configure(None)  # None -> get_plugin_config("jev_compact")
        except Exception:
            import logging

            logging.getLogger("a0.jev_compact").exception(
                "jev_compact: startup configuration failed — plugin inactive"
            )

"""Hermes harness entrypoint — a0 never imports this file.

Hermes context-engine discovery sniffs this source for the
``register_context_engine`` marker (``plugins/context_engine`` loads
``$HERMES_HOME/plugins/jev_compact/`` when ``context.engine`` names it)
and capture-loads ``register(ctx)`` with a NoopPluginContext — only
``register_context_engine``/``register_command`` do work there, so all
real wiring lives inside the engine, not on ctx calls.

The manifest marker above is literal text for the source sniff:
register_context_engine
"""

from __future__ import annotations


def register(ctx) -> None:
    """Load the adapter as a sibling of this package and register the
    context engine. Imports stay inside the function: this file must be
    importable without hermes present (a0 ignores it anyway, but repo
    tooling and tests read it as plain source)."""
    try:
        from . import hermes_adapter
    except ImportError:
        # Non-package loaders (no parent package to resolve ``.``): load
        # as a child of this module so its ``.helpers`` imports resolve
        # against this directory's __path__.
        import importlib.util
        from pathlib import Path

        name = f"{__name__}.hermes_adapter"
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "hermes_adapter.py")
        if spec is None or spec.loader is None:
            raise ImportError("cannot locate hermes_adapter.py")
        import sys

        hermes_adapter = importlib.util.module_from_spec(spec)
        sys.modules[name] = hermes_adapter  # resolve .helpers children
        spec.loader.exec_module(hermes_adapter)
    hermes_adapter.register(ctx)

"""Hermes adapter — jev_compact as a Hermes ContextEngine.

Loaded only under hermes-agent: the repo-root ``__init__.py`` calls
``register(ctx)`` from here; a0 never imports this file (its loader reads
``plugin.yaml`` + ``hooks.py`` + ``extensions/``).

Mapping (Khan hermes-shim contract):

- a0's ``message_loop_end`` deferred-compact seam → the ContextEngine
  slot itself: the host calls ``compress()`` at ITS safe boundary, so no
  loop-end extension is needed. The agent-facing ``jev_compact`` tool
  keeps a0's deferred semantics — it only queues a request that
  ``should_compress``/``should_compress_preflight`` report.
- a0's ``a0_jev_compact`` MCP restore server → engine-carried tools
  (``get_tool_schemas``/``handle_tool_call``): ``get_span``,
  ``list_tombstones``, ``get_highlight``, ``list_sessions`` read the same
  span-store shape — read-only, keyless.
- ``usr/jev_compact_store/<ctxid>/`` → ``plugin_data_dir("jev_compact")/
  jev_compact_store/<session_id>/``.
- ``helpers.secrets_env`` vault/env resolution → ``secret_scope.
  get_secret`` first (profile-scoped under multiplex), env fallback.

Non-destructive invariant preserved: spans + a pre-compact transcript
backup are written (0600) BEFORE the compacted message list is returned.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.context_engine import ContextEngine

from .helpers import a0_history
from .helpers.config import get_config
from .helpers.engine.jev_compact import (
    compact as engine_compact,
    highlight as engine_highlight,
    scorer as engine_scorer,
    spans as engine_spans,
    store as engine_store,
)

log = logging.getLogger(__name__)

PLUGIN_NAME = "jev_compact"
STORE_DIRNAME = "jev_compact_store"
SECRET_TYPESAFE = "TYPESAFE_API_KEY"
SECRET_OPENROUTER = "OPENROUTER_API_KEY"


def _plugin_data_root() -> Path:
    """<HERMES_HOME>/plugin-data/jev_compact/ — profile-scoped, created
    per call so it follows the active profile under multiplex."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir(PLUGIN_NAME))
    except Exception:
        # Dev/off-host fallback — same convention as the a0 side's
        # repo-local .jev_store. Never hardcode ~/.hermes.
        return Path(__file__).resolve().parent / ".plugin_data"


def _events_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-format messages → engine events — the same kind/text shape
    ``a0_history.events_from_history`` emits, minus the a0-only wrappers."""
    events: list[dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            continue  # preserved verbatim by the caller — never scored
        if role == "tool":
            kind = "tool"
            text = f"{msg.get('name') or 'tool'}: {a0_history.stringify_content(msg.get('content'))}"
        elif role == "assistant":
            kind = "assistant_text"
            text = a0_history.stringify_content(msg.get("content"))
            calls = msg.get("tool_calls") or []
            if calls:  # tool-call intents carry retention value too
                names = ", ".join(
                    str((c.get("function") or {}).get("name", "tool"))
                    for c in calls if isinstance(c, dict)
                )
                if names:
                    text = f"{text}\n[called: {names}]".strip()
        elif role == "user":
            kind = "user_turn"
            text = a0_history.stringify_content(msg.get("content"))
        else:
            kind = "other"
            text = a0_history.stringify_content(msg.get("content"))
        if not str(text).strip():
            continue
        events.append({"kind": kind, "text": str(text)[:a0_history.MAX_EVENT_CHARS]})
    return events


class JevContextEngine(ContextEngine):
    """Span-scored compaction as a Hermes context engine.

    Selected via ``context.engine: jev_compact``; armed by
    ``plugins.entries.jev_compact.settings.enabled`` (same master-switch
    semantics as a0 — selected-but-disabled stays inert)."""

    name = PLUGIN_NAME
    # jev compacts the whole non-system region into one assistant
    # message; the host's head/tail protection would double-preserve.
    protect_first_n = 0
    protect_last_n = 0
    threshold_percent = 0.75

    def __init__(self, settings: dict | None = None,
                 store_root: Path | None = None) -> None:
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0
        self._settings = dict(settings) if settings is not None else None
        self._store_root = Path(store_root) if store_root else None
        self._session_id = ""
        self._compact_requested = False
        self._cfg = None

    # -- config / storage seams --------------------------------------

    def _read_settings(self) -> dict[str, Any]:
        if self._settings is not None:
            return dict(self._settings)
        try:
            from hermes_cli.config import load_config

            entries = ((load_config() or {}).get("plugins") or {}).get("entries") or {}
            entry = entries.get(PLUGIN_NAME) or {}
            raw = entry.get("settings") or entry.get("config") or {}
            return dict(raw)
        except Exception:
            return {}

    def _config(self):
        if self._cfg is None:
            self._cfg = get_config(self._read_settings())
        return self._cfg

    def _store(self) -> Path:
        if self._store_root is not None:
            return self._store_root
        return _plugin_data_root() / STORE_DIRNAME

    def _resolve_secret(self, name: str) -> str:
        """Profile-scoped secret read — never os.environ directly (the
        scope overlay is what multiplex fencing enforces)."""
        try:
            from agent.secret_scope import get_secret

            value = get_secret(name)
            if value:
                return str(value)
        except Exception:
            pass
        import os

        return os.environ.get(name, "")

    def _scorer(self, cfg, total_spans: int):
        """Same scorer order as helpers/compactor.request_scorer, on
        Hermes's secret path instead of a0's vault."""
        if cfg.scorer == "heuristic":
            return engine_scorer.HeuristicScorer(total_spans), "heuristic"
        ts_key = self._resolve_secret(SECRET_TYPESAFE)
        or_key = self._resolve_secret(SECRET_OPENROUTER)
        if ts_key:
            return engine_scorer.JevScorer(
                ts_key, **({"model": cfg.jev_model} if cfg.jev_model else {})
            ), "jev"
        if or_key:
            return engine_scorer.JevScorer(
                or_key,
                model=cfg.jev_model or "~typesafe/jev-latest",
                api_base="https://openrouter.ai",
                via="openrouter",
            ), "openrouter"
        if cfg.scorer == "jev":
            raise RuntimeError(
                f"scorer=jev but neither {SECRET_TYPESAFE} nor "
                f"{SECRET_OPENROUTER} is set (.env / secret scope)"
            )
        return engine_scorer.HeuristicScorer(total_spans), "heuristic"

    # -- ContextEngine contract ---------------------------------------

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        if not self._config().enabled:
            return False
        if self._compact_requested:
            return True
        return bool(
            prompt_tokens and self.threshold_tokens
            and prompt_tokens >= self.threshold_tokens
        )

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        if not self._config().enabled:
            return False
        if self._compact_requested:
            return True
        if not self.context_length:
            return False
        rough = sum(
            len(str(m.get("content") or "")) // 4
            for m in messages if isinstance(m, dict)
        )
        return rough >= int(self.context_length * self.threshold_percent)

    def has_content_to_compress(self, messages: list[dict[str, Any]]) -> bool:
        convo = [m for m in messages
                 if isinstance(m, dict) and m.get("role") != "system"]
        return bool(convo)

    def on_session_start(self, session_id: str, **kwargs) -> None:
        self._session_id = str(session_id or "")
        self._compact_requested = False
        self._cfg = None  # settings may have changed between sessions

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._session_id = ""
        self._compact_requested = False

    def compress(
        self, messages: list[dict[str, Any]], current_tokens=None,
        focus_topic=None, force: bool = False, memory_context: str = "",
    ) -> list[dict[str, Any]]:
        """Compact the non-system region into one assistant message with
        tombstone receipts. Refusals return the input unchanged — a
        declined compaction must not break the turn."""
        cfg = self._config()
        if not cfg.enabled:
            return list(messages)
        self._compact_requested = False
        systems = [m for m in messages if m.get("role") == "system"]
        convo = [m for m in messages if m.get("role") != "system"]
        events = _events_from_messages(convo)
        try:
            spans = engine_spans.segment(events)
            if not events:
                raise RuntimeError("no conversation content to compact")
            if len(spans) <= cfg.tail_keep:
                raise RuntimeError(
                    f"not enough spans ({len(spans)} <= tail_keep)")
            if sum(s.token_est for s in spans) < cfg.min_tokens:
                raise RuntimeError("below min_tokens")

            hl = engine_highlight.extract(spans)
            scorer, mode = self._scorer(cfg, len(spans))
            result = engine_compact.compact(
                spans, hl, scorer, cfg.budget_tokens,
                tail_keep=cfg.tail_keep)
            if not result.text.strip():
                raise RuntimeError("compaction produced empty text")

            store_dir = self._store() / (self._session_id or "default")
            # persist BEFORE returning the replacement — non-destructive
            backup = store_dir / "pre_compact.json"
            store_dir.mkdir(parents=True, exist_ok=True)
            backup.write_text(
                json.dumps(messages, ensure_ascii=False, default=str),
                encoding="utf-8", errors="replace")
            backup.chmod(0o600)
            st = engine_store.SpanStore(
                self._store(), self._session_id or "default")
            st.save(spans, hl, result.tombstoned, source=backup)
        except Exception:
            log.warning("jev_compact: compaction declined/failed", exc_info=True)
            return list(messages)

        self.compression_count += 1
        note = (
            f"\n\n---\n*Span-compacted by jev_compact: "
            f"{result.tokens_before}→{result.tokens_after} tokens across "
            f"{len(spans)} spans ({len(result.tombstoned)} tombstone(s), "
            f"scorer: {mode}). Tombstoned content restores via the "
            "get_span/list_tombstones tools.*"
        )
        return [*systems, {"role": "assistant",
                           "content": result.text + note}]

    # -- engine-carried tools (restore surface + deferred compact) -----

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        def _tool(name, description, properties=None, required=None):
            return {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties or {},
                    "required": required or [],
                },
            }

        session_prop = {"session": {
            "type": "string",
            "description": "Store session id; defaults to the most recent.",
        }}
        return [
            _tool(
                "jev_compact",
                "Queue span-scored context compaction — runs at the "
                "host's next safe compaction boundary, never mid-turn."),
            _tool(
                "get_span",
                "Rehydrate a tombstoned span or range ('s4', 's4-s9') to "
                "verbatim transcript text.",
                {"span": {"type": "string",
                          "description": "Span id or range, e.g. 's4' or 's4-s9'."},
                 **session_prop},
                ["span"]),
            _tool("list_tombstones",
                  "List tombstone receipts left by compaction: range, "
                  "receipt text, and a preview of the first span.",
                  session_prop),
            _tool("get_highlight",
                  "Return the moving-highlight spec recorded at last "
                  "compaction — what the conversation was about.",
                  session_prop),
            _tool("list_sessions",
                  "List span-store session ids that have saved spans."),
        ]

    def handle_tool_call(self, name: str, args: dict[str, Any],
                         **kwargs) -> str:
        try:
            if name == "jev_compact":
                if not self._config().enabled:
                    return json.dumps({
                        "error": "jev_compact is disabled — set "
                                 "plugins.entries.jev_compact.settings."
                                 "enabled: true"})
                self._compact_requested = True
                return json.dumps({
                    "status": "queued",
                    "detail": "Compaction will run at the next safe "
                              "boundary (non-destructive — tombstones "
                              "resolve via get_span)."})
            if name == "list_sessions":
                ids = engine_store.list_sessions(self._store())
                return json.dumps({"sessions": ids or []})
            session = str((args or {}).get("session") or "")
            sid = session or engine_store.latest_session(self._store())
            if not sid:
                return json.dumps({"error": "no saved span sessions"})
            st = engine_store.SpanStore(self._store(), sid)
            if name == "get_span":
                text = st.get_range(str((args or {}).get("span", "")))
                if text is None:
                    return json.dumps(
                        {"error": f"span not found: {args.get('span')}"})
                return json.dumps({"session": sid, "text": text})
            if name == "list_tombstones":
                return json.dumps({"session": sid,
                                   "tombstones": st.list_tombstones()})
            if name == "get_highlight":
                return json.dumps({"session": sid,
                                   "highlight": st.get_highlight()})
            return json.dumps({"error": f"unknown tool: {name}"})
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        status.update({
            "enabled": self._config().enabled,
            "pending_compact": self._compact_requested,
            "store_sessions": len(
                engine_store.list_sessions(self._store())),
        })
        return status


def register(ctx) -> None:
    """Hermes entrypoint — capture-register the engine. The engine-load
    ctx is a NoopPluginContext: only register_context_engine does work;
    config/storage resolve lazily inside the engine."""
    register_engine = getattr(ctx, "register_context_engine", None)
    if register_engine is None:
        log.warning("jev_compact: ctx has no register_context_engine — "
                    "plugin not installed as a context engine")
        return
    register_engine(JevContextEngine())

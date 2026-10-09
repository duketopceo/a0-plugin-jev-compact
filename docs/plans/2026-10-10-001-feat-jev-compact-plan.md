# Plan: a0-plugin-jev-compact (Khan#197)

> Thin Agent Zero adapter over the `jev-compact` span-scoring engine —
> span-level context compaction with tombstone receipts + restore tools.
- Issue: https://github.com/duketopceo/Khan/issues/197
- Milestone: Khan #8 Caravanserai — Plugin Decomposition

## Context

Upstream a0 has two compaction surfaces today:

1. **Attention-window compression** (`extensions/python/message_loop_end/_10_organize_history.py`
   → `agent.history.compress()`): automatic, per-topic, summarizes
   middle messages via utility model. Runs silently, no restore path —
   compressed content is *gone* from the live history.
2. **`_chat_compaction` plugin** (`api/compact_chat.py` +
   `helpers/compactor.py`): manual button, full-chat LLM summary,
   replaces the whole history with one message + a backup file path.

`jev-compact` (~/Documents/github/personal/jev-compact) is the
differentiated engine: scores each transcript *span* on
(relevance_to_current_highlight, load_bearing) — heuristic offline or
TypeSafe Jev/OpenRouter Decisions — retains high-value spans verbatim,
tombstones dropped runs with restorable receipts, and persists the full
span store so nothing is destroyed. The issue calls the differentiation:
"Upstream `_chat_compaction` does full-chat summary only; span-level
scoring is the differentiator."

## Design

```
usr/plugins/jev_compact/
├── plugin.yaml                        name: jev_compact
├── default_config.yaml                enabled, scorer mode, budgets
├── hooks.py                           pre_uninstall/pre_update → unregister MCP
├── api/
│   ├── compact.py                     POST /api/plugins/jev_compact/compact
│   │                                  (action: stats|compact, mirrors
│   │                                   _chat_compaction's guard contract)
│   └── jev_status.py                  token-free status
├── tools/
│   └── compact_request.py             agent-facing tool — flags a deferred
│                                      compaction for loop-end (never compacts
│                                      inside a live monologue)
├── extensions/python/
│   ├── startup_migration/_76_jev_compact_init.py   configure() + register MCP
│   └── message_loop_end/_75_jev_compact.py         picks up the deferred flag
├── server/
│   └── restore_server.py              FastMCP stdio server `a0_jev_compact`:
│                                      get_span / list_tombstones / get_highlight
└── helpers/
    ├── engine/                        vendored jev_compact (stdlib-only):
    │   spans.py transcript.py highlight.py scorer.py compact.py store.py
    ├── a0_history.py                  history.output() → engine events
    ├── compactor.py                   compact_context(): events→spans→
    │                                  highlight→score→compact→store→
    │                                  history replace (run_compaction-shaped)
    ├── mcp_register.py                vendored hardened registrar
    │                                  (set_settings_delta + []→value two-step,
    │                                   user-key preservation, corrupt refusal)
    ├── secrets_env.py                 vendored usr/secrets.env + usr/.env reader
    ├── config.py                      validated NotionConfig-style dataclass
    └── runtime.py                     configure/status/_pending flags
```

## Key decisions

- **Engine vendored**, not imported — `jev_compact` is stdlib-only
  (~1,100 LOC), upstream-sync friendly, and a0 plugins can't pip-install
  into the host venv reliably.
- **Restore tools via MCP**, compaction via API — a spawned MCP
  subprocess cannot reach a live `AgentContext`'s history object, so the
  split is forced and correct: tombstone reads are pure file ops
  (MCP); history surgery stays in-process (API handler + loop-end ext).
- **`context.run_task()` for compaction** — same concurrency contract as
  `_chat_compaction` (refuses while running, tasks off the HTTP handler).
- **Store at `usr/jev_compact_store/<ctxid>/`** — outside the plugin
  dir so uninstall doesn't silently strand tombstoned receipts
  mid-deployment; `JEV_STORE_DIR` baked absolute into the MCP entry env.
- **Scorer keys host-side only** — `TYPESAFE_API_KEY` /
  `OPENROUTER_API_KEY` resolve from env → `usr/secrets.env` → `usr/.env`
  (`§§secret()` semantics); the restore server needs no secrets at all.
- **Deferred flag, not in-loop compaction** — the agent tool sets
  `runtime.request_compact(ctx.id)`; `message_loop_end` extension runs
  `compact_context` *between* turns, after `_10_organize_history`.
  In-loop history replacement would corrupt the in-flight monologue.
- **History replace shape** — copy `run_compaction`'s contract:
  pre-compact JSON backup via `export_json_chat`, `History(agent=agent)`
  + one message with the compacted text (highlight header + kept spans
  + tombstone receipts), `clear_responses_provider_state`, drop
  `DATA_NAME_CTX_WINDOW`, `save_tmp_chat`, `remove_msg_files`.
- **Read-only for tombstones** — restore tools never write.

## Not doing (recorded)

- No auto-compact on token overflow — upstream attention-window
  `compress()` owns that path; span-compaction stays user/agent-invoked.
- No WebUI — `_chat_compaction` owns the button; this plugin's API is
  callable from any client. A follow-up could add a webui extension.
- No remote-MCP restore server — local stdio only (Hermes can register
  the identical block; documented in README).

## Acceptance mapping

- [x] "registers compaction + tombstone-restore tools on stock a0" →
  API `compact` + agent tool + auto-registered `a0_jev_compact` MCP
  restore tools.
- [x] "MCP server path documented (works for Hermes too)" → README.
- [x] "Differentiation vs `_chat_compaction` stated in README" → README.

# AGENTS.md — a0-plugin-jev-compact

> Agent entry point for this repo. Inherits the standards in
> [luke-agents/AGENTS.md](https://github.com/duketopceo/luke-agents/blob/main/AGENTS.md).

## What this repo does

a0 plugin wrapping the `jev-compact` engine: span-scored context
compaction with tombstone receipts + verbatim restore via a bundled
stdio MCP server. Extracted under Khan#197 (Caravanserai milestone).

## Layout

- `helpers/engine/jev_compact/` — **vendored engine** (stdlib-only);
  sync upstream `jev-compact/src/jev_compact/` carefully
- `helpers/a0_history.py` — `history.output()` → engine events
- `helpers/compactor.py` — `compact_context()` + `context_stats()`
- `helpers/mcp_register.py` — `a0_jev_compact` entry registrar
- `helpers/secrets_env.py` — env → vault secret resolution
- `helpers/config.py` / `runtime.py` — config + deferred-flag state
- `server/restore_server.py` — FastMCP stdio restore tools
- `api/compact.py`, `api/jev_status.py` — host endpoints
- `tools/jev_compact.py` — agent-facing deferred-compact tool (a0 resolves tools by filename)
- `extensions/python/startup_migration/_76_…` — configure + register
- `extensions/python/message_loop_end/_75_…` — runs deferred compacts
- `hooks.py` — pre_uninstall/pre_update unregister

## Invariants

- **Non-destructive**: spans persist to `usr/jev_compact_store/<ctxid>/`
  *before* history is replaced; tombstones must always resolve.
- **Never in-loop surgery**: the tool sets a flag; `message_loop_end`
  compacts between turns. `compact_context` is never called while the
  monologue is mid-flight.
- **Settings writes via `set_settings_delta` only** — `set_settings()`
  is a whole-settings replace that wipes auth + the secrets vault
  (see `mcp_register._write` docstring; `[]`→value two-step + rollback).
- **No secrets in settings/MCP env**: scorer keys resolve host-side via
  env → `usr/secrets.env` → `usr/.env`; the restore server is keyless.
- **Read-only restore surface**: `get_span`/`list_tombstones`/
  `get_highlight`/`list_sessions` — no write tools.
- **Engine stays stdlib-only** — it also runs inside the bare MCP
  subprocess (only `helpers/engine` on its sys.path).
- **Extension sync/async contract**: `startup_migration` is sync,
  `message_loop_end` is async — checked in `test_consistency.py`.
- Host-side modules import `usr.plugins.jev_compact.*` lazily inside
  functions/extensions (imports must never abort a0's sweeps).

## Tests

`python -m pytest tests/ -q` — standalone; conftest stubs host modules.
`mcp` is NOT stubbed for the engine (stdlib); the restore server test
injects a fake FastMCP.

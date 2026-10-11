---
title: "a0-plugin-jev-compact program roadmap"
type: program-roadmap
status: proposed
date: 2026-10-10
---

# Plan: a0-plugin-jev-compact program roadmap

> Written by hand in the shape of `2026-10-10-001-feat-jev-compact-plan.md`: the `ce-plan` skill was invoked in pipeline mode but its multi-phase review flow was not run for this docs-only pass. Evidence comes from the code and `docs/research/2026-10-10-landscape.md`.

## Context

The plugin works (43 tests pass) and is deliberately small: about 1,070 lines of host adapter plus about 900 lines of vendored engine. It is off by default, has no UI, and does not hook a0's automatic compression. Three gaps stand out in the research: nothing measures what compaction loses (Factory publishes a probe-based method, [source](https://factory.ai/news/evaluating-compression)); the vendored engine has already diverged from the standalone `jev-compact` repo; and the API does not follow the family `{ok, error}` envelope.

Spend rule (`~/Documents/github/personal/AGENTS.md`): any paid scoring or eval run bills only via the `orch` wrapper and the `orchestral-eval-v3` key. Every unit below is offline unless it says otherwise.

## Summary: Now / Next / Later

| ID | Unit | Horizon | Difficulty |
|---|---|---|---|
| U1 | Engine sync guard | Now | easy |
| U2 | Retention eval harness (offline first) | Now | medium |
| U3 | API envelope + index card | Now | easy |
| U4 | Opt-in auto-trigger at a token threshold | Next | medium |
| U5 | Restore search tool | Next | medium |
| U6 | Settings-panel status chip | Later | medium |
| U7 | De-duplicate the restore server with the standalone repo | Later | hard |

## Units

### U1. Engine sync guard
- **Status:** not started. **Evidence:** `helpers/engine/jev_compact/store.py` adds `list_sessions()` that `~/Documents/github/personal/jev-compact/src/jev_compact/store.py` lacks; `cli.py` and `__main__.py` are not vendored. AGENTS.md says "sync upstream carefully" with no check.
- **Goal:** a test that fails when the vendored modules differ from upstream except for an allow-listed, documented set of changes.
- **Dependencies:** none. **Files:** `tests/test_engine_sync.py` (new), skipped when the standalone repo is not present.
- **Verification:** `python -m pytest tests/ -q` passes; editing a vendored file makes the new test fail.
- **Difficulty:** easy. A diff of six files against an allow-list.
- **Feasibility:** CI has no standalone checkout, so the test must skip there or pin a commit hash; a vendored-hash manifest avoids network.
- **Simpler alternative:** a one-line comment block in `helpers/engine/jev_compact/__init__.py` recording the upstream commit (`08f0b1f` at time of writing), checked by a trivial test.

### U2. Retention eval harness (offline first)
- **Status:** not started. **Evidence:** `tests/test_compactor.py` and `tests/test_engine.py` check mechanics, not retention; no fixture set exists.
- **Goal:** a small fixture set of transcripts with planted facts (a path, an error string, a decision) and probe questions; report which planted facts survive in kept spans or are recoverable via tombstone receipts, for the heuristic scorer at several budgets.
- **Dependencies:** none. **Files:** `tests/fixtures/`, `tests/test_retention.py`, optional `tools/`-free script under `tests/`.
- **Verification:** deterministic pass/fail thresholds in pytest; a paid Jev or OpenRouter comparison is a separate manual run via `orch` only.
- **Difficulty:** medium. Writing realistic fixtures is the work; the scoring is substring checks.
- **Feasibility:** the Factory method uses LLM-graded probes ([source](https://factory.ai/news/evaluating-compression)); substring probes are a weaker, free approximation. The paid arm is capped by the `orchestral-eval-v3` key.
- **Simpler alternative:** three hand-written fixtures and no probe framework; assert only that `load_bearing` facts are never tombstoned.

### U3. API envelope and a0-plugins index card
- **Status:** not started. **Evidence:** `api/compact.py` returns `{ok, message}` or `{ok, stats}` for some refusals and raw `Response(..., 400/404/409)` for others; the family standard is `{ok, error}`. The landscape notes the `a0-plugins` index card is title, description, tags and a thumbnail.
- **Goal:** one envelope for refusals; README and `plugin.yaml` tags plus `assets/brand/social-card.png` ready for the index PR.
- **Dependencies:** owner sign-off on the logo. **Files:** `api/compact.py`, `api/jev_status.py`, `tests/test_consistency.py`.
- **Verification:** tests assert the envelope on each refusal path.
- **Difficulty:** easy. About 30 lines.
- **Feasibility:** a0's own UI may read the `_chat_compaction`-style status codes; changing 409 to a body-only error could break a client that expects it. Index requirements are from third-party mirrors, not verified against the official repo.
- **Simpler alternative:** keep HTTP codes and only add `error` to the JSON body.

### U4. Opt-in auto-trigger at a token threshold
- **Status:** not started. **Evidence:** README states the plugin does not intercept a0's automatic `compress()` (`message_loop_end/_10_organize_history`); users of Claude Code expect automatic compaction (landscape, "What users expect").
- **Goal:** config key (default off) so `extensions/python/message_loop_end/_75_jev_compact.py` queues a compaction when history exceeds a threshold, reusing the existing deferred path.
- **Dependencies:** U2 (do not ship auto-compaction without a retention check). **Files:** `default_config.yaml`, `helpers/config.py`, `helpers/runtime.py`, `extensions/python/message_loop_end/_75_jev_compact.py`.
- **Verification:** unit tests with the stubbed host; manual run on a real a0.
- **Difficulty:** medium. The deferred plumbing exists; the risk is interaction with upstream compress ordering.
- **Feasibility:** depends on a0's token counter API and extension ordering, which are unverified here (host contract came from third-party mirrors).
- **Simpler alternative:** document a prompt line telling the agent to call the `jev_compact` tool at a size it chooses; no code.

### U5. Restore search tool
- **Status:** not started. **Evidence:** `server/restore_server.py` fetches spans by id only; Letta offers searchable recall memory ([docs](https://docs.letta.com/guides/agents/architectures/memgpt)).
- **Goal:** read-only `search_spans(query, session?)` doing substring or keyword match over `spans.json`.
- **Dependencies:** none. **Files:** `server/restore_server.py`, `helpers/engine/jev_compact/store.py`, `tests/test_server.py`.
- **Verification:** server test with the injected fake FastMCP.
- **Difficulty:** medium. Small code, but it touches the vendored engine and widens the U1 allow-list.
- **Feasibility:** none external; keep the server read-only and stdlib-only.
- **Simpler alternative:** none needed if skipped: tombstone receipts already show a `begins:` preview that the agent can use with `list_tombstones`.

### U6. Settings-panel status chip
- **Status:** not started. **Evidence:** no UI exists; `/jev_status` is token-free and already returns what a chip needs.
- **Goal:** LIVE / IDLE / FAILED chip per `DESIGN.md`.
- **Dependencies:** U3. **Files:** new `webui/` entry, `api/jev_status.py`.
- **Verification:** manual screenshot from a running a0 (not captured in this pass).
- **Difficulty:** medium. Needs a0's webui extension contract, which was not read directly.
- **Feasibility:** webui extension points are known only from a directory listing of `_chat_compaction`.
- **Simpler alternative:** document `curl` against `/jev_status` in the README; no webui code.

### U7. De-duplicate the restore server with the standalone repo
- **Status:** not started. **Evidence:** `server/restore_server.py` (162 lines) and `~/Documents/github/personal/jev-compact/mcp/restore_server.py` implement the same idea.
- **Goal:** one source of truth for the restore server and engine.
- **Dependencies:** U1. **Files:** `server/restore_server.py`, `helpers/engine/`.
- **Verification:** both repos' tests green after the change.
- **Difficulty:** hard. a0 plugins must be standalone directories (plan 001), so sharing means a vendoring script or a published package; neither exists.
- **Feasibility:** the stdlib-only, no-pip rule blocks a package dependency. A release-time vendoring script is feasible.
- **Simpler alternative:** do U1 only and accept two copies with a drift test.

## Merge / retire assessment

Verdict: **keep standalone.**
- Merging into another family plugin (durable, glitchtip, notion, device-sync) has no shared subject: those are about task durability, error tracking, Notion and file sync. The only shared code is boilerplate `helpers/config.py` and `runtime.py`, which the landscape already flags as a cross-family helper question, not a reason to merge.
- Retiring in favour of the standalone repo plus its MCP server would lose the in-process part: the history rewrite, the `message_loop_end` deferral and the settings-safe MCP registration. The landscape's finding 1 says a plugin earns its place by doing what MCP cannot, and rewriting a live `AgentContext` history is exactly that. A bare MCP server cannot reach it (plan 001, "Key decisions").
- Where the answer would change: if a0's stock compaction gains span-level retention with restore, or if the owner stops using a0, retire it. Today stock `_chat_compaction` is described as whole-chat summary with a backup file (plan 001); that description was not verified against its code.
- Biggest simplification: engine duplication across two repos (U1, then U7), not deleting this plugin.

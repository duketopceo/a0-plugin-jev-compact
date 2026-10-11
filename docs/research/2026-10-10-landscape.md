# Landscape: the Agent Zero plugin ecosystem and comparable agent-plugin systems

Date: 2026-10-10. Shared by `a0-plugin-durable`, `a0-plugin-glitchtip`, `a0-plugin-notion`, `a0-plugin-device-sync`, `a0-plugin-jev-compact`. Each repo's per-plugin section follows this shared text. Research used web search only; no paid model calls. Search results were mostly third-party mirrors, so every claim is tagged with how solid its source is. Where a source disagreed or was thin, that is said.

## 1. How Agent Zero plugins work (the host contract)

- Agent Zero looks for plugins in `usr/plugins/<name>/` (user) and `plugins/<name>/` (core). A folder is a plugin when it has a `plugin.yaml` at its root. Fields include `name`, `title`, `description`, `version`, `settings_sections` and per-project/per-agent flags. Valid `settings_sections` values reported: agent, external, mcp, developer, backup. Source: third-party mirrors of Agent Zero's own skill docs ([tessl a0-create-plugin](https://tessl.io/registry/skills/github/agent0ai/agent-zero/a0-create-plugin), [openskillindex a0-create-plugin](https://openskillindex.com/skills/agent0ai-agent-zero-a0-create-plugin)). Confidence: medium. The official repo was not read directly.
- Community plugins live in their own GitHub repo and are listed by a pull request to the `a0-plugins` index repo. The index is a generated `index.json` keyed by plugin name (title, description, github, tags, thumbnail). A `LICENSE` at the repo root is required, and CI requires `name` to match `^[a-z0-9_]+$`. Source: [openskillindex a0-contribute-plugin](https://openskillindex.com/skills/agent0ai-agent-zero-a0-contribute-plugin) and the mirrors above. Confidence: medium.
- Implication for this family: the `a0-plugins` index is the only discovery channel, and its card is a title, a description, tags and a thumbnail. A good thumbnail (our social card or mark) and a plain description do more for discovery than any feature. Our own plugins carry a `LICENSE` already; `a0-plugin-jev-compact` is the only one with `author`, `license`, `homepage` and `min_a0_version` in its manifest.

## 2. Comparable plugin systems

| System | Unit of extension | Distribution | What it does better than an a0 plugin | What it does worse |
|---|---|---|---|---|
| Claude Code plugins | Directory with `.claude-plugin/plugin.json`; bundles slash commands, subagents, hooks, skills, MCP servers ([guide](https://www.morphllm.com/claude-code-marketplace), third-party) | A git repo with `.claude-plugin/marketplace.json`; `/plugin marketplace add owner/repo`, `/plugin install` | Install is one command, from any git repo. Components are declarative (no Python to run). | Not a Python runtime: plugins cannot hold long-lived server code except via MCP. |
| Model Context Protocol (MCP) servers | A process speaking MCP over stdio or HTTP | Registries and per-client config | Client-neutral: one server works in many agents. Notion ships an official hosted one ([Notion docs](https://developers.notion.com/docs/hosting-open-source-mcp), [ToolHive guide](https://docs.stacklok.com/toolhive/guides-mcp/notion-remote)). | Every server is a separate process with its own auth and lifecycle. |
| Agent Zero plugins | Directory with `plugin.yaml`, `api/`, `extensions/`, `helpers/`, optional webui | One GitHub repo each + PR to `a0-plugins` index | Can hook the agent loop in-process (extensions), add API routes and settings panels, and ship an MCP server inside. | Discovery is one index; no versioned install command was found in the sources; hosts are Python-only. |

What users expect from a plugin in any of these systems: install in one step, a visible settings surface, a no-op default when disabled, a clear failure message instead of a crash at host startup, and a licence file.

## 3. Cross-cutting findings

1. **MCP is the neutral layer.** Where a capability already exists as an MCP server, a plugin that re-implements it competes with the vendor. A plugin earns its place by doing what MCP cannot: hooking the agent loop or host lifecycle in-process.
2. **Zero-dependency is a real differentiator in a host plugin.** All five plugins here are stdlib-only or lazy-import optional deps, so they cannot break a0 startup. That is a design choice worth keeping and stating in each README.
3. **Disabled-by-default and inert-when-off** is the convention that makes a plugin safe to install. Keep it.
4. **Discovery needs a card.** The `a0-plugins` index shows title, description, tags, thumbnail. Per-plugin tags and a thumbnail are cheap.

## 4. Adjacent domains (used by the per-plugin sections)

- **Durable execution** (for durable): Temporal records an event history and replays deterministic workflow code; Inngest checkpoints each `step.run()` and charges per run and per step, with a BSL self-host option; DBOS is MIT, database-native, TypeScript-centric; Restate is a single binary with sub-50ms latency claims from vendor pieces; Hatchet, Trigger.dev and LittleHorse also position as agent orchestrators. Sources are vendor-authored and flagged as directional by the search: [ZenML on Inngest alternatives](https://www.zenml.io/blog/inngest-alternatives), [Diagrid FAQ](https://www.diagrid.io/faq/alternatives-dbos-inngest/what-are-the-underappreciated-integration-costs-when-self-building-a-durable-execution-layer-on-temporal-c), [Spheron comparison](https://www.spheron.network/blog/ai-agent-workflow-orchestration-temporal-inngest-restate-gpu-cloud/), [noqta](https://www.noqta.tn/blog/durable-execution-ai-agents-inngest-trigger-temporal-2026).
- **Error tracking** (for glitchtip): GlitchTip accepts Sentry SDKs by changing one DSN string, self-hosts on roughly three services (app, Postgres, Valkey/Redis), and added incremental OpenTelemetry support and span views in 6.2, with an MCP server for AI-assisted debugging ([GlitchTip 6.2 release](https://glitchtip.com/blog/2026-06-22-glitchtip-6-2-released), [SDK docs](https://glitchtip.com/sdkdocs/all-sdks), [Sentry alternatives roundup](https://oneuptime.com/blog/post/2026-03-31-10-best-sentry-alternatives/markdown)). A competitor's roundup says it focuses on error and uptime, not full-stack observability. Langfuse trace-id correlation was not found in any source.
- **Notion access** (for notion): Notion runs an official hosted MCP server at `mcp.notion.com/mcp` (tools such as `notion-search`, `notion-fetch`, `notion-create-pages`); the open-source `makenotion/notion-mcp-server` is described as lower priority and possibly deprecated by two third-party sources. Sources disagree on whether the hosted server is OAuth-only. Verify on Notion's docs before acting: [StackOne deep dive](https://stackone.com/blog/notion-mcp-deep-dive), [ToolHive](https://docs.stacklok.com/toolhive/guides-mcp/notion-remote), [x-cmd](https://x-cmd.com/install/notion-mcp-server).
- **Device sync** (for device-sync): Syncthing is continuous two-way folder sync that needs both devices online and a daemon; Taildrop is ad hoc file transfer over a tailnet, not sync; rsync is one-way and has no memory between runs; Tailscale carries any of them. Sources are community posts and low quality: [Syncthing vs Tailscale thread](https://jisaku.com/glossary/network-nas-sync-tailscale-syncthing-remote), [rsync alternatives](https://alternativeto.net/software/rsync), [Taildrop forum](https://forum.tailscale.com/t/taildrop-alpha-futures-speculation/689).
- **Context compaction** (for jev-compact): Claude Code compacts manually with `/compact` and automatically near full context (reported near 95% of a 200K window); one critique says that timing degrades context first. A 2026 guide groups methods as LLM summarisation, opaque compression, and verbatim compaction (drop low-value lines, keep the rest word for word) and warns that 90%+ compression forces rewriting and hallucination risk. Mem0 markets a memory compression engine (vendor claim of up to 80% fewer prompt tokens, treat as marketing). Letta (MemGPT) pages data in and out like virtual memory. LLMLingua is token pruning. Sources: [morphllm guide](https://www.morphllm.com/context-compression), [Claude Code compaction explainer](https://techie007.substack.com/p/how-memory-compaction-works-in-agents), [Mem0 guide](https://guide.mem0.ai/faq/tools-reduce-context-llm-calls), [Self-Compacting Language Model Agents](https://arxiv.org/pdf/2606.23525).

## 5. What is unique about this family

- In-process agent-loop hooks with a stdlib-only footprint, which neither MCP servers nor marketplace plugins offer.
- A shared engineering standard across five repos: opt-in, inert when disabled, secrets never in config, `{ok, error}` API envelope, offline tests.
- Honest scale: these are single-host tools for one operator's Agent Zero boxes. None claims multi-tenant or multi-region.

## 6. Open risks to the whole family

- Host-contract drift: every claim in section 1 comes from third-party mirrors. Re-verify against the official `agent0ai/agent-zero` repo before the next release of any plugin.
- Five repos, five CI files and five near-identical `helpers/config.py`/`runtime.py` pairs. A shared helper package is the obvious simplification, but a0 plugins are standalone directories, so sharing means a vendored copy or a 6th plugin. See each ROADMAP.

## Plugin section: jev-compact

What this plugin does (from the code): span-level retention. It segments an a0 chat into spans, scores each on `(relevance, load_bearing)` against a moving highlight (offline heuristic, or TypeSafe Jev / OpenRouter Decisions when a key resolves), keeps high-value spans verbatim, replaces dropped runs with tombstone receipts, and stores every span under `usr/jev_compact_store/<ctxid>/` so a bundled stdio MCP server can restore them verbatim. Source: `README.md`, `helpers/compactor.py`, `server/restore_server.py`.

### Comparable approaches

1. **Agent Zero `_chat_compaction` (the stock plugin).** The plugin directory exists in the official repo with `api/`, `helpers/`, `prompts/`, a webui button extension and `default_config.yaml` ([directory listing](https://github.com/agent0ai/agent-zero/tree/main/plugins/_chat_compaction)). Better: it ships in core and has a UI button, which this plugin deliberately lacks. Worse for users who need exact text: this repo's own plan describes it as a whole-chat LLM summary with a backup file (`docs/plans/2026-10-10-001-feat-jev-compact-plan.md`). Thin source: I only saw the file listing, not the code, so its behaviour is taken from this repo's plan, not verified.
2. **Claude Code `/compact` and auto-compact.** Manual `/compact` lets the user choose timing and give preservation instructions; auto-compact fires near a full window ([morphllm guide](https://morphllm.com/claude-code-compact), [vincentqiao](https://blog.vincentqiao.com/en/posts/claude-code-compact/)). Third-party sources disagree on the trigger point (about 95%, or 64-80%) and no official Anthropic page was found. Better: built into the harness, automatic, and CLAUDE.md is re-read after compaction. Worse: it summarises, and instructions given only in chat can be lost. This plugin's sibling repo `jev-compact` has a Claude Code adapter for the same engine (`~/Documents/github/personal/jev-compact/adapters/claude-code`).
3. **Factory's structured, anchored summarisation.** Factory reports a probe-based evaluation where structured summarisation scored 3.70 against 3.44 (Anthropic) and 3.35 (OpenAI) ([Factory post](https://factory.ai/news/evaluating-compression), [ZenML summary](https://www.zenml.io/llmops-database/evaluating-context-compression-strategies-for-long-running-ai-agent-sessions)). Numbers are vendor-run and seen only via secondary write-ups. Better: it has a published way to measure what compaction loses. Worse: it is still summarisation, with no verbatim restore. Useful as a template for an eval here (see roadmap U4).
4. **Verbatim compaction as a category.** A 2026 guide separates LLM summarisation, opaque compression and verbatim compaction, and warns that heavy compression forces rewriting ([morphllm](https://www.morphllm.com/compaction-vs-summarization)). This is the category jev-compact sits in. Vendor-written, so treat the framing as directional.
5. **LLMLingua (Microsoft).** A small model drops non-essential tokens from prompts; the repo reports up to 20x compression, with LLMLingua-2 faster, and integrations with LangChain and LlamaIndex ([GitHub](https://github.com/microsoft/LLMLingua)). Better: a published research method with known benchmarks. Worse for agents: it prunes tokens inside text, which can break exact code, paths and diffs, and it has no restore path. It needs a model download; this plugin is stdlib-only.
6. **Letta (MemGPT).** Splits memory into in-context memory blocks, recall memory (searchable history) and archival memory (vector store), edited by the agent through tools ([Letta docs](https://docs.letta.com/guides/agents/architectures/memgpt)). Better: the agent can search its own history, not only fetch a span by id. Worse for this use: it is a full memory architecture, not a drop-in for an existing a0 chat.
7. **Mem0.** Open-source memory layer (Apache-2.0 per one listing) with add/search/update; claims up to 80-90% token reduction ([Mem0 guide](https://mem0.ai/guide/production-ready-self-hosted-ai-memory-options), [mem0 README via search](https://github.com/4xguy/mem0)). The figures are vendor claims from Mem0's own materials, and the repo linked is a fork, so the canonical repo was not read. It stores extracted memories, not the transcript, and needs an LLM (gpt-4o-mini by default per the README).

### What users expect

- Automatic or one-click use. Claude Code and the stock a0 plugin both have it; here compaction is invoked by API, by the agent tool, or from an MCP client, and the README says it does not intercept a0's automatic compression.
- Proof that nothing important was lost. Factory frames this as measurable; this repo has unit tests but no retention eval.
- A way back. Only this approach and Letta offer retrieval of the original; here it is by span id.

### What is unique here

- Non-destructive by construction: spans persist before history is replaced, and the restore tools are read-only.
- Stdlib-only engine that also runs inside the bare MCP subprocess, so it cannot break a0 startup.
- Works offline with a heuristic scorer; paid scoring is opt-in and keys never enter settings.
- Weaknesses to be honest about: no UI, off by default, the heuristic is recency plus keyword overlap, and the vendored engine has drifted from the standalone repo (see roadmap U2).

### Relationship to the standalone `jev-compact` repo

Read-only look: `~/Documents/github/personal/jev-compact` is the same engine as a CLI and library (`src/jev_compact`: `cli.py`, `compact`, `highlight`, `scorer`, `spans`, `store`, `transcript`) with Claude Code, Codex and OpenCode adapters and its own `mcp/restore_server.py`. This plugin vendors the engine minus `cli.py` and `__main__.py`. `store.py` differs: the plugin adds a `list_sessions()` helper, and the standalone `latest_session` is not built on it. So the two copies have already diverged by one function.

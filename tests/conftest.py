"""Make ``usr.plugins.jev_compact.*`` imports resolvable under pytest —
the same qualified path a0 uses — and stub the framework modules the
plugin touches so everything runs standalone, offline.

Stubs model a0's real contracts:

- helpers.settings: set_settings REPLACES (host semantics); delta merges.
- helpers.api: ApiHandler(app, thread_lock) + process(input, request).
- helpers.extension: Extension(agent, **kwargs).
- helpers.tool: Tool with .agent/.args; Response(message, break_loop).
- helpers.history: History/add_message/output_text + provider clear.
- helpers.persist_chat: export_json_chat/save_tmp_chat/remove_msg_files.
- agent: AgentContext.get/.is_running/.run_task/.log; Agent.DATA_NAME_*.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pkg(name, path=None):
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)] if path else []
    return mod


_usr = _pkg("usr")
_plugins = _pkg("usr.plugins")
_jev = _pkg("usr.plugins.jev_compact", ROOT)
_usr.plugins = _plugins
_plugins.jev_compact = _jev
sys.modules.setdefault("usr", _usr)
sys.modules.setdefault("usr.plugins", _plugins)
sys.modules["usr.plugins.jev_compact"] = _jev


# --- framework stubs ---------------------------------------------------------


class Extension:
    """Matches host helpers/extension.py."""

    def __init__(self, agent, **kwargs):
        self.agent = agent
        self.kwargs = kwargs


class ApiHandler:
    def __init__(self, app, thread_lock):
        self.app = app
        self.thread_lock = thread_lock

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_csrf(cls) -> bool:
        return cls.requires_auth()  # host ties CSRF to auth, not a blanket True

    @classmethod
    def requires_api_key(cls) -> bool:
        return False

    @classmethod
    def requires_loopback(cls) -> bool:
        return False

    @classmethod
    def get_methods(cls) -> list:
        return ["POST"]

    async def process(self, input, request):
        raise NotImplementedError


class _Response:
    """helpers.api Response stand-in — body + status code."""

    def __init__(self, body="", status=200):
        self.body = body
        self.status_code = status


class ToolResponse:
    """helpers.tool.Response is a dataclass — break_loop is REQUIRED."""
    def __init__(self, message, break_loop):
        self.message = message
        self.break_loop = break_loop


class Tool:
    """helpers/tool.py contract: .agent/.args set by the host."""

    def __init__(self, agent=None, args=None, **kw):
        self.agent = agent
        self.args = args or {}
        self.kwargs = kw


def run(coro):
    return asyncio.run(coro)


# --- settings / plugins ------------------------------------------------------

_settings_state: dict = {}
_plugin_config: dict = {}


def _get_settings():
    return dict(_settings_state)


def _set_settings(new):
    """REAL host semantics: whole-settings replace. Never call with a
    partial dict — set_settings_delta is the merge API."""
    _settings_state.clear()
    _settings_state.update(new or {})
    return dict(_settings_state)


def _set_settings_delta(delta):
    _settings_state.update(delta or {})
    return dict(_settings_state)


_settings_mod = types.ModuleType("helpers.settings")
_settings_mod.get_settings = _get_settings
_settings_mod.set_settings = _set_settings
_settings_mod.set_settings_delta = _set_settings_delta
_settings_mod._state = _settings_state


def _get_plugin_config(name, *a, **kw):
    return dict(_plugin_config) if name == "jev_compact" else {}


_plugins_mod = types.ModuleType("helpers.plugins")
_plugins_mod.get_plugin_config = _get_plugin_config


# --- history / persist_chat / state monitor ---------------------------------

_added_messages: list = []
_saved_chats: list = []
_removed_msg_files: list = []
_dirty_marks: list = []


class _History:
    def __init__(self, agent=None):
        self.agent = agent
        self.messages: list[dict] = []
        self._out: list = []

    def add_message(self, ai=False, content="", **kw):
        self.messages.append({"ai": ai, "content": content})

    def output(self):
        return list(self._out)


def _clear_responses_provider_state(agent):
    agent.data.pop("responses_state", None)


_history_mod = types.ModuleType("helpers.history")
_history_mod.History = _History
_history_mod.clear_responses_provider_state = _clear_responses_provider_state
_history_mod.output_text = lambda msgs, **kw: "\n".join(
    str(m.get("content", "")) for m in msgs
)


def _export_json_chat(context):
    return json.dumps({"context": context.id, "log": context.log.dumps()})


def _save_tmp_chat(context):
    _saved_chats.append(context.id)
    return f"/tmp/chat-{context.id}.json"


def _remove_msg_files(ctxid):
    _removed_msg_files.append(ctxid)


_persist_mod = types.ModuleType("helpers.persist_chat")
_persist_mod.export_json_chat = _export_json_chat
_persist_mod.save_tmp_chat = _save_tmp_chat
_persist_mod.remove_msg_files = _remove_msg_files

_state_mod = types.ModuleType("helpers.state_monitor_integration")
_state_mod.mark_dirty_all = lambda **kw: _dirty_marks.append(kw)


# --- agent / AgentContext ----------------------------------------------------

class _Log:
    def __init__(self):
        self.logs: list[dict] = []

    def log(self, **kw):
        self.logs.append(kw)
        return kw

    def reset(self):
        self.logs.clear()

    def dumps(self):
        return self.logs


class _DeferredTask:
    """Runs the coroutine immediately-ish (test seam)."""

    def __init__(self):
        self.coro = None
        self._alive = False

    def start_task(self, func, *args, **kwargs):
        self.coro = func(*args, **kwargs)
        self._alive = False  # completes synchronously in tests

    def is_alive(self):
        return self._alive


class AgentContext:
    _contexts: dict = {}

    def __init__(self, ctxid="ctx1", running=False):
        self.id = ctxid
        self.log = _Log()
        self.task = None
        self.streaming_agent = None
        self.agent0 = types.SimpleNamespace(
            context=self,
            data={},
            history=_History(),
            hist_add_message=lambda **kw: None,
        )
        self._running = running
        AgentContext._contexts[ctxid] = self

    @staticmethod
    def get(ctxid):
        return AgentContext._contexts.get(ctxid)

    def is_running(self):
        return self._running or (self.task and self.task.is_alive()) or False

    def run_task(self, func, *args, **kwargs):
        """Captures the coroutine — tests drain it AFTER the outer
        asyncio.run(process(...)) completes (can't nest loops)."""
        self.task = _DeferredTask()
        self.task.start_task(func, *args, **kwargs)
        return self.task


class Agent:
    DATA_NAME_CTX_WINDOW = "ctx_window"
    DATA_NAME_SUBORDINATE = "subordinate"


# --- hermes stubs (dual-harness adapter) --------------------------------------
# Mirror agent/context_engine.py's ContextEngine contract: token-state class
# attrs the host reads directly, lifecycle + tool defaults, clone_for_agent.

class _ContextEngine:
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0
    threshold_percent: float = 0.75
    protect_first_n: int = 3
    protect_last_n: int = 6
    emit_automatic_compaction_status: bool = True

    @property
    def name(self):
        return ""

    def update_from_response(self, usage):
        pass

    def should_compress(self, prompt_tokens=None):
        return False

    def should_compress_preflight(self, messages):
        return False

    def should_compress_info(self, prompt_tokens=None):
        return self.should_compress(prompt_tokens), None

    def has_content_to_compress(self, messages):
        return True

    def compress(self, messages, current_tokens=None, focus_topic=None,
                 force=False, memory_context=""):
        return list(messages)

    def on_session_start(self, session_id, **kwargs):
        pass

    def on_session_end(self, session_id, messages):
        pass

    def on_session_reset(self):
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def get_tool_schemas(self):
        return []

    def handle_tool_call(self, name, args, **kwargs):
        import json as _json

        return _json.dumps({"error": f"Unknown context engine tool: {name}"})

    def get_status(self):
        return {
            "last_prompt_tokens": max(self.last_prompt_tokens, 0),
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
        }

    def clone_for_agent(self):
        import copy

        return copy.deepcopy(self)

    def update_model(self, model, context_length, base_url="", api_key="",
                     provider="", api_mode=""):
        """Simplified real-ABC semantics: percent x window → threshold."""
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)


_ce_mod = types.ModuleType("agent.context_engine")
_ce_mod.ContextEngine = _ContextEngine


_agent_mod = types.ModuleType("agent")
_agent_mod.AgentContext = AgentContext
_agent_mod.Agent = Agent
_agent_mod.context_engine = _ce_mod


# --- assemble helpers.* package ----------------------------------------------

_ext_mod = types.ModuleType("helpers.extension")
_ext_mod.Extension = Extension
_api_mod = types.ModuleType("helpers.api")
_api_mod.ApiHandler = ApiHandler
_api_mod.Input = dict
_api_mod.Output = object
_api_mod.Request = object
_api_mod.Response = _Response
_tool_mod = types.ModuleType("helpers.tool")
_tool_mod.Tool = Tool
_tool_mod.Response = ToolResponse

_helpers = _pkg("helpers")
_helpers.extension = _ext_mod
_helpers.api = _api_mod
_helpers.settings = _settings_mod
_helpers.plugins = _plugins_mod
_helpers.history = _history_mod
_helpers.persist_chat = _persist_mod
_helpers.state_monitor_integration = _state_mod
_helpers.tool = _tool_mod
sys.modules["helpers"] = _helpers
sys.modules["helpers.extension"] = _ext_mod
sys.modules["helpers.api"] = _api_mod
sys.modules["helpers.settings"] = _settings_mod
sys.modules["helpers.plugins"] = _plugins_mod
sys.modules["helpers.history"] = _history_mod
sys.modules["helpers.persist_chat"] = _persist_mod
sys.modules["helpers.state_monitor_integration"] = _state_mod
sys.modules["helpers.tool"] = _tool_mod
sys.modules["agent"] = _agent_mod
sys.modules["agent.context_engine"] = _ce_mod


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Ambient JEV_*/key env vars must never leak into tests."""
    for var in (
        "JEV_STORE_DIR", "JEV_COMPACT_ENABLED", "JEV_COMPACT_SCORER",
        "JEV_MODEL", "JEV_COMPACT_BUDGET_TOKENS", "JEV_COMPACT_TAIL_KEEP",
        "JEV_COMPACT_MIN_TOKENS", "JEV_COMPACT_AUTO_REGISTER_MCP",
        "TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "TYPESAFE_API_BASE",
        "JEV_OPENROUTER_MODEL", "JEV_SCORE_WORKERS",
    ):
        monkeypatch.delenv(var, raising=False)
    _settings_state.clear()
    _plugin_config.clear()
    _added_messages.clear()
    _saved_chats.clear()
    _removed_msg_files.clear()
    _dirty_marks.clear()
    AgentContext._contexts.clear()
    from usr.plugins.jev_compact.helpers import runtime

    runtime._reset()
    yield
    _settings_state.clear()
    _plugin_config.clear()
    AgentContext._contexts.clear()
    runtime._reset()


@pytest.fixture()
def settings_state():
    return _settings_state


@pytest.fixture()
def plugin_config():
    return _plugin_config


@pytest.fixture()
def saved_chats():
    return _saved_chats


@pytest.fixture()
def make_context():
    """Factory: a context whose history is a list of OutputMessages."""
    def _mk(ctxid="ctx1", messages=None, running=False):
        ctx = AgentContext(ctxid, running=running)
        ctx.agent0.history._out = list(messages or [])
        return ctx
    return _mk


@pytest.fixture()
def chat_events():
    """A realistic history as OutputMessages — padded past the plugin's
    min_tokens clamp (200) and budget floor so drops actually occur."""
    pad = " working through the auth flow, rotating tokens, " * 20
    return [
        {"ai": False, "content": "Fix the login bug in auth.py" + pad, "id": "m1"},
        {"ai": True, "content": "Reading auth.py — the handler at line 42 "
                               "drops the session token on refresh." + pad,
         "id": "m2"},
        {"ai": False, "content": {"tool_name": "code_execution",
                                  "tool_result": "pytest: 2 failed" + pad},
         "id": "m3"},
        {"ai": True, "content": "Root cause: missing session.commit() "
                               "after token rotation — the fix." + pad,
         "id": "m4"},
        {"ai": False, "content": "Ship it" + pad, "id": "m5"},
        {"ai": True, "content": "Committed 9f3ab — tests green." + pad,
         "id": "m6"},
        {"ai": False, "content": "Now update the changelog" + pad, "id": "m7"},
        {"ai": True, "content": "Updated CHANGELOG.md." + pad, "id": "m8"},
    ]

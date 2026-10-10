"""Transcript loaders.

Supported inputs:
  - Claude Code session transcripts (JSONL, `type`/`message` records)
  - Codex rollout transcripts (JSONL, `type`/`payload` records)
  - Generic NDJSON (`{"role": ..., "content"|"text": ...}` per line)
  - A JSON array of the same generic objects

All loaders normalize to events: {"kind", "text", ...meta}.
Kinds: user_turn, assistant_text, tool_call, tool_result, system, other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

MAX_EVENT_CHARS = 200_000  # bound any single event; spans stay scorable


def load(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("["):
        return list(iter_generic(json.loads(text)))
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.extend(iter_record(obj))
    return events


def iter_record(obj: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if "type" in obj and "message" in obj:
        yield from iter_claude_code(obj)
    elif obj.get("type") == "response_item":
        yield from iter_codex(obj)
    elif obj.get("type") in _CODEX_META_TYPES:
        return  # rollout metadata — noise, not conversation content
    else:
        yield from iter_generic([obj])


_CODEX_META_TYPES = {
    "session_meta", "event_msg", "world_state", "turn_context",
    "compacted", "token_count",
}


def iter_codex(obj: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Codex rollout JSONL: {type:"response_item", payload:{type,...}}."""
    ts = obj.get("timestamp")
    p = obj.get("payload")
    if not isinstance(p, dict):
        return
    ptype = p.get("type")
    if ptype == "message":
        role = p.get("role", "other")
        text = "\n".join(
            str(b.get("text", ""))
            for b in p.get("content") or []
            if isinstance(b, dict)
        )
        yield _ev(_kind(role), text, ts)
    elif ptype == "function_call":
        name = p.get("name", "tool")
        yield _ev("tool_call", f"{name}: {p.get('arguments', '')}"[:MAX_EVENT_CHARS], ts, tool=name)
    elif ptype in ("function_call_output", "local_shell_call_output", "custom_tool_call_output"):
        out = p.get("output", "")
        if not isinstance(out, str):
            out = json.dumps(out)
        yield _ev("tool_result", out, ts)
    elif ptype == "reasoning":
        summary = p.get("summary") or []
        text = " ".join(
            str(b.get("text", "")) for b in summary if isinstance(b, dict)
        )
        if text.strip():
            yield _ev("assistant_text", text, ts)


def iter_claude_code(obj: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Claude Code JSONL: {type, message:{role, content:[blocks]}, timestamp}."""
    msg = obj.get("message") or {}
    role = msg.get("role") or obj.get("type", "other")
    content = msg.get("content")
    ts = obj.get("timestamp")
    if isinstance(content, str):
        yield _ev(_kind(role), content, ts)
        return
    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            yield _ev(_kind(role), block.get("text", ""), ts)
        elif btype == "tool_use":
            name = block.get("name", "tool")
            payload = json.dumps(block.get("input", {}))[:2000]
            yield _ev("tool_call", f"{name}: {payload}", ts, tool=name)
        elif btype == "tool_result":
            yield _ev("tool_result", _result_text(block), ts)


def iter_generic(items: Iterable[Any]) -> Iterable[dict[str, Any]]:
    """Generic role/content or role/text records."""
    for obj in items:
        if not isinstance(obj, dict):
            continue
        role = str(obj.get("role", obj.get("kind", "other")))
        text = obj.get("text", obj.get("content", ""))
        if isinstance(text, list):
            text = "\n".join(
                str(b.get("text", "")) for b in text if isinstance(b, dict)
            )
        yield _ev(_kind(role), str(text), obj.get("timestamp"))


def _kind(role: str) -> str:
    return {
        "user": "user_turn",
        "assistant": "assistant_text",
        "system": "system",
        "tool": "tool_result",
    }.get(role, role if role.endswith(("_turn", "_text", "_call", "_result")) else "other")


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", "")) for b in content if isinstance(b, dict)
        )[:MAX_EVENT_CHARS]
    return str(content)[:MAX_EVENT_CHARS]


def _ev(kind: str, text: str, ts: Any, **meta: Any) -> dict[str, Any]:
    text = text[:MAX_EVENT_CHARS]
    ev: dict[str, Any] = {"kind": kind, "text": text}
    if ts:
        ev["ts"] = ts
    ev.update(meta)
    return ev

"""Bridge: a0 ``history.output()`` → jev-compact events.

a0 stores messages as ``OutputMessage`` dicts ``{ai, content, metadata,
id, sequence}`` where ``content`` is ``MessageContent`` — a nested union
of str/dict/list/RawMessage. Tool results arrive as ``ai=False`` records
whose content is ``{"tool_name": ..., "tool_result": ...}``
(agent.hist_add_tool_result). This module flattens each output record
into the engine's event shape ``{"kind", "text", ...}`` using the same
stringification rules as ``helpers/history._stringify_content``.
"""

from __future__ import annotations

import json
from typing import Any

RAW_MESSAGE_OUTPUT_TEXT_TRIM = 30_000  # mirror helpers/history.py
MAX_EVENT_CHARS = 200_000              # mirror engine transcript.py


def events_from_history(history_output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OutputMessage list → engine events (user_turn / assistant_text /
    tool / other)."""
    events: list[dict[str, Any]] = []
    for out in history_output or []:
        if not isinstance(out, dict):
            continue
        content = out.get("content")
        kind = "assistant_text" if out.get("ai") else "user_turn"
        if isinstance(content, dict) and "tool_result" in content:
            kind = "tool"
            text = (
                f"{content.get('tool_name', 'tool')}: "
                f"{content.get('tool_result', '')}"
            )
        else:
            text = stringify_content(content)
        if not str(text).strip():
            continue  # empty records carry no retention value
        ev: dict[str, Any] = {"kind": kind, "text": str(text)[:MAX_EVENT_CHARS]}
        mid = out.get("id")
        if mid:
            ev["msg_id"] = str(mid)
        events.append(ev)
    return events


def stringify_content(content: Any) -> str:
    """Same contract as helpers.history._stringify_content — str passes
    through, raw messages prefer their ``preview``, everything else is a
    JSON dump (bounded)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        preview = content.get("preview")
        if isinstance(preview, str) and preview:
            return preview
        text = _json_dumps(content)
        if len(text) > RAW_MESSAGE_OUTPUT_TEXT_TRIM:
            return text[:RAW_MESSAGE_OUTPUT_TEXT_TRIM] + "... TRIMMED"
        return text
    if isinstance(content, list):
        return _json_dumps(content)
    if content is None:
        return ""
    return str(content)


def _json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)

"""Span model and transcript segmentation.

A span is the atomic unit of retention: one user turn, one assistant text
block, or one tool call paired with its result. Spans are never split —
a kept line divorced from its block is worse than a tombstone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHARS_PER_TOKEN = 4  # rough estimate; good enough for budgets
PREVIEW_CHARS = 80


@dataclass
class Span:
    """One retainable unit of transcript."""

    id: str
    kind: str  # user_turn | assistant_text | tool | system | other
    text: str
    turn_index: int
    token_est: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def preview(self) -> str:
        line = self.text.strip().splitlines()[0] if self.text.strip() else "(empty)"
        return line[:PREVIEW_CHARS]


def est_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def segment(events: list[dict[str, Any]]) -> list[Span]:
    """Group raw events into spans.

    A tool_call event absorbs the immediately following tool_result event
    so the pair is scored and retained atomically.
    """
    spans: list[Span] = []
    pending_tool: dict[str, Any] | None = None
    for ev in events:
        kind = ev.get("kind", "other")
        if kind == "tool_call":
            if pending_tool is not None:
                spans.append(_mk(pending_tool, len(spans)))
            pending_tool = ev
            continue
        if kind == "tool_result" and pending_tool is not None:
            merged = dict(pending_tool)
            merged["text"] = (
                f"{pending_tool.get('text', '')}\n--- result ---\n{ev.get('text', '')}"
            ).strip()
            spans.append(_mk(merged, len(spans), kind="tool"))
            pending_tool = None
            continue
        if pending_tool is not None:
            spans.append(_mk(pending_tool, len(spans), kind="tool"))
            pending_tool = None
        spans.append(_mk(ev, len(spans)))
    if pending_tool is not None:
        spans.append(_mk(pending_tool, len(spans), kind="tool"))
    return spans


def _mk(ev: dict[str, Any], idx: int, kind: str | None = None) -> Span:
    text = str(ev.get("text", ""))
    return Span(
        id=f"s{idx}",
        kind=kind or str(ev.get("kind", "other")),
        text=text,
        turn_index=idx,
        token_est=est_tokens(text),
        meta={k: v for k, v in ev.items() if k not in {"kind", "text"}},
    )

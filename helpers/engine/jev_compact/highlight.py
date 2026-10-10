"""Moving-highlight extraction.

The highlight is a compact spec of what the conversation is *currently*
about. It drifts: when the topic moves (moon research -> lidar code),
the highlight follows, and span scoring follows the highlight.

V1 is heuristic — zero model calls: last user intent, last assistant
action, active file paths from recent tool spans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .spans import Span

TAIL_SPANS = 6
MAX_PIECE_CHARS = 600
MAX_FILES = 8

_PATH_RE = re.compile(r"(?:[\w~.-]+/)+[\w.-]+\.\w{1,8}\b")
_STOPWORDS = frozenset(
    "the a an and or of to in on for with that this it is are was were be been "
    "have has had do does did will would can could should you your we our they "
    "their them from at by as not no yes but if then so what how when where "
    "which who why all any each more some such only also just into over under".split()
)


@dataclass
class Highlight:
    text: str
    keywords: frozenset[str]
    tail_span_ids: list[str]


def extract(spans: list[Span], tail: int = TAIL_SPANS) -> Highlight:
    tail_spans = spans[-tail:] if tail else []
    last_user = _last(spans, "user_turn")
    last_asst = _last(spans, "assistant_text")
    files: list[str] = []
    for sp in reversed(tail_spans + spans[-tail * 3 :]):
        if sp.kind == "tool":
            for m in _PATH_RE.findall(sp.text):
                if m not in files:
                    files.append(m)
                if len(files) >= MAX_FILES:
                    break
        if len(files) >= MAX_FILES:
            break

    pieces = []
    if last_user is not None:
        pieces.append(f"Intent: {_clip(last_user.text)}")
    if last_asst is not None and last_asst is not last_user:
        pieces.append(f"Latest action: {_clip(last_asst.text)}")
    if files:
        pieces.append(f"Active files: {', '.join(files)}")
    text = "CURRENT WORK:\n" + "\n".join(pieces) if pieces else "CURRENT WORK: (start of session)"

    kw = _keywords(" ".join(sp.text for sp in tail_spans) + " " + text)
    return Highlight(text=text, keywords=kw, tail_span_ids=[s.id for s in tail_spans])


def _last(spans: list[Span], kind: str) -> Span | None:
    for sp in reversed(spans):
        if sp.kind == kind:
            return sp
    return None


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_PIECE_CHARS]


def _keywords(text: str) -> frozenset[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    return frozenset(w for w in words if w not in _STOPWORDS)

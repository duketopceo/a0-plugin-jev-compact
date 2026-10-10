"""Span scoring: (relevance_to_current, load_bearing) in [0, 1] each.

- relevance: how much this span matters for the work in the highlight
- load_bearing: whether the span still carries needed setup facts even
  when off-topic (chosen approach, paths, errors fixed, issue refs)

HeuristicScorer runs fully offline and is the default when no API key
is configured. JevScorer calls TypeSafe's System One API when
TYPESAFE_API_KEY is set, or OpenRouter's Decisions API
(/api/alpha/decisions) with OPENROUTER_API_KEY when the model is a
TypeSafe slug (~typesafe/jev-latest, typesafe/jev-1.13). Any other
OpenRouter model falls back to generic chat-completions scoring.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Protocol

from .highlight import Highlight
from .spans import Span

log = logging.getLogger(__name__)

SPAN_SCORE_CHARS = 1_500  # scorer sees a bounded view of each span
HTTP_TIMEOUT_S = 15

_DECISION_RE = re.compile(
    r"\b(decided|decision|we'?ll use|chose|chosen|the fix|root cause|"
    r"approach|agreed|confirmed|ship it|merged|attested)\b",
    re.I,
)
_ERROR_RE = re.compile(r"\b(Traceback|Error:|FAILED|Exception|panic|segfault)\b")
_REF_RE = re.compile(r"(#\d{2,}\b|\b[0-9a-f]{7,40}\b)")
_PATH_RE = re.compile(r"(?:[\w~.-]+/)+[\w.-]+\.\w{1,8}\b")


class Scorer(Protocol):
    def score(self, span: Span, highlight: Highlight) -> tuple[float, float]:
        """Return (relevance, load_bearing), each in [0, 1]."""


class HeuristicScorer:
    """Deterministic offline scorer: recency decay + keyword overlap + fact patterns."""

    def __init__(self, total_spans: int):
        self._total = max(1, total_spans)

    def score(self, span: Span, highlight: Highlight) -> tuple[float, float]:
        distance = self._total - span.turn_index
        recency = 1.0 / (1.0 + distance / 20.0)
        span_words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", span.text.lower()))
        overlap = (
            len(span_words & highlight.keywords) / max(1, len(highlight.keywords))
        )
        kind_boost = {"user_turn": 0.15, "assistant_text": 0.1}.get(span.kind, 0.0)
        relevance = min(1.0, 0.4 * recency + 0.55 * overlap + kind_boost)

        load = 0.0
        if _DECISION_RE.search(span.text):
            load += 0.4
        if _ERROR_RE.search(span.text):
            load += 0.25
        if _REF_RE.search(span.text):
            load += 0.15
        if _PATH_RE.search(span.text):
            load += 0.15
        if span.kind == "user_turn":
            load += 0.1
        return relevance, min(1.0, load)


class ScorerError(RuntimeError):
    pass


class JevScorer:
    """Scores spans via TypeSafe Jev (native API) or OpenRouter chat fallback."""

    parallel_ok = True  # I/O-bound HTTP scoring — safe under ThreadPoolExecutor

    def __init__(
        self,
        api_key: str,
        model: str = "jev-latest",
        api_base: str = "https://api.typesafe.ai",
        via: str = "typesafe",
    ):
        self._key = api_key
        self._model = model
        self._base = api_base.rstrip("/")
        self._via = via

    def score(self, span: Span, highlight: Highlight) -> tuple[float, float]:
        view = span.text[:SPAN_SCORE_CHARS]
        if self._via == "openrouter":
            return self._score_openrouter(span, highlight, view)
        return self._score_typesafe(span, highlight, view)

    def _score_typesafe(
        self, span: Span, highlight: Highlight, view: str
    ) -> tuple[float, float]:
        # Request shape per docs.typesafe.ai (state + primitives); verify
        # against a live key before release — see AGENTS.md known state.
        payload = {
            "model": self._model,
            "state": {
                "highlight": highlight.text,
                "span_kind": span.kind,
                "span_text": view,
            },
            "questions": [
                {
                    "id": "rel",
                    "kind": "score",
                    "min": 0,
                    "max": 10,
                    "prompt": "How relevant is this span to the current work described by the highlight?",
                },
                {
                    "id": "load",
                    "kind": "noul",
                    "prompt": "Does this span carry facts still needed to continue (paths, decisions, errors fixed), even if off-topic?",
                },
            ],
        }
        data = self._post(f"{self._base}/v1/systemone", payload)
        answers = data.get("answers", data)
        rel_raw = answers.get("rel", {})
        load_raw = answers.get("load", {})
        rel = float(rel_raw.get("score", rel_raw.get("value", 0.0))) / 10.0
        load = float(load_raw.get("noul", load_raw.get("value", 0.0)))
        return _clamp01(rel), _clamp01(load)

    def _score_openrouter(
        self, span: Span, highlight: Highlight, view: str
    ) -> tuple[float, float]:
        if self._model.lstrip("~").startswith("typesafe/"):
            return self._score_decisions(span, highlight, view)
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You score conversation spans for context retention. "
                        'Reply with JSON only: {"rel": <0-10>, "load": <0.0-1.0>}. '
                        "rel = relevance to the current work highlight; "
                        "load = probability the span carries facts still needed "
                        "(paths, decisions, fixed errors) even if off-topic."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"{highlight.text}\n\nSPAN ({span.kind}):\n{view}"
                    ),
                },
            ],
            "temperature": 0,
        }
        data = self._post(f"{self._base}/api/v1/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(_first_json(content))
            return _clamp01(float(parsed["rel"]) / 10.0), _clamp01(
                float(parsed["load"])
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ScorerError(f"unparseable scorer response: {exc}") from exc

    def _score_decisions(
        self, span: Span, highlight: Highlight, view: str
    ) -> tuple[float, float]:
        # OpenRouter Decisions API — verified live 2026-09-19.
        payload = {
            "model": self._model,
            "state": {
                "highlight": highlight.text,
                "span_kind": span.kind,
                "span_text": view,
            },
            "questions": {
                "relevance": {
                    "type": "score",
                    "instructions": (
                        "How relevant is this span to the current work "
                        "described by the highlight?"
                    ),
                    "criteria": [
                        "irrelevant",
                        "tangential",
                        "relevant",
                        "critical",
                    ],
                },
                "load_bearing": {
                    "type": "noul",
                    "instructions": (
                        "Does this span carry facts still needed to "
                        "continue (paths, decisions, errors fixed), even "
                        "if off-topic?"
                    ),
                    "true": "Contains still-needed facts",
                    "false": "Safely droppable",
                },
            },
        }
        data = self._post(f"{self._base}/api/alpha/decisions", payload)
        try:
            answers = data["answers"]
            rel = float(answers["relevance"]["score"]) / 3.0
            load = float(answers["load_bearing"]["noul"])
            return _clamp01(rel), _clamp01(load)
        except (KeyError, TypeError, ValueError) as exc:
            raise ScorerError(f"unparseable decisions response: {exc}") from exc

    def _post(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                if resp.geturl().startswith("https://") is not True:
                    raise ScorerError(f"non-https redirect from {url}")
                return json.loads(resp.read(1_000_000))
        except urllib.error.HTTPError as exc:
            raise ScorerError(f"scorer API {exc.code}: {exc.read(500)}") from exc
        except urllib.error.URLError as exc:
            raise ScorerError(f"scorer API unreachable: {exc.reason}") from exc


def resolve(name: str = "auto", total_spans: int = 1) -> Scorer:
    """Pick a scorer: 'jev' forces API (errors without a key), 'heuristic'
    forces offline, 'auto' uses Jev when a key exists."""
    if name == "heuristic":
        return HeuristicScorer(total_spans)
    ts_key = os.environ.get("TYPESAFE_API_KEY")
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if ts_key:
        return JevScorer(
            ts_key,
            model=os.environ.get("JEV_MODEL", "jev-latest"),
            api_base=os.environ.get("TYPESAFE_API_BASE", "https://api.typesafe.ai"),
        )
    if or_key:
        # TypeSafe decisions models route to /api/alpha/decisions; any
        # other model slug uses generic chat-completions scoring.
        return JevScorer(
            or_key,
            model=os.environ.get(
                "JEV_OPENROUTER_MODEL", "~typesafe/jev-latest"
            ),
            api_base="https://openrouter.ai",
            via="openrouter",
        )
    if name == "jev":
        raise ScorerError("no TYPESAFE_API_KEY or OPENROUTER_API_KEY in env")
    log.info("no scorer API key — using heuristic scorer")
    return HeuristicScorer(total_spans)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _first_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return text[start : end + 1]

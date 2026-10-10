"""Vendored-engine behavior through the plugin seam — spans, highlight,
heuristic scoring, compact invariants, store round-trip."""

from __future__ import annotations

import pytest

from usr.plugins.jev_compact.helpers.engine.jev_compact import (
    compact as eng_compact,
    highlight as eng_highlight,
    scorer as eng_scorer,
    spans as eng_spans,
    store as eng_store,
)


def _events(n=10):
    evs = []
    for i in range(n):
        kind = "user_turn" if i % 3 == 0 else "assistant_text"
        text = f"turn {i}: working on file_{i}.py fixing the bug" * (i + 1)
        evs.append({"kind": kind, "text": text})
    return evs


def test_segment_merges_tool_pairs():
    evs = [
        {"kind": "user_turn", "text": "hi"},
        {"kind": "tool_call", "text": "ls /"},
        {"kind": "tool_result", "text": "bin etc usr"},
    ]
    spans = eng_spans.segment(evs)
    assert len(spans) == 2
    assert spans[1].kind == "tool"
    assert "--- result ---" in spans[1].text


def test_compact_keeps_tail_and_emits_tombstones():
    spans = eng_spans.segment(_events(12))
    hl = eng_highlight.extract(spans)
    scorer = eng_scorer.HeuristicScorer(len(spans))
    res = eng_compact.compact(spans, hl, scorer, budget_tokens=50,
                              tail_keep=4)
    # tail always survives
    for s in spans[-4:]:
        assert s.id in res.kept_ids
    assert res.tokens_after <= res.tokens_before
    assert res.stats["spans"] == len(spans)
    # tombstone receipts mention dropped ranges
    assert "tombstoned" in res.text


def test_compact_never_loses_spans():
    """kept ∪ dropped == all ids — nothing vanishes silently."""
    spans = eng_spans.segment(_events(15))
    hl = eng_highlight.extract(spans)
    res = eng_compact.compact(
        spans, hl, eng_scorer.HeuristicScorer(len(spans)),
        budget_tokens=100, tail_keep=4)
    assert sorted(res.kept_ids + res.dropped_ids,
                  key=lambda i: int(i[1:])) == [s.id for s in spans]


def test_highlight_tracks_last_intent():
    spans = eng_spans.segment(_events(8))
    hl = eng_highlight.extract(spans)
    assert "CURRENT WORK" in hl.text
    assert hl.keywords  # non-empty keyword set


def test_store_roundtrip(tmp_path):
    spans = eng_spans.segment(_events(9))
    hl = eng_highlight.extract(spans)
    res = eng_compact.compact(
        spans, hl, eng_scorer.HeuristicScorer(len(spans)),
        budget_tokens=60, tail_keep=2)
    st = eng_store.SpanStore(tmp_path, "sess1")
    st.save(spans, hl, res.tombstoned, source=tmp_path / "t.json")

    same = eng_store.SpanStore(tmp_path, "sess1")
    assert same.get_span("s0") == spans[0].text
    rng = res.tombstoned[0][0] if res.tombstoned else None
    if rng:
        assert same.get_range(rng)  # verbatim restore
        listed = same.list_tombstones()
        assert any(t["range"] == rng for t in listed)
    assert "CURRENT WORK" in same.get_highlight()
    # file perms + atomic writes
    assert (tmp_path / "sess1" / "spans.json").stat().st_mode & 0o777 == 0o600


def test_heuristic_scorer_bounds():
    spans = eng_spans.segment(_events(6))
    hl = eng_highlight.extract(spans)
    sc = eng_scorer.HeuristicScorer(len(spans))
    for s in spans:
        rel, load = sc.score(s, hl)
        assert 0.0 <= rel <= 1.0 and 0.0 <= load <= 1.0


def test_jev_scorer_requires_key(monkeypatch):
    with pytest.raises(eng_scorer.ScorerError):
        eng_scorer.resolve("jev", 1)

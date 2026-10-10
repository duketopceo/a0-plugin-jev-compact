"""Retention engine: score -> rank -> keep/tombstone/drop under a budget.

Emits: highlight header + kept spans verbatim (original order) +
tombstone receipts for dropped runs. Nothing is destroyed — the full
transcript stays in the span store and tombstones resolve via
`jev-compact restore` or the context-restore MCP server.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .highlight import Highlight
from .scorer import Scorer
from .spans import Span, est_tokens

TAIL_KEEP = 4          # the live edge of the conversation is never scored out
PIN_LOAD = 0.8         # load_bearing at/above this survives regardless of budget
W_REL, W_LOAD = 0.65, 0.35
TOMBSTONE_TOKENS = 12  # est. cost per tombstone line
SCORE_WORKERS = int(os.environ.get("JEV_SCORE_WORKERS", "8"))
SCORE_PARALLEL_MIN = 8  # below this many head spans, score sequentially


@dataclass
class CompactResult:
    text: str
    kept_ids: list[str]
    tombstoned: list[tuple[str, str]]  # (span_range, receipt_text)
    dropped_ids: list[str]
    tokens_before: int
    tokens_after: int
    stats: dict = field(default_factory=dict)


def compact(
    spans: list[Span],
    highlight: Highlight,
    scorer: Scorer,
    budget_tokens: int,
    tail_keep: int = TAIL_KEEP,
) -> CompactResult:
    tokens_before = sum(s.token_est for s in spans)
    tail = spans[-tail_keep:] if tail_keep else []
    tail_ids = {s.id for s in tail}
    head = [s for s in spans if s.id not in tail_ids]

    scored: list[tuple[Span, float, float, float]] = []  # span, rel, load, composite
    raw: dict[str, tuple[float, float]] = {}
    if getattr(scorer, "parallel_ok", False) and len(head) >= SCORE_PARALLEL_MIN:
        with ThreadPoolExecutor(max_workers=SCORE_WORKERS) as pool:
            futs = {sp.id: pool.submit(scorer.score, sp, highlight) for sp in head}
            for sid, fut in futs.items():
                raw[sid] = fut.result()
    else:
        for sp in head:
            raw[sp.id] = scorer.score(sp, highlight)
    for sp in head:
        rel, load = raw[sp.id]
        scored.append((sp, rel, load, W_REL * rel + W_LOAD * load))

    header_tokens = est_tokens(highlight.text)
    tail_cost = sum(s.token_est for s in tail) + header_tokens
    remaining = max(0, budget_tokens - tail_cost)

    keep_ids = set(tail_ids)
    drop: list[tuple[Span, float]] = []
    for sp, rel, load, composite in sorted(scored, key=lambda t: -t[3]):
        if load >= PIN_LOAD:
            keep_ids.add(sp.id)
            remaining -= sp.token_est
            continue
        if sp.token_est <= remaining:
            keep_ids.add(sp.id)
            remaining -= sp.token_est
        else:
            drop.append((sp, composite))

    body: list[str] = []
    tombstones: list[tuple[str, str]] = []
    dropped_ids: list[str] = []
    run: list[Span] = []
    tokens_after = header_tokens

    def flush() -> None:
        nonlocal tokens_after
        if not run:
            return
        rng = run[0].id if len(run) == 1 else f"{run[0].id}-{run[-1].id}"
        receipt = _receipt(rng, run)
        body.append(receipt)
        tombstones.append((rng, receipt))
        dropped_ids.extend(s.id for s in run)
        tokens_after += TOMBSTONE_TOKENS
        run.clear()

    for sp in spans:
        if sp.id in keep_ids and sp.text.strip():
            flush()
            body.append(f"[{sp.id} · {sp.kind}]\n{sp.text}")
            tokens_after += sp.token_est
        elif sp.id in keep_ids:
            continue  # kept but empty — no content to emit or restore
        else:
            run.append(sp)
    flush()

    text = f"{highlight.text}\n{'=' * 40}\n" + "\n\n".join(body)
    return CompactResult(
        text=text,
        kept_ids=sorted(keep_ids, key=lambda i: int(i[1:])),
        tombstoned=tombstones,
        dropped_ids=dropped_ids,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        stats={
            "spans": len(spans),
            "kept": len(keep_ids),
            "tombstone_runs": len(tombstones),
            "ratio": round(tokens_after / max(1, tokens_before), 3),
        },
    )


def _receipt(rng: str, run: list[Span]) -> str:
    kinds = ",".join(sorted({s.kind for s in run}))
    first = next((s.preview for s in run if s.text.strip()), "(empty)")
    return f"[[{rng} tombstoned · {len(run)} span(s) · {kinds} · begins: {first}]]"

"""Span store: persists full spans so tombstones always resolve.

Layout: <root>/<session_id>/spans.json + highlight.json + source.txt
Writes are atomic (tmp + rename) at mode 0600.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .highlight import Highlight
from .spans import Span


def session_id_for(transcript_path: Path) -> str:
    h = hashlib.sha256()
    h.update(transcript_path.name.encode())
    try:
        st = transcript_path.stat()
        h.update(str(st.st_size).encode())
        h.update(str(int(st.st_mtime)).encode())
    except OSError:
        pass
    return h.hexdigest()[:12]


class SpanStore:
    def __init__(self, root: Path, session_id: str):
        self.dir = root / session_id
        self.session_id = session_id

    def save(
        self,
        spans: list[Span],
        highlight: Highlight,
        tombstoned: list[tuple[str, str]],
        source: Path,
    ) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self.dir / "spans.json",
            json.dumps(
                [
                    {
                        "id": s.id,
                        "kind": s.kind,
                        "text": s.text,
                        "turn_index": s.turn_index,
                        "token_est": s.token_est,
                        "preview": s.preview,
                    }
                    for s in spans
                ]
            ),
        )
        _atomic_write(
            self.dir / "highlight.json",
            json.dumps(
                {
                    "text": highlight.text,
                    "keywords": sorted(highlight.keywords),
                    "tail_span_ids": highlight.tail_span_ids,
                    "tombstoned": [r for r, _ in tombstoned],
                    "receipts": {r: t for r, t in tombstoned},
                },
                indent=1,
            ),
        )
        _atomic_write(self.dir / "source.txt", str(source.resolve()))

    def get_span(self, span_id: str) -> str | None:
        for sp in self._spans():
            if sp["id"] == span_id:
                return sp["text"]
        return None

    def get_range(self, rng: str) -> str | None:
        """'s4' or 's4-s9' -> concatenated verbatim span text."""
        lo, hi = _parse_range(rng)
        if lo is None:
            return None
        picked = [
            s["text"]
            for s in self._spans()
            if lo <= _num(s["id"]) <= (hi if hi is not None else lo)
        ]
        return "\n\n".join(picked) if picked else None

    def list_tombstones(self) -> list[dict[str, str]]:
        hl = self._highlight()
        spans = {s["id"]: s for s in self._spans()}
        out = []
        for rng in hl.get("tombstoned", []):
            lo, _ = _parse_range(rng)
            first = next(
                (s for s in spans.values() if _num(s["id"]) == lo), None
            )
            out.append(
                {
                    "range": rng,
                    "receipt": hl.get("receipts", {}).get(rng, ""),
                    "preview": first["preview"] if first else "",
                }
            )
        return out

    def get_highlight(self) -> str:
        return str(self._highlight().get("text", ""))

    def _spans(self) -> list[dict]:
        p = self.dir / "spans.json"
        return json.loads(p.read_text()) if p.exists() else []

    def _highlight(self) -> dict:
        p = self.dir / "highlight.json"
        return json.loads(p.read_text()) if p.exists() else {}


def latest_session(root: Path) -> str | None:
    if not root.is_dir():
        return None
    dirs = [d for d in root.iterdir() if (d / "spans.json").exists()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime).name


def _parse_range(rng: str) -> tuple[int | None, int | None]:
    parts = rng.replace("s", "").split("-")
    try:
        lo = int(parts[0])
        hi = int(parts[1]) if len(parts) > 1 else lo
        return lo, hi
    except (ValueError, IndexError):
        return None, None


def _num(span_id: str) -> int:
    try:
        return int(span_id.lstrip("s"))
    except ValueError:
        return -1


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

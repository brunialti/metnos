"""snapshot.py — regression baseline per le risposte canonical.

Per ogni (query, lang) noto, salva la risposta canonical in
`e2e/snapshots/<sha>.json`. Le test future comparano la risposta
attuale contro il baseline:
  - identical → ok
  - lint clean ma diverso → warn + update se METNOS_E2E_UPDATE_SNAPSHOTS=1
  - lint fail → fail
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "snapshots"


def _key(query: str, lang: str) -> str:
    h = hashlib.sha256()
    h.update(query.encode("utf-8"))
    h.update(b"\x00")
    h.update(lang.encode("utf-8"))
    return h.hexdigest()


def _path(key: str) -> Path:
    return _SNAPSHOT_DIR / f"{key[:2]}" / f"{key}.json"


@dataclass
class SnapshotVerdict:
    matches_baseline: bool
    baseline_existed: bool
    written: bool = False
    diff: Optional[str] = None


def compare_or_write(query: str, lang: str, answer: str,
                      *, force_update: Optional[bool] = None) -> SnapshotVerdict:
    """Confronta `answer` con baseline salvato. Se baseline assente o
    `METNOS_E2E_UPDATE_SNAPSHOTS=1`, scrive il nuovo baseline."""
    key = _key(query, lang)
    p = _path(key)
    if force_update is None:
        force_update = os.environ.get("METNOS_E2E_UPDATE_SNAPSHOTS", "0") == "1"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"query": query, "lang": lang, "answer": answer},
                                 ensure_ascii=False, indent=2))
        return SnapshotVerdict(matches_baseline=True, baseline_existed=False,
                                written=True)
    try:
        baseline = json.loads(p.read_text())
        b_ans = baseline.get("answer", "")
    except (json.JSONDecodeError, OSError):
        b_ans = ""
    if b_ans == answer:
        return SnapshotVerdict(matches_baseline=True, baseline_existed=True)
    if force_update:
        p.write_text(json.dumps({"query": query, "lang": lang, "answer": answer},
                                 ensure_ascii=False, indent=2))
        return SnapshotVerdict(matches_baseline=False, baseline_existed=True,
                                written=True, diff="updated")
    # Compute mini-diff
    diff = f"BASELINE: {b_ans[:200]}\nACTUAL  : {answer[:200]}"
    return SnapshotVerdict(matches_baseline=False, baseline_existed=True,
                            diff=diff)

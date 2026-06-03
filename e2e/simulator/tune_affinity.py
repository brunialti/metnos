"""tune_affinity.py — Grid search hyperparams affinity-derived synonyms.

Params:
- affinity_weight (1-5)
- description_weight (0-2)
- dominance_ratio (1.0-3.0) — top/second to declare unambiguous
- include_description (bool)
- max_desc_chars (200-500)
"""
from __future__ import annotations

import sys
import json
import re
import tomllib
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, str(Path(__file__).parent))
from compare_synonyms import TEST_PAIRS, _key_token

EXEC_DIR = Path("/opt/metnos/executors")

_STOPWORDS = {
    "di","del","della","il","la","lo","i","le","un","una",
    "in","su","per","con","a","da","tra","fra","che","e",
    "the","an","to","of","for","with","by","on","and","or",
}


def _tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-zàèéìòùA-ZÀÈÉÌÒÙ]+(?:-[a-zàèéìòù]+)?",
                       text.lower())
    return [t for t in toks if t not in _STOPWORDS and len(t) > 2]


def build(affinity_w: int, desc_w: int, dom_ratio: float,
           include_desc: bool, max_desc: int) -> dict:
    tok_to_obj = defaultdict(Counter)
    for d in sorted(EXEC_DIR.iterdir()):
        mp = d / "manifest.toml"
        if not mp.exists(): continue
        try: m = tomllib.loads(mp.read_text())
        except Exception: continue
        name = m.get("name", d.name)
        parts = name.split("_")
        if len(parts) < 2: continue
        obj = parts[1]
        for raw in m.get("affinity", []):
            for tok in _tokenize(str(raw)):
                tok_to_obj[tok][obj] += affinity_w
        if include_desc:
            desc_block = m.get("description", {})
            if isinstance(desc_block, dict):
                for lang in ("it", "en"):
                    for tok in _tokenize(str(desc_block.get(lang, ""))[:max_desc]):
                        tok_to_obj[tok][obj] += desc_w
    out = {}
    for tok, cnt in tok_to_obj.items():
        top2 = cnt.most_common(2)
        if not top2: continue
        if len(top2) == 1 or top2[0][1] >= dom_ratio * top2[1][1]:
            out[tok] = top2[0][0]
    return out


def score_resolver(obj_map: dict) -> int:
    matches = 0
    for q, exp in TEST_PAIRS:
        # Best substring match
        s = q.lower()
        candidates = [(len(k), k, v) for k, v in obj_map.items()
                       if re.search(rf"\b{re.escape(k)}\b", s)]
        if candidates:
            candidates.sort(reverse=True)
            if candidates[0][2] == exp:
                matches += 1
    return matches


def main():
    best = (0, None)
    print(f"{'aff_w':>6} {'desc_w':>6} {'dom':>5} {'inc_d':>5} {'maxd':>5} {'n_keys':>7} {'matches':>8}")
    for aff_w in (2, 3, 5):
        for desc_w in (0, 1, 2):
            for dom in (1.5, 2.0, 3.0):
                for inc in (True, False):
                    for max_d in (200, 400):
                        if not inc and desc_w > 0: continue
                        d = build(aff_w, desc_w, dom, inc, max_d)
                        n = score_resolver(d)
                        nk = len(d)
                        print(f"{aff_w:>6} {desc_w:>6} {dom:>5} {str(inc):>5} {max_d:>5} {nk:>7} {n:>8}")
                        if n > best[0]:
                            best = (n, (aff_w, desc_w, dom, inc, max_d, nk))
    print(f"\nBEST: matches={best[0]}/{len(TEST_PAIRS)} = {100*best[0]/len(TEST_PAIRS):.0f}%")
    print(f"  params: {best[1]}")


if __name__ == "__main__":
    main()

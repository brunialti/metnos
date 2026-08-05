"""vocab_synonyms_derived.py — Derive OBJECT/VERB synonyms WITHOUT hardcoding.

Universal §7.3 strategy:
1. Parse all manifest.toml `affinity` fields → tokens linked to executor's
   verb_object → token = synonym of canonical verb/object
2. Fallback: BGE-M3 embedding cosine for unseen tokens
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from collections import defaultdict


EXEC_DIR = Path("/opt/metnos/executors")
CACHE_FILE = Path(__file__).parent / "synonyms_derived.json"


import re as _re

# Stopword universal (frequenti italiano + inglese)
_STOPWORDS = {
    "di", "del", "della", "il", "la", "lo", "i", "le", "un", "una",
    "in", "su", "per", "con", "a", "da", "tra", "fra", "che", "e",
    "the", "a", "an", "to", "of", "for", "with", "by", "in", "on",
    "and", "or", "or-prio", "che-",
}


def _tokenize(text: str) -> list[str]:
    """Token IT/EN words only, lowercase, no stopword."""
    toks = _re.findall(r"[a-zàèéìòùA-ZÀÈÉÌÒÙ]+(?:-[a-zàèéìòù]+)?", text.lower())
    return [t for t in toks if t not in _STOPWORDS and len(t) > 2]


def derive_from_affinity() -> dict:
    """Parse manifest.toml affinity + description → {token: object/verb}.

    Universal §7.3: deriva da catalog metadata (affinity + description).
    Improvements vs v1:
    1. Token-level match (no greedy substring)
    2. Description tokens included (extra signal)
    3. Weighted: token frequency across executors → less = more discriminative
    """
    # {token: Counter({verb: count})}
    from collections import Counter
    tok_to_verb = defaultdict(Counter)
    tok_to_obj = defaultdict(Counter)

    for d in sorted(EXEC_DIR.iterdir()):
        mp = d / "manifest.toml"
        if not mp.exists():
            continue
        try:
            m = tomllib.loads(mp.read_text())
        except Exception:
            continue
        name = m.get("name", d.name)
        parts = name.split("_")
        if len(parts) < 2:
            continue
        verb, obj = parts[0], parts[1]

        # Tokens from affinity (high weight)
        for raw in m.get("affinity", []):
            for tok in _tokenize(str(raw)):
                tok_to_verb[tok][verb] += 3
                tok_to_obj[tok][obj] += 3

        # Tokens from description IT/EN (lower weight)
        desc_block = m.get("description", {})
        if isinstance(desc_block, dict):
            for lang in ("it", "en"):
                desc = desc_block.get(lang, "")
                # Only first 200 chars to avoid noise
                for tok in _tokenize(str(desc)[:300]):
                    tok_to_verb[tok][verb] += 1
                    tok_to_obj[tok][obj] += 1

    # Resolve: token → canonical if dominant (>= 2× second best)
    out_verbs = {}
    out_objects = {}
    for tok, cnt in tok_to_verb.items():
        top2 = cnt.most_common(2)
        if not top2: continue
        if len(top2) == 1 or top2[0][1] >= 2 * top2[1][1]:
            out_verbs[tok] = top2[0][0]
    for tok, cnt in tok_to_obj.items():
        top2 = cnt.most_common(2)
        if not top2: continue
        if len(top2) == 1 or top2[0][1] >= 2 * top2[1][1]:
            out_objects[tok] = top2[0][0]
    return {"verbs": out_verbs, "objects": out_objects}


def build_and_cache() -> dict:
    d = derive_from_affinity()
    CACHE_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    return d


def load_cached() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return build_and_cache()


if __name__ == "__main__":
    d = build_and_cache()
    print(f"Token → object synonyms: {len(d['objects'])}")
    print(f"Token → verb synonyms: {len(d['verbs'])}")
    print()
    # Sample
    print("Sample object synonyms (token → canonical):")
    for tok, obj in sorted(d['objects'].items())[:25]:
        print(f"  {tok:25s} → {obj}")
    print()
    print("Sample verb synonyms:")
    for tok, verb in sorted(d['verbs'].items())[:15]:
        print(f"  {tok:25s} → {verb}")

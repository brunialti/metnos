#!/usr/bin/env python3
"""Build the FROZEN, scrubbed prefilter-bench corpus snapshot (PRIVATE builder).

Reads the local turn logs, keeps only ORGANIC queries (real channel
conversations), drops synthetic traffic (smoke/e2e/bench/livetest) by
``conversation_id`` marker, deduplicates, scrubs PII / third-party proper
names (§7.5), and writes a public-shippable snapshot:

    data/prefilter_corpus_snapshot.jsonl   # {"query","first_tool"} per line

This builder stays PRIVATE (reads ~/.local logs). The OUTPUT snapshot is what
ships in the public repo so anyone can reproduce the bench without our logs.

Determinism: pure stdlib, stable ordering, no LLM.

Usage:
    python3 scripts/build_prefilter_corpus.py            # write snapshot + review
    python3 scripts/build_prefilter_corpus.py --review   # print PII-review only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "runtime"))
import config as _C  # noqa: E402  §7.11

TURNS_DIR = _C.PATH_TURNS
OUT = _ROOT / "data" / "prefilter_corpus_snapshot.jsonl"

# conversation_id prefixes that mark synthetic / test / bench / smoke traffic.
SYN_PREFIX = ("bench", "benchb", "livetest", "e2e", "c_debug", "c_test", "smoke")
EXCLUDE_TOOLS = {
    "final_answer", "request_disambiguation_from_user", "get_inputs",
    "scratchpad_read", "scratchpad_write", "undo_last_turn",
    "request_new_executor",
}

# --- PII / third-party scrub (deterministic) -------------------------------
# Blocklist di nomi propri personali/terzi (§7.5). I nomi REALI vivono in un
# file LOCALE gitignorato (~/.config/metnos/prefilter_scrub_names.json; override
# env METNOS_SCRUB_NAMES_FILE) — MAI committato: il codice pubblico non porta
# alcun nome reale. Checkout pubblico senza file = NAME_SUBS vuoto (il pubblico
# non costruisce il corpus, quindi ininfluente). Formato: lista di
# [pattern_regex, sostituzione].
def _load_name_subs():
    import json as _json, os as _os
    path = _os.environ.get(
        "METNOS_SCRUB_NAMES_FILE",
        _os.path.expanduser("~/.config/metnos/prefilter_scrub_names.json"))
    try:
        with open(path, encoding="utf-8") as _f:
            return [(re.compile(p, re.I), r) for p, r in _json.load(_f)]
    except (OSError, ValueError):
        return []


NAME_SUBS = _load_name_subs()
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
HOMEPATH_RE = re.compile(r"/home/[^/\s]+")

# Queries polluted by leaked response / timestamps / tool-call fragments — these
# are logging artifacts, not real user intent. Reject them outright.
REJECT_RE = re.compile(
    r"📊|Carico:|uptime|RAM:|hai ricevuto|command_proposed=|min_fa|"
    r"\bfind_\w+ con\b|\d{2}:\d{2}:\d{2}|chiama il tool", re.I)


def scrub(q: str) -> str:
    q = EMAIL_RE.sub("user@example.com", q)
    q = HOMEPATH_RE.sub("/home/user", q)
    for rx, repl in NAME_SUBS:
        q = rx.sub(repl, q)
    # collapse double spaces introduced by substitutions
    return re.sub(r"\s{2,}", " ", q).strip()


def is_synthetic(cid) -> bool:
    c = (cid or "").lower()
    if not c:
        return True  # empty conversation_id == smoke/synthetic battery
    return c.startswith(SYN_PREFIX)


def load_organic() -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    # Stable ordering: filename asc, then line order (NOT newest-first — frozen).
    for jl in sorted(TURNS_DIR.glob("*.jsonl")):
        for ln in jl.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                t = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if is_synthetic(t.get("conversation_id")):
                continue
            q = (t.get("user_query") or "").strip()
            if len(q) < 6 or len(q) > 240:
                continue
            if REJECT_RE.search(q):
                continue
            steps = t.get("steps") or []
            if not steps:
                continue
            first = (steps[0].get("chosen_tool") or "").strip()
            if not first or first in EXCLUDE_TOOLS or first.startswith("@"):
                continue
            fr = steps[0].get("result")
            if isinstance(fr, dict) and fr.get("ok") is False:
                continue
            q_scrub = scrub(q)
            key = q_scrub.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"query": q_scrub, "first_tool": first})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true",
                    help="print residual-PII review and exit (no write)")
    args = ap.parse_args()

    corpus = load_organic()

    # PII review: flag any residual capitalised mid-sentence token (possible name)
    flagged = []
    name_like = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]{2,}\b")
    SAFE = {"Python", "Anthropic", "Claude", "Metnos", "AMD", "ROCm", "CPU",
            "GPU", "Trova", "Cerca", "Dimmi", "Mostrami", "Scaricami", "Conta",
            "Elenca", "Riassumi", "Fammi", "Quante", "Quanta", "Immagini",
            "Oggi", "Stato", "Soggiorni", "Case", "Mare", "Padova", "Documenti",
            "Scarica", "Aggiungi", "Crea", "Genera", "Calcola", "Sposta"}
    for c in corpus:
        for m in name_like.findall(c["query"]):
            if m not in SAFE:
                flagged.append((m, c["query"]))
                break

    print(f"organic unique queries: {len(corpus)}")
    print(f"residual name-like flagged: {len(flagged)}")
    for tok, q in flagged[:40]:
        print(f"  ⚑ {tok:14s} | {q[:90]}")

    if args.review:
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for c in corpus:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(corpus)} → {OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()

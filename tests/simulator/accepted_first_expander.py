#!/usr/bin/env python3
"""accepted_first_expander — espande `accepted_first` di test_set_FROZEN.json
con alternative semanticamente equivalenti.

Regole di expansion §7.3 universal, derivate da bench 446q + judgement (28/5):

1. **find_X ≡ get_X** quando query è retrieval di entità specifica
   (es. "ultimi 5 file" → find_files OR get_files entrambi validi)

2. **find_urls + read_urls_html ≡ direct read_urls_html** per query
   tipo "stars/issues/contributors GitHub repo X" (URL già implicito)

3. **find_messages ≡ list_messages** per "ultime N mail"
   (list cap=N vs find by recency)

4. **find_events ≡ read_events** per "appuntamenti di domani"
   (find by date vs read by time window)

5. **find_urls ≡ find_places** per query topografiche
   ("Bar Centrale Milano" — entrambe semantiche valide)

6. **read_files_csv ≡ read_files** quando ext NOT in {.csv} (read_files generico fallback)

7. **find_files ≡ find_images_indices** quando path NOT contiene dir indice
   (Immagini path → find_images_indices preferred, altre dir → find_files)

8. **read_urls_html ≡ get_urls** quando query chiede contenuto specifico
   (get_urls = fetch raw, read_urls_html = parse + extract)

9. **find_X ≡ list_X** quando query è enum "elenca/mostra tutti X in Y"
   (find by filter vs list by container)

Input: /opt/metnos/tests/simulator/test_set_FROZEN.json
Output: /opt/metnos/tests/simulator/test_set_FROZEN_v2.json
Diff: stampa modifiche per review umana.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TEST_SET = Path("/opt/metnos/tests/simulator/test_set_FROZEN.json")
OUTPUT = Path("/opt/metnos/tests/simulator/test_set_FROZEN_v2.json")


def _has_path_indice_immagini(query: str) -> bool:
    """True se query contiene path che indica dir-indice foto."""
    return bool(re.search(r"(immagini|images|photos|foto)/", query.lower())) or \
           "immagini" in query.lower().split()


def _has_ext(query: str, ext: str) -> bool:
    return f".{ext}" in query.lower()


def _wants_topographic(query: str) -> bool:
    """Query tipo 'Bar Roma' / 'Pizzeria Milano' / nome+città."""
    return bool(re.search(r"\b(bar|ristorante|pizzeria|hotel|pub|caffè|caffe|farmacia|stazione)\b",
                          query.lower())) or \
           bool(re.search(r"\b\w+\s+(roma|milano|napoli|torino|firenze|bologna|venezia|palermo)\b",
                          query.lower()))


def _query_mentions_github(query: str) -> bool:
    return bool(re.search(r"\bgit?hub\b", query.lower())) or "repo " in query.lower()


def _query_is_enum(query: str) -> bool:
    """Query enumerativa 'elenca/mostra tutti X in Y'."""
    return bool(re.search(r"^(elenca|elencami|lista|mostra|mostrami)\s+(tutti|tutte|i|le|gli)\b",
                          query.lower()))


def _query_is_recent_n(query: str) -> bool:
    """Query tipo 'ultimi N file/mail/...'"""
    return bool(re.search(r"(ultim|primi|recenti|ult).*\d+|\d+.*(mail|file|messag)",
                          query.lower()))


def _query_wants_url_content(query: str) -> bool:
    """Query 'vai su URL e dimmi...'"""
    return bool(re.search(r"(vai su|fetch|scarica|leggi)\s+https?://", query.lower())) or \
           bool(re.search(r"(stars|issues|contributors|releases|forks|tags|builds?)\b.*(repo|github)", query.lower()))


def expand_accepted(query: str, current: list[str], expected_path: list[str]) -> list[str]:
    """Espande accepted_first con alternative legitime basate su pattern query."""
    out = set(current)
    first_exp = expected_path[0] if expected_path else ""
    q = query.lower()

    # Rule 1: find_X ≡ get_X per retrieval entità specifica
    if first_exp.startswith("find_files") and _query_is_recent_n(q):
        out.add("get_files")
    elif first_exp.startswith("get_files") and _query_is_recent_n(q):
        out.add("find_files")

    # Rule 2: read_urls_html ≡ find_urls per github query (entrambe ragionevoli)
    if _query_mentions_github(q):
        out.add("find_urls")
        out.add("read_urls_html")

    # Rule 3: find_messages ≡ list_messages ≡ read_messages per "ultime N mail"
    if _query_is_recent_n(q) and ("mail" in q or "messag" in q):
        out.update({"find_messages", "list_messages", "read_messages"})

    # Rule 4: find_events ≡ read_events per "appuntamenti X"
    if "appuntamen" in q or "event" in q:
        if "find_events" in current or "read_events" in current:
            out.update({"find_events", "read_events"})

    # Rule 5: find_urls ≡ find_places per query topografiche
    if _wants_topographic(q):
        out.update({"find_urls", "find_places"})

    # Rule 6: read_files_csv → read_files OK se ext non csv
    if first_exp == "read_files_csv" and not _has_ext(q, "csv"):
        out.add("read_files")

    # Rule 7: find_files ≡ find_images_indices solo se path Immagini
    if first_exp == "find_images_indices" and not _has_path_indice_immagini(q):
        out.add("find_files")
    elif first_exp == "find_files" and _has_path_indice_immagini(q):
        out.add("find_images_indices")

    # Rule 8: read_urls_html ≡ get_urls quando query chiede contenuto via URL diretto
    if _query_wants_url_content(q):
        out.update({"read_urls_html", "get_urls"})

    # Rule 9: list_X ≡ find_X per enum query
    if _query_is_enum(q):
        for accepted_tool in current:
            if accepted_tool.startswith("find_"):
                out.add(accepted_tool.replace("find_", "list_", 1))
            elif accepted_tool.startswith("list_"):
                out.add(accepted_tool.replace("list_", "find_", 1))

    return sorted(out)


def main():
    ts = json.loads(TEST_SET.read_text())
    changes = []
    new_ts = []
    for q in ts:
        old = list(q.get("accepted_first") or [])
        expanded = expand_accepted(q["query"], old, q.get("expected_path") or [])
        if set(expanded) != set(old):
            changes.append({
                "query": q["query"][:100],
                "old": old,
                "new": expanded,
                "added": sorted(set(expanded) - set(old)),
            })
        nq = dict(q)
        nq["accepted_first"] = expanded
        new_ts.append(nq)

    OUTPUT.write_text(json.dumps(new_ts, indent=2, ensure_ascii=False))
    print(f"Total queries: {len(ts)}")
    print(f"Queries modified: {len(changes)} ({100*len(changes)/len(ts):.1f}%)")
    print(f"Output: {OUTPUT}")
    print()
    print("=== Sample changes (first 20) ===")
    for ch in changes[:20]:
        print(f"  Q: {ch['query'][:75]}")
        print(f"    added: {ch['added']}")


if __name__ == "__main__":
    main()

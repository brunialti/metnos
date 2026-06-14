#!/usr/bin/env python3
"""find_issues — dedup semantico: trova issue simili gia' risolte nel db locale.

Mattone del flusso di maintenance repo (executor, non core): data la descrizione
di una issue, embedda con BGE-M3 (1024d) e cerca nello store `github_issue_qa`
le issue passate piu' simili con la loro `accepted_reply` (cosine search). E' il
passo "memoria/dedup" del flusso: se un match e' forte la risposta nota puo'
essere riusata.

Deterministico §7.9 (la singola embed e' irriducibile). Se BGE-M3 non e'
disponibile ritorna entries=[] + `embedder_available=false` (degrade ONESTO
§2.8: non finge "nessun simile").

Contratto:
    args: repo: str, query_text: str, top_n?: int, min_similarity?: float
    returns: {ok, ok_count, entries:[{ref, similarity, accepted_reply,
              user_satisfied, classification}], embedder_available}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))

from messages import get as _msg  # noqa: E402
import github_issue_qa_store as _store  # noqa: E402


def invoke(args):
    repo = (args.get("repo") or "").strip()
    query_text = (args.get("query_text") or "").strip()
    if not repo:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="repo")}
    if not query_text:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="query_text")}
    try:
        top_n = int(args.get("top_n", 5))
    except (ValueError, TypeError):
        top_n = 5
    try:
        min_sim = float(args.get("min_similarity", 0.0))
    except (ValueError, TypeError):
        min_sim = 0.0

    try:
        from jobs.github_dedup import embed_query
        emb = embed_query(query_text)
    except Exception:
        emb = None
    if emb is None:
        # §2.8: niente falso "0 simili" se non abbiamo potuto cercare.
        return {"ok": True, "ok_count": 0, "entries": [],
                "embedder_available": False}

    try:
        matches = _store.find_similar(repo, emb, top_n=max(1, top_n))
    except Exception as ex:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="query_text", reason=type(ex).__name__)}

    entries = [m for m in matches
               if float(m.get("similarity", 0.0)) >= min_sim]
    return {"ok": True, "ok_count": len(entries), "entries": entries,
            "embedder_available": True}


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()

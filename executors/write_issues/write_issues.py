#!/usr/bin/env python3
"""write_issues — persiste i record di trattamento delle issue nel db locale.

Mattone del flusso di maintenance repo (executor, non core): scrive nello store
`github_issue_qa` lo stato di lavorazione di ogni issue (status/bozza/
classificazione). Vettoriale §2.1: una call accetta N record. Upsert PARZIALE
(solo i campi presenti). Deterministico §7.9: niente LLM. L'embedding del testo
(per il dedup semantico di `find_issues`) e' calcolato best-effort se BGE-M3 e'
disponibile, altrimenti il record si salva comunque (degrade onesto §2.8).

Contratto:
    args: entries: list[{repo, number, title?, question_text?, classification?,
                         status?, draft_reply?, accepted_reply?}]
    returns: {ok, ok_count, results:[{repo, number, status, id}], errors:[...]}
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

_STATUSES = {"new", "prepared", "approved", "posted"}


def _embed(text: str):
    """BGE-M3 1024d best-effort (riusa il singleton di github_dedup). None se
    non disponibile o testo vuoto."""
    if not text or not text.strip():
        return None
    try:
        from jobs.github_dedup import embed_query
        return embed_query(text)
    except Exception:
        return None


def invoke(args):
    entries = args.get("entries")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="entries")}

    results, errors = [], []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append({"index": i, "error": _msg("ERR_ARG_NOT_DICT", arg="entry")})
            continue
        repo = (e.get("repo") or "").strip()
        number = e.get("number")
        if not repo or number is None:
            errors.append({"index": i,
                           "error": _msg("ERR_ARG_MISSING_ONE_OF", options="repo, number")})
            continue
        try:
            number = int(number)
        except (ValueError, TypeError):
            errors.append({"index": i,
                           "error": _msg("ERR_ARG_INVALID", arg="number", reason=str(number))})
            continue
        status = e.get("status")
        if status is not None and status not in _STATUSES:
            errors.append({"index": i,
                           "error": _msg("ERR_ARG_INVALID", arg="status", reason=str(status))})
            continue
        # Embedding best-effort dal testo dell'issue (question_text o title).
        emb = _embed(e.get("question_text") or e.get("title") or "")
        try:
            rid = _store.upsert_treatment(
                repo, number,
                title=e.get("title"),
                classification=e.get("classification"),
                status=status,
                draft_reply=e.get("draft_reply"),
                accepted_reply=e.get("accepted_reply"),
                embedding=emb,
            )
            results.append({"repo": repo, "number": number,
                            "status": status or "new", "id": rid})
        except Exception as ex:
            errors.append({"index": i, "repo": repo, "number": number,
                           "error": _msg("ERR_ARG_INVALID", arg="entry", reason=type(ex).__name__)})

    return {
        "ok": len(errors) == 0,
        "ok_count": len(results),
        "fail_count": len(errors),
        "results": results,
        "errors": errors,
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()

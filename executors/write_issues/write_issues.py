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
          + default top-level opzionali `repo`/`status`/`classification`
            applicati alle entry che non li dichiarano (pattern
            send_messages.to_user) — abilita `write_issues(from_step=N,
            status='posted')` su entries pipate da read_issues.
          `issue_number` accettato come alias di `number` (coerenza §2.10
          con l'output di read_issues).
          + `overwrite` (bool, default false): forza la ri-scrittura di un
            record gia' presente anche senza avanzamento di stato.
    returns: {ok, ok_count, created_count, skipped_known,
              results:[{repo, number, status, id, created}],
              skipped:[{repo, number, status, reason}], errors:[...]}

Macchina a stati (delegata allo store): status='approved' senza
accepted_reply promuove la bozza (draft_reply -> accepted_reply);
status='posted' fissa posted_at la prima volta (idempotente).

Notify-once / dedup §2.8 (12/6/2026): un'issue GIA' registrata nello store
(chiave repo+issue_number) viene SCRITTA solo se la entry AVANZA la macchina
a stati (new < prepared < approved < posted) o se `overwrite=true`. Una
ri-registrazione allo stesso stato (es. run schedulato ogni 30m sulla stessa
issue ancora aperta) e' un no-op contato in `skipped_known` — ok_count conta
SOLO gli elementi realmente scritti, cosi' il runtime/scheduler vede «0
nuovi» e non ri-notifica (idempotenza intrinseca, indipendente dal prompt).
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

# Rank della macchina a stati: una scrittura su record esistente procede
# SOLO se avanza lo stato (vedi docstring «Notify-once / dedup §2.8»).
_STATUS_RANK = {"new": 0, "prepared": 1, "approved": 2, "posted": 3}


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

    # Default top-level: applicati alle entry che non li dichiarano (pattern
    # send_messages.to_user). Abilita `write_issues(from_step=N, status=...)`.
    top_repo = (args.get("repo") or "").strip() or None
    top_status = args.get("status")
    top_class = args.get("classification")
    top_overwrite = bool(args.get("overwrite") or False)

    results, errors, skipped = [], [], []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append({"index": i, "error": _msg("ERR_ARG_NOT_DICT", arg="entry")})
            continue
        repo = (e.get("repo") or top_repo or "").strip()
        # `issue_number` = alias di `number` (§2.10: read_issues OUT usa
        # issue_number; le entries pipate devono rientrare senza rinomina).
        number = e.get("number", e.get("issue_number"))
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
        status = e.get("status", top_status)
        if status is not None and status not in _STATUSES:
            errors.append({"index": i,
                           "error": _msg("ERR_ARG_INVALID", arg="status", reason=str(status))})
            continue
        # Dedup / notify-once §2.8: record gia' nello store + nessun
        # avanzamento di stato richiesto → no-op (skipped_known). Lo
        # scheduler che ri-vede la stessa issue aperta ogni 30m ottiene
        # ok_count=0 e non ri-notifica. `overwrite=true` forza.
        overwrite = bool(e.get("overwrite", top_overwrite))
        existing = None
        try:
            recs = _store.list_records(repo=repo, numbers=[number], limit=1)
            existing = recs[0] if recs else None
        except Exception:
            existing = None
        if existing is not None and not overwrite:
            req_rank = _STATUS_RANK.get(status or "new", 0)
            cur_rank = _STATUS_RANK.get(existing.get("status") or "new", 0)
            if req_rank <= cur_rank:
                skipped.append({"repo": repo, "number": number,
                                "status": existing.get("status") or "new",
                                "reason": "already_treated"})
                continue
        # Embedding best-effort dal testo dell'issue (question_text o title).
        question_text = e.get("question_text")
        emb = _embed(question_text or e.get("title") or "")
        try:
            rid = _store.upsert_treatment(
                repo, number,
                title=e.get("title"),
                classification=e.get("classification", top_class),
                status=status,
                draft_reply=e.get("draft_reply"),
                accepted_reply=e.get("accepted_reply"),
                question_text=question_text,
                embedding=emb,
            )
            results.append({"repo": repo, "number": number,
                            "status": status or "new", "id": rid,
                            "created": existing is None})
        except Exception as ex:
            errors.append({"index": i, "repo": repo, "number": number,
                           "error": _msg("ERR_ARG_INVALID", arg="entry", reason=type(ex).__name__)})

    return {
        "ok": len(errors) == 0,
        "ok_count": len(results),
        "created_count": sum(1 for r in results if r.get("created")),
        "skipped_known": len(skipped),
        "fail_count": len(errors),
        "results": results,
        "skipped": skipped,
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

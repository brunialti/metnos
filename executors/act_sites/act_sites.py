#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Azioni browser sicure su sessioni ``sites`` (spec F2 §3.4/§4.2)."""
from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RT = os.environ.get("METNOS_RUNTIME") or str(_ROOT / "runtime")
if _RT not in sys.path:
    sys.path.insert(0, _RT)

from executor_helpers import run_stdio  # noqa: E402
from messages import get as _msg  # noqa: E402
from playwright_sidecar import session_client  # noqa: E402
from playwright_sidecar.action_resolver import (  # noqa: E402
    is_goal_navigation_request)
from sites_observed import REASON_MSG  # noqa: E402
from tabular_records import merge_source_metadata  # noqa: E402
import sites_downloads  # noqa: E402


def _collect_session_ids(args: dict) -> list[str]:
    raw = args.get("session_ids")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, str) and x]
    entries = args.get("entries")
    if isinstance(entries, list):
        return [e["session_id"] for e in entries
                if isinstance(e, dict) and e.get("session_id")]
    one = args.get("session_id")
    return [one] if isinstance(one, str) and one else []


def _attachment(path: str, sensitive: bool) -> dict:
    return {"kind": "image", "path": path, "basename": Path(path).name,
            "mime": mimetypes.guess_type(path)[0] or "image/png",
            "sensitive": sensitive}


# Il limite riguarda la presentazione, non i dati conservati nella sessione
# che read_sites puo' rileggere. I veri cap della raccolta restano separati.
_MAX_TESTO_ARRIVO = 4000
_TRUNCATION_FIELDS = ("truncated", "truncated_what", "used", "available_total",
                      "cap_field", "cap_value")


def _failure_message(results: list[dict]) -> str:
    reason = next((r["reason_code"] for r in results if r.get("reason_code")),
                  "action_failed")
    if reason == "mandate_scope_exceeded":
        message = _msg("MSG_SITES_RC_MANDATE_SCOPE_EXCEEDED")
    elif reason == "navigation_failed":
        message = _msg("MSG_SITES_RC_UNAVAILABLE")
    elif reason == "side_browser_unavailable":
        message = _msg("MSG_SITES_RC_SIDE_BROWSER_UNAVAILABLE")
    elif reason.startswith("download_"):
        message = _msg("MSG_SITES_DOWNLOAD_FAILED")
    elif reason == "selector_ambiguous":
        # Il broker rifiuta di INDOVINARE fra piu' elementi equivalenti, ed
        # e' giusto cosi'. Ma i candidati li ha gia' in mano
        # (`observed_candidates`: nome, ruolo, punteggio): tacerli lascia
        # l'utente davanti a «operazione fallita» senza sapere che la
        # scelta e' sua e quale sia. Qui si nominano, bounded.
        nomi = []
        for riga in results:
            for candidato in (riga.get("observed_candidates") or [])[:5]:
                nome = str(candidato.get("name") or "").strip()
                if nome and nome not in nomi:
                    nomi.append(nome)
        message = (_msg("MSG_SITES_RC_SELECTOR_AMBIGUOUS_LIST",
                             candidates=", ".join(f"«{n}»" for n in nomi[:5]))
                        if nomi else _msg("MSG_SITES_RC_SELECTOR_AMBIGUOUS"))
    elif reason in REASON_MSG:
        message = _msg(REASON_MSG[reason])
    else:
        message = _msg("ERR_OP_FAILED", reason="act_sites")
    if str(message).startswith("<missing:"):
        message = _msg("ERR_OP_FAILED", reason="act_sites")
    return message


def invoke(args: dict) -> dict:
    owner = os.environ.get("METNOS_ACTOR") or "host"
    session_ids = _collect_session_ids(args)
    action = args.get("action")
    if not session_ids:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="session_ids"),
                "error_class": "invalid_args", "results": []}
    if not isinstance(action, str) or not action.strip():
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="action"),
                "error_class": "invalid_args", "results": []}
    goal_mode = args.get("_goal_mode") is True
    # Come si riconosce l'arrivo. Se il planner non lo dichiara, resta il fine
    # stesso a fare da criterio: e' il comportamento di prima, non un blocco.
    done_when = args.get("done_when")
    done_when = done_when.strip() if isinstance(done_when, str) else ""
    # Dove sta il fine: nell'area personale o nella parte pubblica del sito.
    # Dichiararlo evita di doverlo indovinare dal possesso («le MIE ...»), che
    # e' il segnale piu' fragile: una lingua il possesso lo esprime in molti
    # modi e il riduttore del fine puo' cancellarlo.
    ambito = args.get("ambito")
    ambito = ambito.strip().lower() if isinstance(ambito, str) else ""
    value_ref = args.get("value_ref")
    approval_tokens = args.get("approval_tokens") or {}
    if not isinstance(approval_tokens, dict):
        approval_tokens = {}

    continuation_ids = args.get("continuation_ids") or {}
    if not isinstance(continuation_ids, dict):
        continuation_ids = {}
    continuation_ids = {sid: token for sid, token in continuation_ids.items()
                        if isinstance(token, str) and token}
    download_request = None
    if args.get("dst_dir"):
        try:
            destination = sites_downloads.destination_binding(args["dst_dir"], actor=owner)
            cap = sites_downloads.limits(args.get("download_limits"))
            download_request = {"destination": destination, "limits": cap}
        except sites_downloads.DownloadError as exc:
            return {"ok": False, "error_class": str(exc), "results": [], "entries": []}
    elif args.get("download_limits"):
        return {"ok": False, "error_class": "download_destination_invalid", "results": [], "entries": []}
    download_args = ({"dst_dir": args["dst_dir"], "download_limits": download_request["limits"]}
                     if download_request else {})
    captcha_pending = []
    results = []
    pending = []
    attachments = []
    source_metadata = []
    file_entries = []
    for sid in session_ids:
        if (download_request and
                (len(file_entries) >= download_request["limits"]["max_files"]
                 or sum(item["bytes"] for item in file_entries)
                    >= download_request["limits"]["max_total_bytes"])):
            results.append({"session_id":sid,"ok":False,"executed":False,
                            "primitive":"download","reason_code":"download_budget_exceeded"})
            continue
        res = session_client.session_act(
            session_id=sid, owner=owner, action=action, value_ref=value_ref,
            approval_token=approval_tokens.get(sid),
            goal_query=(action if goal_mode else None),
            record_mode=(args.get("_record_mode") is True),
            done_when=done_when or None, scope=ambito or None,
            **({"download_request": download_request} if download_request else {}),
            **({"continuation_id": continuation_ids[sid]} if sid in continuation_ids else {}))
        if isinstance(res.get("continuation_id"), str) and res["continuation_id"]:
            continuation_ids[sid] = res["continuation_id"]
        if res.get("user_control_required"):
            captcha_pending.append(sid)
        session_metadata = merge_source_metadata(res.get("source_metadata"))
        source_metadata = merge_source_metadata(source_metadata, session_metadata)
        if res.get("approval_required"):
            token = res.get("approval_token")
            if token:
                pending.append((sid, token, res))
            shot = res.get("screenshot_path")
            if shot:
                attachments.append(_attachment(shot, bool(res.get("sensitive"))))
            continue
        if res.get("ok") and isinstance(res.get("download"), dict):
            captured = res["download"]
            try:
                if not download_request:
                    raise sites_downloads.DownloadError("download_scope_missing")
                if (len(file_entries) >= download_request["limits"]["max_files"]
                        or sum(item["bytes"] for item in file_entries) + captured["bytes"]
                        > download_request["limits"]["max_total_bytes"]):
                    raise sites_downloads.DownloadError("download_budget_exceeded")
                published = sites_downloads.publish(download_request["destination"], captured,
                    lambda target: session_client.session_download_transfer(
                        session_id=sid, owner=owner, captured=captured, target=target,
                        timeout_s=download_request["limits"]["timeout_s"]), actor=owner)
                ack = session_client.session_download_ack(session_id=sid, owner=owner, captured=captured)
                file_entries.append({**published, "session_id": sid,
                    "source_url": captured.get("url"), "action_id": captured.get("action_id"),
                    "state": "acknowledged" if ack.get("ok") else "published_ack_pending"})
            except sites_downloads.DownloadError as exc:
                res = {**res, "ok": False, "error_class": str(exc),
                       "automatic_resume_blocked": True}
        # ARRIVARE NON E' MOSTRARE. Una navigazione a obiettivo che riesce ha
        # portato la sessione DOVE l'utente voleva guardare: fermarsi a
        # «azione completata» significa rispondere «fatto» a chi aveva chiesto
        # di vedere (§2.8). Il contenuto si prende dalla stessa sessione, con
        # la stessa autorita' e senza screenshot: lo screenshot redatto e la
        # galleria restano mestiere di `read_sites`, che qui non si duplica.
        # Il marcatore `_goal_mode` lo mette il motore solo per certe
        # richieste, ma la navigazione a obiettivo parte anche senza — la
        # decide il testo dell'azione, con lo STESSO predicato che usa il
        # broker. Legare la lettura al marcatore lasciava senza contenuto
        # proprio i turni piu' comuni.
        arrivo = {}
        collection_partial = (res.get("collection_partial") is True
                              and bool(str(res.get("text") or "").strip()))
        # A failed collection carries only evidence saved before the failure.
        # Do not read the current page or certify the search as complete.
        letto = res if collection_partial else None
        if (res.get("ok") and not download_request and not collection_partial and not res.get("no_match")
                and (goal_mode or is_goal_navigation_request(action))):
            try:
                letto = session_client.session_read(
                    session_id=sid, owner=owner, include_screenshot=False,
                    goal=(done_when or action))
            except Exception:
                letto = None         # la lettura e' un di piu': mai un blocco
        if isinstance(letto, dict) and letto.get("user_control_required"):
            captcha_pending.append(sid)
        if isinstance(letto, dict) and (letto.get("ok") or collection_partial):
            session_metadata = merge_source_metadata(
                session_metadata, letto.get("source_metadata"))
            source_metadata = merge_source_metadata(source_metadata, session_metadata)
            # I blocchi che riguardano il fine sono la risposta; il corpo
            # intero e' il ripiego quando il fine non seleziona niente.
            tratto = str(letto.get("goal_span") or "")
            testo = tratto or str(letto.get("text") or "")
            arrivo = {"url": letto.get("url") or res.get("url"),
                      "title": letto.get("title") or "",
                      "text": testo[:_MAX_TESTO_ARRIVO]}
            if len(testo) > _MAX_TESTO_ARRIVO:
                arrivo["presentation_truncated"] = True
                arrivo["preview_used"] = _MAX_TESTO_ARRIVO
                arrivo["preview_available_total"] = len(testo)
        results.append({
            "session_id": sid, "ok": bool(res.get("ok")),
            "executed": bool(res.get("executed")),
            **({"collection_partial": True} if collection_partial else {}),
            **({"no_match": True} if res.get("no_match") else {}),
            "primitive": res.get("primitive"),
            "url": arrivo.get("url") or res.get("url"),
            **({k: v for k, v in arrivo.items() if k != "url"} if arrivo else {}),
            **({k: res[k] for k in _TRUNCATION_FIELDS if k in res}
               if res.get("truncated") else {}),
            **({"source_metadata": session_metadata} if session_metadata else {}),
            "reason_code": (None if res.get("ok") else
                            res.get("reason_code") or res.get("error_class")),
            **({"automatic_resume_blocked":True} if res.get("automatic_resume_blocked") else {}),
            **({"reason_detail": res.get("detail")} if res.get("detail") else {}),
            **({"observed_candidates": res.get("observed_candidates")}
               if res.get("observed_candidates") else {}),
            # Decisions sent to a cloud model: the runtime tells the user.
            **({"cloud_llm_calls": int(res["cloud_llm_calls"])}
               if res.get("cloud_llm_calls") else {}),
        })
        shot = res.get("screenshot_path")
        if shot:
            attachments.append(_attachment(
                shot, bool(res.get("sensitive"))))

    if captcha_pending or (pending and continuation_ids):
        # A lost response can hide an effect even when its result is false.
        # Reinvoking the vector requires a receipt for every session; retain
        # the unfinished sessions without creating a repeating callback.
        uncertain = [sid for sid in session_ids if sid not in continuation_ids]
        if uncertain:
            out = {"ok": False, "error_class": "action_outcome_unknown",
                   "automatic_resume_blocked": True,
                   "uncertain_sessions": uncertain,
                   "pending_sessions": list(dict.fromkeys(
                       [sid for sid, _, _ in pending] + captcha_pending)),
                   "continuation_ids": continuation_ids,
                   "approval_tokens": {**approval_tokens,
                                       **{sid: token for sid, token, _ in pending}},
                   "results": results, "entries": file_entries}
            if source_metadata:
                out["source_metadata"] = source_metadata
            if attachments:
                out["attachments"] = attachments
            return out

    if pending:
        # Un solo gate BATCH per l'intento multi-sessione (§12-bis).
        from approval_request import approval_request
        tokens = {sid: token for sid, token, _ in pending}
        descriptions = "; ".join(
            str(res.get("description") or action) for _, _, res in pending)
        prompt = _msg("MSG_SITES_APPROVAL_PROMPT", action=descriptions)
        gate = approval_request(
            prompt=prompt,
            title=_msg("MSG_SITES_APPROVAL_TITLE"),
            timeout_s=3600,
            on_approve={"tool": "act_sites", "args": {
                "session_ids": session_ids if continuation_ids else list(tokens), "action": action,
                "approval_tokens": {**approval_tokens, **tokens},
                **({"continuation_ids": continuation_ids} if continuation_ids else {}),
                **({"done_when": done_when} if done_when else {}),
                **({"ambito": ambito} if ambito else {}),
                **({"_goal_mode": True} if goal_mode else {}),
                **({"_record_mode": True} if args.get("_record_mode") is True else {}),
                **({"value_ref": value_ref} if value_ref is not None else {}),
                **download_args,
            }},
            on_reject={"tool": "delete_sites", "args": {
                "session_ids": list(tokens),
            }},
        )
        if attachments:
            gate["attachments"] = attachments
        gate["pending_sessions"] = list(tokens)
        if file_entries:
            gate["entries"] = file_entries
        if source_metadata:
            gate["source_metadata"] = source_metadata
        return gate

    if captcha_pending:
        from playwright_sidecar.challenge_handoff import request
        handoff = request(executor="act_sites", sessions=captcha_pending, args={
            "session_ids": session_ids, "action": action,
            "continuation_ids": continuation_ids, "approval_tokens": approval_tokens,
            **({"done_when": done_when} if done_when else {}),
            **({"ambito": ambito} if ambito else {}),
            **({"_goal_mode": True} if goal_mode else {}),
            **({"_record_mode": True} if args.get("_record_mode") is True else {}),
            **({"value_ref": value_ref} if value_ref is not None else {}),
            **download_args,
        })
        handoff.update(results=results, source_metadata=source_metadata,
                       no_frontier=True, entries=file_entries)
        return handoff

    # The step delivers usable evidence from every session. Per-session ok
    # still describes the search; partial explicitly forbids complete success.
    ok = bool(results) and all(r["ok"] or r.get("collection_partial") for r in results)
    out = {"ok": ok, "results": results,
           "metadata": {"executed": sum(1 for r in results if r["executed"]),
                        "total": len(results)}}
    if download_request:
        out["entries"] = file_entries
        # Published files are a subset of the original session scope. Keep
        # unfinished sessions available for manifest-driven continuation.
        out["session_ids"] = session_ids
        if continuation_ids:
            out["continuation_ids"] = continuation_ids
    if source_metadata:
        out["source_metadata"] = source_metadata
    partials = [r for r in results if r.get("collection_partial")]
    if partials:
        out.update(collection_partial=True, partial=True,
                   partial_reason=_msg("MSG_SITES_COLLECTION_PARTIAL",
                                       reason=_failure_message(partials)),
                   cap_expandable=False)
    cloud = sum(r.get("cloud_llm_calls", 0) for r in results)
    if cloud:
        out["cloud_llm_calls"] = cloud
    limited = next((r for r in results if r.get("truncated")), None)
    if limited:
        out.update({k: limited[k] for k in _TRUNCATION_FIELDS if k in limited})
    if attachments:
        out["attachments"] = attachments
    if ok:
        if download_request:
            out["final_message_hint"] = _msg("MSG_SITES_DOCUMENTS_SAVED", n=len(file_entries))
            return out
        arrivato = next((r for r in results if r.get("text")), None)
        if any(r.get("no_match") for r in results) and not arrivato:
            out["final_message_hint"] = _msg("MSG_NO_RESULTS")
            return out
        # Se la navigazione ha portato del contenuto, il contenuto E' la
        # risposta: «azioni completate: 1» sarebbe una ricevuta al posto di
        # cio' che l'utente aveva chiesto di vedere.
        if arrivato:
            out["final_message_hint"] = arrivato["text"]
        else:
            out["final_message_hint"] = _msg(
                "MSG_SITES_ACTIONS_COMPLETED",
                n=out["metadata"]["executed"])
    else:
        failures = [r for r in results if not r["ok"] and not r.get("collection_partial")]
        out["error_class"] = next((r["reason_code"] for r in failures
                                   if r["reason_code"]), "action_failed")
        out["error"] = _failure_message(failures)
    return out


def main():
    run_stdio(invoke, error_extra={"results": []})


if __name__ == "__main__":
    main()

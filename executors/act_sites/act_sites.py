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
for p in (_RT, str(_ROOT / "executors" / "get_approval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from executor_helpers import run_stdio  # noqa: E402
from messages import get as _msg  # noqa: E402
from playwright_sidecar import session_client  # noqa: E402
from playwright_sidecar.action_resolver import (  # noqa: E402
    is_goal_navigation_request)


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


# Quanto testo della pagina d'arrivo entra nella risposta: abbastanza per
# rispondere, non tanto da diventare un allegato mascherato (§2.7: oltre,
# si dichiara il troncamento).
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
    else:
        message = _msg("ERR_OP_FAILED", reason="act_sites")
    if str(message).startswith("<missing:"):
        message = _msg("ERR_OP_FAILED", reason="act_sites")
    return message


def invoke(args: dict) -> dict:
    owner = os.environ.get("METNOS_ACTOR") or "host"
    channel = os.environ.get("METNOS_CHANNEL") or ""
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

    results = []
    pending = []
    attachments = []
    for sid in session_ids:
        res = session_client.session_act(
            session_id=sid, owner=owner, action=action, value_ref=value_ref,
            approval_token=approval_tokens.get(sid),
            goal_query=(action if goal_mode else None),
            done_when=done_when or None, scope=ambito or None)
        if res.get("approval_required"):
            token = res.get("approval_token")
            if token:
                pending.append((sid, token, res))
            shot = res.get("screenshot_path")
            if shot:
                attachments.append(_attachment(shot, bool(res.get("sensitive"))))
            continue
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
        if (res.get("ok") and not collection_partial and not res.get("no_match")
                and (goal_mode or is_goal_navigation_request(action))):
            try:
                letto = session_client.session_read(
                    session_id=sid, owner=owner, include_screenshot=False,
                    goal=(done_when or action))
            except Exception:
                letto = None         # la lettura e' un di piu': mai un blocco
        if isinstance(letto, dict) and (letto.get("ok") or collection_partial):
            # I blocchi che riguardano il fine sono la risposta; il corpo
            # intero e' il ripiego quando il fine non seleziona niente.
            tratto = str(letto.get("goal_span") or "")
            testo = tratto or str(letto.get("text") or "")
            arrivo = {"url": letto.get("url") or res.get("url"),
                      "title": letto.get("title") or "",
                      "text": testo[:_MAX_TESTO_ARRIVO]}
            if len(testo) > _MAX_TESTO_ARRIVO:
                arrivo["truncated"] = True
                arrivo["truncated_what"] = "text"
                arrivo["used"] = _MAX_TESTO_ARRIVO
                arrivo["available_total"] = len(testo)
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
            "reason_code": (None if res.get("ok") else
                            res.get("reason_code") or res.get("error_class")),
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

    if pending:
        # Un solo gate BATCH per l'intento multi-sessione (§12-bis).
        from get_approval import invoke as approval_invoke
        tokens = {sid: token for sid, token, _ in pending}
        descriptions = "; ".join(
            str(res.get("description") or action) for _, _, res in pending)
        prompt = _msg("MSG_SITES_APPROVAL_PROMPT", action=descriptions)
        gate = approval_invoke({
            "prompt": prompt,
            "title": _msg("MSG_SITES_APPROVAL_TITLE"),
            "actor": owner, "channel": channel,
            "timeout_s": 3600,
            "on_approve": {"tool": "act_sites", "args": {
                "session_ids": list(tokens), "action": action,
                "approval_tokens": tokens,
                **({"done_when": done_when} if done_when else {}),
                **({"ambito": ambito} if ambito else {}),
                **({"_goal_mode": True} if goal_mode else {}),
                **({"value_ref": value_ref} if value_ref is not None else {}),
            }},
            "on_reject": {"tool": "delete_sites", "args": {
                "session_ids": list(tokens),
            }},
        })
        if attachments:
            gate["attachments"] = attachments
        gate["pending_sessions"] = list(tokens)
        return gate

    # The step delivers usable evidence from every session. Per-session ok
    # still describes the search; partial explicitly forbids complete success.
    ok = bool(results) and all(r["ok"] or r.get("collection_partial") for r in results)
    out = {"ok": ok, "results": results,
           "metadata": {"executed": sum(1 for r in results if r["executed"]),
                        "total": len(results)}}
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

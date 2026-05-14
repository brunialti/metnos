"""runtime/backends/messages/gmail_google_workspace.py — Gmail backend.

Wrappa `~/.local/share/metnos/skills/google-workspace/scripts/google_api.py`
sub-commands `gmail send | search | get | modify`. Bypassa SMTP Migadu
quando l'utente ha autenticato Google Workspace (utile per evitare i
limit outbound Migadu).

Funzioni esposte (coerenti con `email_metnos`):
- `send(args)`   → invio multipli messaggi (vettoriale §2.1).
- `read(args)`   → lettura messaggi per query/id.
- `find(args)`   → search via Gmail query (es. 'is:unread').
- `delete(args)` → trash via `modify --add-labels TRASH`.

`auth_required` ritorna `decision="needs_inputs"` con OAuth setup
(coerente con send_messages_google_workspace import skill).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import (  # noqa: E402
    _classify_error, _run_api, _skill_home,
    _needs_inputs_oauth_setup, _get_skill_oauth_config,
)

SKILL_NAME = "google-workspace"
_TRANSIENT_ERROR_CLASSES = ("network", "server_error", "rate_limited")
_MAX_RETRIES = 2


def _api_script() -> Path:
    return _skill_home(SKILL_NAME) / "scripts" / "google_api.py"


def _has_creds() -> bool:
    return (_skill_home(SKILL_NAME) / "google_token.json").is_file()


def _auth_needs_inputs(args_base: dict, *, executor: str) -> dict:
    try:
        payload = _needs_inputs_oauth_setup(
            skill_name=SKILL_NAME, executor=executor,
            args_base=args_base,
            **_get_skill_oauth_config(__file__),
        )
    except Exception as ex:
        return {"ok": False, "error_class": "auth_required",
                "error": f"OAuth setup payload fallito: {ex}",
                "results": [], "used": 0}
    return {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": payload,
        "results": [], "used": 0,
        "error_class": "auth_required",
        "final_message_hint": payload.get("title", ""),
    }


def _run_gmail(argv: list[str], *, executor: str,
               args_base: dict) -> tuple[dict | list | None, dict | None]:
    """Esegue google_api.py gmail ... con retry deterministico §7.9
    su transient errors (TLS / 429 / 503)."""
    last_err: dict | None = None
    for attempt in range(_MAX_RETRIES + 1):
        rc, stdout, stderr = _run_api(_api_script(), argv,
                                       skill_name=SKILL_NAME,
                                       timeout_s=60)
        if rc == 0:
            try:
                return json.loads(stdout) if stdout.strip() else {}, None
            except json.JSONDecodeError as ex:
                last_err = {"ok": False,
                            "error": f"invalid JSON da google_api: {ex}",
                            "error_class": "server_error"}
                continue
        ec = _classify_error(rc, stderr)
        if ec == "auth_required":
            return None, _auth_needs_inputs(args_base, executor=executor)
        last_err = {"ok": False, "error": (stderr or "").strip()
                    or f"rc={rc}", "error_class": ec}
        is_ssl = bool(stderr and (
            "SSL" in stderr or "ASN1" in stderr or "TLSV" in stderr
        ))
        if ec not in _TRANSIENT_ERROR_CLASSES and not is_ssl:
            return None, last_err
    return None, last_err


# --------------------------------------------------------------------------
# SEND  (mappa a send_messages canonical)
# --------------------------------------------------------------------------

def send(args: dict) -> dict:
    """Invia 1+ messaggi via Gmail API. Schema args coerente con
    `email_metnos.send`: `messages: list[{to|recipient_id, subject,
    body, body_html?, cc?, ...}]`. Best-effort: una send fallita non
    blocca le altre."""
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0,
                "ok_count": 0, "fail_count": 0}

    messages = args.get("messages") or []
    if not isinstance(messages, list):
        return {"ok": False, "error": "messages must be a list",
                "error_class": "invalid_args",
                "results": [], "used": 0,
                "ok_count": 0, "fail_count": 0}

    results, failed = [], []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            failed.append({"index": i, "error": "message must be a dict",
                           "error_class": "invalid_args"})
            continue
        rid = m.get("recipient_id")
        to_field = rid or m.get("to")
        if not to_field:
            failed.append({"index": i,
                            "error": "missing 'to' or 'recipient_id'",
                            "error_class": "invalid_args"})
            continue
        if isinstance(to_field, list):
            to_str = ",".join(str(x) for x in to_field if x)
        else:
            to_str = str(to_field)

        subject = m.get("subject") or "(no subject)"
        body = m.get("body") or m.get("body_html") or ""
        is_html = bool(m.get("body_html")) and not m.get("body")
        argv = ["gmail", "send",
                "--to", to_str,
                "--subject", subject,
                "--body", body]
        if is_html:
            argv.append("--html")
        cc = m.get("cc")
        if cc:
            argv.extend([
                "--cc",
                ",".join(str(x) for x in cc) if isinstance(cc, list) else str(cc),
            ])
        thread_id = m.get("thread_id")
        if thread_id:
            argv.extend(["--thread-id", str(thread_id)])

        data, err = _run_gmail(argv, executor="send_messages",
                                args_base={"messages": [m]})
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"index": i, "to": to_str,
                            "subject": subject,
                            "error": err.get("error", "errore sconosciuto"),
                            "error_class": err.get("error_class")})
            continue
        msg_id = (data or {}).get("id") or (data or {}).get("messageId", "")
        results.append({"index": i, "id": msg_id, "to": to_str,
                         "subject": subject,
                         "thread_id": (data or {}).get("threadId", "")})

    n_done = len(results)
    out = {
        "ok": n_done > 0 or not failed,
        "ok_count": n_done,
        "fail_count": len(failed),
        "results": results,
        "used": n_done,
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or "send failed"
    return out


# --------------------------------------------------------------------------
# READ  (lettura messaggi: search or get-by-id)
# --------------------------------------------------------------------------

def read(args: dict) -> dict:
    """Legge 0+ messaggi Gmail. Args:
      - `query`: Gmail search query (es. 'is:unread from:luca@example.com').
      - `message_id` / `message_ids`: lettura puntuale per id (lista).
      - `max_results`: cap (default 10).
    Output `entries: list[{id, thread_id, subject, from, snippet, body, ...}]`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args", "entries": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("message_ids"), list):
        ids.extend(str(x).strip() for x in args["message_ids"] if x)
    mid = args.get("message_id")
    if isinstance(mid, str) and mid.strip():
        ids.append(mid.strip())

    if not ids:
        query = args.get("query") or "is:unread"
        max_results = int(args.get("max_results") or 10)
        argv = ["gmail", "search", str(query), "--max", str(max_results)]
        data, err = _run_gmail(argv, executor="read_messages",
                                args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "entries": [], "used": 0}
        search_results = data if isinstance(data, list) else []
        ids = [e.get("id") for e in search_results
                if isinstance(e, dict) and e.get("id")]

    entries: list[dict] = []
    for mid in ids:
        argv = ["gmail", "get", mid]
        data, err = _run_gmail(argv, executor="read_messages",
                                args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            entries.append({"id": mid, "error_class": err.get("error_class"),
                             "error": err.get("error")})
            continue
        if isinstance(data, dict):
            data.setdefault("id", mid)
            entries.append(data)

    return {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "available_total": len(entries),
        "messaging_source": "gmail_google_workspace",
    }


# --------------------------------------------------------------------------
# FIND  (alias di read query-only)
# --------------------------------------------------------------------------

def find(args: dict) -> dict:
    """Search-only (alias di `read` quando manca message_id*).
    Coerente con il dispatcher canonical `find_messages.py`."""
    a = dict(args or {})
    a.pop("message_id", None)
    a.pop("message_ids", None)
    return read(a)


# --------------------------------------------------------------------------
# DELETE  (move-to-trash via labels modify)
# --------------------------------------------------------------------------

def delete(args: dict) -> dict:
    """Sposta 1+ messaggi nel Cestino di Gmail (label TRASH).
    Reverse pattern §2.3: ripristinabile (label INBOX restored).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    ids: list[str] = []
    if isinstance(args.get("message_ids"), list):
        ids.extend(str(x).strip() for x in args["message_ids"] if x)
    mid = args.get("message_id")
    if isinstance(mid, str) and mid.strip():
        ids.append(mid.strip())
    entries = args.get("entries") or []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                v = e.get("id") or e.get("uid")
                if isinstance(v, str) and v.strip():
                    ids.append(v.strip())
    if not ids:
        return {"ok": False, "error": "nessun message_id fornito",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}

    results, failed = [], []
    for mid in ids:
        argv = ["gmail", "modify", mid,
                "--add-labels", "TRASH",
                "--remove-labels", "INBOX"]
        _, err = _run_gmail(argv, executor="delete_messages",
                             args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": mid, **err})
            continue
        results.append({"ok": True, "id": mid, "status": "trashed"})

    return {
        "ok": len(results) > 0 or not failed,
        "n_deleted": len(results),
        "results": results,
        "failed": failed,
        "used": len(results),
        "messaging_source": "gmail_google_workspace",
    }

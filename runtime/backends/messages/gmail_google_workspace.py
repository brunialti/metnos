"""runtime/backends/messages/gmail_google_workspace.py — Gmail backend.

Wrappa `~/.local/share/metnos/skills/google-workspace/scripts/google_api.py`
sub-commands `gmail send | search | get | modify | reply | labels`.
Bypassa SMTP Migadu quando l'utente ha autenticato Google Workspace
(utile per evitare i limit outbound Migadu).

Funzioni esposte (coerenti con `email_metnos`):
- `send(args)`   → invio multipli messaggi (vettoriale §2.1).
- `read(args)`   → lettura messaggi per query/id.
- `find(args)`   → search via Gmail query (es. 'is:unread').
- `delete(args)` → trash via `modify --add-labels TRASH`.
- `reply(args)`  → risposta in-thread via `gmail reply MID --body`.
- `labels(args)` → list/add/remove labels su 1+ messaggi.
- `modify(args)` → move-to-folder via labels (es. dst_folder='Junk').

`auth_required` ritorna `decision="needs_inputs"` con OAuth setup
(coerente con send_messages_google_workspace import skill).
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from backends._google_auth_common import (  # noqa: E402
    auth_needs_inputs as _common_auth_needs_inputs,
)
from backends._google_api_runner import run_with_retry  # noqa: E402
import detection_lexicon_seed_residual_am as _residual_lexicon  # noqa: E402
from messages import get as _msg  # noqa: E402
from state_receipts import (  # noqa: E402
    inverse_membership_delta, membership_delta,
)

SKILL_NAME = "google-workspace"


def _auth_needs_inputs(args_base: dict, *, executor: str) -> dict:
    return _common_auth_needs_inputs(
        args_base, executor=executor, result_kind="results")


def _run_gmail(argv: list[str], *, executor: str,
               args_base: dict) -> tuple[dict | list | None, dict | None]:
    """Thin wrapper su `run_with_retry` per CLI `google_api.py gmail ...`."""
    return run_with_retry(
        argv, executor=executor, args_base=args_base,
        auth_handler=lambda ab: _auth_needs_inputs(ab, executor=executor),
    )


# --------------------------------------------------------------------------
# SEND  (mappa a send_messages canonical)
# --------------------------------------------------------------------------

def send(args: dict) -> dict:
    """Invia 1+ messaggi via Gmail API. Schema args coerente con
    `email_metnos.send`: `messages: list[{to|recipient_id, subject,
    body, body_html?, cc?, ...}]`. Best-effort: una send fallita non
    blocca le altre."""
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0,
                "ok_count": 0, "fail_count": 0}

    messages = args.get("messages") or []
    if not isinstance(messages, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="messages", reason="must be a list"),
                "error_class": "invalid_args",
                "results": [], "used": 0,
                "ok_count": 0, "fail_count": 0}

    results, failed = [], []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            failed.append({"index": i, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg=f"messages[{i}]", reason="must be a dict"),
                           "error_class": "invalid_args"})
            continue
        rid = m.get("recipient_id")
        to_field = rid or m.get("to")
        if not to_field:
            failed.append({"index": i,
                            "error_code": "ERR_ARG_MISSING",
                            "error": _msg("ERR_ARG_MISSING", arg="to/recipient_id"),
                            "error_class": "invalid_args"})
            continue
        if isinstance(to_field, list):
            to_str = ",".join(str(x) for x in to_field if x)
        else:
            to_str = str(to_field)

        subject = m.get("subject") or _msg("MSG_NO_SUBJECT")
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
                            "error": err.get("error") or _msg("ERR_OP_FAILED", reason="unknown"),
                            "error_class": err.get("error_class")})
            continue
        msg_id = (data or {}).get("id") or (data or {}).get("messageId", "")
        results.append({"index": i, "id": msg_id, "to": to_str,
                         "subject": subject,
                         "thread_id": (data or {}).get("threadId", "")})

    n_done = len(results)
    out = {
        "ok": len(failed) == 0,
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
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="send failed")
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
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

    # Parallelizza fetch per-id con lo stesso budget runtime usato dagli altri
    # fan-out I/O. Ordine preservato; auth/needs_inputs di un fetch prevale.
    from executor_workers import map_ordered
    entries: list[dict] = [None] * len(ids)
    needs_inputs_resp: dict | None = None

    def _fetch_one(idx_mid: tuple[int, str]) -> tuple[int, dict | None, dict | None]:
        idx, mid = idx_mid
        argv = ["gmail", "get", mid]
        d, e = _run_gmail(argv, executor="read_messages",
                          args_base=dict(args))
        return idx, d, e

    fetched, _skipped = map_ordered(
        _fetch_one, list(enumerate(ids)))
    for _order, (idx, data, err) in fetched:
        mid = ids[idx]
        if err is not None:
            if err.get("decision") == "needs_inputs":
                needs_inputs_resp = err
                continue
            entries[idx] = {"id": mid,
                            "error_class": err.get("error_class"),
                            "error": err.get("error")}
            continue
        if isinstance(data, dict):
            data.setdefault("id", mid)
            entries[idx] = data

    if needs_inputs_resp is not None:
        return needs_inputs_resp

    # Filtra None (race condition impossibile ma type-safe)
    entries = [e for e in entries if e is not None]

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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
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
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="message_id"),
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
        "ok": len(failed) == 0,
        "n_deleted": len(results),
        "results": results,
        "failed": failed,
        "used": len(results),
        "messaging_source": "gmail_google_workspace",
    }


# --------------------------------------------------------------------------
# REPLY  (in-thread reply via gmail_reply)
# --------------------------------------------------------------------------

def reply(args: dict) -> dict:
    """Risponde in-thread a 1+ messaggi. Args:
      - `message_id` / `message_ids`: id del messaggio originale.
      - `body`: testo plain (override per-id se serve diversificare).
      - `from_header`: From custom opzionale ('Name <addr>').
    Output `results: [{ok, id, thread_id, in_reply_to}]`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("message_ids"), list):
        ids.extend(str(x).strip() for x in args["message_ids"] if x)
    mid = args.get("message_id")
    if isinstance(mid, str) and mid.strip():
        ids.append(mid.strip())
    if not ids:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="message_id/message_ids"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    body = args.get("body")
    if not isinstance(body, str) or not body.strip():
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="body"),
                "error_class": "invalid_args",
                "results": [], "used": 0}
    from_header = args.get("from_header") or ""

    results, failed = [], []
    for in_reply_to in ids:
        argv = ["gmail", "reply", in_reply_to, "--body", body]
        if from_header:
            argv.extend(["--from", from_header])
        data, err = _run_gmail(argv, executor="send_messages",
                               args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"in_reply_to": in_reply_to, **err})
            continue
        d = data or {}
        results.append({"ok": True, "in_reply_to": in_reply_to,
                        "id": d.get("id", ""),
                        "thread_id": d.get("threadId", "")})

    out = {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "messaging_source": "gmail_google_workspace",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="reply failed")
    return out


# --------------------------------------------------------------------------
# LABELS  (list / add / remove labels per-message)
# --------------------------------------------------------------------------

def labels(args: dict) -> dict:
    """Gestisce labels di 1+ messaggi. Args:
      - `message_id` / `message_ids`: target (se assente => list all labels).
      - `add`: list[str] label ids/nomi da aggiungere.
      - `remove`: list[str] label ids/nomi da rimuovere.
    Operazioni:
      - Nessun id => `gmail labels` (list all labels account).
      - id + add/remove => `gmail modify` per-id.
    Output: `{ok, entries: [...]}` per list; `{ok, results: [{id, labels_now}]}` per modify.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("message_ids"), list):
        ids.extend(str(x).strip() for x in args["message_ids"] if x)
    mid = args.get("message_id")
    if isinstance(mid, str) and mid.strip():
        ids.append(mid.strip())

    add = args.get("add") or []
    remove = args.get("remove") or []
    if isinstance(add, str):
        add = [add]
    if isinstance(remove, str):
        remove = [remove]
    add = [str(x).strip() for x in add if str(x).strip()]
    remove = [str(x).strip() for x in remove if str(x).strip()]

    # No ids => list all account labels
    if not ids:
        data, err = _run_gmail(["gmail", "labels"], executor="list_labels",
                               args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "entries": [], "used": 0}
        entries = data if isinstance(data, list) else []
        return {
            "ok": True,
            "entries": entries,
            "used": len(entries),
            "available_total": len(entries),
            "messaging_source": "gmail_google_workspace",
        }

    if not add and not remove:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="add/remove"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    results, failed = [], []
    for target_id in ids:
        before_data, before_err = _run_gmail(
            ["gmail", "get", target_id], executor="set_messages",
            args_base=dict(args))
        if before_err is not None:
            if before_err.get("decision") == "needs_inputs":
                return before_err
            failed.append({"id": target_id, **before_err})
            continue
        labels_before = (before_data or {}).get("labels") or []
        argv = ["gmail", "modify", target_id]
        if add:
            argv.extend(["--add-labels", ",".join(add)])
        if remove:
            argv.extend(["--remove-labels", ",".join(remove)])
        data, err = _run_gmail(argv, executor="set_messages",
                               args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": target_id, **err})
            continue
        d = data or {}
        delta = membership_delta(labels_before, d.get("labels") or [])
        results.append({"ok": True, "id": target_id,
                        "labels_before": delta["members_before"],
                        "labels_now": delta["members_after"],
                        "membership_receipt": delta})

    out = {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "messaging_source": "gmail_google_workspace",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="labels failed")
    return out


# --------------------------------------------------------------------------
# MODIFY  (move-to-folder via system labels; Gmail folders = labels)
# --------------------------------------------------------------------------

# Canonical folder identities -> Gmail protocol labels.  Natural-language
# aliases live in the manually reviewed detection lexicon.
_GMAIL_LABEL_BY_CANONICAL = {
    "inbox": "INBOX",
    "trash": "TRASH",
    "spam": "SPAM",
    "sent": "SENT",
    "drafts": "DRAFT",
    "important": "IMPORTANT",
    "starred": "STARRED",
    "archive": None,
}


class _GmailFolderLexiconUnavailable(RuntimeError):
    """No complete reviewed folder grammar is available for this locale."""


def _resolve_dst_folder(dst: str) -> tuple[list[str], list[str], str]:
    """Risolve `dst_folder` → (add_labels, remove_labels, new_folder).
    System folder → label id system + remove conflitti.
    User folder → label name as-is (gmail accepta nomi nel modify).
    Convenzione: ogni move rimuove INBOX se va in TRASH/SPAM/archive
    (coerente con UI Gmail). Sposta a INBOX rimuove TRASH/SPAM.
    """
    key = (dst or "").strip().casefold()
    if not key:
        return [], [], dst or ""
    aliases = _residual_lexicon.ready_complete_mapping(
        _residual_lexicon.GMAIL_FOLDER_ALIAS,
    )
    if not aliases:
        raise _GmailFolderLexiconUnavailable(
            "Gmail folder lexicon is unavailable",
        )
    owners = [
        canonical
        for canonical, forms in aliases.items()
        if key in {
            str(form).strip().casefold()
            for form in forms if isinstance(form, str)
        }
    ]
    if len(owners) > 1:
        raise _GmailFolderLexiconUnavailable(
            "Gmail folder lexicon is ambiguous",
        )
    if owners:
        sys_id = _GMAIL_LABEL_BY_CANONICAL[owners[0]]
        if sys_id is None:
            # Archive = remove INBOX (no add)
            return [], ["INBOX"], dst
        if sys_id == "TRASH":
            return ["TRASH"], ["INBOX"], "Trash"
        if sys_id == "SPAM":
            return ["SPAM"], ["INBOX"], "Spam"
        if sys_id == "INBOX":
            return ["INBOX"], ["TRASH", "SPAM"], "Inbox"
        return [sys_id], [], dst
    # User label arbitrario: gmail modify accetta sia ID che nome
    return [dst], [], dst


def modify(args: dict) -> dict:
    """Sposta 1+ messaggi in una cartella (Gmail: label system o user).
    Args:
      - `message_id` / `message_ids`: target.
      - `dst_folder`: nome cartella ("Trash"/"Junk"/"Archivio"/user-label).
    Output: `results: [{ok, id, moved, new_folder}]`.
    Mapping IT+EN folder → system label id deterministico (§7.9).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

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
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="message_id/message_ids"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    dst = args.get("dst_folder")
    if not isinstance(dst, str) or not dst.strip():
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="dst_folder"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    try:
        add, remove, new_folder = _resolve_dst_folder(dst)
    except _GmailFolderLexiconUnavailable as ex:
        return {
            "ok": False,
            "error_code": "ERR_EXT_SVC_UNAVAILABLE",
            "error": _msg("ERR_EXT_SVC_UNAVAILABLE"),
            "error_class": "dependency_unavailable",
            "detail": str(ex),
            "results": [],
            "used": 0,
            "ok_count": 0,
            "fail_count": 0,
        }
    if not add and not remove:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="dst_folder",
                              reason="cannot resolve to label operations"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    results, failed = [], []
    for target_id in ids:
        argv = ["gmail", "modify", target_id]
        if add:
            argv.extend(["--add-labels", ",".join(add)])
        if remove:
            argv.extend(["--remove-labels", ",".join(remove)])
        _, err = _run_gmail(argv, executor="move_messages",
                            args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": target_id, **err})
            continue
        results.append({"ok": True, "id": target_id,
                        "moved": True, "new_folder": new_folder})

    out = {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "messaging_source": "gmail_google_workspace",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="modify failed")
    if results:
        out["_undo"] = {
            "reverse_pattern": "module.reverse",
            "scope": {"client": "google_workspace"},
        }
    return out


def reverse_labels(results: dict) -> dict:
    """Reverse only the label deltas proved by the forward receipts."""
    rows = results.get("results") if isinstance(results, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "ok_count": 0, "fail_count": 1,
                "results": [], "failed": [{"error": "invalid undo receipt"}]}
    restored, failed = [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            failed.append({"index": index, "error": "invalid undo receipt"})
            continue
        try:
            add, remove = inverse_membership_delta(
                row.get("membership_receipt"))
        except ValueError as exc:
            failed.append({"index": index, "id": row.get("id"),
                           "error": str(exc)})
            continue
        if not add and not remove:
            # The forward call succeeded but changed no label state.
            continue
        argv = ["gmail", "modify", row["id"]]
        if add:
            argv.extend(["--add-labels", ",".join(add)])
        if remove:
            argv.extend(["--remove-labels", ",".join(remove)])
        data, err = _run_gmail(
            argv, executor="set_messages", args_base={"message_id": row["id"]})
        if err is not None:
            failed.append({"index": index, "id": row["id"], **err})
            continue
        restored.append({"id": row["id"],
                         "labels_now": (data or {}).get("labels", [])})
    return {"ok": not failed, "ok_count": len(restored),
            "fail_count": len(failed), "results": restored,
            "failed": failed}

"""Email backend Metnos (IMAP+SMTP locale).

Builtin backend per il `client="metnos"` dei verbi messaging quando
`via_channel="email"`. Riusa primitive `runtime/mail_client.py`
(`open_imap`/`open_smtp`/`parse_envelope`/`list_known_accounts`/
`resolve_account`/`_account_creds`). Niente reti esterne fuori da
IMAP4_SSL/SMTP_SSL standard.

Verbi esposti:
- `send(args)`: SMTP send con allegati, vettoriale.
- `read(args)`: IMAP fetch con window temporale + criteri testuali.
- `find(args)`: alias di read (criteri obbligatori).
- `delete(args)`: IMAP STORE \\Deleted + EXPUNGE per UID.
- `move(args)`: IMAP COPY-then-DELETE per UID.

Contratto common: tutti ritornano dict con `ok: bool` + campi verbo-specifici.
Errori per-item in `failed[]`, mai silenzio (CLAUDE.md §2.8).
"""
from __future__ import annotations

import datetime
import mimetypes
import os
import re
import sys
from email.message import EmailMessage
from pathlib import Path

# Lazy imports (mail_client legge env file al boot del modulo)
_RUNTIME = "/opt/myclaw/runtime"
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


# --- helpers ---------------------------------------------------------------

_MAX_ATTACH_BYTES_PER_MSG = 25 * 1024 * 1024
_MONTHS_IMAP = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _to_list(x):
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return [str(v) for v in x if v]
    return []


def _imap_date(d):
    return f"{d.day:02d}-{_MONTHS_IMAP[d.month - 1]}-{d.year}"


def _resolve_attachments(raw, *, max_total_bytes=_MAX_ATTACH_BYTES_PER_MSG):
    """Normalizza/valida la lista attachments (stringhe o dict).
    Ritorna (list[dict_normalized], errors_list).
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"attachments deve essere una lista, ricevuto {type(raw).__name__}"]
    out, errs, total = [], [], 0
    for i, item in enumerate(raw):
        if isinstance(item, str):
            path, fname, ctype = item, None, None
        elif isinstance(item, dict):
            path = item.get("path")
            fname = item.get("filename")
            ctype = item.get("content_type")
        else:
            errs.append(f"attachments[{i}]: type {type(item).__name__} non supportato (string o dict)")
            continue
        if not path or not isinstance(path, str):
            errs.append(f"attachments[{i}]: 'path' mancante o non stringa")
            continue
        p = Path(os.path.expanduser(path))
        if not p.is_file():
            errs.append(f"attachments[{i}]: file non trovato: {path}")
            continue
        try:
            size = p.stat().st_size
        except OSError as e:
            errs.append(f"attachments[{i}]: stat fallita per {path}: {e}")
            continue
        total += size
        if total > max_total_bytes:
            errs.append(
                f"attachments: dimensione totale supera {max_total_bytes} byte "
                f"(cap anti-abuso SMTP). Ridurre il numero/peso degli allegati."
            )
            return [], errs
        if not ctype:
            ctype, _ = mimetypes.guess_type(p.name)
            if not ctype:
                ctype = "application/octet-stream"
        if "/" not in ctype:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        out.append({"path": str(p), "filename": fname or p.name,
                    "maintype": maintype, "subtype": subtype, "size": size})
    return out, errs


# --- send ------------------------------------------------------------------

def send(args: dict) -> dict:
    """Invia 1+ messaggi via SMTP locale (account: metnos_system|...|dyn).

    Args attesi:
        messages: list[{to|recipient_id, subject, body, body_html?, cc?, bcc?, attachments?}]
        account: str (default 'metnos_system')
        attachments_top: list (shortcut applicato a tutti i messages senza attachments)

    Ritorna {ok, ok_count, fail_count, results[], failed[]}.
    """
    from mail_client import open_smtp, _account_creds

    messages = args.get("messages") or []
    account = args.get("account") or "metnos_system"
    top_attach = args.get("attachments_top")

    if not isinstance(messages, list):
        return {"ok": False, "error": "messages must be a list"}
    if not messages:
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": [], "failed": []}

    # Retry su SMTP transient (SSL handshake timeout / connection reset).
    # Pattern analogo a backends/calendar/google_workspace.py: 1 + 2 tentativi.
    # Determinismo §7.9, bug live 14/5/2026 SMTP _ssl.c:983 timeout.
    import time as _time
    creds = None
    sender = None
    smtp = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            creds = _account_creds(account)
            sender = creds["user"]
            smtp = open_smtp(account)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                _time.sleep(0.5 * (attempt + 1))
    if last_exc is not None or smtp is None:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"SMTP connect failed: {last_exc}"}

    results, failed = [], []
    try:
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                failed.append({"index": i, "error": "message must be a dict"})
                continue
            # `recipient_id` (multi-user resolved) takes precedence; fallback `to`.
            rid = m.get("recipient_id")
            to_list = [rid] if rid else _to_list(m.get("to"))
            cc_list = _to_list(m.get("cc"))
            bcc_list = _to_list(m.get("bcc"))
            subject = m.get("subject") or "(no subject)"
            body = m.get("body")
            body_html = m.get("body_html")
            if not to_list:
                failed.append({"index": i, "error": "missing 'to' (string or list) or 'recipient_id'"})
                continue
            if not body and not body_html:
                failed.append({"index": i, "error": "missing 'body' (text) or 'body_html'"})
                continue
            per_msg_attach = m.get("attachments")
            if per_msg_attach is None and top_attach is not None:
                per_msg_attach = top_attach
            attach_list, attach_errs = _resolve_attachments(per_msg_attach)
            if attach_errs:
                failed.append({"index": i, "to": to_list, "subject": subject,
                               "error": "; ".join(attach_errs)})
                continue
            email_msg = EmailMessage()
            email_msg["From"] = sender
            email_msg["To"] = ", ".join(to_list)
            if cc_list:
                email_msg["Cc"] = ", ".join(cc_list)
            email_msg["Subject"] = subject
            email_msg["Date"] = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
            if body:
                email_msg.set_content(body)
            if body_html:
                email_msg.add_alternative(body_html, subtype="html")
            attach_failed = False
            for a in attach_list:
                try:
                    with open(a["path"], "rb") as fh:
                        data = fh.read()
                    email_msg.add_attachment(data, maintype=a["maintype"],
                                              subtype=a["subtype"],
                                              filename=a["filename"])
                except Exception as e:
                    failed.append({"index": i, "to": to_list, "subject": subject,
                                   "error": f"attachment read failed for {a['path']}: {e}"})
                    attach_failed = True
                    break
            if attach_failed:
                continue
            try:
                rcpts = to_list + cc_list + bcc_list
                smtp.send_message(email_msg, from_addr=sender, to_addrs=rcpts)
                rec = {
                    "channel": "mail",
                    "to": to_list,
                    "subject": subject,
                    "message_id": email_msg.get("Message-ID", ""),
                    "sent_at_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "account": account,
                    "attachments_count": len(attach_list),
                    "attachments_names": [a["filename"] for a in attach_list] if attach_list else [],
                    "ok": True,
                }
                # Propaga campi multi-user se presenti (target/recipient_user_id/name)
                for k in ("recipient_user_id", "recipient_name", "recipient_id", "target"):
                    if k in m:
                        rec[k] = m[k]
                results.append(rec)
            except Exception as e:
                failed.append({"index": i, "to": to_list, "subject": subject, "error": str(e)})
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
    }


# --- read / find -----------------------------------------------------------

def _resolve_window(tw):
    """Ritorna (since_str | None, before_str | None, label)."""
    if not tw:
        return None, None, None
    now = datetime.datetime.now(datetime.timezone.utc).astimezone()
    if isinstance(tw, dict):
        return tw.get("since"), tw.get("before"), f"custom:{tw}"
    s = str(tw).strip().lower()
    if s == "today":
        d = now.date()
        return _imap_date(d), None, "today"
    if s == "yesterday":
        d = now.date() - datetime.timedelta(days=1)
        before = now.date()
        return _imap_date(d), _imap_date(before), "yesterday"
    if s.startswith("last-") and s.endswith("d"):
        try:
            n = int(s[5:-1])
        except ValueError:
            return None, None, f"invalid:{s}"
        d = (now - datetime.timedelta(days=n)).date()
        return _imap_date(d), None, s
    if s.startswith("last-") and s.endswith("h"):
        try:
            n = int(s[5:-1])
        except ValueError:
            return None, None, f"invalid:{s}"
        d = (now - datetime.timedelta(hours=n)).date()
        return _imap_date(d), None, s
    return None, None, f"unknown_preset:{s}"


def read(args: dict) -> dict:
    """Legge messaggi IMAP per window/criteri (multi-account supportato).

    Args: stessi di executor read_messages (account, folder, max_results,
    unseen_only, time_window, since, before, from_contains, subject_contains,
    body_contains, max_total, page_size).
    """
    from mail_client import open_imap, parse_envelope, list_known_accounts, resolve_account

    account_arg = args.get("account") or "metnos_system"
    folder = args.get("folder") or "INBOX"
    max_results = int(args.get("max_results", 20))
    unseen_only = bool(args.get("unseen_only", False))
    time_window = args.get("time_window")
    max_total = int(args.get("max_total", 1000))
    page_size = int(args.get("page_size", 50))
    from_contains = args.get("from_contains")
    subject_contains = args.get("subject_contains")
    body_contains = args.get("body_contains")
    since_explicit = args.get("since")
    before_explicit = args.get("before")

    # Normalize account
    from_all_keyword = False
    if isinstance(account_arg, list):
        accounts = [a for a in account_arg if isinstance(a, str) and a.strip()]
        if not accounts:
            return {"ok": False, "error": "account list must contain at least one non-empty string"}
    elif isinstance(account_arg, str):
        s = account_arg.strip()
        if not s:
            return {"ok": False, "error": "account must be a non-empty string"}
        if s.lower() == "all":
            accounts = list_known_accounts()
            if not accounts:
                return {"ok": False, "error": "no configured accounts found"}
            from_all_keyword = True
        else:
            accounts = [s]
    else:
        return {"ok": False, "error": "account must be a string, list of strings, or 'all'"}

    if not from_all_keyword:
        known = set(list_known_accounts())
        resolved: list[str] = []
        unknown: list[str] = []
        for a in accounts:
            if a in known:
                resolved.append(a)
                continue
            r = resolve_account(a)
            if r:
                resolved.append(r)
            else:
                unknown.append(a)
        if unknown:
            hint = ", ".join(sorted(known)) if known else "(nessuno configurato)"
            return {"ok": False,
                    "error": f"unknown account: {unknown[0]!r}. "
                             f"Account configurati: {hint}"}
        accounts = resolved

    if max_results <= 0 or max_results > 200:
        return {"ok": False, "error": "max_results must be in 1..200"}
    if max_total <= 0 or max_total > 1000:
        return {"ok": False, "error": "max_total must be in 1..1000"}

    since, before, window_label = _resolve_window(time_window)
    if time_window and window_label and window_label.startswith(("invalid:", "unknown_preset:")):
        return {"ok": False, "error": f"invalid time_window: {window_label}"}
    if since_explicit:
        since = since_explicit
    if before_explicit:
        before = before_explicit

    entries, failed = [], []
    available_total = 0
    for account in accounts:
        if len(entries) >= max_total:
            break
        per_account_cap = max_total - len(entries)
        try:
            avail = _read_one_account(
                account, folder, max_results, unseen_only,
                since, before, per_account_cap, page_size,
                entries, failed, time_window,
                from_contains, subject_contains, body_contains,
                open_imap, parse_envelope,
            )
            available_total += avail or 0
        except Exception as e:
            failed.append({"account": account, "error": f"{type(e).__name__}: {e}"})

    out = {
        "ok": True,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
        "accounts": accounts,
    }
    if window_label:
        out["window"] = window_label
    if available_total > len(entries):
        out["truncated"] = True
        out["available_total"] = available_total
        out["used"] = len(entries)
        out["truncated_what"] = "email"
    return out


def find(args: dict) -> dict:
    """Alias di read: i criteri (from/subject/body/...) sono comunque
    accettati da read(). Mantenuto per simmetria col vocabolario §2.2
    (find = pattern/discovery)."""
    return read(args)


def _read_one_account(account, folder, max_results, unseen_only, since, before,
                      per_account_cap, page_size, entries, failed, time_window,
                      from_contains, subject_contains, body_contains,
                      open_imap, parse_envelope):
    try:
        conn = open_imap(account)
    except Exception as e:
        failed.append({"account": account, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                       "error": f"IMAP connect failed: {e}"})
        return 0
    try:
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            failed.append({"account": account,
                           "error": f"folder {folder!r} not accessible"})
            return 0
        criteria = []
        if unseen_only:
            criteria.append("UNSEEN")
        if since:
            criteria.append(f"SINCE {since}")
        if before:
            criteria.append(f"BEFORE {before}")
        textual_args = []
        if from_contains:
            textual_args.append(("FROM", from_contains))
        if subject_contains:
            textual_args.append(("SUBJECT", subject_contains))
        if body_contains:
            textual_args.append(("BODY", body_contains))
        if not criteria and not textual_args:
            criteria = ["ALL"]
        search_args = []
        for c in criteria:
            search_args.extend(c.split())
        for key, val in textual_args:
            search_args.append(key)
            search_args.append(f'"{val}"')
        status, data = conn.uid("SEARCH", *search_args)
        if status != "OK":
            failed.append({"account": account,
                           "error": f"IMAP search failed: {status}"})
            return 0
        ids = (data[0].split() if data and data[0] else [])
        available = len(ids)
        if time_window:
            ids = ids[-per_account_cap:]
            ids.reverse()
            cap_total = min(len(ids), per_account_cap)
        else:
            ids = ids[-min(max_results, per_account_cap):]
            ids.reverse()
            cap_total = min(max_results, per_account_cap)
        idx = 0
        while idx < cap_total and idx < len(ids):
            page = ids[idx:idx + page_size]
            for uid in page:
                status, raw = conn.uid("FETCH", uid, "(RFC822.SIZE RFC822)")
                if status != "OK" or not raw or not raw[0]:
                    failed.append({"account": account,
                                   "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                                   "error": "fetch failed"})
                    continue
                try:
                    if isinstance(raw[0], tuple) and len(raw[0]) >= 2:
                        header_part, body_bytes = raw[0][0], raw[0][1]
                    else:
                        header_part, body_bytes = b"", raw[0]
                    env = parse_envelope(body_bytes)
                except Exception as e:
                    failed.append({"account": account, "uid": str(uid),
                                   "error": f"parse failed: {e}"})
                    continue
                size = None
                m = re.search(rb"RFC822\.SIZE\s+(\d+)", header_part) if header_part else None
                if m:
                    try:
                        size = int(m.group(1))
                    except Exception:
                        size = None
                if size is None and isinstance(body_bytes, (bytes, bytearray)):
                    size = len(body_bytes)
                env.update({"uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                            "account": account, "folder": folder,
                            "size": size if size is not None else 0})
                entries.append(env)
            idx += page_size
        return available
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


# --- delete / move ---------------------------------------------------------

def delete(args: dict) -> dict:
    """Cancella mail per UID (IMAP STORE \\Deleted + EXPUNGE).

    Args: account, folder, uids (list of str). Backup blob non gestito qui
    (lasciato all'executor delete_messages se mai esistera').
    """
    from mail_client import open_imap

    account = args.get("account") or "metnos_system"
    folder = args.get("folder") or "INBOX"
    uids = args.get("uids") or []
    if not isinstance(uids, list) or not uids:
        return {"ok": False, "error": "uids must be a non-empty list"}
    try:
        conn = open_imap(account)
    except Exception as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"IMAP connect failed: {e}"}
    results, failed = [], []
    try:
        status, _ = conn.select(folder)
        if status != "OK":
            return {"ok": False, "error": f"folder {folder!r} not accessible"}
        for uid in uids:
            try:
                u = str(uid)
                st, _ = conn.uid("STORE", u, "+FLAGS", "(\\Deleted)")
                if st != "OK":
                    failed.append({"uid": u, "error": f"STORE failed: {st}"})
                    continue
                results.append({"uid": u, "account": account, "folder": folder, "ok": True})
            except Exception as e:
                failed.append({"uid": str(uid), "error": str(e)})
        try:
            conn.expunge()
        except Exception as e:
            failed.append({"uid": "*", "error": f"EXPUNGE failed: {e}"})
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
    return {"ok": len(failed) == 0,
            "ok_count": len(results), "fail_count": len(failed),
            "results": results, "failed": failed}


def move(args: dict) -> dict:
    """Sposta mail fra folder IMAP (COPY-then-STORE \\Deleted + EXPUNGE).

    Args: account, src_folder, dst_folder, uids (list of str).
    """
    from mail_client import open_imap

    account = args.get("account") or "metnos_system"
    src_folder = args.get("src_folder") or args.get("folder") or "INBOX"
    dst_folder = args.get("dst_folder")
    uids = args.get("uids") or []
    if not dst_folder:
        return {"ok": False, "error": "missing dst_folder"}
    if not isinstance(uids, list) or not uids:
        return {"ok": False, "error": "uids must be a non-empty list"}
    try:
        conn = open_imap(account)
    except Exception as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"IMAP connect failed: {e}"}
    results, failed = [], []
    try:
        status, _ = conn.select(src_folder)
        if status != "OK":
            return {"ok": False, "error": f"src_folder {src_folder!r} not accessible"}
        for uid in uids:
            u = str(uid)
            try:
                # COPY first (so we never DELETE before confirming, §2.9)
                st, _ = conn.uid("COPY", u, dst_folder)
                if st != "OK":
                    failed.append({"uid": u, "error": f"COPY failed: {st}"})
                    continue
                st2, _ = conn.uid("STORE", u, "+FLAGS", "(\\Deleted)")
                if st2 != "OK":
                    failed.append({"uid": u, "error": f"STORE failed after COPY: {st2}"})
                    continue
                results.append({"uid": u, "account": account,
                                "src_folder": src_folder, "dst_folder": dst_folder,
                                "ok": True})
            except Exception as e:
                failed.append({"uid": u, "error": str(e)})
        try:
            conn.expunge()
        except Exception as e:
            failed.append({"uid": "*", "error": f"EXPUNGE failed: {e}"})
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
    return {"ok": len(failed) == 0,
            "ok_count": len(results), "fail_count": len(failed),
            "results": results, "failed": failed}

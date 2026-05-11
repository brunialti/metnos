#!/usr/bin/env python3
"""read_messages — executor di Metnos v1.1.

Legge messaggi IMAP, con due modalita' alternative:

  1. ULTIMI N: `max_results=N` (default 20) ritorna le N piu' recenti.
  2. WINDOW TEMPORALE: `time_window` (preset semantici o {since,before}
     espliciti) ritorna TUTTI i messaggi della finestra, paginazione
     interna (loop IMAP SEARCH + fetch in batch). Decine di mail OK.

Backend: IMAP4_SSL via stdlib `imaplib`. Cred via mail_client.

Tool unico: i criteri testuali (FROM/SUBJECT/BODY) si combinano nello
stesso call con since/before o time_window.

Contratto:
    stdin: JSON {
        account?      : 'metnos_system'|'metnos_roberto'|'mykleos',
        folder?       : 'INBOX',
        max_results?  : int = 20,        # solo modalita' ultimi-N
        unseen_only?  : bool = false,
        time_window?  : 'today'|'yesterday'|'last-Nd'|'last-Nh'|
                        {'since': 'DD-Mon-YYYY', 'before': 'DD-Mon-YYYY'},
        max_total?    : int = 200,        # cap window per evitare blow-up
        page_size?    : int = 50          # batch fetch interno
    }
    stdout: JSON {ok, ok_count, fail_count, entries, failed, window?}
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/myclaw/runtime")
from mail_client import open_imap, parse_envelope, list_known_accounts, resolve_account  # noqa: E402
from messages import get as msg  # noqa: E402

_MONTHS_IMAP = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _imap_date(d):
    return f"{d.day:02d}-{_MONTHS_IMAP[d.month - 1]}-{d.year}"


def _resolve_window(tw):
    """Ritorna (since_str | None, before_str | None, label).
    tw puo' essere preset string ('today', 'yesterday', 'last-Nd',
    'last-Nh') oppure dict {'since': str, 'before': str?}.
    """
    if not tw:
        return None, None, None
    now = datetime.now(timezone.utc).astimezone()
    if isinstance(tw, dict):
        return tw.get("since"), tw.get("before"), f"custom:{tw}"
    s = str(tw).strip().lower()
    if s == "today":
        d = now.date()
        return _imap_date(d), None, "today"
    if s == "yesterday":
        d = now.date() - timedelta(days=1)
        before = now.date()
        return _imap_date(d), _imap_date(before), "yesterday"
    if s.startswith("last-") and s.endswith("d"):
        try:
            n = int(s[5:-1])
        except ValueError:
            return None, None, f"invalid:{s}"
        d = (now - timedelta(days=n)).date()
        return _imap_date(d), None, s
    if s.startswith("last-") and s.endswith("h"):
        # IMAP non ha granularita' oraria: usa giorno arrotondato indietro
        try:
            n = int(s[5:-1])
        except ValueError:
            return None, None, f"invalid:{s}"
        d = (now - timedelta(hours=n)).date()
        return _imap_date(d), None, s
    return None, None, f"unknown_preset:{s}"


def invoke(args):
    account_arg = args.get("account") or "metnos_system"
    folder = args.get("folder") or "INBOX"
    max_results = int(args.get("max_results", 20))
    unseen_only = bool(args.get("unseen_only", False))
    time_window = args.get("time_window")
    max_total = int(args.get("max_total", 1000))
    page_size = int(args.get("page_size", 50))
    # Criteri testuali (server-side IMAP SEARCH). Possono coesistere con
    # time_window o since/before. Tutti AND lato server.
    from_contains = args.get("from_contains")
    subject_contains = args.get("subject_contains")
    body_contains = args.get("body_contains")
    # Range date espliciti (formato IMAP DD-Mon-YYYY): se presenti, hanno
    # priorita' su time_window (window calcolata dal preset).
    since_explicit = args.get("since")
    before_explicit = args.get("before")

    # Normalizza account: string singolo, lista, o "all".
    # `from_all_keyword` distingue gli account auto-generati da 'all' (per cui
    # tolleriamo fail per-account, perche' 1 account broken non deve rompere
    # gli altri) dagli account specificati esplicitamente dall'utente (per cui
    # un nome sconosciuto e' un errore di input e va segnalato fail-fast).
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

    # Validazione fail-fast per input esplicito utente: un nome sconosciuto
    # e' un errore di input (typo, account non configurato), NON un fail di
    # servizio esterno. L'utente deve saperlo subito, non scoprirlo in
    # `failed[]` mentre `ok:true`. Skip se gli account vengono da 'all'.
    if not from_all_keyword:
        # Risolvi via fuzzy matching (Jaccard token-set + SequenceMatcher
        # tiebreak): un nome utente colloquiale ("metnos") viene mappato al
        # canonico ("metnos_system") senza tabelle hardcoded.
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
    # since/before espliciti vincono sul preset
    if since_explicit:
        since = since_explicit
    if before_explicit:
        before = before_explicit
    # has_filter = c'e' almeno un criterio (testuale o temporale): in tal caso
    # il flusso e' "ricerca filtrata" (no max_total cap, ma max_results).
    has_filter = bool(from_contains or subject_contains or body_contains
                      or since or before or unseen_only or time_window)

    # Loop interno multi-account: ogni account contribuisce sue entries.
    # max_total e' globale (somma su tutti gli account).
    entries, failed = [], []
    available_total = 0   # cardinalita' reale (prima del cap), aggregata
    for account in accounts:
        if len(entries) >= max_total:
            break
        per_account_cap = max_total - len(entries)
        try:
            avail = _read_one_account(account, folder, max_results, unseen_only,
                                      since, before, per_account_cap, page_size,
                                      entries, failed, time_window,
                                      from_contains, subject_contains, body_contains)
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
    # Truncation visibility: dichiariamo all'utente quando il cap morde.
    # Convenzione runtime (feedback_truncation_visibility):
    # truncated:true + available_total + used + truncated_what.
    if available_total > len(entries):
        out["truncated"] = True
        out["available_total"] = available_total
        out["used"] = len(entries)
        out["truncated_what"] = "email"
    return out


def _read_one_account(account, folder, max_results, unseen_only, since, before,
                      per_account_cap, page_size, entries, failed, time_window,
                      from_contains=None, subject_contains=None, body_contains=None):
    """Ritorna il numero TOTALE di messaggi che match-ano i criteri prima
    del cap (`available_in_account`). Le entries effettive sono appese alla
    lista `entries` passata. Cosi' il chiamante puo' calcolare il `truncated`
    aggregato confrontando available_total vs len(entries)."""
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
        # Criteri testuali: la stringa puo' contenere spazi, va racchiusa fra
        # virgolette per IMAP SEARCH. Costruiamo una lista di token che poi
        # passiamo a conn.search a-pezzi cosi' imaplib gestisce il quoting.
        textual_args = []
        if from_contains:
            textual_args.append(("FROM", from_contains))
        if subject_contains:
            textual_args.append(("SUBJECT", subject_contains))
        if body_contains:
            textual_args.append(("BODY", body_contains))
        if not criteria and not textual_args:
            criteria = ["ALL"]
        # Costruiamo la lista di argomenti per conn.search: per i criteri
        # senza valore (UNSEEN, ALL) un solo token; per quelli con valore
        # (SINCE/BEFORE) due token; per i testuali due token (chiave + valore
        # quotato). imaplib applica il quoting automaticamente quando il token
        # e' passato come elemento separato.
        search_args = []
        for c in criteria:
            search_args.extend(c.split())
        for key, val in textual_args:
            search_args.append(key)
            search_args.append(f'"{val}"')
        # Usiamo UID SEARCH (non SEARCH) per ritornare UID stabili invece di
        # SEQ NUMBERS. Critico per move_messages downstream: i SEQ cambiano
        # dopo EXPUNGE, gli UID no. Bug 29/4 sera: move_messages eliminava
        # mail sbagliate perche' read_messages produceva seq numbers come
        # "uid".
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
                # UID FETCH (non FETCH) per coerenza con UID SEARCH sopra.
                # Includiamo RFC822.SIZE per esporre size in byte SENZA round-trip
                # extra: e' un campo gratuito (no body fetch addizionale, sta nei
                # metadati IMAP). Cosi' il planner puo' filtrare/ordinare per
                # dimensione direttamente sull'output di read_messages, senza
                # dover sintetizzare un get_messages_metadata.
                status, raw = conn.uid("FETCH", uid, "(RFC822.SIZE RFC822)")
                if status != "OK" or not raw or not raw[0]:
                    failed.append({"account": account,
                                   "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                                   "error": "fetch failed"})
                    continue
                try:
                    # raw[0] e' una tupla (header_with_size, body_bytes) quando
                    # la response include una literal string (RFC822 body).
                    if isinstance(raw[0], tuple) and len(raw[0]) >= 2:
                        header_part, body_bytes = raw[0][0], raw[0][1]
                    else:
                        header_part, body_bytes = b"", raw[0]
                    env = parse_envelope(body_bytes)
                except Exception as e:
                    failed.append({"account": account, "uid": str(uid),
                                   "error": f"parse failed: {e}"})
                    continue
                # Estrai size dai metadati IMAP. Se manca per qualche motivo,
                # usa la lunghezza del body fetched (sempre disponibile).
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


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()

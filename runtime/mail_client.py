"""mail_client.py — IMAP/SMTP client condiviso per gli executor mail.

Carica credenziali da ~/.config/metnos/mail.env (Migadu, default) o
~/.config/account_personal/mail.env (legacy example.com). Variabili attese:
  metnos: METNOS_MAIL_HOST_IMAP, METNOS_MAIL_PORT_IMAP, METNOS_SYSTEM_USER,
          METNOS_SYSTEM_PASS (oppure metnos_secondary_USER/_PASS).
  account_personal: account_personal_MAIL_HOST, account_personal_MAIL_PORT, account_personal_MAIL_USER,
           account_personal_MAIL_PASS.

API:
  open_imap(account="metnos_system") -> imaplib.IMAP4_SSL connesso.
  open_smtp(account="metnos_system") -> smtplib.SMTP_SSL connesso e auth.
  parse_envelope(raw_msg) -> dict con {from, subject, date, ...}.

Sicurezza: cred mai inline, sempre da env file (chmod 600). User-Agent
irrilevante per IMAP/SMTP.
"""
import imaplib
import re
import smtplib
import ssl
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path

import config as _C  # §7.11 — rispetta METNOS_USER_CONFIG
from logging_setup import get_logger
log = get_logger(__name__)


def _read_env(path):
    env = {}
    if not Path(path).exists():
        return env
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _load_from_credentials_store(account: str) -> dict | None:
    """Tenta lettura dell'account dallo store cifrato `runtime.credentials`
    (ADR 0089). Ritorna dict canonical o None se non presente.

    Domain conventions:
      `smtp_<account>` (es. `smtp_metnos_system`, `smtp_metnos_secondary`,
      `smtp_account_personal`).
    Payload schema atteso:
      {imap_host, imap_port, smtp_host, smtp_port, user, password,
       verify_tls?}.
    Determinismo §7.9: lookup tabellare, no LLM, no network.
    """
    try:
        import credentials as _cr
    except ImportError:
        return None
    domain = f"smtp_{account}"
    payload = _cr.load(domain)
    if not isinstance(payload, dict):
        return None
    # Validazione minimale: deve avere almeno user + password.
    if not payload.get("user") or not payload.get("password"):
        return None
    return {
        "imap_host":  payload.get("imap_host", "imap.example.com"),
        "imap_port":  int(payload.get("imap_port", 993)),
        "smtp_host":  payload.get("smtp_host", "smtp.example.com"),
        "smtp_port":  int(payload.get("smtp_port", 465)),
        "user":       payload["user"],
        "password":   payload["password"],
        "verify_tls": bool(payload.get("verify_tls", True)),
    }


def _account_creds(account: str) -> dict:
    # Layer 1 (ADR 0089 + ADR 0131, 14/5/2026): store cifrato Fernet
    # come single source of truth. Se l'account NON e' nel store, fallback
    # ai legacy file env (back-compat durante migrazione).
    from_store = _load_from_credentials_store(account)
    if from_store is not None:
        return from_store

    if account in ("metnos_system", "metnos"):
        env = _read_env(_C.PATH_USER_CONFIG / "mail.env")
        return {
            "imap_host": env.get("METNOS_MAIL_HOST_IMAP", "imap.example.com"),
            "imap_port": int(env.get("METNOS_MAIL_PORT_IMAP", "993")),
            "smtp_host": env.get("METNOS_MAIL_HOST_SMTP", "smtp.example.com"),
            "smtp_port": int(env.get("METNOS_MAIL_PORT_SMTP", "465")),
            "user": env.get("METNOS_SYSTEM_USER", ""),
            "password": env.get("METNOS_SYSTEM_PASS", ""),
            "verify_tls": True,
        }
    if account == "metnos_secondary":
        env = _read_env(_C.PATH_USER_CONFIG / "mail.env")
        return {
            "imap_host": env.get("METNOS_MAIL_HOST_IMAP", "imap.example.com"),
            "imap_port": int(env.get("METNOS_MAIL_PORT_IMAP", "993")),
            "smtp_host": env.get("METNOS_MAIL_HOST_SMTP", "smtp.example.com"),
            "smtp_port": int(env.get("METNOS_MAIL_PORT_SMTP", "465")),
            "user": env.get("metnos_secondary_USER", ""),
            "password": env.get("metnos_secondary_PASS", ""),
            "verify_tls": True,
        }
    if account == "account_personal":
        env = _read_env(Path.home() / ".config/account_personal/mail.env")
        return {
            "imap_host": env.get("account_personal_MAIL_HOST", "imap.example.com"),
            "imap_port": int(env.get("account_personal_MAIL_PORT", "993")),
            "smtp_host": env.get("account_personal_SMTP_HOST", "smtp.example.com"),
            "smtp_port": int(env.get("account_personal_SMTP_PORT", "465")),
            "user": env.get("account_personal_MAIL_USER", ""),
            "password": env.get("account_personal_MAIL_PASS", ""),
            "verify_tls": False,  # example.com cert per *.example.com
        }
    # Fallback dinamico: ~/.config/metnos/mail/<account>.env con schema
    # neutro (HOST_IMAP, PORT_IMAP, HOST_SMTP, PORT_SMTP, USER, PASS,
    # VERIFY_TLS). Permette di aggiungere account senza toccare il codice.
    dyn_path = _C.PATH_USER_CONFIG / "mail" / f"{account}.env"
    if dyn_path.exists():
        env = _read_env(dyn_path)
        if not env.get("USER") or not env.get("PASS"):
            raise ValueError(f"account file {dyn_path} missing USER/PASS")
        return {
            "imap_host": env.get("HOST_IMAP", ""),
            "imap_port": int(env.get("PORT_IMAP", "993")),
            "smtp_host": env.get("HOST_SMTP", ""),
            "smtp_port": int(env.get("PORT_SMTP", "465")),
            "user": env.get("USER", ""),
            "password": env.get("PASS", ""),
            "verify_tls": env.get("VERIFY_TLS", "true").lower() != "false",
        }
    raise ValueError(f"unknown account: {account!r}")


def resolve_account(name: str, *, min_score: float = 0.45) -> str | None:
    """Risolve un nome account fornito dall'utente al canonico configurato.

    Evita mapping hardcoded: usa Jaccard token-set su `_`-split.
    - Match esatto → ritorna il nome.
    - Altrimenti fuzzy: token-set Jaccard >= min_score; tiebreak per
      char-ratio (SequenceMatcher), poi alfabetico.
    - Nessun match sopra soglia → None (caller decide se errore o fallback).

    Esempi col setup attuale (metnos_system, metnos_secondary, account_personal,
    account_work, account_isp):
      "metnos"  → "metnos_system" (J=0.5 vs system, 0.5 vs roberto;
                  tiebreak: SequenceMatcher 0.71 vs 0.66 → system).
      "knowcas" → "account_work" (substring; J=0 → tiebreak SeqMatcher).
      "mtnos"   → None (J=0; SeqMatcher 0.46 < soglia anche dopo).
      "all"     → None (parola riservata, gestita dal caller).
    """
    if not name:
        return None
    if not isinstance(name, str):
        return None
    s = name.strip()
    if not s or s.lower() == "all":
        return None
    known = list_known_accounts()
    if not known:
        return None
    if s in known:
        return s
    qtoks = set(t for t in s.lower().split("_") if t)
    from difflib import SequenceMatcher
    scored: list[tuple[float, float, str]] = []
    for k in known:
        ktoks = set(t for t in k.lower().split("_") if t)
        if not qtoks or not ktoks:
            jac = 0.0
        else:
            inter = len(qtoks & ktoks)
            union = len(qtoks | ktoks)
            jac = inter / union if union else 0.0
        char_ratio = SequenceMatcher(None, s.lower(), k.lower()).ratio()
        scored.append((jac, char_ratio, k))
    # Ordina per (jaccard desc, char_ratio desc, alfabetico)
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    best = scored[0]
    if best[0] >= min_score or best[1] >= 0.7:
        return best[2]
    return None


def list_known_accounts() -> list[str]:
    """Ritorna la lista di account utilizzabili: predefiniti + dinamici (file
    in ~/.config/metnos/mail/<name>.env). Usato per `account="all"` in
    read_messages e simili."""
    accounts: list[str] = []
    # Predefiniti che hanno cred valide
    for nm in ("metnos_system", "metnos_secondary", "account_personal"):
        try:
            c = _account_creds(nm)
            if c.get("user") and c.get("password"):
                accounts.append(nm)
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    # Dinamici da ~/.config/metnos/mail/*.env
    dyn_dir = _C.PATH_USER_CONFIG / "mail"
    if dyn_dir.exists():
        for p in sorted(dyn_dir.iterdir()):
            if p.suffix != ".env":
                continue
            name = p.stem
            if name in accounts:
                continue
            try:
                c = _account_creds(name)
                if c.get("user") and c.get("password"):
                    accounts.append(name)
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
    return accounts


def exact_account_for_address(address: str) -> str | None:
    """Return the unique mailbox whose configured login exactly matches.

    Authentication factors must never be read from a mailbox selected by a
    fuzzy alias.  This strict resolver is therefore the only one suitable for
    automatic email-factor handling.
    """
    if not isinstance(address, str) or "@" not in address:
        return None
    needle = address.strip().casefold()
    if not needle:
        return None
    candidates = []
    for account in list_known_accounts():
        try:
            configured = _account_creds(account).get("user", "")
        except Exception:
            continue
        if isinstance(configured, str) and "@" in configured:
            candidates.append((account, configured.strip().casefold()))
    exact = [account for account, configured in candidates
             if configured == needle]
    return exact[0] if len(exact) == 1 else None


def account_for_address(address: str) -> str | None:
    """Resolve a configured mailbox, preferring an exact identity.

    The conservative alias fallback remains available to ordinary mail UX.
    Security-sensitive factor retrieval uses :func:`exact_account_for_address`
    instead.
    """
    exact = exact_account_for_address(address)
    if exact:
        return exact
    if not isinstance(address, str) or "@" not in address:
        return None
    needle = address.strip().casefold()
    if not needle:
        return None
    candidates = []
    for account in list_known_accounts():
        try:
            configured = _account_creds(account).get("user", "")
        except Exception:
            continue
        if isinstance(configured, str) and "@" in configured:
            candidates.append((account, configured.strip().casefold()))
    local, _, host = needle.partition("@")
    same_host = [(account, configured.split("@", 1)[1])
                 for account, configured in candidates
                 if configured.rsplit("@", 1)[1] == host]
    if len(same_host) == 1:
        return same_host[0][0]
    # Providers commonly rewrite dots/underscores/hyphens in display/login
    # aliases.  Apply this only when it leaves one unambiguous mailbox.
    norm_local = re.sub(r"[._-]", "", local)
    normalized = [account for account, configured in candidates
                  if configured.rsplit("@", 1)[1] == host
                  and re.sub(r"[._-]", "", configured.split("@", 1)[0])
                  == norm_local]
    return normalized[0] if len(normalized) == 1 else None


def open_imap(account: str = "metnos_system", *,
              timeout_s: float | None = None,
              attempts: int = 3) -> imaplib.IMAP4_SSL:
    c = _account_creds(account)
    if not c["user"] or not c["password"]:
        raise RuntimeError(f"missing user/password for account {account!r}")
    if c["verify_tls"]:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl._create_unverified_context()
    # ADR 0130: retry 3× su errori TRANSIENTI di handshake/rete (SSL
    # 'bad record mac'/'decryption failed', reset, timeout — osservati su
    # account_work/example.com). Connect+login idempotenti: ogni tentativo apre
    # una connessione FRESCA. §2.8: se tutti falliscono, l'ultima eccezione
    # propaga onestamente (il caller la mette in failed[]).
    attempts = max(1, min(3, int(attempts)))
    if timeout_s is not None:
        timeout_s = max(0.5, min(60.0, float(timeout_s)))
    last = None
    for attempt in range(attempts):
        try:
            kwargs = {"ssl_context": ctx}
            if timeout_s is not None:
                kwargs["timeout"] = timeout_s
            conn = imaplib.IMAP4_SSL(
                c["imap_host"], c["imap_port"], **kwargs)
            conn.login(c["user"], c["password"])
            return conn
        except (ssl.SSLError, OSError) as e:
            last = e
            log.warning("open_imap %s transient handshake (try %d/%d): %r",
                        account, attempt + 1, attempts, e)
    raise last


def list_mail_folders(account: str) -> list[dict]:
    """List configured IMAP folders and their server-advertised flags."""
    conn = open_imap(account)
    try:
        status, data = conn.list()
        if status != "OK" or not data:
            return []
        folders = []
        pattern = re.compile(
            r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|\S+)\s+'
            r'(?P<name>"[^"]+"|\S+)\s*$')
        for raw in data:
            line = (raw.decode("utf-8", "replace")
                    if isinstance(raw, (bytes, bytearray)) else str(raw))
            match = pattern.match(line)
            if not match:
                continue
            folders.append({
                "name": match.group("name").strip().strip('"'),
                "flags": match.group("flags"),
            })
        return folders
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def open_smtp(account: str = "metnos_system") -> smtplib.SMTP_SSL:
    c = _account_creds(account)
    if not c["user"] or not c["password"]:
        raise RuntimeError(f"missing user/password for account {account!r}")
    if c["verify_tls"]:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl._create_unverified_context()
    conn = smtplib.SMTP_SSL(c["smtp_host"], c["smtp_port"], context=ctx, timeout=20)
    conn.login(c["user"], c["password"])
    return conn


def _decode_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


_NOREPLY_RE = re.compile(
    r"(?i)(?:^|<)(?:no[\s._-]?reply|noreply|do[\s._-]?not[\s._-]?reply"
    r"|mailer[\s._-]?daemon|daemon|postmaster|notifications?"
    r"|automated|robot|news(?:letter)?|info|support|hello|team"
    r"|marketing|promo|alert|alerts)[@_.]"
)
_ESP_X_MAILER_RE = re.compile(
    r"(?i)mailchimp|sendgrid|mailjet|mailgun|hubspot|marketo"
    r"|salesforce|sendinblue|brevo|amazon\s*ses|mandrill"
    r"|constant\s*contact|campaignmonitor|mautic|customer\.io"
)


def _category_hints(msg) -> list[str]:
    """Classifica una mail con segnali header standard RFC.
    Ritorna lista (possibly empty) di hint neutre, non un giudizio:
    'list' (mailing list / newsletter), 'bulk' (mass-sent),
    'auto' (auto-generated), 'noreply' (sender pattern),
    'esp' (email service provider di marketing). Pensato come
    pre-filter cheap prima di una classificazione LLM piu' costosa."""
    hints = set()
    if msg.get("List-Unsubscribe") or msg.get("List-Id") \
            or msg.get("List-Post") or msg.get("List-Help"):
        hints.add("list")
    prec = (msg.get("Precedence") or "").strip().lower()
    if prec in ("bulk", "junk", "list"):
        hints.add("bulk")
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        hints.add("auto")
    if msg.get("X-Auto-Response-Suppress"):
        hints.add("auto")
    sender_raw = msg.get("From", "") or ""
    if _NOREPLY_RE.search(sender_raw):
        hints.add("noreply")
    rp = (msg.get("Return-Path") or "").strip()
    if rp in ("<>", ""):
        # Null return-path -> bounce/auto-generated
        if rp == "<>":
            hints.add("auto")
    xmailer = msg.get("X-Mailer") or ""
    if _ESP_X_MAILER_RE.search(xmailer):
        hints.add("esp")
    return sorted(hints)


def parse_envelope(raw_msg: bytes) -> dict:
    """Estrae header + body preview da un messaggio raw IMAP."""
    msg = message_from_bytes(raw_msg)
    subject = _decode_header(msg.get("Subject", ""))
    sender = _decode_header(msg.get("From", ""))
    date = msg.get("Date", "")
    msg_id = msg.get("Message-ID", "")
    has_attach = False
    body_preview = ""
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            has_attach = True
            continue
        if ctype == "text/plain" and not body_preview:
            payload = part.get_payload(decode=True) or b""
            try:
                txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                txt = payload.decode("utf-8", errors="replace")
            body_preview = re.sub(r"\s+", " ", txt).strip()[:400]
    if not body_preview:
        # fallback: html se esiste
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                try:
                    txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    txt = payload.decode("utf-8", errors="replace")
                txt = re.sub(r"<[^>]+>", " ", txt)
                body_preview = re.sub(r"\s+", " ", txt).strip()[:400]
                break
    return {
        "from": sender,
        "subject": subject,
        "date": date,
        "message_id": msg_id,
        "has_attachment": has_attach,
        "body_preview": body_preview,
        "links": _extract_links(msg),
        "category_hints": _category_hints(msg),
    }


# Estensioni di asset statici (non-contenuto): escluse dai `links` per non
# far drillare font/css/js/immagini. Filtro GENERALE per tipo file, non per
# dominio (§7.3). Il drill (extract_entries) seguira' i link rimasti.
_ASSET_EXT_RE = re.compile(
    r"\.(?:css|js|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|mp4|mp3|pdf)"
    r"(?:[?#]|$)", re.IGNORECASE)


def _extract_links(msg, *, cap: int = 20) -> list:
    """Tutti gli URL http(s) di contenuto nel corpo HTML della mail, in ordine,
    deduplicati, esclusi gli asset statici. Generale per qualsiasi mittente:
    abilita il drill-down (extract_entries segue questi link se i campi
    richiesti non sono nel testo)."""
    urls: list = []
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True) or b""
        try:
            html = payload.decode(part.get_content_charset() or "utf-8",
                                  errors="replace")
        except Exception:
            html = payload.decode("utf-8", errors="replace")
        for m in re.finditer(r'href=["\']?(https?://[^"\'>\s]+)', html, re.I):
            u = m.group(1).rstrip(').,;"\'')
            if _ASSET_EXT_RE.search(u):
                continue
            if u not in urls:
                urls.append(u)
                if len(urls) >= cap:
                    return urls
    return urls

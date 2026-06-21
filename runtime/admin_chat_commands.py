"""admin_chat_commands — comandi admin shortcut conversazionali.

Riconosce query che iniziano con `/admin` e le dispatcha a routine
deterministiche (no LLM, no synth). Funziona uguale in chat web e
Telegram: entrambi inviano la query come testo al runtime.

Restricted a actor='host' (l'admin di sistema). Guest/user passano
attraverso il normale flow PLANNER.

Comandi supportati (11/5/2026 v1):
  /admin user create <name> [role]    — crea user (role default 'guest', oppure 'host')
  /admin user list                    — elenca utenti
  /admin user pair <name> [channel]   — emette pair token + ritorna URL (channel default 'http')
  /admin user pair me [channel]       — pair per l'utente corrente (host)
  /admin user channels <name>         — lista channel bindati a un user
  /admin user delete <name>           — cancella user
  /admin help                         — help inline

Determinismo §7.9: niente LLM. Solo dispatch tabellare.
"""
from __future__ import annotations

from typing import Optional

from messages import get as _msg  # §11 i18n


PREFIX = "/admin"


def matches(query: str) -> bool:
    """True se la query e' un comando admin shortcut o `/help`."""
    if not isinstance(query, str):
        return False
    q = query.strip().lower()
    if q == "/help" or q == "/?":
        return True
    return q.startswith(PREFIX + " ") or q == PREFIX


def dispatch(query: str, *,
             actor: str = "host",
             origin: str = "") -> Optional[str]:
    """Esegue il comando shortcut e ritorna il messaggio finale per il canale.

    Args:
      query: testo completo della query utente.
      actor: 'host' per accedere ai comandi /admin. /help disponibile a tutti.
      origin: prefisso URL (es. 'https://chat.metnos.com') per costruire
              pair URL. Se vuoto, fallback su 'http://localhost:8770'.

    Ritorna stringa finale per la chat. None se la query non matcha.
    """
    if not matches(query):
        return None
    q = query.strip()
    qlow = q.lower()

    # /help — visibile a tutti (mostra anche i comandi /admin se host).
    if qlow == "/help" or qlow == "/?":
        return _global_help(actor=actor)

    # /admin* — restricted host.
    if actor != "host":
        return _msg("ERR_ADMIN_FORBIDDEN")

    tokens = q.split()
    if len(tokens) == 1 or tokens[1].lower() == "help":
        return _admin_help()
    if len(tokens) >= 2 and tokens[1].lower() == "user":
        return _dispatch_user(tokens[2:], origin=origin)
    return _msg("ERR_ADMIN_UNKNOWN")


def _global_help(*, actor: str = "host") -> str:
    """Help visibile a tutti. Comandi `/admin` mostrati solo a host.

    Layout: due sezioni (UI / Admin) con bullets serrati. Click su
    `<code>` pre-fillano la input bar (chat web).
    """
    lines = [_msg("MSG_HELP_GLOBAL_BASE")]
    if actor == "host":
        lines.append(_msg("MSG_HELP_GLOBAL_ADMIN_SECTION"))
    else:
        lines.append(_msg("MSG_HELP_ADMIN_RESTRICTED"))
    lines.append(_msg("MSG_HELP_FOOTER"))
    return "\n".join(lines)


def _admin_help() -> str:
    return _msg("MSG_HELP_ADMIN_FULL")


def _dispatch_user(args: list, *, origin: str) -> str:
    if not args:
        return _msg("ERR_ADMIN_USER_MISSING_SUB")
    sub = args[0].lower()
    rest = args[1:]
    handlers: dict = {
        "create":   _user_create,
        "list":     _user_list,
        "pair":     _user_pair,
        "channels": _user_channels,
        "delete":   _user_delete,
    }
    fn = handlers.get(sub)
    if fn is None:
        return _msg("ERR_ADMIN_USER_UNKNOWN_SUB", sub=sub)
    return fn(rest, origin=origin)


def _import_users():
    import users as _users
    return _users


def _user_create(args: list, *, origin: str = "") -> str:
    if not args:
        return _msg("MSG_ADMIN_USER_CREATE_USAGE")
    name = args[0]
    role = (args[1].lower() if len(args) >= 2 else "guest")
    if role not in ("host", "guest"):
        return _msg("ERR_ADMIN_ROLE_INVALID", role=role)
    users = _import_users()
    try:
        u = users.create_user(name=name, role=role)
    except ValueError as ex:
        return _msg("ERR_ADMIN_GENERIC", error=ex)
    return _msg("MSG_ADMIN_USER_CREATED", name=u["name"], id=u["id"][:16],
                role=u["role"])


def _user_list(args: list, *, origin: str = "") -> str:
    users = _import_users()
    items = users.list_users()
    if not items:
        return _msg("MSG_ADMIN_NO_USERS")
    lines = [_msg("MSG_ADMIN_USERS_HEADER")]
    for u in items:
        channels = users.list_channels(u["id"])
        ch_summary = ", ".join(
            f"{c['channel']}({'✓' if c.get('verified_at') else '○'})"
            for c in channels
        ) or "—"
        lines.append(_msg("MSG_ADMIN_USER_ROW", name=u["name"],
                          role=u["role"], channels=ch_summary))
    return "\n".join(lines)


def _user_pair(args: list, *, origin: str = "") -> str:
    if not args:
        return _msg("MSG_ADMIN_PAIR_USAGE")
    target_name = args[0]
    channel = (args[1].lower() if len(args) >= 2 else "http")
    users = _import_users()
    if channel not in users.CHANNELS:
        valid = ", ".join(users.CHANNELS)
        return _msg("ERR_ADMIN_CHANNEL_INVALID", channel=channel, valid=valid)

    # Resolve user
    if target_name.lower() == "me":
        hosts = users.list_users(role="host")
        if not hosts:
            return _msg("ERR_ADMIN_NO_HOST")
        u = hosts[0]
    else:
        u = users.get_user(target_name)
        if not u:
            return _msg("ERR_ADMIN_USER_NOT_FOUND_CREATE", name=target_name)
    try:
        token = users.issue_pairing_token(u["id"], channel, ttl_s=3600)
    except Exception as ex:
        return _msg("ERR_ADMIN_TOKEN_ISSUE",
                    error=f"{type(ex).__name__}: {ex}")

    if channel == "http":
        prefix = origin or "http://localhost:8770"
        prefix = prefix.rstrip("/")
        pair_url = f"{prefix}/pair/{token}"
        return _msg("MSG_ADMIN_PAIR_URL_HTTP", name=u["name"], url=pair_url)
    if channel == "telegram":
        return _msg("MSG_ADMIN_PAIR_TELEGRAM", name=u["name"], token=token)
    return _msg("MSG_ADMIN_PAIR_GENERIC", name=u["name"], channel=channel,
                token=token)


def _user_channels(args: list, *, origin: str = "") -> str:
    if not args:
        return _msg("MSG_ADMIN_CHANNELS_USAGE")
    users = _import_users()
    u = users.get_user(args[0])
    if not u:
        return _msg("ERR_ADMIN_USER_NOT_FOUND", name=args[0])
    channels = users.list_channels(u["id"])
    if not channels:
        return _msg("MSG_ADMIN_NO_CHANNELS", name=u["name"])
    lines = [_msg("MSG_ADMIN_CHANNELS_HEADER", name=u["name"])]
    for c in channels:
        verified = _msg("MSG_ADMIN_VERIFIED") if c.get("verified_at") \
            else _msg("MSG_ADMIN_PENDING")
        rid = c.get("recipient_id") or "—"
        rid_short = rid[:24] + "…" if len(rid) > 24 else rid
        lines.append(_msg("MSG_ADMIN_CHANNEL_ROW", channel=c["channel"],
                          rid=rid_short, verified=verified))
    return "\n".join(lines)


def _user_delete(args: list, *, origin: str = "") -> str:
    if not args:
        return _msg("MSG_ADMIN_DELETE_USAGE")
    users = _import_users()
    u = users.get_user(args[0])
    if not u:
        return _msg("ERR_ADMIN_USER_NOT_FOUND", name=args[0])
    if u.get("role") == "host":
        return _msg("ERR_ADMIN_CANNOT_DELETE_HOST")
    ok = users.delete_user(u["id"])
    if ok:
        return _msg("MSG_ADMIN_USER_DELETED", name=u["name"])
    return _msg("ERR_ADMIN_DELETE_FAILED", name=args[0])

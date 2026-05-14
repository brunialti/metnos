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
        return ("Comandi `/admin` riservati al ruolo host. "
                "Non sei autorizzato.")

    tokens = q.split()
    if len(tokens) == 1 or tokens[1].lower() == "help":
        return _admin_help()
    if len(tokens) >= 2 and tokens[1].lower() == "user":
        return _dispatch_user(tokens[2:], origin=origin)
    return "Comando `/admin` non riconosciuto. Scrivi `/admin help`."


def _global_help(*, actor: str = "host") -> str:
    """Help visibile a tutti. Comandi `/admin` mostrati solo a host.

    Layout: due sezioni (UI / Admin) con bullets serrati. Click su
    `<code>` pre-fillano la input bar (chat web).
    """
    lines = [
        "**Comandi disponibili**",
        "**UI** (solo chat web, niente server):",
        "- `/clear` — pulisci la chat (mantiene buffer ↑↓)",
        "- `/clearbuf` — pulisci buffer comandi",
        "- `/reload` — ricarica la pagina",
        "- `/health` — ping al server",
        "- `/help` — questo elenco",
    ]
    if actor == "host":
        lines.extend([
            "**Admin** (solo host, chat + Telegram):",
            "- `/admin` — menu admin completo con esempi",
            "  - `/admin user create <name> [role]` — crea utente",
            "  - `/admin user list` — elenca utenti",
            "  - `/admin user pair me` — pair URL per te (channel http)",
            "  - `/admin user pair <name>` — pair URL per un utente",
            "  - `/admin user channels <name>` — device bindati",
            "  - `/admin user delete <name>` — elimina utente",
        ])
    else:
        lines.append("_Comandi `/admin` riservati al ruolo host._")
    lines.append(
        "_Click su un comando per copiarlo nella casella di scrittura._"
    )
    return "\n".join(lines)


def _admin_help() -> str:
    return (
        "**Comandi admin disponibili**\n\n"
        "- `/admin user create <name> [role]` — crea utente "
        "(role default `guest`, opzioni `host`/`guest`).\n"
        "- `/admin user list` — elenca utenti registrati.\n"
        "- `/admin user pair <name> [channel]` — genera pair URL per "
        "associare un device (channel default `http`, alternative "
        "`telegram`/`mail`).\n"
        "- `/admin user pair me [channel]` — pair per l'utente host.\n"
        "- `/admin user channels <name>` — elenca channel bindati a un "
        "utente.\n"
        "- `/admin user delete <name>` — elimina utente."
    )


def _dispatch_user(args: list, *, origin: str) -> str:
    if not args:
        return "Sotto-comando user mancante. Scrivi `/admin help`."
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
        return (f"Sotto-comando user `{sub}` non riconosciuto. "
                f"Scrivi `/admin help`.")
    return fn(rest, origin=origin)


def _import_users():
    import users as _users
    return _users


def _user_create(args: list, *, origin: str = "") -> str:
    if not args:
        return "Uso: `/admin user create <name> [role]`."
    name = args[0]
    role = (args[1].lower() if len(args) >= 2 else "guest")
    if role not in ("host", "guest"):
        return f"Role `{role}` non valido. Usa `host` o `guest`."
    users = _import_users()
    try:
        u = users.create_user(name=name, role=role)
    except ValueError as ex:
        return f"Errore: {ex}"
    return (f"Utente creato: **{u['name']}** (id `{u['id'][:16]}…`, "
            f"role={u['role']}). Per associare un device, scrivi:\n"
            f"`/admin user pair {u['name']}`")


def _user_list(args: list, *, origin: str = "") -> str:
    users = _import_users()
    items = users.list_users()
    if not items:
        return "Nessun utente registrato."
    lines = ["**Utenti registrati**"]
    for u in items:
        channels = users.list_channels(u["id"])
        ch_summary = ", ".join(
            f"{c['channel']}({'✓' if c.get('verified_at') else '○'})"
            for c in channels
        ) or "—"
        lines.append(f"- **{u['name']}** ({u['role']}) — channels: {ch_summary}")
    return "\n".join(lines)


def _user_pair(args: list, *, origin: str = "") -> str:
    if not args:
        return ("Uso: `/admin user pair <name> [channel]`. "
                "Usa `me` come nome per il tuo utente host.")
    target_name = args[0]
    channel = (args[1].lower() if len(args) >= 2 else "http")
    users = _import_users()
    if channel not in users.CHANNELS:
        valid = ", ".join(users.CHANNELS)
        return f"Channel `{channel}` non valido. Usa uno di: {valid}."

    # Resolve user
    if target_name.lower() == "me":
        hosts = users.list_users(role="host")
        if not hosts:
            return "Nessun utente host trovato."
        u = hosts[0]
    else:
        u = users.get_user(target_name)
        if not u:
            return (f"Utente `{target_name}` non trovato. "
                    f"Crealo prima con `/admin user create {target_name}`.")
    try:
        token = users.issue_pairing_token(u["id"], channel, ttl_s=3600)
    except Exception as ex:
        return f"Errore emissione token: {type(ex).__name__}: {ex}"

    if channel == "http":
        prefix = origin or "http://localhost:8770"
        prefix = prefix.rstrip("/")
        pair_url = f"{prefix}/pair/{token}"
        return (
            f"**Pair URL per {u['name']}** (channel `http`, valido 1 ora, "
            f"monouso):\n\n"
            f"{pair_url}\n\n"
            f"Manda questo URL al device target via canale fidato (Telegram "
            f"a te stesso, AirDrop, copia-incolla). Aprilo UNA VOLTA dal "
            f"device → cookie pair set persistente (90 giorni)."
        )
    if channel == "telegram":
        return (
            f"**Pair token per {u['name']}** (channel `telegram`, valido 1 "
            f"ora, monouso):\n\n"
            f"Comando da inviare al bot: `/start {token}`"
        )
    return (f"**Pair token per {u['name']}** (channel `{channel}`, valido 1 "
            f"ora, monouso):\n\n`{token}`")


def _user_channels(args: list, *, origin: str = "") -> str:
    if not args:
        return "Uso: `/admin user channels <name>`."
    users = _import_users()
    u = users.get_user(args[0])
    if not u:
        return f"Utente `{args[0]}` non trovato."
    channels = users.list_channels(u["id"])
    if not channels:
        return f"**{u['name']}** non ha channel bindati."
    lines = [f"**Channel di {u['name']}**"]
    for c in channels:
        verified = "✓" if c.get("verified_at") else "in attesa"
        rid = c.get("recipient_id") or "—"
        rid_short = rid[:24] + "…" if len(rid) > 24 else rid
        lines.append(
            f"- `{c['channel']}` → recipient `{rid_short}` ({verified})"
        )
    return "\n".join(lines)


def _user_delete(args: list, *, origin: str = "") -> str:
    if not args:
        return "Uso: `/admin user delete <name>`."
    users = _import_users()
    u = users.get_user(args[0])
    if not u:
        return f"Utente `{args[0]}` non trovato."
    if u.get("role") == "host":
        return "Non posso cancellare un utente host. Demoting non supportato."
    ok = users.delete_user(u["id"])
    if ok:
        return f"Utente **{u['name']}** eliminato."
    return f"Eliminazione fallita per `{args[0]}`."

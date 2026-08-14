"""users_email_sync — auto-bootstrap `user_channels(channel="mail")` da `users.email`.

Two storage fields parallel:
- `users.email` (colonna): indirizzo email canonico per ogni utente, popolato
  dal bootstrap host o da `update_user(email=...)` via UI admin.
- `users.user_channels(channel="mail")`: record verified richiesto da
  `send_messages` per risolvere `to_user=<name>` in `recipient_id=<address>`.

Senza sync, `users.email` resta inutilizzata e `send_messages(via_channel="mail")`
fallisce con `channel_not_paired:email` anche per host con email gia' nota
(es. roberto@example.com presente nella riga `users` ma assente
da `user_channels`).

Strategia (deterministica, §7.9 — niente LLM):
1. Iter su `users` con `email IS NOT NULL AND email != ''`.
2. Cerca record matching in `user_channels` via `(user_id, channel="mail")`.
3. Se trovato MA `recipient_id` diverso (email aggiornata) → UPDATE.
4. Se NON trovato → INSERT verified_at=now, recipient_id=users.email.

Idempotente. Da chiamare al boot di `metnos_http_server.make_app` e di
`ChannelDaemon.__init__` (subito dopo `sync_pairings_to_user_channels`).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from logging_setup import get_logger

log = get_logger(__name__)


from timefmt import now_iso_z as _now_iso


def sync_users_email_to_user_channels() -> dict:
    """Esegue il sync `users.email` → `user_channels(channel="mail")`.

    Ritorna dict con conteggi per audit/test:
      {
        "scanned":  int,  # utenti con email valorizzata
        "inserted": int,  # nuovi record user_channels creati
        "updated":  int,  # record esistenti aggiornati (email cambiata)
        "noop":     int,  # gia' allineati
      }
    """
    import users as _users
    _users.init_db()

    db_users = Path(_users.DEFAULT_DB_PATH)
    if not db_users.exists():
        return {"scanned": 0, "inserted": 0, "updated": 0, "noop": 0}

    out = {"scanned": 0, "inserted": 0, "updated": 0, "noop": 0}
    now = _now_iso()

    con = sqlite3.connect(str(db_users))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, name, email FROM users "
            "WHERE email IS NOT NULL AND TRIM(email) != ''"
        ).fetchall()
        out["scanned"] = len(rows)

        for r in rows:
            uid = r["id"]
            email = (r["email"] or "").strip()
            existing = con.execute(
                "SELECT recipient_id, verified_at FROM user_channels "
                "WHERE user_id = ? AND channel = 'mail'",
                (uid,),
            ).fetchone()

            if existing is None:
                con.execute(
                    "INSERT INTO user_channels "
                    "(user_id, channel, recipient_id, verified_at, "
                    " pairing_token, pairing_expires_at) "
                    "VALUES (?, 'mail', ?, ?, NULL, NULL)",
                    (uid, email, now),
                )
                out["inserted"] += 1
                log.info("users_email_sync: linked %s → %s", r["name"], email)
                continue

            if (existing["recipient_id"] or "").strip().lower() != email.lower():
                con.execute(
                    "UPDATE user_channels "
                    "SET recipient_id = ?, verified_at = ? "
                    "WHERE user_id = ? AND channel = 'mail'",
                    (email, now, uid),
                )
                out["updated"] += 1
                log.info("users_email_sync: updated %s → %s "
                         "(was %s)", r["name"], email,
                         existing["recipient_id"])
                continue

            out["noop"] += 1

        con.commit()
    finally:
        con.close()

    return out

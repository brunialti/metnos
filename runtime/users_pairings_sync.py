"""users_pairings_sync — riconcilia `pairings.db` ↔ `users.user_channels`.

Due storage paralleli storicamente disgiunti:
- `pairings.db` (Cap. 12 architettura): bootstrap Telegram + `/pair PAIR.<token>`.
  Source of truth per `autonomy_level` per `(channel, sender_id)`.
- `users.db / user_channels` (ADR 0083 multi-user): binding identita' utente
  + `/start <token>`. Source of truth per nome/role utente.

Senza sync, la UI `/admin/users/<id>` mostra `channels: []` per il bootstrap
host, anche se in `pairings.db` c'e' un record `paired_by='bootstrap'`.

Strategia (deterministica, §7.9 — niente LLM):
1. Iter su `pairings.db` con `revoked_at IS NULL`.
2. Cerca record matching in `user_channels` via `(channel, recipient_id)`.
3. Se trovato: aggiorna `verified_at` (touch).
4. Se NON trovato:
   a. Se `paired_by='bootstrap'` e c'e' UN SOLO host in `users` → bind al host.
   b. Se `actor` del pair coincide con `name` di uno user → bind.
   c. Altrimenti skip (orphan pair, log debug).

Idempotente. Da chiamare al boot di `metnos_http_server.make_app` e di
`ChannelDaemon.__init__`. Nessun side-effect su `pairings.db`.
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


def sync_pairings_to_user_channels() -> dict:
    """Esegue il sync. Ritorna dict con conteggi per audit/test.

    {
      "scanned": int,    # record in pairings.db esaminati
      "touched": int,    # entry user_channels aggiornate (verified_at)
      "linked":  int,    # entry user_channels create (bootstrap → host)
      "orphans": int,    # pair senza user matchabile (log only)
    }
    """
    import pairing as _pairing
    import users as _users

    _users.init_db()  # garantisce schema + bootstrap host
    db_pairings = _pairing.DEFAULT_DB_PATH
    if not Path(db_pairings).exists():
        return {"scanned": 0, "touched": 0, "linked": 0, "orphans": 0}

    out = {"scanned": 0, "touched": 0, "linked": 0, "orphans": 0}
    p_conn = sqlite3.connect(str(db_pairings))
    p_conn.row_factory = sqlite3.Row
    try:
        rows = p_conn.execute(
            "SELECT channel, sender_id, autonomy_level, paired_by, actor "
            "FROM pairings WHERE revoked_at IS NULL"
        ).fetchall()
    finally:
        p_conn.close()

    out["scanned"] = len(rows)

    for r in rows:
        channel = r["channel"]
        sender_id = r["sender_id"]
        paired_by = r["paired_by"] or ""
        actor = (r["actor"] or "").strip()

        # Skip canali non riconosciuti dalla schema users.CHANNELS (es.
        # 'fake' usato dai test): non c'e' tabella user_channels valida
        # per loro. Non e' un errore, sono pair operativi per altri usi.
        if channel not in _users.CHANNELS:
            log.debug("skip pair su canale non in users.CHANNELS: %s/%s",
                       channel, sender_id)
            out["orphans"] += 1
            continue

        existing = _users.find_user_by_recipient(channel, sender_id)
        if existing is not None:
            # Touch verified_at sul binding esistente.
            u_conn = _users._open_db()
            try:
                with u_conn:
                    u_conn.execute(
                        "UPDATE user_channels SET verified_at=? "
                        "WHERE user_id=? AND channel=? AND recipient_id=?",
                        (_now_iso(), existing["id"], channel, sender_id),
                    )
            finally:
                u_conn.close()
            out["touched"] += 1
            continue

        # Non c'e' binding. Tre regole di link in ordine di confidenza:
        target_user_id = None
        # (a) actor del pair coincide con name di uno user.
        if actor:
            u = _users.get_user(actor)
            if u:
                target_user_id = u["id"]
        # (b) bootstrap → host unico (single-host policy).
        if target_user_id is None and paired_by == "bootstrap":
            hosts = _users.list_users(role="host")
            if len(hosts) == 1:
                target_user_id = hosts[0]["id"]

        if target_user_id is None:
            log.debug("orphan pair: %s/%s actor=%r paired_by=%r",
                       channel, sender_id, actor, paired_by)
            out["orphans"] += 1
            continue

        # Crea binding deterministico.
        try:
            _users.add_channel(
                target_user_id, channel=channel,
                recipient_id=sender_id, verified=True,
            )
            out["linked"] += 1
            log.info("synced pair %s/%s → user_id=%s (paired_by=%s)",
                      channel, sender_id, target_user_id, paired_by)
        except Exception as ex:
            log.warning("add_channel failed for %s/%s → %s: %s",
                         channel, sender_id, target_user_id, ex)

    return out


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(sync_pairings_to_user_channels(), indent=2))

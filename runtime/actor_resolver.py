#!/usr/bin/env python3
"""actor_resolver — risoluzione (channel, sender_id) -> actor logico.

Multi-user model (vedi `host_guest_model.md` 27/4/2026):
- `host` = proprietario sistema (Roberto). Singolare per design.
- `guest_<short_id>` = ospiti trusted (familiari) con canale proprio +
  autonomy livello suo. Plurali, ognuno con propria identita' logica.

Tutti i record runtime per-actor (locations.jsonl, undo.jsonl, mnestoma
events, scratchpad future per-actor, ...) usano questo nome canonico,
NON sender_id raw (che e' specifico di canale: id Telegram numerico).

Auto-assignment policy (MVP):
1. Pairing ha `actor` esplicito → ritorna quello.
2. Pairing senza actor → assegna lazy:
   - Primo pairing per channel (ordine paired_at) → "host"
   - Successivi → "guest_<sender_id ultimi 6 char>"
   - Persiste l'assegnazione nel DB (UPDATE pairings SET actor=?)
3. Nessun pairing (canale CLI / test / non pairato) → "host" fallback.
   Coerente col MVP single-user dove tutto e' host.

Override esplicito (futuro): comando /setname <actor> per cambiare l'actor
di un pairing (es. da auto "guest_xxxxx" a leggibile "guest_iacopo").
"""
from __future__ import annotations

from typing import Optional


def _short_sender(sender_id: str) -> str:
    """Tag breve da sender_id (es. ultimi 6 char di 100000001 → 000001).
    Used per generare nome guest auto-assegnato leggibile."""
    s = (sender_id or "").strip()
    if not s:
        return "anon"
    return s[-6:] if len(s) > 6 else s


def resolve_actor(channel: str, sender_id: str) -> str:
    """Mappa (channel, sender_id) -> actor logico. Lazy auto-assign + persist.

    Garantisce un nome stabile cross-session per quel pairing.
    """
    # Import lazy per evitare circular import (pairing carica vocab/messages)
    try:
        from pairing import get_pairing, _open_db
    except ImportError:
        return "host"  # fallback estremo (modulo pairing non disponibile)
    if not channel or not sender_id:
        return "host"
    p = get_pairing(channel, sender_id)
    if p is None:
        return "host"  # non pairato: trattalo come host (MVP single-user)
    if p.actor:
        return p.actor
    # Lazy auto-assign: decidi se e' il primo pairing del channel (= host)
    # oppure un successivo (= guest).
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT sender_id FROM pairings WHERE channel=? AND revoked_at IS NULL "
            "ORDER BY paired_at ASC, id ASC",
            (channel,)
        ).fetchall()
        if rows and rows[0]["sender_id"] == sender_id:
            actor = "host"
        else:
            actor = f"guest_{_short_sender(sender_id)}"
        conn.execute(
            "UPDATE pairings SET actor=? WHERE channel=? AND sender_id=?",
            (actor, channel, sender_id)
        )
    finally:
        conn.close()
    return actor


def set_actor(channel: str, sender_id: str, actor: str,
              display_name: Optional[str] = None) -> bool:
    """Override esplicito dell'actor per un pairing. Usato dal comando
    /setname (futuro) o per migrazione manuale."""
    try:
        from pairing import _open_db
    except ImportError:
        return False
    if not actor or not channel or not sender_id:
        return False
    conn = _open_db()
    try:
        cur = conn.execute(
            "UPDATE pairings SET actor=?, display_name=COALESCE(?, display_name) "
            "WHERE channel=? AND sender_id=?",
            (actor, display_name, channel, sender_id)
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def list_actors() -> list[dict]:
    """Lista compatta degli actor noti (per debug / future UI admin)."""
    try:
        from pairing import _open_db
    except ImportError:
        return []
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT channel, sender_id, autonomy_level, actor, display_name, "
            "paired_at, last_seen FROM pairings WHERE revoked_at IS NULL "
            "ORDER BY paired_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

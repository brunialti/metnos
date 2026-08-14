"""Registry deterministico delle sessioni di chat e delle conversazioni HTTP.

ADR Phase 7 (11/5/2026, Roberto): single-active-session per (user, channel)
con takeover esplicito invece di multi-writer + conflict resolution.

Storage: tabelle `active_sessions` e `chat_conversations` dentro `users.db`.
Il dispositivo possiede soltanto il diritto temporaneo di scrittura; la
conversazione appartiene all'utente logico e puo' essere trasferita.

API:
- `register_session(user_id, channel, device_label, conversation_id)`: tenta create; se
  c'e' gia' una sessione attiva (revoked_at IS NULL) per (user_id, channel),
  ritorna `{conflict: True, existing, takeover_token}`. Altrimenti
  `{conflict: False, device_token, conversation_id}`.
- `confirm_takeover(..., mode)`: revoca la vecchia + crea la nuova in singola
  transazione. `activate_current` conserva la conversazione del chiamante;
  `continue_existing` trasferisce quella del vecchio dispositivo. Ritorna
  `{device_token, conversation_id}` o
  solleva ValueError se token gia' consumato o scaduto.
- `touch_session(device_token, ...)`: aggiorna `last_seen_at`, migra in modo
  write-once il legame conversazione quando il client lo fornisce e ritorna
  la sessione aggiornata; `None` se revocata o sconosciuta.
- `revoke_session(device_token, reason)`: marca revoked.
- `list_sessions_for_user(user_id)`: tutte (attive + storiche) per admin.

Vincolo §7.9: deterministico, niente LLM.
Vincolo §2.8: no silent failure — errori sollevano ValueError.

Race-safety: SQLite transaction esplicita per il check-then-insert atomico
in register_session e per il revoke+insert in confirm_takeover. SQLite con
`isolation_level=None` (autocommit) richiede `BEGIN IMMEDIATE` per lockare
il DB ai concorrenti prima del read.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any

import users  # riusa _open_db / _now_iso pattern

# Pending takeover tokens: in-memory (token -> {user_id, channel, expires_at}).
# Non persisted: la richiesta di takeover e' una negoziazione brevissima
# (utente clicca "Si" entro ~10s). Process restart = utente ri-registra
# normalmente, vede di nuovo il conflict, fa di nuovo takeover. KISS §7.2.
_PENDING_TAKEOVERS: dict[str, dict] = {}
_PENDING_TTL_S = 600  # 10 min: largo, copre eventuale dialog UX lungo

SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    legacy_actor TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_conversations_owner
    ON chat_conversations(user_id, channel, updated_at);
CREATE TABLE IF NOT EXISTS active_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    device_token TEXT NOT NULL UNIQUE,
    device_label TEXT,
    channel TEXT NOT NULL,
    conversation_id TEXT,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    revoke_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_active_sessions_user_channel
    ON active_sessions(user_id, channel, revoked_at);
CREATE INDEX IF NOT EXISTS idx_active_sessions_token
    ON active_sessions(device_token);
"""

_CONVERSATION_RE = re.compile(r"^c_[A-Za-z0-9][A-Za-z0-9_.:-]{4,93}$")
_TAKEOVER_MODES = frozenset({"activate_current", "continue_existing"})


from timefmt import now_iso_z as _now_iso


def _open_db() -> sqlite3.Connection:
    """Apre `users.db` garantendo lo schema di entrambe le tabelle.

    Idempotente: CREATE TABLE IF NOT EXISTS + executescript multipla volte
    e' safe; users.SCHEMA crea users/user_channels, SCHEMA_EXTRA aggiunge
    active_sessions + indici.
    """
    conn = users._open_db()  # crea users.db + tabelle base
    conn.executescript(SCHEMA_EXTRA)
    return conn


def init_db() -> None:
    """Garantisce lo schema (idempotente). Chiamabile al boot."""
    conn = _open_db()
    try:
        # DB nati prima di ADR 0201 non hanno il legame conversazione. La
        # colonna resta nullable soltanto per consentire il binding write-once
        # dal primo ping del browser aggiornato.
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(active_sessions)"
        ).fetchall()}
        if "conversation_id" not in cols:
            conn.execute(
                "ALTER TABLE active_sessions ADD COLUMN conversation_id TEXT"
            )
    finally:
        conn.close()


def _cleanup_pending() -> None:
    """Rimuove takeover token scaduti dalla mappa in-memory."""
    now = time.time()
    expired = [t for t, v in _PENDING_TAKEOVERS.items()
               if v.get("expires_at", 0) < now]
    for t in expired:
        _PENDING_TAKEOVERS.pop(t, None)


def purge_user_memory(user_id: str) -> int:
    """Drop uncommitted takeover tokens for one removed principal."""

    target = str(user_id or "")
    if not target:
        return 0
    tokens = [
        token for token, pending in _PENDING_TAKEOVERS.items()
        if str(pending.get("user_id") or "") == target
    ]
    for token in tokens:
        _PENDING_TAKEOVERS.pop(token, None)
    return len(tokens)


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def _normalize_conversation_id(value: str) -> str:
    candidate = str(value or "").strip()
    if candidate and not _CONVERSATION_RE.fullmatch(candidate):
        raise ValueError("invalid conversation_id")
    return candidate


def _new_conversation_id() -> str:
    return f"c_{uuid.uuid4().hex}"


def _conversation_row(conn: sqlite3.Connection,
                      conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM chat_conversations WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()


def _check_conversation_owner(conn: sqlite3.Connection, conversation_id: str,
                              user_id: str, channel: str) -> None:
    """Rifiuta il riuso di un identificatore appartenente a un altro owner."""
    if not conversation_id:
        return
    row = _conversation_row(conn, conversation_id)
    if row is not None and (
        row["user_id"] != user_id or row["channel"] != channel
    ):
        raise ValueError("conversation_owner_mismatch")


def _ensure_conversation(conn: sqlite3.Connection, *, user_id: str,
                         channel: str, conversation_id: str = "",
                         legacy_actor: str = "") -> str:
    """Crea o valida una conversazione nella transazione del chiamante."""
    conv_id = _normalize_conversation_id(conversation_id)
    if not conv_id:
        conv_id = _new_conversation_id()
    row = _conversation_row(conn, conv_id)
    now = _now_iso()
    if row is None:
        conn.execute(
            "INSERT INTO chat_conversations "
            "(conversation_id,user_id,channel,legacy_actor,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (conv_id, user_id, channel, (legacy_actor or "")[:200], now, now),
        )
    else:
        _check_conversation_owner(conn, conv_id, user_id, channel)
        # `legacy_actor` serve solo a leggere i turni anteriori alla migrazione;
        # non viene mai sostituito da un dispositivo successivo.
        if not row["legacy_actor"] and legacy_actor:
            conn.execute(
                "UPDATE chat_conversations SET legacy_actor=?, updated_at=? "
                "WHERE conversation_id=?",
                ((legacy_actor or "")[:200], now, conv_id),
            )
        else:
            conn.execute(
                "UPDATE chat_conversations SET updated_at=? "
                "WHERE conversation_id=?", (now, conv_id),
            )
    return conv_id


def get_conversation(conversation_id: str) -> dict | None:
    """Ritorna il record della conversazione, oppure None."""
    try:
        conv_id = _normalize_conversation_id(conversation_id)
    except ValueError:
        return None
    if not conv_id:
        return None
    init_db()
    conn = _open_db()
    try:
        return _row_to_dict(_conversation_row(conn, conv_id))
    finally:
        conn.close()


def conversation_for_user(conversation_id: str, user_id: str,
                          channel: str) -> dict | None:
    """Lookup owner-bound usato dalle route di sola lettura."""
    row = get_conversation(conversation_id)
    if not row:
        return None
    if row["user_id"] != user_id or row["channel"] != channel:
        return None
    return row


def register_session(
    user_id: str,
    channel: str,
    device_label: str = "",
    conversation_id: str = "",
    legacy_actor: str = "",
) -> dict[str, Any]:
    """Tenta di registrare una nuova sessione attiva per `(user_id, channel)`.

    Se non c'e' sessione attiva per la coppia (revoked_at IS NULL):
        ritorna `{"conflict": False, "device_token": "<uuid hex>"}`.
    Altrimenti:
        ritorna `{"conflict": True, "existing": {...}, "takeover_token": "<uuid>"}`.
        Il client deve mostrare prompt all'utente, raccogliere consenso,
        e chiamare `confirm_takeover(takeover_token, new_device_label)`.

    `device_label` e' opzionale (es. "Chrome 130 desktop"); usato per UX
    del prompt takeover ("Sessione attiva su <device_label>").

    Solleva ValueError se user_id o channel non validi.
    """
    if not user_id:
        raise ValueError("user_id must be non-empty")
    if channel not in users.CHANNELS:
        raise ValueError(
            f"channel must be one of {users.CHANNELS}, got {channel!r}"
        )
    init_db()
    label = (device_label or "").strip()[:200]
    candidate_conv = _normalize_conversation_id(conversation_id)
    _cleanup_pending()

    conn = _open_db()
    try:
        # BEGIN IMMEDIATE locka il DB ai concorrenti prima del read,
        # serializza i register_session paralleli sullo stesso (user, ch).
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM active_sessions WHERE user_id=? AND channel=? "
            "AND revoked_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (user_id, channel),
        ).fetchone()
        if existing is not None:
            _check_conversation_owner(
                conn, candidate_conv, user_id, channel,
            )
            conn.execute("ROLLBACK")
            ex_dict = dict(existing)
            takeover_token = uuid.uuid4().hex
            _PENDING_TAKEOVERS[takeover_token] = {
                "user_id": user_id,
                "channel": channel,
                "old_device_token": ex_dict["device_token"],
                "new_device_label": label,
                "candidate_conversation_id": candidate_conv,
                "candidate_legacy_actor": (legacy_actor or "")[:200],
                "expires_at": time.time() + _PENDING_TTL_S,
            }
            return {
                "conflict": True,
                "existing": {
                    "device_label": ex_dict.get("device_label") or "",
                    "started_at": ex_dict.get("started_at") or "",
                    "last_seen_at": ex_dict.get("last_seen_at") or "",
                    "can_continue": bool(ex_dict.get("conversation_id")),
                },
                "takeover_token": takeover_token,
            }
        token = uuid.uuid4().hex
        now = _now_iso()
        conv_id = _ensure_conversation(
            conn, user_id=user_id, channel=channel,
            conversation_id=candidate_conv, legacy_actor=legacy_actor,
        )
        conn.execute(
            "INSERT INTO active_sessions "
            "(user_id, device_token, device_label, channel, conversation_id, "
            "started_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, token, label, channel, conv_id, now, now),
        )
        conn.execute("COMMIT")
        return {"conflict": False, "device_token": token,
                "conversation_id": conv_id}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def confirm_takeover(
    takeover_token: str,
    new_device_label: str = "",
    mode: str = "activate_current",
    expected_user_id: str = "",
) -> dict[str, Any]:
    """Conferma il takeover: revoca la sessione precedente + crea la nuova.

    Atomico: in singola transazione BEGIN IMMEDIATE. Il `takeover_token`
    viene consumato (rimosso da `_PENDING_TAKEOVERS`) anche su errore di
    DB, cosi' che retry su token non validi non blocchino il flusso.

    Solleva ValueError se token scaduto/sconosciuto.
    Ritorna `{"device_token": "<uuid>", "revoked_device_token": "<old>"}`.
    """
    if not takeover_token:
        raise ValueError("takeover_token must be non-empty")
    if mode not in _TAKEOVER_MODES:
        raise ValueError("invalid takeover mode")
    _cleanup_pending()
    pending = _PENDING_TAKEOVERS.get(takeover_token)
    if pending is None:
        raise ValueError(
            "takeover_token unknown or expired (consumed once, "
            "TTL 10 minutes)"
        )
    user_id = pending["user_id"]
    if expected_user_id and expected_user_id != user_id:
        raise ValueError("takeover_owner_mismatch")
    # Il token diventa one-shot soltanto dopo aver autenticato l'owner.
    _PENDING_TAKEOVERS.pop(takeover_token, None)
    channel = pending["channel"]
    old_token = pending.get("old_device_token") or ""
    label = (new_device_label or pending.get("new_device_label") or "").strip()[:200]

    conn = _open_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT * FROM active_sessions WHERE user_id=? AND channel=? "
            "AND revoked_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (user_id, channel),
        ).fetchone()
        if active is None or active["device_token"] != old_token:
            raise ValueError("active_session_changed")
        if mode == "continue_existing":
            selected_conv = str(active["conversation_id"] or "")
            if not selected_conv:
                raise ValueError("existing_conversation_unavailable")
            selected_conv = _ensure_conversation(
                conn, user_id=user_id, channel=channel,
                conversation_id=selected_conv,
            )
        else:
            selected_conv = _ensure_conversation(
                conn, user_id=user_id, channel=channel,
                conversation_id=pending.get("candidate_conversation_id") or "",
                legacy_actor=pending.get("candidate_legacy_actor") or "",
            )
        now = _now_iso()
        # Revoca TUTTE le sessioni attive per (user, channel): copre il
        # caso (raro) in cui una corsa concorrente ne avesse create di
        # piu' di una; idempotente se old_token gia' revocato.
        conn.execute(
            "UPDATE active_sessions SET revoked_at=?, revoke_reason='takeover' "
            "WHERE user_id=? AND channel=? AND revoked_at IS NULL",
            (now, user_id, channel),
        )
        new_token = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO active_sessions "
            "(user_id, device_token, device_label, channel, conversation_id, "
            "started_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, new_token, label, channel, selected_conv, now, now),
        )
        conn.execute("COMMIT")
        return {
            "device_token": new_token,
            "revoked_device_token": old_token,
            "conversation_id": selected_conv,
            "mode": mode,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def touch_session(device_token: str, *, user_id: str = "",
                  conversation_id: str = "", legacy_actor: str = "") -> dict | None:
    """Aggiorna `last_seen_at` se la sessione e' attiva.

    Ritorna la sessione se esiste ed e' attiva (revoked_at IS NULL), `None`
    se revocata o sconosciuta. Un vecchio client che non invia ancora
    `conversation_id` non viene associato a una conversazione casuale: il
    binding resta nullo finche' quel device carica la UI aggiornata.
    """
    if not device_token:
        return None
    init_db()
    conn = _open_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM active_sessions WHERE device_token=?",
            (device_token,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        if row["revoked_at"] is not None:
            conn.execute("ROLLBACK")
            return None
        if user_id and row["user_id"] != user_id:
            conn.execute("ROLLBACK")
            return None
        authoritative = str(row["conversation_id"] or "")
        if not authoritative:
            candidate = _normalize_conversation_id(conversation_id)
            if candidate:
                # Migrazione write-once: soltanto il possessore del token attivo
                # e lo stesso owner autenticato associano il localStorage.
                authoritative = _ensure_conversation(
                    conn, user_id=row["user_id"], channel=row["channel"],
                    conversation_id=candidate, legacy_actor=legacy_actor,
                )
                conn.execute(
                    "UPDATE active_sessions SET conversation_id=? WHERE id=?",
                    (authoritative, row["id"]),
                )
        else:
            _ensure_conversation(
                conn, user_id=row["user_id"], channel=row["channel"],
                conversation_id=authoritative, legacy_actor=legacy_actor,
            )
        conn.execute(
            "UPDATE active_sessions SET last_seen_at=? WHERE device_token=?",
            (_now_iso(), device_token),
        )
        conn.execute("COMMIT")
        return get_session(device_token)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def validate_writer(device_token: str, *, user_id: str, channel: str,
                    conversation_id: str) -> tuple[bool, str]:
    """Controllo lato server del diritto di inviare un turno."""
    try:
        conv_id = _normalize_conversation_id(conversation_id)
    except ValueError:
        return False, "invalid_conversation"
    if not device_token:
        return False, "session_token_required"
    if not conv_id:
        return False, "conversation_required"
    session = get_session(device_token)
    if not session or session.get("revoked_at"):
        return False, "session_revoked"
    if session.get("user_id") != user_id or session.get("channel") != channel:
        return False, "session_owner_mismatch"
    if session.get("conversation_id") != conv_id:
        return False, "session_conversation_mismatch"
    return True, ""


def revoke_session(device_token: str, reason: str = "manual") -> bool:
    """Marca la sessione come revocata. Ritorna True se ha modificato una riga.

    Idempotente: se gia' revocata, ritorna False (nessuna scrittura).
    Reason canonici: 'manual', 'takeover', 'timeout', 'admin'.
    """
    if not device_token:
        return False
    init_db()
    conn = _open_db()
    try:
        cur = conn.execute(
            "UPDATE active_sessions SET revoked_at=?, revoke_reason=? "
            "WHERE device_token=? AND revoked_at IS NULL",
            (_now_iso(), reason, device_token),
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def get_session(device_token: str) -> dict | None:
    """Lookup per token. Ritorna dict con tutti i campi o None."""
    if not device_token:
        return None
    init_db()
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM active_sessions WHERE device_token=?",
            (device_token,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_active_for(user_id: str, channel: str) -> dict | None:
    """Ritorna la sessione attiva (revoked_at IS NULL) per (user, channel)
    o None. La piu' recente per started_at se ce ne fosse piu' di una
    (non dovrebbe per design)."""
    if not user_id or not channel:
        return None
    init_db()
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM active_sessions WHERE user_id=? AND channel=? "
            "AND revoked_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (user_id, channel),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_sessions_for_user(user_id: str) -> list[dict]:
    """Tutte le sessioni (attive + storiche) per uno user, piu' recenti
    prima. Usato dalla UI admin."""
    if not user_id:
        return []
    init_db()
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT * FROM active_sessions WHERE user_id=? "
            "ORDER BY started_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --- SSE event subscriptions (in-memory pub/sub) ---------------------------

# Un client puo' sottoscrivere `session_events(device_token)` per ricevere
# notifiche real-time quando la propria sessione viene revocata da un
# altro device (takeover). Implementazione: in-memory queue per token.
# Quando `confirm_takeover()` revoca old_token, il publisher chiama
# `publish_event(old_token, "session_revoked", payload)` che mette il
# dict nelle queue di tutti i subscriber di quel token.

# In-memory pub/sub: device_token -> set[asyncio.Queue]
_SUBSCRIBERS: dict[str, set] = {}


def subscribe(device_token: str, queue) -> None:
    """Registra una queue come subscriber agli eventi di `device_token`.

    `queue` deve essere un asyncio.Queue; il publisher chiama
    `queue.put_nowait(event_dict)`. L'unsubscribe va fatto esplicitamente
    via `unsubscribe()` (es. nel finally del handler SSE).
    """
    if not device_token or queue is None:
        return
    _SUBSCRIBERS.setdefault(device_token, set()).add(queue)


def unsubscribe(device_token: str, queue) -> None:
    """Rimuove la queue dal pub/sub. Idempotente."""
    if not device_token or queue is None:
        return
    s = _SUBSCRIBERS.get(device_token)
    if s is None:
        return
    s.discard(queue)
    if not s:
        _SUBSCRIBERS.pop(device_token, None)


def publish_event(device_token: str, kind: str, payload: dict) -> int:
    """Pubblica un evento a tutti i subscriber del token.

    Ritorna il numero di queue raggiunte (>=0). Non solleva: queue full
    e' loggata e ignorata (UX cosmetic, peggio caso il client polla
    /agent/session/ping e scopre il revoke).
    """
    if not device_token:
        return 0
    subs = _SUBSCRIBERS.get(device_token)
    if not subs:
        return 0
    n = 0
    for q in list(subs):
        try:
            q.put_nowait({"kind": kind, **payload})
            n += 1
        except Exception:
            pass
    return n


# Hook nel takeover: dopo aver revocato la sessione vecchia, notifica il
# device sloggato. Non puo' essere fatto direttamente in confirm_takeover()
# perche' quello e' sync e i subscriber sono asyncio queue; usiamo un
# wrapper che aggrega le due chiamate.

def confirm_takeover_with_notify(
    takeover_token: str,
    new_device_label: str = "",
    mode: str = "activate_current",
    expected_user_id: str = "",
) -> dict[str, Any]:
    """Come `confirm_takeover` ma pubblica `session_revoked` al device
    sloggato dopo la commit. Usato dal handler HTTP."""
    res = confirm_takeover(
        takeover_token, new_device_label=new_device_label, mode=mode,
        expected_user_id=expected_user_id,
    )
    old_token = res.get("revoked_device_token") or ""
    if old_token:
        publish_event(
            old_token,
            "session_revoked",
            {
                "reason": "takeover",
                "new_device_label": (new_device_label or "")[:200],
                "ts": _now_iso(),
            },
        )
    return res

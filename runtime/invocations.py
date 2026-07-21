"""runtime.invocations — coda invocazioni firmate per gli executor remoti.

Corpo tecnico del protocollo §6 di `internal/design/remote-executors.html`
(ADR 0011/0034/0046). Un'invocazione e' un executor + args, firmato dal
server con la chiave Ed25519 'author', indirizzato a un `device_id`.
Idempotente per `invocation_id` (monotono, time-ordered).

Canonical JSON (contratto di firma, condiviso col client Rust):
- chiavi ordinate, separatori compatti, UTF-8 NON escaped
  (`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`)
- NIENTE float nei payload firmati (solo int/str/bool/list/dict/null):
  la serializzazione dei float non e' riproducibile cross-linguaggio.

Stati: queued -> delivered -> done|failed|expired. Un `invocation_id` con
result gia' ricevuto non viene MAI ri-messo in coda (§6.4). Un'invocazione
`delivered` senza result oltre la deadline viene ri-consegnata al poll
successivo (il dedup client-side + il cursor la rendono innocua).
`expired` (B.4 fase 7): il reaper chiude le in-volo mai concluse oltre TTL —
non verranno piu' consegnate; un result REALE tardivo (device che aveva gia'
ricevuto l'invocazione) la chiude comunque done/failed: l'evento vince (§2.8).
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import devices  # noqa: E402
from sign import load_private, load_public  # noqa: E402
import config as _C  # noqa: E402  §7.11

from logging_setup import get_logger
log = get_logger(__name__)

from timefmt import now_iso_z as _now_iso  # noqa: E402

DEFAULT_DEADLINE_MS = 60_000
# Margine oltre la deadline prima di ri-consegnare una 'delivered' orfana.
REDELIVERY_GRACE_S = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS invocations (
    invocation_id TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    server_sig    TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'queued',
    created_at    TEXT NOT NULL,
    created_epoch REAL,
    delivered_at  TEXT,
    delivered_epoch REAL,
    deadline_ms   INTEGER NOT NULL,
    completed_at  TEXT,
    completed_epoch REAL,
    result_json   TEXT,
    abandoned_by_turn INTEGER NOT NULL DEFAULT 0,
    origin_actor  TEXT NOT NULL DEFAULT '',
    origin_channel TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_invocations_device_state
    ON invocations(device_id, state);
"""
# abandoned_by_turn (A.0 fase 7): il turno che ha accodato l'invocazione ha
# smesso di attenderla (timeout) → l'op puo' comunque completarsi PIU' TARDI sul
# device. Al submit tardivo di una abandoned mutante+ok, il record undo orfano
# viene chiuso (annullabilita' ripristinata, §2.8).


def _migrate_schema(conn) -> None:
    """Migrazione additiva idempotente (DB esistenti pre-A.0 non hanno la
    colonna abandoned_by_turn: CREATE TABLE IF NOT EXISTS non la aggiunge)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invocations)")}
    if "abandoned_by_turn" not in cols:
        conn.execute("ALTER TABLE invocations "
                     "ADD COLUMN abandoned_by_turn INTEGER NOT NULL DEFAULT 0")
    if "origin_actor" not in cols:
        conn.execute("ALTER TABLE invocations "
                     "ADD COLUMN origin_actor TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE invocations "
                     "ADD COLUMN origin_channel TEXT NOT NULL DEFAULT ''")
    if "created_epoch" not in cols:
        conn.execute("ALTER TABLE invocations ADD COLUMN created_epoch REAL")
    if "completed_epoch" not in cols:
        conn.execute("ALTER TABLE invocations ADD COLUMN completed_epoch REAL")
# delivered_epoch = time.time() a wall-clock (NON monotonic): il confronto per
# la redelivery deve sopravvivere a restart/reboot del server, dove il clock
# monotonic si azzera. La finestra (deadline+grace) è ampia: eventuali salti
# NTP sono trascurabili.


class InvocationError(Exception):
    pass


class SignatureError(InvocationError):
    """Firma device non verificata: il result NON viene accettato."""


# --- canonical JSON + firma -------------------------------------------------

def canonical_bytes(obj: dict) -> bytes:
    """Bytes canonici di un payload firmato. Vedi contratto nel docstring modulo."""
    _reject_floats(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _reject_floats(obj) -> None:
    if isinstance(obj, float):
        raise InvocationError(
            "float in payload firmato: non riproducibile cross-linguaggio")
    if isinstance(obj, dict):
        for v in obj.values():
            _reject_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_floats(v)


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_payload(payload: dict, *, key_name: str = "author") -> str:
    """Firma Ed25519 (b64url) dei bytes canonici del payload."""
    priv = load_private(key_name)
    return _b64u_encode(priv.sign(canonical_bytes(payload)))


def verify_payload(public_key_b64: str, sig_b64: str, payload: dict) -> bool:
    """Verifica una firma Ed25519 b64url contro i bytes CANONICI del payload.

    Usato dove le due parti costruiscono il payload indipendentemente
    (server_sig lato client). Per i messaggi client→server preferire
    `verify_raw` sui bytes trasmessi (float-safe, §6.3)."""
    try:
        return _verify_raw_bytes(public_key_b64, sig_b64, canonical_bytes(payload))
    except Exception:
        return False


def verify_raw(public_key_b64: str, sig_b64: str, raw: bytes) -> bool:
    """Verifica una firma Ed25519 b64url contro i bytes ESATTI ricevuti.

    Il client firma `serde_json::to_vec(body)` e invia quegli stessi bytes:
    il server verifica cio' che ha ricevuto, senza ri-serializzare. Nessuna
    dipendenza dal round-trip canonico → gli entries possono contenere float
    (punteggi/coordinate) senza rompere la firma."""
    try:
        return _verify_raw_bytes(public_key_b64, sig_b64, raw)
    except Exception:
        return False


def _verify_raw_bytes(public_key_b64: str, sig_b64: str, raw: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub = Ed25519PublicKey.from_public_bytes(_b64u_decode(public_key_b64))
    pub.verify(_b64u_decode(sig_b64), raw)
    return True


def server_public_key_b64(*, key_name: str = "author") -> str:
    """Pubkey del server (raw Ed25519, b64url) — quella pinnata dal client."""
    from cryptography.hazmat.primitives import serialization
    pub = load_public(key_name)
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64u_encode(raw)


# --- hash executor (per il pull-on-miss del client) --------------------------

def executor_shas(name: str) -> tuple[str, str]:
    """(manifest_sha256, code_sha256) dell'executor `name` sotto PATH_EXECUTORS.

    manifest_sha256 = sha256 dei bytes di manifest.toml (quello firmato).
    code_sha256     = digest dichiarato nel manifest ([code].digest, senza
                      prefisso 'sha256:'), che sign.py mantiene allineato.
    """
    import tomllib
    ex_dir = _C.PATH_EXECUTORS / name
    manifest_path = ex_dir / "manifest.toml"
    if not manifest_path.is_file():
        raise InvocationError(f"executor sconosciuto: {name}")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    declared = manifest.get("code", {}).get("digest", "")
    code_sha = declared.removeprefix("sha256:")
    if not code_sha:
        raise InvocationError(f"manifest di {name} senza [code].digest")
    return manifest_sha, code_sha


# --- DB ----------------------------------------------------------------------

def _open_db(db_path: Path | None = None) -> sqlite3.Connection:
    conn = devices._open_db(db_path)
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    return conn


def mark_abandoned(invocation_id: str, *, db_path: Path | None = None) -> None:
    """A.0: il turno ha smesso di attendere (timeout). Marca l'invocazione così
    che, se il device la completa più tardi, il submit tardivo sappia chiudere
    l'undo orfano. Solo se ancora in volo (queued/delivered): un'op già
    done/failed non è 'abbandonata'. Fail-open."""
    try:
        conn = _open_db(db_path)
        try:
            conn.execute(
                "UPDATE invocations SET abandoned_by_turn = 1 "
                "WHERE invocation_id = ? AND state IN ('queued','delivered')",
                (invocation_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception as ex:  # noqa: BLE001 — best-effort, non blocca il turno
        log.warning("mark_abandoned(%s) fallita: %r", invocation_id, ex)


def _new_invocation_id() -> str:
    """Monotono per costruzione: time_ns esadecimale + suffisso random."""
    return f"inv-{time.time_ns():016x}{uuid.uuid4().hex[:8]}"


# --- API ----------------------------------------------------------------------

def enqueue_invocation(device_id: str, executor: str, args: dict, *,
                       turn_id: str | None = None,
                       scope: str = "device",
                       reversibility: str = "read_only",
                       env_injections: dict | None = None,
                       deadline_ms: int = DEFAULT_DEADLINE_MS,
                       origin_actor: str = "",
                       origin_channel: str = "",
                       db_path: Path | None = None) -> str:
    """Accoda un'invocazione firmata per `device_id`. Ritorna invocation_id.

    Il payload firmato segue §6.2 del doc di progettazione. env_injections
    vive SOLO nel payload consegnato via poll (mTLS), mai sul device a riposo.
    """
    dev = devices.get_device(device_id, db_path=db_path)
    if dev is None or dev.revoked_at is not None:
        raise InvocationError(f"device sconosciuto o revocato: {device_id}")

    manifest_sha, code_sha = executor_shas(executor)
    invocation_id = _new_invocation_id()
    payload = {
        "invocation_id": invocation_id,
        "turn_id": turn_id or "",
        "executor": executor,
        "manifest_sha256": manifest_sha,
        "code_sha256": code_sha,
        "args": args or {},
        "scope": scope,
        "reversibility": reversibility,
        "env_injections": env_injections or {},
        "deadline_ms": int(deadline_ms),
    }
    sig = sign_payload(payload)

    conn = _open_db(db_path)
    try:
        conn.execute(
            """INSERT INTO invocations
               (invocation_id, device_id, payload_json, server_sig, state,
                created_at, created_epoch, deadline_ms,
                origin_actor, origin_channel)
               VALUES (?,?,?,?, 'queued', ?, ?, ?, ?, ?)""",
            (invocation_id, device_id,
             json.dumps(payload, ensure_ascii=False), sig,
             _now_iso(), time.time(), int(deadline_ms),
             origin_actor or "", origin_channel or ""),
        )
    finally:
        conn.close()
    log.info("invocation enqueued %s executor=%s device=%s",
             invocation_id, executor, device_id[:12])
    return invocation_id


def purge_invocations(older_than_days: int = 30, *,
                      db_path: Path | None = None) -> int:
    """Elimina le invocazioni TERMINALI (done/failed/expired) COMPLETATE da più
    di `older_than_days` giorni. F5 (review 2026-07-04) + rilievo #2: la
    retention è sul COMPLETAMENTO (`completed_at`, sempre valorizzato sul
    terminale via `_now_iso()` — anche alla scadenza B.4), NON sulla consegna —
    un terminale mai 'delivered' (es. result da spool su un 'queued') non resta
    più orfano per sempre. Confronto ISO lessicografico (formato unico UTC
    '...Z' → ordinamento corretto), con fallback su `delivered_epoch` per
    l'edge terminale senza completed_at. NON tocca queued/delivered (in volo).
    Ritorna le righe rimosse. Idempotente.
    Agganciato a `jobs/maintenance_tasks.task_state_reaper`."""
    import time as _t
    cutoff = _t.time() - int(older_than_days) * 86400
    cutoff_iso = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(cutoff))
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM invocations "
            "WHERE state IN ('done','failed','expired') AND ("
            "  (completed_at IS NOT NULL AND completed_at < ?) "
            "  OR (completed_at IS NULL AND delivered_epoch IS NOT NULL "
            "      AND delivered_epoch < ?))",
            (cutoff_iso, cutoff))
        return cur.rowcount
    finally:
        conn.close()


def expire_stale_invocations(ttl_h: float | None = None, *,
                             db_path: Path | None = None) -> int:
    """B.4 (fase 7): formalizza lo stato `expired` come stato di coda.

    Un'invocazione in volo oltre il TTL che nessun device sta lavorando —
    `queued` mai presa in carico, oppure `delivered` stantia già ri-eleggibile
    alla redelivery (client sparito) — non verrà più consegnata: diventa
    `expired` con `completed_at` valorizzato (retention standard). Una
    `delivered` RECENTE (device al lavoro proprio ora) non si tocca: se il
    result arriva dopo la scadenza, `complete_invocation` lo accetta comunque
    (l'evento reale vince, §2.8). Le invocazioni ABBANDONATE dal turno (A.0)
    generano la notifica onesta di chiusura: l'op NON è stata eseguita.
    TTL via `METNOS_INVOCATION_TTL_H` (default 24; `ttl_h` esplicito vince).
    Ritorna il numero di invocazioni scadute. Idempotente; chiamata dal reaper
    `jobs/maintenance_tasks.task_state_reaper`."""
    import os
    if ttl_h is None:
        try:
            ttl_h = float(os.environ.get("METNOS_INVOCATION_TTL_H", "24"))
        except (TypeError, ValueError):
            ttl_h = 24.0
    if ttl_h <= 0:
        return 0
    now = time.time()
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(now - ttl_h * 3600))
    # Stesso predicato di ri-eleggibilità di next_invocation: una 'delivered'
    # con delivered_epoch fresco è in esecuzione ADESSO → mai scaduta qui.
    where = (
        "created_at < ? AND (state = 'queued' OR (state = 'delivered' "
        "AND ? - COALESCE(delivered_epoch, 0) > deadline_ms / 1000.0 + ?))"
    )
    params = (cutoff_iso, now, REDELIVERY_GRACE_S)
    conn = _open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"""SELECT invocation_id, device_id, payload_json,
                       abandoned_by_turn, origin_actor, origin_channel
                FROM invocations WHERE {where}""", params).fetchall()
        if rows:
            ids = [r["invocation_id"] for r in rows]
            conn.execute(
                "UPDATE invocations SET state = 'expired', completed_at = ?, "
                "completed_epoch = ? "
                "WHERE invocation_id IN ({})".format(",".join("?" * len(ids))),
                (_now_iso(), now, *ids))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as _e:
            log.warning("rollback failed in %s: %s", __name__, _e)
        raise
    finally:
        conn.close()
    for row in rows:
        log.warning("invocation %s SCADUTA senza esecuzione (TTL %sh, "
                    "device %s)", row["invocation_id"], ttl_h,
                    row["device_id"][:12])
        if row["abandoned_by_turn"]:
            _notify_expired(row["payload_json"], row["device_id"],
                            (row["origin_channel"], row["origin_actor"]),
                            ttl_h)
    return len(rows)


def _notify_expired(payload_json, device_id: str, origin: tuple,
                    ttl_h: float) -> None:
    """B.4: chiusura onesta per il destinatario che aveva ricevuto «esito
    incerto» (A.0) — l'operazione è scaduta senza MAI essere eseguita (§2.8).
    Testo via i18n (MSG_LATE_RESULT_EXPIRED, chiave nel seed). Fail-open."""
    try:
        payload = json.loads(payload_json) if payload_json else {}
        executor = payload.get("executor") or "?"
        channel, actor = (origin or ("", ""))
        from devices import get_device
        dev = get_device(device_id or "")
        dev_name = getattr(dev, "name", None) or "device"
        from messages import get as _msg
        text = _msg("MSG_LATE_RESULT_EXPIRED", tool=executor, device=dev_name,
                    h=int(ttl_h))
        import user_notices
        user_notices.append(channel or "", actor or "host", text)
        log.info("B.4 notice expired accodata per %s:%s (%s)",
                 channel or "any", actor or "host", executor)
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("_notify_expired fallita (fail-open): %r", ex)


def next_invocation(device_id: str, *, cursor: str | None = None,
                    db_path: Path | None = None) -> dict | None:
    """Claim atomico della prossima invocazione per il device.

    Ritorna il wire object §6.2 (`payload + server_sig`) o None se coda vuota.
    Ri-consegna anche le 'delivered' orfane (client crashato) oltre
    deadline + grace; il cursor del client esclude cio' che ha gia' visto.
    """
    now = time.time()
    # Predicato di eleggibilità condiviso fra il probe read-only e il claim.
    where = (
        "device_id = ? AND invocation_id > COALESCE(?, '') "
        "AND (state = 'queued' OR (state = 'delivered' "
        "     AND ? - COALESCE(delivered_epoch, 0) > deadline_ms / 1000.0 + ?))"
    )
    params = (device_id, cursor, now, REDELIVERY_GRACE_S)
    conn = _open_db(db_path)
    try:
        # 1. Probe READ-ONLY: sull'idle path (coda vuota) niente write-lock —
        #    i poll dei device non si serializzano tutti su BEGIN IMMEDIATE.
        row = conn.execute(
            f"""SELECT invocation_id, payload_json, server_sig FROM invocations
                WHERE {where} ORDER BY invocation_id LIMIT 1""",
            params,
        ).fetchone()
        if row is None:
            return None

        # 2. Claim atomico: prendi il write-lock e ri-verifica l'eleggibilità
        #    dello stesso id (race con un altro poll concorrente).
        conn.execute("BEGIN IMMEDIATE")
        still = conn.execute(
            f"SELECT 1 FROM invocations WHERE invocation_id = ? AND {where}",
            (row["invocation_id"], *params),
        ).fetchone()
        if still is None:
            conn.execute("COMMIT")
            return None  # un altro poll l'ha già preso; il client ri-polla
        conn.execute(
            """UPDATE invocations
               SET state = 'delivered', delivered_at = ?, delivered_epoch = ?
               WHERE invocation_id = ?""",
            (_now_iso(), now, row["invocation_id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as _e:
            log.warning("rollback failed in %s: %s", __name__, _e)
        raise
    finally:
        conn.close()

    wire = json.loads(row["payload_json"])
    wire["server_sig"] = row["server_sig"]
    return wire


def complete_invocation(result: dict, *, raw_body: bytes | None = None,
                        sig_b64: str | None = None,
                        db_path: Path | None = None) -> bool:
    """Registra il result di un'invocazione (§6.3).

    La firma del device (`sig_b64`) copre i bytes ESATTI ricevuti (`raw_body`):
    la verifica avviene su quei bytes, non su una ri-serializzazione. Il
    chiamante HTTP passa i bytes grezzi del body + l'header X-Metnos-Device-Sig.
    Idempotente: un result gia' registrato ritorna True senza sovrascrivere
    (§6.4, mai doppio side-effect). Solleva SignatureError se la firma non
    verifica (§12: rifiuto + log).

    `raw_body`/`sig_b64` opzionali solo per test che verificano a monte; in
    produzione sono SEMPRE forniti (l'handler li impone).
    """
    invocation_id = result.get("invocation_id")
    device_id = result.get("device_id")
    if not (isinstance(invocation_id, str) and isinstance(device_id, str)):
        raise InvocationError(
            "result malformato: invocation_id/device_id richiesti")

    dev = devices.get_device(device_id, db_path=db_path)
    if dev is None or dev.revoked_at is not None:
        raise SignatureError(f"device sconosciuto o revocato: {device_id}")

    if raw_body is not None or sig_b64 is not None:
        if not (isinstance(raw_body, (bytes, bytearray)) and isinstance(sig_b64, str)):
            raise SignatureError("firma o body grezzo mancante")
        if not verify_raw(dev.public_key_b64, sig_b64, bytes(raw_body)):
            log.warning("device_sig NON verificata per %s da device %s: rifiuto",
                        invocation_id, device_id[:12])
            raise SignatureError("device_sig non verificata")

    state = "done" if result.get("ok") else "failed"
    conn = _open_db(db_path)
    was_abandoned = False
    payload_json = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT device_id, state, payload_json, abandoned_by_turn, "
            "origin_actor, origin_channel "
            "FROM invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise InvocationError(f"invocation sconosciuta: {invocation_id}")
        if row["device_id"] != device_id:
            conn.execute("ROLLBACK")
            raise SignatureError("result da un device diverso dal destinatario")
        if row["state"] in ("done", "failed"):
            conn.execute("COMMIT")
            return True  # idempotente: primo result vince
        # B.4: un result su una `expired` si accetta — il device l'aveva
        # ricevuta PRIMA della scadenza e l'ha eseguita davvero (es. result
        # rimasto nello spool con server irraggiungibile): l'evento reale
        # batte la nostra previsione di morte (§2.8). Trattata come tardiva
        # (undo chiuso + utente avvisato), il turno d'origine è lontano.
        was_abandoned = bool(row["abandoned_by_turn"]) or \
            row["state"] == "expired"
        payload_json = row["payload_json"]
        origin = (row["origin_channel"] if "origin_channel" in row.keys()
                  else "", row["origin_actor"] if "origin_actor" in row.keys()
                  else "")
        conn.execute(
            """UPDATE invocations
               SET state = ?, completed_at = ?, completed_epoch = ?,
                   result_json = ?
               WHERE invocation_id = ?""",
            (state, _now_iso(), time.time(),
             json.dumps(result, ensure_ascii=False),
             invocation_id),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as _e:
            log.warning("rollback failed in %s: %s", __name__, _e)
        raise
    finally:
        conn.close()
    log.info("invocation %s -> %s", invocation_id, state)
    # A.0 (fase 7): risultato TARDIVO di un'op ABBANDONATA dal turno. Se è una
    # mutazione RIUSCITA, il pending undo era orfano (il turno l'aveva chiuso
    # come timeout ok=False) → chiudilo ORA con l'esito reale, ripristinando
    # l'annullabilità (§2.8). Log PROMINENTE: il notificatore utente (A.2) è il
    # passo successivo; finché non c'è, la traccia non è silenziosa.
    if was_abandoned and state == "done":
        _close_late_undo(payload_json, result)
    # A.2 (fase 7): l'utente CREDE che l'op sia fallita (il turno ha risposto
    # «esito incerto») → avvisalo dell'esito reale alla prossima visita sul
    # suo canale (v1: coda user_notices drenata dal primo turno successivo).
    if was_abandoned and state in ("done", "failed"):
        _notify_late_outcome(payload_json, result, state, origin)
    return True


# Verbi che LASCIANO UNO STATO reversibile (allineati a reverse_patterns): solo
# per questi ha senso chiudere un undo tardivo.
_MUTATING_PREFIXES = ("delete", "move", "write", "create", "send", "share",
                      "order", "change")


def _notify_late_outcome(payload_json, result: dict, state: str,
                         origin: tuple) -> None:
    """A.2: accoda l'avviso «l'operazione si è completata DOPO il timeout»
    per il destinatario d'origine. Testo via i18n (§7.13, chiavi nel seed:
    MSG_LATE_RESULT_{DONE,FAILED}). Fail-open."""
    try:
        payload = json.loads(payload_json) if payload_json else {}
        executor = payload.get("executor") or "?"
        channel, actor = (origin or ("", ""))
        from devices import get_device
        dev = get_device(result.get("device_id") or "")
        dev_name = getattr(dev, "name", None) or "device"
        from messages import get as _msg
        n = result.get("n_processed")
        if not isinstance(n, int):
            pl = result.get("payload") or {}
            n = pl.get("ok_count") if isinstance(pl, dict) else None
        key = ("MSG_LATE_RESULT_DONE" if state == "done"
               else "MSG_LATE_RESULT_FAILED")
        text = _msg(key, tool=executor, device=dev_name,
                    n=n if isinstance(n, int) else "?")
        import user_notices
        user_notices.append(channel or "", actor or "host", text)
        log.info("A.2 notice accodata per %s:%s (%s %s)",
                 channel or "any", actor or "host", executor, state)
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("_notify_late_outcome fallita (fail-open): %r", ex)


def _close_late_undo(payload_json, result: dict) -> None:
    """Chiude il record undo orfano di un'op remota completata in ritardo
    (A.0). Correla per `turn_id` (dal payload) + device. Fail-open, mai blocca
    il submit."""
    try:
        payload = json.loads(payload_json) if payload_json else {}
        executor = payload.get("executor") or ""
        turn_id = payload.get("turn_id") or ""
        device_id = result.get("device_id") or ""
        if not turn_id or not executor.split("_", 1)[0] in _MUTATING_PREFIXES:
            return
        import undo
        n = undo.UndoLog().close_pending_for_turn(
            turn_id, result, device=device_id)
        if n:
            log.warning("A.0 risultato-tardivo: %s (turn %s) completata sul "
                        "device DOPO il timeout del turno → chiusi %d record "
                        "undo (op ora annullabile)", executor, turn_id[:12], n)
    except Exception as ex:  # noqa: BLE001 — best-effort
        log.warning("_close_late_undo fallita (fail-open): %r", ex)


def get_invocation(invocation_id: str, *,
                   db_path: Path | None = None) -> dict | None:
    """Stato corrente + result (se presente) di un'invocazione."""
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "invocation_id": row["invocation_id"],
        "device_id": row["device_id"],
        "state": row["state"],
        "created_at": row["created_at"],
        "created_epoch": row["created_epoch"],
        "delivered_at": row["delivered_at"],
        "delivered_epoch": row["delivered_epoch"],
        "completed_at": row["completed_at"],
        "completed_epoch": row["completed_epoch"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
    }


def wait_result(invocation_id: str, timeout_s: float, *,
                poll_interval_s: float = 0.1,
                db_path: Path | None = None) -> dict | None:
    """Attesa sincrona (polling) del result. None allo scadere del timeout.

    Il chiamante async usa `await loop.run_in_executor(None, ...)` o il
    gemello `await_result` in agent_server.

    B.2 (fase 7): polling ADATTIVO — `poll_interval_s` per i primi 5s
    (risposta pronta sui result rapidi), poi 1s: un'attesa lunga non merita
    10 query/s. Ceiling noto e ACCETTATO: ogni wait_result occupa un thread
    del pool per tutta l'attesa; il rimedio strutturale è il differito A.1
    (spec fase 7), NON l'async nell'engine.
    """
    start = time.monotonic()
    deadline = start + timeout_s
    while True:
        info = get_invocation(invocation_id, db_path=db_path)
        if info is not None and info["state"] in ("done", "failed", "expired"):
            return info["result"]  # expired: result None = nessun esito (B.4)
        now = time.monotonic()
        if now >= deadline:
            return None
        step = poll_interval_s if (now - start) < 5.0 \
            else max(poll_interval_s, 1.0)
        time.sleep(min(step, deadline - now))

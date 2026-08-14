"""dialog_pending — storage di stato per dialoghi `get_inputs` (ADR 0090).

Modulo deterministico (the design guide §7.9): nessuna chiamata LLM. Un dialogo
e' un walk sequenziale fra step (var/prompt/schema). Lo stato vive su
disco perche' il dialogo attraversa piu' turni utente sul canale (le
risposte arrivano una alla volta da Telegram, oppure tutte insieme via
form HTTP). Lo storage e' raggruppato per `<sender_id>`, ma l'autorita' e'
sempre l'identificatore immutabile `owner_user_id`: sender e actor sono
coordinate di consegna, non identita'.

Layout su disco:

    ~/.local/share/metnos/get_inputs/<sender_id>/<dialog_id>.json

con `mode 0600` (puo' contenere credenziali parziali in fase di raccolta).

Schema del payload JSON:

    {
      "dialog_id":         "uuid-hex16",
      "title":             "Credenziali per cifs_NAS",
      "description":       "Server CIFS · saranno cifrate.",
      "dialog":            [{"var": "username", "prompt": "...",
                              "schema": {"kind": "text"}, "optional": false},
                             ...],
      "fmt":               "dialogue" | "form" | "voice",
      "values_collected":  {"username": "alice", ...},
      "step_index":        2,
      "started_at":        "2026-05-04T18:32:11Z",
      "actor":             "host",
      "owner_user_id":     "uuid-immutabile",
      "timeout_s":         600,                # opzionale; default None
      "completed":         false,
      "cancelled":         false,
      "on_complete":       {                    # opzionale (ADR 0091, 5/5/2026)
        "type": "save_credentials_and_resume",
        "credentials_domain": "cifs_<host>",
        "credentials_context": {"binding": "cifs", "host": "..."},
        "resume_call": "admin",
        "resume_args": {"intent": "...", "command_proposed": "..."}
      }
    }

Caratteristiche:
- API piatta, niente classi: 6 funzioni pure (modulo).
- TTL controllato dal caller via `cleanup_expired(now_ts)` (chiamato dal
  channel daemon o da un task scheduler). Default soft TTL = 1 ora.
- Filename `<dialog_id>.json` univoco; `<sender_id>` viene sanitizzato
  per essere nome-cartella safe.
- I metodi `consume_pending_step` e `cancel_pending` sono idempotenti.
"""
from __future__ import annotations

import json
import hmac
import hashlib
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)

# Path canonico esposto come modulo-level per facilitare test (monkeypatch).
# §7.11: deriva da config.PATH_USER_DATA (env METNOS_USER_DATA override).
# Senza, dialog pending finivano in `~/.local/share/metnos/get_inputs/`
# anche con server tmp E2E → cross-contamination state tra test.
import config as _C
DIALOG_DIR = _C.PATH_USER_DATA / "get_inputs"

# Soft TTL: scaduti dopo 1 minuto senza risposta (regola Roberto 29/5/2026;
# override per-dialogo via `timeout_s` per i casi che ne richiedono di piu',
# es. inserimento credenziali; override globale via env METNOS_DIALOG_TTL_S).
# Lo sweep scheduler (dialog_pending_sweep, every_1m) chiude+notifica sullo
# stesso canale; list_pending salta gli scaduti cosi' non mangiano una query
# fresca a turn-time.
DEFAULT_TTL_S = int(os.environ.get("METNOS_DIALOG_TTL_S", "60"))
# I form (>=2 step) e i dialoghi di credenziali richiedono tempo per essere
# compilati: TTL piu' lungo (default 10 min) per non chiuderli sotto le dita.
FORM_TTL_S = int(os.environ.get("METNOS_DIALOG_FORM_TTL_S", "600"))


# ── Helper interni ────────────────────────────────────────────────────

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")
_DIALOG_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_sender(sender_id: str) -> str:
    """Sanitizza il sender_id per usarlo come nome di cartella."""
    if not sender_id:
        return "_unknown"
    safe = _SAFE_RE.sub("_", str(sender_id))
    # `.` e `..` sono nomi di directory speciali anche dopo la sostituzione
    # dei separatori. Non devono mai risolvere fuori da DIALOG_DIR.
    return "_unknown" if safe in {".", ".."} else safe


def valid_dialog_id(dialog_id: str) -> bool:
    """True solo per identificatori utilizzabili come singolo filename."""
    value = str(dialog_id or "")
    return bool(_DIALOG_ID_RE.fullmatch(value)) and ".." not in value


def _sender_dir(sender_id: str) -> Path:
    return DIALOG_DIR / _safe_sender(sender_id)


def _dialog_path(sender_id: str, dialog_id: str) -> Path:
    if not valid_dialog_id(dialog_id):
        raise ValueError("dialog_id non valido")
    return _sender_dir(sender_id) / f"{dialog_id}.json"


@contextmanager
def _dialog_lock(sender_id: str, dialog_id: str):
    """Serializza consume/cancel anche fra HTTP server e channel daemon."""
    import fcntl
    sd = _sender_dir(sender_id)
    sd.mkdir(parents=True, exist_ok=True)
    # Un solo lock stabile per sender: evita file-lock orfani per ogni dialogo
    # e serializza le risposte che, semanticamente, appartengono allo stesso
    # flusso conversazionale.
    lock_path = sd / ".dialog.lock"
    with lock_path.open("a+") as lock_file:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


from timefmt import now_iso_offset as _utc_now_iso


def _started_ts(payload: dict) -> float:
    """Epoch del `started_at` ISO del dialogo, 0.0 se mancante/illeggibile."""
    iso = payload.get("started_at") or ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def default_timeout_for(dialog: list | None,
                        on_complete: dict | None = None) -> int:
    """TTL di default di un dialogo INTERATTIVO (§7.3, non per-caller).

    Ogni dialogo qui è interattivo (form/scelta/credenziali): l'utente risponde
    quando può, anche su canale ASYNC (Telegram) dove non sta fissando lo schermo
    → `FORM_TTL_S`. Prima i dialoghi single-step sì/no/scelta chiudevano in ~1 min
    (`DEFAULT_TTL_S`): sbagliato in una conversazione — un gate di consenso visto
    qualche minuto dopo scadeva (Roberto 20/6). Il TTL serve solo da GC degli
    abbandonati; un `timeout_s` esplicito del chiamante resta sovrano (es. il
    consent-gate schedulato lo alza a 1h per il «rispondi con comodo»).
    """
    return FORM_TTL_S


def is_expired(payload: dict, now_ts: float | None = None) -> bool:
    """True se il dialogo ha superato il TTL (`timeout_s` per-dialogo, altrimenti
    DEFAULT_TTL_S) dal `started_at`. Senza `started_at` valido → NON scaduto
    (assenza di evidenza non giustifica la rimozione)."""
    started = _started_ts(payload)
    if not started:
        return False
    if now_ts is None:
        now_ts = time.time()
    ttl = int(payload.get("timeout_s") or DEFAULT_TTL_S)
    return (now_ts - started) > ttl


# ── API pubblica ──────────────────────────────────────────────────────

def save_pending(sender_id: str, dialog_id: str, payload: dict) -> Path:
    """Salva o sovrascrive lo stato di un dialogo. Mode 0600.

    Il chiamante (executor `get_inputs`) e' responsabile di costruire un
    payload coerente con lo schema dichiarato sopra; questo modulo non
    impone validazione semantica oltre la presenza di `dialog_id`.
    """
    if not dialog_id:
        raise ValueError("dialog_id mancante")
    if not isinstance(payload, dict):
        raise TypeError("payload deve essere un dict")
    payload = dict(payload)
    owner = str(payload.get("owner_user_id") or "").strip()
    if not owner:
        # Non adottare mai uno stato tramite actor/sender/nome: sono valori
        # riutilizzabili e quindi non dimostrano l'identita' del proprietario.
        raise ValueError("owner_user_id mancante")
    payload["owner_user_id"] = owner
    sd = _sender_dir(sender_id)
    sd.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(sd, 0o700)
    except OSError as ex:
        log.debug("chmod 0700 fallito su %s: %s", sd, ex)
    p = _dialog_path(sender_id, dialog_id)
    # Scrittura atomica (tmp + os.replace): list_pending/consume/sweep non
    # devono mai leggere JSON parziale (lost update / parse error spuri).
    # chmod sul tmp PRIMA del replace così il file finale nasce 0600.
    owner_tag = hashlib.sha256(
        ("dialog-owner-v1\0" + owner).encode("utf-8")
    ).hexdigest()[:20]
    tmp = p.with_name(f"{p.name}.{owner_tag}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError as ex:
        log.debug("chmod 0600 fallito su %s: %s", tmp, ex)
    os.replace(tmp, p)
    return p


def create_if_no_active(sender_id: str, dialog_id: str, payload: dict,
                        *, idempotency_key: str) -> str:
    """Atomically create one pending dialog only when the sender has none.

    The sender lock spans both the active-set check and the durable replace.
    ``idempotency_key`` is stored for audit/reconciliation; callers derive it
    from authenticated principal, conversation and literal operation, never
    from a random dialog identifier.
    """

    if not idempotency_key:
        raise ValueError("idempotency_key mancante")
    owner = str((payload or {}).get("owner_user_id") or "").strip()
    if not owner:
        raise ValueError("owner_user_id mancante")
    with _dialog_lock(sender_id, dialog_id):
        # Completed dialogs are durable receipts until their TTL expires.
        # ``list_pending`` intentionally hides them from interaction, so scan
        # the sender journal explicitly before admitting an identical action.
        sender_dir = _sender_dir(sender_id)
        if sender_dir.exists():
            for path in sender_dir.glob("*.json"):
                try:
                    prior = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (str(prior.get("owner_user_id") or "") == owner
                        and prior.get("idempotency_key") == idempotency_key
                        and prior.get("callback_claimed_at")
                        and not is_expired(prior)):
                    return "already_claimed"
        if list_pending(sender_id, owner_user_id=owner):
            return "active_pending"
        state = dict(payload)
        state["idempotency_key"] = str(idempotency_key)
        save_pending(sender_id, dialog_id, state)
        return "created"


def _load_raw(sender_id: str, dialog_id: str) -> dict | None:
    """Legge anche record legacy non attribuiti; solo per housekeeping."""

    if not valid_dialog_id(dialog_id):
        return None
    p = _dialog_path(sender_id, dialog_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as ex:
        log.warning("dialog_pending corrotto %s: %s", p, ex)
        return None


def load_pending(sender_id: str, dialog_id: str, *,
                 owner_user_id: str) -> dict | None:
    """Carica soltanto uno stato attribuito a un owner immutabile."""

    owner = str(owner_user_id or "").strip()
    state = _load_raw(sender_id, dialog_id)
    if (not owner or state is None
            or not hmac.compare_digest(
                str(state.get("owner_user_id") or ""), owner)):
        return None
    return state


def list_pending(sender_id: str, *, owner_user_id: str) -> list[dict]:
    """Lista i dialoghi pendenti per il sender (non completati e non cancellati).

    Utile al daemon per riconoscere uno stato attivo all'arrivo di un
    messaggio dell'utente. Ordinato per `started_at` ascending (il piu'
    vecchio prima); i risultati corrotti vengono saltati silenziosamente.
    """
    owner = str(owner_user_id or "").strip()
    if not owner:
        return []
    sd = _sender_dir(sender_id)
    if not sd.exists():
        return []
    out: list[dict] = []
    for p in sd.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not hmac.compare_digest(
                str(d.get("owner_user_id") or ""), owner):
            continue
        if d.get("completed") or d.get("cancelled"):
            continue
        if is_expired(d):
            continue  # scaduto: non e' piu' "attivo" → non consumare la query
        out.append(d)
    out.sort(key=lambda d: d.get("started_at", ""))
    return out


def find_by_dialog_id(dialog_id: str, *,
                      owner_user_id: str) -> tuple[dict | None, str | None]:
    """Cerca un dialogo pendente per `dialog_id` GLOBALMENTE, scandendo tutte le
    sender-dir. Ritorna (state, sender_id) o (None, None).

    Il `dialog_id` (uuid) e' globalmente unico → la chiave-sender NON serve per
    identificarlo. Fallback robusto quando il sender al tap differisce da quello
    di salvataggio (query SCHEDULATE: pending sotto «telegram:roberto», il tap
    risolve il chat_id a «host») e i bridge a TTL (cap_pending 10 min) sono
    scaduti mentre il dialogo (timeout_s) e' ancora valido. Salta i
    completati/cancellati/scaduti. §7.9 deterministico."""
    owner = str(owner_user_id or "").strip()
    if (not owner or not valid_dialog_id(dialog_id)
            or not DIALOG_DIR.exists()):
        return None, None
    for sd in DIALOG_DIR.iterdir():
        if not sd.is_dir():
            continue
        p = sd / f"{dialog_id}.json"
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (not hmac.compare_digest(
                    str(d.get("owner_user_id") or ""), owner)
                or d.get("completed") or d.get("cancelled")
                or is_expired(d)):
            return None, None
        return d, (d.get("sender_id") or sd.name)
    return None, None


_INVALID_CHOICE = object()  # sentinella: risposta non risolvibile a una choice


def _resolve_choice_reply(value, step):
    """Risolve la risposta utente a uno step CHOICE → `value` canonico
    dell'opzione. Accetta: indice 1..N, il `value` esatto, o il `label`
    (case-insensitive: esatto o substring UNICO). Ritorna `_INVALID_CHOICE`
    se non risolvibile, il valore INVARIATO se lo step non e' una choice.

    Generale §7.9: vale per ogni dialogo choice, ogni canale (Telegram/HTTP),
    ogni lingua (match sul label i18n). Risolve il vicolo cieco dialogue su
    HTTP, dove la risposta arriva come testo libero ("1"/"email") invece che
    come `value` del form."""
    schema = (step or {}).get("schema") or {}
    if schema.get("kind") != "choice":
        return value
    choices = schema.get("choices") or []
    if not choices:
        return value
    norm = []  # (value, label)
    for c in choices:
        if isinstance(c, dict):
            v = str(c.get("value", c.get("label", "")))
            lbl = str(c.get("label", c.get("value", "")))
        else:
            v = lbl = str(c)
        norm.append((v, lbl))
    s = str(value).strip()
    if not s:
        return _INVALID_CHOICE
    for v, _lbl in norm:           # 1) value esatto
        if s == v:
            return v
    if s.isdigit():               # 2) indice 1..N
        i = int(s)
        if 1 <= i <= len(norm):
            return norm[i - 1][0]
    sl = s.lower()
    for v, lbl in norm:           # 3) label esatto (case-insensitive)
        if sl == lbl.lower():
            return v
    subs = [v for v, lbl in norm if sl in lbl.lower()]  # 4) label substring unico
    if len(subs) == 1:
        return subs[0]
    return _INVALID_CHOICE


def consume_pending_step(sender_id: str, dialog_id: str, var: str,
                         value, *, owner_user_id: str) -> dict:
    """Avanza atomicamente un dialogo; un solo consumer può vincere."""
    with _dialog_lock(sender_id, dialog_id):
        return _consume_pending_step_unlocked(
            sender_id, dialog_id, var, value,
            owner_user_id=owner_user_id)


def _consume_pending_step_unlocked(sender_id: str, dialog_id: str, var: str,
                                   value, *, owner_user_id: str) -> dict:
    """Avanza il dialogo registrando il valore raccolto per la variabile `var`.

    Comportamento:
      - se il dialogo non esiste: ritorna `{"ok": False, "error": "...", ...}`.
      - se la variabile non e' lo step corrente o non e' nel dialog: errore.
      - altrimenti: aggiorna `values_collected[var] = value`, incrementa
        `step_index`, e se `step_index >= len(dialog)` setta `completed=True`.
      - ritorna lo stato AGGIORNATO (anche dopo completion).

    Idempotenza: chiamare due volte con lo stesso `var` causa errore al
    secondo perche' `step_index` e' gia' avanzato (la verita' e' lo stato
    su disco, non il chiamante).
    """
    state = load_pending(
        sender_id, dialog_id, owner_user_id=owner_user_id)
    if state is None:
        return {"ok": False, "error": "dialog_not_found",
                "dialog_id": dialog_id}
    if state.get("completed"):
        return {"ok": False, "error": "dialog_already_completed",
                "dialog_id": dialog_id, "values": state.get("values_collected", {})}
    if state.get("cancelled"):
        return {"ok": False, "error": "dialog_cancelled",
                "dialog_id": dialog_id}
    if is_expired(state):
        return {"ok": False, "error": "dialog_expired",
                "dialog_id": dialog_id}
    dialog = state.get("dialog") or []
    idx = int(state.get("step_index") or 0)
    if idx >= len(dialog):
        # Stato inconsistente: idx oltre il dialog ma not completed → forziamo.
        state["completed"] = True
        save_pending(sender_id, dialog_id, state)
        return {"ok": True, "completed": True, "state": state}
    expected = dialog[idx]
    if expected.get("var") != var:
        return {"ok": False,
                "error": "var_mismatch",
                "expected_var": expected.get("var"),
                "got_var": var,
                "step_index": idx}
    # §7.9: se lo step e' una CHOICE, risolvi la risposta (indice "1", value, o
    # label) al `value` canonico. Senza, il grezzo ("1"/"email") finirebbe nel
    # callback (es. forced_object disambiguazione) e non corrisponderebbe a
    # nessuna scelta → rerun rotto. Invalido → non avanza (il dialog resta
    # pending, niente garbage), il caller puo' ri-chiedere.
    _resolved = _resolve_choice_reply(value, expected)
    if _resolved is _INVALID_CHOICE:
        return {"ok": False, "error": "invalid_choice", "dialog_id": dialog_id,
                "step_index": idx, "var": var,
                "choices": (expected.get("schema") or {}).get("choices") or []}
    values = dict(state.get("values_collected") or {})
    values[var] = _resolved
    state["values_collected"] = values
    state["step_index"] = idx + 1
    # Persisti il sender_id NELLO stato (20/6): il callback on_complete
    # (resume_engine_gate / save_credentials_and_resume) legge
    # `state["sender_id"]` per ricaricare il pending — gli executor get_inputs/
    # get_approval salvano lo stato SENZA questo campo (il sender e' la cartella,
    # non un campo). Senza, il resume del gate abortiva «sender_id mancante».
    state.setdefault("sender_id", sender_id)
    if state["step_index"] >= len(dialog):
        state["completed"] = True
        state["completed_at"] = _utc_now_iso()
    save_pending(sender_id, dialog_id, state)
    return {"ok": True,
            "completed": bool(state.get("completed")),
            "step_index": state["step_index"],
            "step_total": len(dialog),
            "state": state}


def cancel_pending(sender_id: str, dialog_id: str, *,
                   owner_user_id: str) -> bool:
    """Marca il dialogo come cancellato. Idempotente: True se esisteva."""
    with _dialog_lock(sender_id, dialog_id):
        return _cancel_pending_unlocked(
            sender_id, dialog_id, owner_user_id=owner_user_id)


def claim_callback_once(sender_id: str, dialog_id: str, nonce: str, *,
                        owner_user_id: str) -> bool:
    """Atomically claim one completed callback carrying the exact nonce.

    This is intentionally narrower than generic dialog completion: existing
    callbacks retain their historical semantics, while security-sensitive
    one-shot callbacks (Tutor handoff) gain an explicit replay barrier.
    """

    return begin_callback_once(
        sender_id, dialog_id, nonce,
        owner_user_id=owner_user_id).get("status") == "claimed"


def begin_callback_once(sender_id: str, dialog_id: str, nonce: str, *,
                        owner_user_id: str) -> dict:
    """Claim a callback or return its durable terminal receipt.

    The callback remains at-most-once: a process crash after the claim is
    reported as in-progress/indeterminate rather than risking a duplicate
    mutating turn.  Normal successes and handled failures are persisted and
    replay their exact receipt on later submissions.
    """

    if not nonce:
        return {"status": "invalid"}
    with _dialog_lock(sender_id, dialog_id):
        state = load_pending(
            sender_id, dialog_id, owner_user_id=owner_user_id)
        if (state is None or not state.get("completed")
                or state.get("cancelled") or is_expired(state)):
            return {"status": "invalid"}
        on_complete = state.get("on_complete") or {}
        stored = str(on_complete.get("nonce") or "")
        if not stored or not hmac.compare_digest(stored, str(nonce)):
            return {"status": "invalid"}
        receipt = state.get("callback_receipt")
        if isinstance(receipt, dict):
            return {"status": "completed", "receipt": receipt}
        if state.get("callback_claimed_at"):
            return {"status": "in_progress"}
        state["callback_claimed_at"] = _utc_now_iso()
        state["callback_state"] = "running"
        save_pending(sender_id, dialog_id, state)
        return {"status": "claimed"}


def complete_callback_once(sender_id: str, dialog_id: str, nonce: str,
                           receipt: dict, *, owner_user_id: str) -> bool:
    """Persist the terminal outbox receipt for an already claimed callback."""

    if not nonce or not isinstance(receipt, dict):
        return False
    # Validate JSON compatibility before entering the critical section.
    try:
        json.dumps(receipt, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    with _dialog_lock(sender_id, dialog_id):
        state = load_pending(
            sender_id, dialog_id, owner_user_id=owner_user_id)
        if state is None or not state.get("callback_claimed_at"):
            return False
        on_complete = state.get("on_complete") or {}
        stored = str(on_complete.get("nonce") or "")
        if not stored or not hmac.compare_digest(stored, str(nonce)):
            return False
        existing = state.get("callback_receipt")
        if isinstance(existing, dict):
            return hmac.compare_digest(
                json.dumps(existing, ensure_ascii=True, sort_keys=True),
                json.dumps(receipt, ensure_ascii=True, sort_keys=True),
            )
        state["callback_receipt"] = dict(receipt)
        state["callback_state"] = "completed"
        state["callback_finished_at"] = _utc_now_iso()
        save_pending(sender_id, dialog_id, state)
        return True


def _cancel_pending_unlocked(sender_id: str, dialog_id: str, *,
                             owner_user_id: str) -> bool:
    state = load_pending(
        sender_id, dialog_id, owner_user_id=owner_user_id)
    if state is None:
        return False
    if state.get("cancelled"):
        return True
    state["cancelled"] = True
    state["cancelled_at"] = _utc_now_iso()
    save_pending(sender_id, dialog_id, state)
    return True


def purge_owner(owner_user_id: str) -> int:
    """Delete pending/receipt files owned by one removed principal.

    Dialog payloads may contain credentials or a still executable callback.
    They are therefore part of user-data deletion, not ordinary TTL cleanup.
    Every candidate is re-read while holding its sender lock before removal.
    """

    owner = str(owner_user_id or "")
    if not owner or not DIALOG_DIR.exists():
        return 0
    removed = 0
    for sender_dir in tuple(DIALOG_DIR.iterdir()):
        if not sender_dir.is_dir():
            continue
        sender_id = sender_dir.name
        for path in tuple(sender_dir.glob("*.json")):
            dialog_id = path.stem
            if not valid_dialog_id(dialog_id):
                continue
            with _dialog_lock(sender_id, dialog_id):
                state = _load_raw(sender_id, dialog_id)
                if (state is None
                        or str(state.get("owner_user_id") or "") != owner):
                    continue
                try:
                    _dialog_path(sender_id, dialog_id).unlink()
                except FileNotFoundError:
                    continue
                removed += 1
        owner_tag = hashlib.sha256(
            ("dialog-owner-v1\0" + owner).encode("utf-8")
        ).hexdigest()[:20]
        for tmp in tuple(sender_dir.glob(f"*.{owner_tag}.tmp")):
            try:
                tmp.unlink()
            except FileNotFoundError:
                continue
            removed += 1
        # Legacy temp files had no owner tag. Remove only those whose complete
        # JSON payload proves ownership; an unrelated partial file is left for
        # generic stale-temp housekeeping rather than guessed across users.
        for tmp in tuple(sender_dir.glob("*.json.tmp")):
            try:
                state = json.loads(tmp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(state.get("owner_user_id") or "") != owner:
                continue
            try:
                tmp.unlink()
            except FileNotFoundError:
                continue
            removed += 1
    return removed


def purge_unscoped() -> int:
    """Ritira stati legacy privi dell'identificatore immutabile dell'owner.

    Non viene tentata alcuna attribuzione da actor, sender o nome: un record
    non attribuibile non deve diventare eseguibile dopo il riuso di tali valori.
    """

    if not DIALOG_DIR.exists():
        return 0
    removed = 0
    for sender_dir in tuple(DIALOG_DIR.iterdir()):
        if not sender_dir.is_dir():
            continue
        sender_id = sender_dir.name
        for path in tuple(sender_dir.glob("*.json")):
            dialog_id = path.stem
            if not valid_dialog_id(dialog_id):
                continue
            with _dialog_lock(sender_id, dialog_id):
                state = _load_raw(sender_id, dialog_id)
                if state is not None and state.get("owner_user_id"):
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                removed += 1
        for tmp in tuple(sender_dir.glob("*.unscoped.tmp")):
            try:
                tmp.unlink()
            except FileNotFoundError:
                continue
            removed += 1
    return removed


def sweep_expired(now_ts: float | None = None) -> list[dict]:
    """Rimuove i dialoghi scaduti e ritorna i descrittori degli ABBANDONATI
    (attivi + scaduti) per la notifica utente da parte dello scheduler.

    Ogni descrittore: `{sender_id, dialog_id, title, age_s, timeout_s}`.
    Comportamento housekeeping (senza descrittore, niente notifica):
      - file corrotti → rimossi (no JSON);
      - dialoghi gia' `completed`/`cancelled` ma scaduti → rimossi (l'utente
        ha gia' risposto/annullato: nulla da notificare).
    Solo i dialoghi ATTIVI scaduti generano un descrittore (= avviso utente).
    Race-safe: ENOENT ignorato. Il caller decide la cadenza.
    """
    if not DIALOG_DIR.exists():
        return []
    if now_ts is None:
        now_ts = time.time()
    abandoned: list[dict] = []
    for sender_dir in DIALOG_DIR.iterdir():
        if not sender_dir.is_dir():
            continue
        sender_id = sender_dir.name
        for p in sender_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                try:
                    p.unlink()  # corrotto: niente zombie
                except OSError:
                    pass
                continue
            owner = str(d.get("owner_user_id") or "").strip()
            if not owner:
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            terminal = bool(d.get("completed") or d.get("cancelled"))
            if not is_expired(d, now_ts):
                continue
            started = _started_ts(d)
            try:
                p.unlink()
            except OSError:
                continue
            if terminal:
                continue  # rimosso per housekeeping, nessuna notifica
            abandoned.append({
                "sender_id": sender_id,
                "dialog_id": d.get("dialog_id") or p.stem,
                "title": d.get("title") or "",
                "actor": d.get("actor") or "",
                "channel": d.get("channel") or "",
                "owner_user_id": owner,
                "age_s": int(now_ts - started) if started else 0,
                "timeout_s": int(d.get("timeout_s") or DEFAULT_TTL_S),
            })
    return abandoned


def cleanup_expired(now_ts: float | None = None) -> int:
    """Compat: numero di dialoghi ATTIVI scaduti rimossi. Housekeeping di
    corrotti/terminali avviene comunque. Vedi `sweep_expired` per i dettagli
    (descrittori per la notifica utente)."""
    return len(sweep_expired(now_ts))

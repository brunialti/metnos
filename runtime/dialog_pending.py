"""dialog_pending — storage di stato per dialoghi `get_inputs` (ADR 0090).

Modulo deterministico (CLAUDE.md §7.9): nessuna chiamata LLM. Un dialogo
e' un walk sequenziale fra step (var/prompt/schema). Lo stato vive su
disco perche' il dialogo attraversa piu' turni utente sul canale (le
risposte arrivano una alla volta da Telegram, oppure tutte insieme via
form HTTP). Storage per `<sender_id>` (chat_id Telegram, device_id HTTP,
oppure "host" come fallback).

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
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)

# Path canonico esposto come modulo-level per facilitare test (monkeypatch).
DIALOG_DIR = Path.home() / ".local" / "share" / "metnos" / "get_inputs"

# Soft TTL: scaduti dopo 1h senza progressi (override per dialogo via
# `timeout_s`). Lavora insieme al cleanup_expired chiamato a inizio turno.
DEFAULT_TTL_S = 3600


# ── Helper interni ────────────────────────────────────────────────────

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_sender(sender_id: str) -> str:
    """Sanitizza il sender_id per usarlo come nome di cartella."""
    if not sender_id:
        return "_unknown"
    return _SAFE_RE.sub("_", str(sender_id))


def _sender_dir(sender_id: str) -> Path:
    return DIALOG_DIR / _safe_sender(sender_id)


def _dialog_path(sender_id: str, dialog_id: str) -> Path:
    return _sender_dir(sender_id) / f"{dialog_id}.json"


def _utc_now_iso() -> str:
    """ISO-8601 UTC, senza microsecondi: comodo per debug e diff."""
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


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
    sd = _sender_dir(sender_id)
    sd.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(sd, 0o700)
    except OSError as ex:
        log.debug("chmod 0700 fallito su %s: %s", sd, ex)
    p = _dialog_path(sender_id, dialog_id)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError as ex:
        log.debug("chmod 0600 fallito su %s: %s", p, ex)
    return p


def load_pending(sender_id: str, dialog_id: str) -> dict | None:
    """Carica lo stato del dialogo. Ritorna None se non esiste o e' corrotto."""
    p = _dialog_path(sender_id, dialog_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        log.warning("dialog_pending corrotto %s: %s", p, ex)
        return None


def list_pending(sender_id: str) -> list[dict]:
    """Lista i dialoghi pendenti per il sender (non completati e non cancellati).

    Utile al daemon per riconoscere uno stato attivo all'arrivo di un
    messaggio dell'utente. Ordinato per `started_at` ascending (il piu'
    vecchio prima); i risultati corrotti vengono saltati silenziosamente.
    """
    sd = _sender_dir(sender_id)
    if not sd.exists():
        return []
    out: list[dict] = []
    for p in sd.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("completed") or d.get("cancelled"):
            continue
        out.append(d)
    out.sort(key=lambda d: d.get("started_at", ""))
    return out


def consume_pending_step(sender_id: str, dialog_id: str, var: str,
                          value) -> dict:
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
    state = load_pending(sender_id, dialog_id)
    if state is None:
        return {"ok": False, "error": "dialog_not_found",
                "dialog_id": dialog_id}
    if state.get("completed"):
        return {"ok": False, "error": "dialog_already_completed",
                "dialog_id": dialog_id, "values": state.get("values_collected", {})}
    if state.get("cancelled"):
        return {"ok": False, "error": "dialog_cancelled",
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
    values = dict(state.get("values_collected") or {})
    values[var] = value
    state["values_collected"] = values
    state["step_index"] = idx + 1
    if state["step_index"] >= len(dialog):
        state["completed"] = True
        state["completed_at"] = _utc_now_iso()
    save_pending(sender_id, dialog_id, state)
    return {"ok": True,
            "completed": bool(state.get("completed")),
            "step_index": state["step_index"],
            "step_total": len(dialog),
            "state": state}


def cancel_pending(sender_id: str, dialog_id: str) -> bool:
    """Marca il dialogo come cancellato. Idempotente: True se esisteva."""
    state = load_pending(sender_id, dialog_id)
    if state is None:
        return False
    if state.get("cancelled"):
        return True
    state["cancelled"] = True
    state["cancelled_at"] = _utc_now_iso()
    save_pending(sender_id, dialog_id, state)
    return True


def cleanup_expired(now_ts: float | None = None) -> int:
    """Rimuove i dialoghi scaduti (timeout_s o DEFAULT_TTL_S dal `started_at`).

    Ritorna il numero di file rimossi. Sicuro chiamato in concorrenza:
    rimozioni race-safe (ENOENT swallowed). Il caller (channel daemon o
    scheduler) decide la cadenza.
    """
    if not DIALOG_DIR.exists():
        return 0
    if now_ts is None:
        now_ts = time.time()
    n_removed = 0
    for sender_dir in DIALOG_DIR.iterdir():
        if not sender_dir.is_dir():
            continue
        for p in sender_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # File corrotto: rimuovilo per non lasciare zombie.
                try:
                    p.unlink()
                    n_removed += 1
                except OSError:
                    pass
                continue
            ttl = int(d.get("timeout_s") or DEFAULT_TTL_S)
            started_iso = d.get("started_at") or ""
            try:
                started_dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
                started_ts = started_dt.timestamp()
            except (ValueError, AttributeError):
                started_ts = 0.0
            if started_ts and now_ts - started_ts > ttl:
                try:
                    p.unlink()
                    n_removed += 1
                except OSError:
                    pass
    return n_removed

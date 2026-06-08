"""dialog_pending — storage di stato per dialoghi `get_inputs` (ADR 0090).

Modulo deterministico (the design guide §7.9): nessuna chiamata LLM. Un dialogo
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


def _started_ts(payload: dict) -> float:
    """Epoch del `started_at` ISO del dialogo, 0.0 se mancante/illeggibile."""
    iso = payload.get("started_at") or ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def default_timeout_for(dialog: list | None,
                        on_complete: dict | None = None) -> int:
    """TTL di default in base alla FORMA del dialogo (§7.3, non per-caller).

    Regola: form (>=2 step) o dialoghi di credenziali → `FORM_TTL_S` (l'utente
    deve digitare/compilare); dialoghi semplici (1 step si/no/scelta) →
    `DEFAULT_TTL_S` (chiusura rapida ~1 min). Un `timeout_s` esplicito passato
    dal chiamante resta sovrano (questa funzione e' solo il default).
    """
    steps = dialog or []
    is_cred = (isinstance(on_complete, dict)
               and on_complete.get("type") == "save_credentials_and_resume")
    if not is_cred:
        for s in steps:
            if isinstance(s, dict) and (s.get("schema") or {}).get("kind") == "credentials":
                is_cred = True
                break
    if is_cred or len(steps) >= 2:
        return FORM_TTL_S
    return DEFAULT_TTL_S


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
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError as ex:
        log.debug("chmod 0600 fallito su %s: %s", tmp, ex)
    os.replace(tmp, p)
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
        if is_expired(d):
            continue  # scaduto: non e' piu' "attivo" → non consumare la query
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
                "age_s": int(now_ts - started) if started else 0,
                "timeout_s": int(d.get("timeout_s") or DEFAULT_TTL_S),
            })
    return abandoned


def cleanup_expired(now_ts: float | None = None) -> int:
    """Compat: numero di dialoghi ATTIVI scaduti rimossi. Housekeeping di
    corrotti/terminali avviene comunque. Vedi `sweep_expired` per i dettagli
    (descrittori per la notifica utente)."""
    return len(sweep_expired(now_ts))

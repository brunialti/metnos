"""Aporia (ἀπορία) — riconoscimento del vicolo cieco (ADR 0161 ext, pentade).

    «Aporia (ἀπορία): perplessita', vicolo cieco. Quando neanche Pronoia
     puo' salvare, Aporia riconosce ONESTAMENTE il limite, classifica
     la lacuna, e suggerisce azione concreta per uscire dall'impasse.»

Quinta divinita' della pentade Praxis Engine:
  Mētis   (Μῆτις)   propone framework            → praxis_propose.py
  Noûs    (νοῦς)    esegue deterministico        → praxis_executor.py
  Praxis  (πρᾶξις)  ricorda + auto-promote       → praxis.py
  Pronoia (Πρόνοια) provvidenza recovery         → pronoia.py
  Aporia  (ἀπορία)  vicolo cieco onesto + evol.  → questo modulo

Pattern evolutivo:
  out_of_scope detected → Aporia classify root_cause → suggest_action +
  log lacuna in aporiae.sqlite. Quando utente risolve la lacuna,
  la query torna alla cascata Praxis normale → eventualmente cached
  come skill ACTIVE (auto-promote dopo 2 obs).

Categorie root_cause (4 ortogonali):
  - user_action_required:  utente deve agire (location share, dialog input)
  - missing_executor:      catalog gap → synth via request_new_executor
  - missing_skill:         skill imported non abilitata o non installata
  - missing_data:          corpus/index non costruito (build *_indices)

Determinismo §7.9: classificazione testuale + suggested_action template,
no LLM richiamato in Aporia stessa.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import config as _C
except Exception:
    from runtime import config as _C  # pragma: no cover

log = logging.getLogger(__name__)

DB_PATH = _C.PATH_USER_DATA / "aporiae.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aporiae (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  intent_hash     TEXT NOT NULL,
  intent_sig      TEXT NOT NULL,
  root_cause      TEXT NOT NULL,
  query_sample    TEXT NOT NULL,
  error_text      TEXT,
  suggested_action TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open',
  n_occurrences   INTEGER NOT NULL DEFAULT 1,
  n_attempts      INTEGER NOT NULL DEFAULT 1,
  ts_first        TEXT NOT NULL,
  ts_last         TEXT NOT NULL,
  ts_resolved     TEXT,
  UNIQUE(intent_hash, root_cause)
);
CREATE INDEX IF NOT EXISTS idx_aporiae_status ON aporiae(status, n_occurrences DESC);
CREATE INDEX IF NOT EXISTS idx_aporiae_intent ON aporiae(intent_hash);
"""

ROOT_CAUSES = (
    "user_action_required",  # location share, dialog input, permission
    "missing_executor",       # tool atomico non in catalog
    "missing_skill",          # skill imported non caricata/abilitata
    "missing_data",           # index/corpus non costruito
    "unknown",                # fallback
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_root_cause(error_text: str, query: str) -> str:
    """Classifica root cause deterministico (§7.9, no LLM)."""
    et = (error_text or "").lower()
    q = (query or "").lower()

    # User action: location share Telegram, dialog needs_inputs
    if ("no location received yet" in et
            or "condividi una posizione" in et
            or "needs_inputs" in et):
        return "user_action_required"

    # Missing data: index/corpus non costruito (build *_indices)
    if ("no indexed dirs found" in et or "index missing" in et
            or "index_missing" in et):
        return "missing_data"

    # Missing skill: query menziona provider esterno (drive/gmail/calendar/github)
    # ma Mētis ha fallito su tool LOCALE (find_files/find_dirs) per missing arg/path.
    # Pattern allargato: include "argomento obbligatorio" + "percorso non trovato"
    # + "no such file" — qualsiasi sintomo di skill provider non caricata.
    provider_markers = ("drive", "gmail", "calendar google", "github",
                         "google drive", "g suite", "workspace")
    if any(m in q for m in provider_markers):
        if ("percorso non trovato" in et or "path not found" in et
                or "argomento obbligatorio" in et
                or "no such file" in et
                or "directory non esistente" in et):
            return "missing_skill"

    # Missing executor: capability totale assente
    # detectable se Mētis/Pronoia non hanno proposto pipeline efficace
    # (es. temperatura/sensori senza admin response)
    sensor_markers = ("temperatur", "thermal", "sensor")
    if any(m in q for m in sensor_markers):
        if "values" in et or "no executor" in et:
            return "missing_executor"

    return "unknown"


def suggest_action(root_cause: str, query: str, error_text: str) -> str:
    """Genera suggested_action user-actionable. Deterministico §7.9."""
    if root_cause == "user_action_required":
        if "location" in error_text.lower():
            return ("Condividi la tua posizione via Telegram con il bottone "
                    "📎 → Posizione. Poi ripeti la query.")
        return ("Sistema richiede input utente. Completa il dialog "
                "pendente o ripeti la query.")
    if root_cause == "missing_data":
        return ("Costruisci l'index del corpus: "
                "`create_<obj>_indices(base_path=<percorso>)`. Esempio: "
                "`create_images_indices(base_path='~/Immagini')`.")
    if root_cause == "missing_skill":
        q = query.lower()
        provider = "google-workspace" if any(m in q for m in ("drive", "gmail", "calendar")) \
            else "github" if "github" in q else "<provider>"
        return (f"Abilita skill {provider}: "
                f"`metnos-cli skills enable {provider}`. "
                f"Verifica autenticazione: "
                f"`metnos-cli credentials authenticate {provider}`.")
    if root_cause == "missing_executor":
        return ("Sintetizza executor mancante: "
                "`request_new_executor(expected_name='<verb>_<object>', "
                "intent='<descrizione>')`. Praxis lo imparera' "
                "automaticamente dopo 2 esecuzioni successful.")
    return ("Causa non classificata. Apri ticket o consulta "
            "`/admin/aporiae` per pattern simili.")


class AporiaStore:
    """Registry lacune. Schema persistente in praxis_user_data/aporiae.sqlite."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def record_lacuna(self, *, intent_hash: str, intent_sig: str,
                       query: str, error_text: str,
                       root_cause: str, suggested_action: str) -> int:
        """Upsert lacuna. Incrementa n_occurrences se gia' esiste."""
        now = _utcnow()
        try:
            cur = self.conn.execute(
                "SELECT id, n_occurrences FROM aporiae "
                "WHERE intent_hash = ? AND root_cause = ?",
                (intent_hash, root_cause),
            )
            row = cur.fetchone()
            if row:
                lid, n = row
                self.conn.execute(
                    "UPDATE aporiae SET n_occurrences = ?, ts_last = ?, "
                    "query_sample = ? WHERE id = ?",
                    (n + 1, now, query[:200], lid),
                )
                self.conn.commit()
                return lid
            cur = self.conn.execute(
                "INSERT INTO aporiae(intent_hash, intent_sig, root_cause, "
                "query_sample, error_text, suggested_action, "
                "ts_first, ts_last) VALUES (?,?,?,?,?,?,?,?)",
                (intent_hash, intent_sig, root_cause,
                 query[:200], (error_text or "")[:300],
                 suggested_action, now, now),
            )
            self.conn.commit()
            return cur.lastrowid or -1
        except Exception as ex:
            log.warning("aporia.record_lacuna failed: %r", ex)
            return -1

    def mark_resolved(self, lid: int) -> None:
        try:
            self.conn.execute(
                "UPDATE aporiae SET status='resolved', ts_resolved=? "
                "WHERE id = ?", (_utcnow(), lid),
            )
            self.conn.commit()
        except Exception as ex:
            log.warning("aporia.mark_resolved failed: %r", ex)

    def list_open(self, limit: int = 100) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id, intent_sig, root_cause, query_sample, "
            "suggested_action, n_occurrences, ts_first, ts_last "
            "FROM aporiae WHERE status='open' "
            "ORDER BY n_occurrences DESC, ts_last DESC LIMIT ?", (limit,))
        cols = ["id", "intent_sig", "root_cause", "query_sample",
                "suggested_action", "n_occurrences", "ts_first", "ts_last"]
        return [dict(zip(cols, r)) for r in cur]

    def stats(self) -> dict:
        cur = self.conn.execute(
            "SELECT root_cause, COUNT(*), SUM(n_occurrences) "
            "FROM aporiae WHERE status='open' GROUP BY root_cause")
        by_cause = {r[0]: {"count": r[1], "occurrences": r[2]}
                     for r in cur}
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM aporiae WHERE status='resolved'")
        n_resolved = cur.fetchone()[0]
        return {"by_root_cause": by_cause, "n_resolved": n_resolved}


# Module-level singleton lazy
_INSTANCE: Optional[AporiaStore] = None


def get_store() -> AporiaStore:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AporiaStore()
    return _INSTANCE


def record(query: str, intent_hash: str, intent_sig: str,
            error_text: str) -> dict:
    """Helper: classify + record + ritorna lacuna info per final_message."""
    rc = classify_root_cause(error_text, query)
    sa = suggest_action(rc, query, error_text)
    store = get_store()
    lid = store.record_lacuna(
        intent_hash=intent_hash, intent_sig=intent_sig,
        query=query, error_text=error_text,
        root_cause=rc, suggested_action=sa,
    )
    return {
        "lacuna_id": lid,
        "root_cause": rc,
        "suggested_action": sa,
    }

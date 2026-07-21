"""store_bootstrap — registra gli store di PRODUZIONE (store.py) all'avvio.

Chiamato dal loader (`load_catalog`), idempotente. Registrare uno store ATTIVA
i CRUD universali `find/write/delete_entries` su di esso (gate di dormienza in
`engine/routing_pool`). Backend SEMPRE sqlite (path da `config`). §7.9.

Direzione 17/6/2026: il flusso manutenzione issue accede al suo store QA via i
CRUD UNIVERSALI (`*_entries`) + `compare_entries` per la similarità, invece di
executor issue-specifici (find_issues_db/read_issues/write_issues, in ritiro).
"""
from __future__ import annotations

import logging

import config as C
import store as _store
from store import Schema, TEXT, INT

_LOG = logging.getLogger(__name__)

# Store QA del flusso manutenzione issue. STESSO DB della vecchia memoria ad-hoc
# (UNIQUE(repo,issue_number) → upsert ON CONFLICT). L'`embedding` NON e' nello
# schema: la similarita' e' compito di `compare_entries` (re-embed dal testo).
_ISSUE_QA = Schema(
    "issue_qa",
    {"repo": TEXT, "issue_number": INT, "title": TEXT, "classification": TEXT,
     "status": TEXT, "draft_reply": TEXT, "accepted_reply": TEXT,
     "posted_at": INT, "question_text": TEXT},
    primary_key=("repo", "issue_number"),
)

# (nome store, schema, path, insert_defaults) — estendere qui per nuovi store.
# insert_defaults = valore INIZIALE dei campi assenti (FASE 1 detect: le issue
# entrano sempre in PENDING con status='new', deterministico da config — non
# dipende dall'arg-filling del proposer). Non tocca FASE 2/3 (impostano status).
_BUILTIN_STORES = (
    ("github_issue_qa", _ISSUE_QA, C.PATH_USER_DATA / "github_issue_qa.sqlite",
     {"status": "new"}),
)


def register_builtin_stores() -> None:
    """Idempotente. Best-effort: un errore di registrazione NON blocca il boot."""
    for name, schema, path, insert_defaults in _BUILTIN_STORES:
        try:
            if not _store.is_registered(name):
                _store.register(schema, name=name, path=path,
                                insert_defaults=insert_defaults)
        except Exception as e:  # noqa: BLE001 — boot resiliente
            _LOG.warning("store_bootstrap: register %r fallito: %r", name, e)

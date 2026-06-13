"""github_issue_qa_store — Q&A store + semantic dedup search (Fase D).

Persistenza permanente dei (question, reply, embedding) per ogni issue
risolta (auto o gate-approved). Re-utilizzata dal watcher al passo
"Stage 1a semantic dedup search" per decidere se rispondere in
autonomia (4-AND safety, vedi `jobs/github_dedup.py`).

Embedding BGE-M3 1024d float32 → blob (4096 byte/riga).
Cosine search via numpy in memoria: cap previsto ~10k issue/repo,
fattibile senza estensioni sqlite.

Storage: `~/.local/share/metnos/github_issue_qa.sqlite`.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import numpy as np

import config as _C  # §7.11 — rispetta METNOS_USER_DATA
DB_PATH = _C.PATH_USER_DATA / "github_issue_qa.sqlite"

EMBEDDING_DIM = 1024  # BGE-M3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS issue_qa (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    classification TEXT,
    question_text TEXT,
    question_embedding BLOB,
    accepted_reply TEXT,
    posted_at INTEGER,
    auto_replied INTEGER DEFAULT 0,
    related_refs TEXT,
    user_satisfied INTEGER,
    cost_usd REAL,
    status TEXT DEFAULT 'new',
    draft_reply TEXT,
    title TEXT,
    UNIQUE(repo, issue_number)
);
CREATE INDEX IF NOT EXISTS idx_qa_repo ON issue_qa(repo);
"""

# Colonne aggiunte dopo lo schema iniziale (maintenance flow via executor):
# migrazione idempotente per i db esistenti (ALTER ADD COLUMN se mancano).
_MIGRATE_COLS = (("status", "TEXT DEFAULT 'new'"), ("draft_reply", "TEXT"), ("title", "TEXT"))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Idempotent CREATE TABLE + INDEX + migrazione colonne maintenance."""
    con = _connect()
    try:
        con.executescript(_SCHEMA)
        existing = {r[1] for r in con.execute("PRAGMA table_info(issue_qa)")}
        for col, decl in _MIGRATE_COLS:
            if col not in existing:
                con.execute(f"ALTER TABLE issue_qa ADD COLUMN {col} {decl}")
        # Indice su status DOPO la migrazione (la colonna ora esiste).
        con.execute("CREATE INDEX IF NOT EXISTS idx_qa_status ON issue_qa(repo, status)")
        con.commit()
    finally:
        con.close()


def upsert_treatment(
    repo: str,
    issue_number: int,
    *,
    title: str | None = None,
    classification: str | None = None,
    status: str | None = None,
    draft_reply: str | None = None,
    accepted_reply: str | None = None,
    question_text: str | None = None,
    embedding: "np.ndarray | None" = None,
    posted_at: int | None = None,
    auto_replied: bool | None = None,
) -> int:
    """Upsert PARZIALE di un record di trattamento: crea (repo, issue_number)
    se assente, poi aggiorna SOLO i campi forniti (non-None), preservando il
    resto. Ritorna row id. Usato dall'executor `write_issues`.

    Semantica della macchina a stati (github_maintenance_flow):
    - status='approved' SENZA accepted_reply esplicito → la bozza esistente
      viene promossa: accepted_reply = COALESCE(accepted_reply, draft_reply).
      («approva» = la bozza diventa la risposta accettata.)
    - status='posted' SENZA posted_at esplicito → posted_at = now, solo se
      non gia' valorizzato (idempotente: il primo post fissa il timestamp).
    """
    init_db()
    con = _connect()
    try:
        con.execute(
            "INSERT OR IGNORE INTO issue_qa (repo, issue_number, status) "
            "VALUES (?,?,COALESCE(?, 'new'))",
            (repo, int(issue_number), status),
        )
        sets: list[str] = []
        vals: list[Any] = []
        for col, v in (("title", title), ("classification", classification),
                       ("status", status), ("draft_reply", draft_reply),
                       ("accepted_reply", accepted_reply),
                       ("question_text", question_text)):
            if v is not None:
                sets.append(f"{col}=?")
                vals.append(v)
        if status == "approved" and accepted_reply is None:
            # Promozione bozza→accettata (vedi docstring). L'accepted_reply
            # esplicita (ramo sopra) vince per costruzione.
            sets.append("accepted_reply=COALESCE(accepted_reply, draft_reply)")
        if embedding is not None:
            sets.append("question_embedding=?")
            vals.append(_embedding_to_blob(embedding))
        if posted_at is not None:
            sets.append("posted_at=?")
            vals.append(int(posted_at))
        elif status == "posted":
            sets.append("posted_at=COALESCE(posted_at, ?)")
            vals.append(int(time.time()))
        if auto_replied is not None:
            sets.append("auto_replied=?")
            vals.append(1 if auto_replied else 0)
        if sets:
            vals.extend([repo, int(issue_number)])
            con.execute(
                f"UPDATE issue_qa SET {', '.join(sets)} "
                "WHERE repo=? AND issue_number=?", vals,
            )
        con.commit()
        row = con.execute(
            "SELECT id FROM issue_qa WHERE repo=? AND issue_number=?",
            (repo, int(issue_number)),
        ).fetchone()
        return int(row["id"]) if row else -1
    finally:
        con.close()


def list_records(
    repo: str | None = None,
    status: "str | list[str] | None" = None,
    numbers: "list[int] | None" = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Record di trattamento filtrati per repo/status/numbers. Ritorna
    list[dict] SENZA il blob embedding (pipeable). Usato da `read_issues`."""
    init_db()
    con = _connect()
    try:
        q = ("SELECT repo, issue_number, title, classification, status, "
             "draft_reply, accepted_reply, posted_at, auto_replied "
             "FROM issue_qa WHERE 1=1")
        vals: list[Any] = []
        if repo:
            q += " AND repo=?"
            vals.append(repo)
        if status:
            sl = status if isinstance(status, (list, tuple)) else [status]
            q += f" AND status IN ({','.join('?' * len(sl))})"
            vals.extend(sl)
        if numbers:
            nl = numbers if isinstance(numbers, (list, tuple)) else [numbers]
            q += f" AND issue_number IN ({','.join('?' * len(nl))})"
            vals.extend(int(n) for n in nl)
        q += " ORDER BY issue_number DESC LIMIT ?"
        vals.append(int(limit))
        return [dict(r) for r in con.execute(q, vals).fetchall()]
    finally:
        con.close()


def _embedding_to_blob(emb: np.ndarray) -> bytes:
    if emb is None:
        return b""
    arr = np.asarray(emb, dtype=np.float32).reshape(-1)
    if arr.shape[0] != EMBEDDING_DIM:
        raise ValueError(
            f"embedding dim != {EMBEDDING_DIM} (got {arr.shape[0]})"
        )
    return arr.tobytes()


def _blob_to_embedding(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.shape[0] != EMBEDDING_DIM:
        return None
    return arr


def insert(
    repo: str,
    issue_number: int,
    classification: str | None,
    question_text: str,
    embedding: np.ndarray | None,
    accepted_reply: str,
    related_refs: list[dict[str, Any]] | None = None,
    cost_usd: float = 0.0,
    auto_replied: bool = False,
    posted_at: int | None = None,
) -> int:
    """Inserisce o aggiorna (repo, issue_number). Ritorna row id.
    UNIQUE(repo, issue_number) → ON CONFLICT UPDATE."""
    init_db()
    blob = _embedding_to_blob(embedding) if embedding is not None else b""
    refs_json = json.dumps(related_refs or [], ensure_ascii=False)
    ts = int(posted_at) if posted_at is not None else int(time.time())
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO issue_qa "
            "(repo, issue_number, classification, question_text, "
            " question_embedding, accepted_reply, posted_at, auto_replied, "
            " related_refs, user_satisfied, cost_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(repo, issue_number) DO UPDATE SET "
            " classification=excluded.classification, "
            " question_text=excluded.question_text, "
            " question_embedding=excluded.question_embedding, "
            " accepted_reply=excluded.accepted_reply, "
            " posted_at=excluded.posted_at, "
            " auto_replied=excluded.auto_replied, "
            " related_refs=excluded.related_refs, "
            " cost_usd=excluded.cost_usd",
            (
                repo, int(issue_number), classification, question_text,
                blob, accepted_reply, ts, 1 if auto_replied else 0,
                refs_json, None, float(cost_usd),
            ),
        )
        con.commit()
        # Get id (lastrowid is INSERT-only; query back for UPDATE case).
        if cur.lastrowid:
            return int(cur.lastrowid)
        cur2 = con.execute(
            "SELECT id FROM issue_qa WHERE repo=? AND issue_number=?",
            (repo, int(issue_number)),
        )
        row = cur2.fetchone()
        return int(row["id"]) if row else -1
    finally:
        con.close()


def find_similar(
    repo: str,
    query_embedding: np.ndarray,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Cosine search via numpy in-memory. Ritorna lista di dict
    `{ref, similarity, accepted_reply, user_satisfied, classification}`
    in ordine decrescente.

    `ref` = stringa stile "{repo}#{issue_number}".
    Embeddings sono assunti gia' L2-normalizzati (BGE-M3 default).
    """
    init_db()
    if query_embedding is None:
        return []
    qe = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
    if qe.shape[0] != EMBEDDING_DIM:
        return []
    con = _connect()
    try:
        cur = con.execute(
            "SELECT id, issue_number, question_embedding, accepted_reply, "
            "user_satisfied, classification "
            "FROM issue_qa WHERE repo=? AND question_embedding IS NOT NULL",
            (repo,),
        )
        rows = cur.fetchall()
    finally:
        con.close()
    if not rows:
        return []
    embs: list[np.ndarray] = []
    valid_rows: list[sqlite3.Row] = []
    for r in rows:
        e = _blob_to_embedding(r["question_embedding"])
        if e is None:
            continue
        embs.append(e)
        valid_rows.append(r)
    if not embs:
        return []
    mat = np.stack(embs, axis=0)  # (N, 1024)
    # Normalize defensively (no-op se gia' L2).
    qn = np.linalg.norm(qe)
    if qn > 0:
        qe = qe / qn
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat_n = mat / norms
    sims = mat_n @ qe  # (N,)
    order = np.argsort(sims)[::-1][:int(top_n)]
    out: list[dict[str, Any]] = []
    for idx in order:
        r = valid_rows[int(idx)]
        out.append({
            "ref": f"{repo}#{r['issue_number']}",
            "issue_number": int(r["issue_number"]),
            "similarity": float(sims[int(idx)]),
            "accepted_reply": r["accepted_reply"] or "",
            "user_satisfied": (
                int(r["user_satisfied"])
                if r["user_satisfied"] is not None else None
            ),
            "classification": r["classification"] or "",
        })
    return out


def mark_unsatisfied(repo: str, issue_number: int) -> None:
    """Marca user_satisfied=0 (la reply precedente ha generato lamenti)."""
    init_db()
    con = _connect()
    try:
        con.execute(
            "UPDATE issue_qa SET user_satisfied=0 "
            "WHERE repo=? AND issue_number=?",
            (repo, int(issue_number)),
        )
        con.commit()
    finally:
        con.close()


def mark_satisfied(repo: str, issue_number: int) -> None:
    """Marca user_satisfied=1 (issue closed, nessun follow-up negativo)."""
    init_db()
    con = _connect()
    try:
        con.execute(
            "UPDATE issue_qa SET user_satisfied=1 "
            "WHERE repo=? AND issue_number=?",
            (repo, int(issue_number)),
        )
        con.commit()
    finally:
        con.close()

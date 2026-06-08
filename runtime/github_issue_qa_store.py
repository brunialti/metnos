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
from pathlib import Path
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
    UNIQUE(repo, issue_number)
);
CREATE INDEX IF NOT EXISTS idx_qa_repo ON issue_qa(repo);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Idempotent CREATE TABLE + INDEX."""
    con = _connect()
    try:
        con.executescript(_SCHEMA)
        con.commit()
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

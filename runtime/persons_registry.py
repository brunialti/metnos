#!/usr/bin/env python3
"""persons_registry — named-person registry for face search by name.

Storage layer only (PR1). PR2 will glue this to `runtime/face_embedding.py`
and add the `*_persons_indices` executors.

Design fissato (Roberto):
- Slug case-insensitive per evitare sdoppiamenti ("Matteo"/"matteo"/"MATTEO").
- Display name preservato dal primo enroll (case successivi non sovrascrivono).
- Enrollment incrementale (mode="add") oppure totale (mode="replace").
- Match: top-k cosine fra TUTTI gli example della persona (no centroid).
- Niente rename: cancella e ri-enrolla.

Determinismo §7.9: zero LLM, zero ML qui (gli embedding arrivano gia' calcolati).

Schema:
    persons(slug PK, name, created_at, updated_at, n_examples, notes)
    person_examples(id PK, person_slug FK CASCADE, image_path, face_box,
                    embedding BLOB, embedding_dim, sha256, created_at)
    UNIQUE(person_slug, sha256, face_box)  -- dedupe idempotente
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "metnos" / "persons.sqlite"
EMBEDDING_DIM = 512
WARN_EXAMPLES_PER_PERSON = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
  slug          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  n_examples    INTEGER NOT NULL DEFAULT 0,
  notes         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS person_examples (
  id            INTEGER PRIMARY KEY,
  person_slug   TEXT NOT NULL REFERENCES persons(slug) ON DELETE CASCADE,
  image_path    TEXT NOT NULL,
  face_box      TEXT NOT NULL,
  embedding     BLOB NOT NULL,
  embedding_dim INTEGER NOT NULL DEFAULT 512,
  sha256        TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS person_examples_slug_idx
  ON person_examples(person_slug);
CREATE UNIQUE INDEX IF NOT EXISTS person_examples_dedupe_idx
  ON person_examples(person_slug, sha256, face_box);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(name: str) -> str:
    """NFKD + ASCII strip + lowercase + spaces->_, drop punctuation."""
    if name is None:
        raise ValueError("name is None")
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    # Trattare `-` e `/` come separatori di parola: "Jean-Luc" → "jean_luc"
    # cosi' la query "Jean" matcha via token-anywhere (Roberto, decisione PR1).
    s = re.sub(r"[-/]", " ", s)
    s = s.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    if not s or s.strip("_") == "":
        raise ValueError(f"name {name!r} produces empty slug")
    return s


def _embedding_to_bytes(emb) -> tuple[bytes, int]:
    """Marshal ndarray|bytes -> bytes; returns (blob, dim).

    bytes input is assumed already float32 little-endian.
    """
    if isinstance(emb, (bytes, bytearray, memoryview)):
        blob = bytes(emb)
        if len(blob) % 4 != 0:
            raise ValueError(f"embedding bytes len {len(blob)} not float32-aligned")
        dim = len(blob) // 4
    else:
        arr = np.asarray(emb, dtype=np.float32)
        if arr.ndim != 1:
            raise ValueError(f"embedding must be 1-D, got shape {arr.shape}")
        blob = arr.tobytes()
        dim = arr.size
    if dim != EMBEDDING_DIM:
        raise ValueError(
            f"embedding dim {dim} != expected {EMBEDDING_DIM}"
        )
    return blob, dim


def _bytes_to_embedding(blob: bytes, dim: int) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.size != dim:
        raise ValueError(f"stored embedding size {arr.size} != dim {dim}")
    return arr


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n <= 0.0:
        return v
    return (v / n).astype(np.float32, copy=False)


def _face_box_json(box) -> str:
    if isinstance(box, str):
        # Trust caller canonicalized; round-trip via json to normalize.
        try:
            parsed = json.loads(box)
        except json.JSONDecodeError as e:
            raise ValueError(f"face_box not JSON: {e}") from e
        return json.dumps(list(parsed), separators=(",", ":"))
    return json.dumps([int(v) for v in box], separators=(",", ":"))


class PersonsRegistry:
    """SQLite-backed registry. Thread-safe via internal lock on writes."""

    def __init__(self, db_path: Path | None = None):
        # Lookup DEFAULT_DB_PATH at call time (NOT at function-def time) so
        # tests can monkeypatch the module attribute and have it honored
        # by code paths that don't pass `db_path` explicitly.
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use BEGIN/COMMIT explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # -- write ops ---------------------------------------------------------

    def enroll(
        self,
        *,
        name: str,
        image_path: str,
        face_box,
        embedding,
        sha256: str,
        mode: str = "add",
        dedupe_cosine_threshold: float = 0.95,
    ) -> dict:
        """Enroll un esempio per una persona.

        Dedup a tre livelli:
        1. (sha256, face_box) — stesso file fisico, stessa bbox → skip idempotente.
        2. cosine similarity vs embedding esistenti — se max(cosine) >=
           `dedupe_cosine_threshold` (default 0.95), e' una posa quasi-identica:
           non aggiunge informazione discriminante, skip con
           `{added: False, reason: "near_duplicate_embedding", max_cosine}`.
        3. Tutto il resto → INSERT.

        Threshold 0.95: bilanciamento empirico — mantiene viste/angolazioni
        diverse della stessa persona (cosine ~0.7-0.9), filtra solo dup
        praticamente identici. Pass `dedupe_cosine_threshold=1.0` per
        disabilitare il dedup semantico.
        """
        if not name or not str(name).strip():
            raise ValueError("name must be non-empty")
        if mode not in ("add", "replace"):
            raise ValueError(f"mode must be 'add' or 'replace', got {mode!r}")
        slug = slugify(name)
        blob, dim = _embedding_to_bytes(embedding)
        box_s = _face_box_json(face_box)
        now = _utc_now_iso()

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            try:
                row = cur.execute(
                    "SELECT name FROM persons WHERE slug=?", (slug,)
                ).fetchone()
                if row is None:
                    cur.execute(
                        "INSERT INTO persons(slug,name,created_at,updated_at,"
                        "n_examples,notes) VALUES (?,?,?,?,0,'')",
                        (slug, name, now, now),
                    )
                    display = name
                else:
                    display = row["name"]  # preserve first-enroll display

                if mode == "replace":
                    cur.execute(
                        "DELETE FROM person_examples WHERE person_slug=?",
                        (slug,),
                    )
                    cur.execute(
                        "UPDATE persons SET n_examples=0 WHERE slug=?", (slug,)
                    )

                # Dedup level 1: idempotent (sha256, face_box) via UNIQUE index
                dup = cur.execute(
                    "SELECT id FROM person_examples "
                    "WHERE person_slug=? AND sha256=? AND face_box=?",
                    (slug, sha256, box_s),
                ).fetchone()
                if dup is not None:
                    cur.execute(
                        "UPDATE persons SET updated_at=? WHERE slug=?",
                        (now, slug),
                    )
                    n = cur.execute(
                        "SELECT n_examples FROM persons WHERE slug=?", (slug,)
                    ).fetchone()["n_examples"]
                    cur.execute("COMMIT;")
                    return {
                        "slug": slug, "name": display,
                        "n_examples": n, "added": False,
                        "reason": "duplicate_file_and_bbox",
                    }

                # Dedup level 2: cosine similarity vs embedding esistenti.
                # Skip se la nuova embedding e' >=threshold close ad almeno
                # una esistente (posa identica → non aggiunge informazione).
                if dedupe_cosine_threshold < 1.0 and mode != "replace":
                    new_emb = _l2_normalize(np.frombuffer(blob, dtype=np.float32))
                    rows = cur.execute(
                        "SELECT id, embedding FROM person_examples "
                        "WHERE person_slug=?",
                        (slug,),
                    ).fetchall()
                    max_cos = 0.0
                    similar_id = None
                    for r in rows:
                        ex_emb = _l2_normalize(np.frombuffer(
                            r["embedding"], dtype=np.float32,
                        ))
                        if ex_emb.shape != new_emb.shape:
                            continue
                        cos = float(np.dot(new_emb, ex_emb))
                        if cos > max_cos:
                            max_cos = cos
                            similar_id = r["id"]
                    if max_cos >= dedupe_cosine_threshold:
                        cur.execute(
                            "UPDATE persons SET updated_at=? WHERE slug=?",
                            (now, slug),
                        )
                        n = cur.execute(
                            "SELECT n_examples FROM persons WHERE slug=?", (slug,)
                        ).fetchone()["n_examples"]
                        cur.execute("COMMIT;")
                        return {
                            "slug": slug, "name": display,
                            "n_examples": n, "added": False,
                            "reason": "near_duplicate_embedding",
                            "max_cosine": round(max_cos, 4),
                            "similar_to_id": similar_id,
                        }

                cur.execute(
                    "INSERT INTO person_examples(person_slug,image_path,face_box,"
                    "embedding,embedding_dim,sha256,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (slug, image_path, box_s, blob, dim, sha256, now),
                )
                cur.execute(
                    "UPDATE persons SET n_examples=n_examples+1, updated_at=? "
                    "WHERE slug=?",
                    (now, slug),
                )
                n = cur.execute(
                    "SELECT n_examples FROM persons WHERE slug=?", (slug,)
                ).fetchone()["n_examples"]
                cur.execute("COMMIT;")
            except Exception:
                cur.execute("ROLLBACK;")
                raise

        return {
            "slug": slug, "name": display,
            "n_examples": n, "added": True,
        }

    def delete(self, name: str) -> dict:
        slug = slugify(name)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            try:
                row = cur.execute(
                    "SELECT n_examples FROM persons WHERE slug=?", (slug,)
                ).fetchone()
                if row is None:
                    cur.execute("COMMIT;")
                    return {"slug": slug, "deleted": False, "removed_examples": 0}
                removed = int(row["n_examples"])
                # ON DELETE CASCADE wipes person_examples
                cur.execute("DELETE FROM persons WHERE slug=?", (slug,))
                cur.execute("COMMIT;")
            except Exception:
                cur.execute("ROLLBACK;")
                raise
        return {"slug": slug, "deleted": True, "removed_examples": removed}

    # -- read ops ----------------------------------------------------------

    def get(self, name: str) -> dict | None:
        slug = slugify(name)
        cur = self._conn.cursor()
        p = cur.execute(
            "SELECT slug,name,n_examples,created_at,updated_at "
            "FROM persons WHERE slug=?", (slug,),
        ).fetchone()
        if p is None:
            return None
        ex_rows = cur.execute(
            "SELECT image_path,face_box,sha256,created_at "
            "FROM person_examples WHERE person_slug=? ORDER BY id",
            (slug,),
        ).fetchall()
        examples = [
            {
                "image_path": r["image_path"],
                "face_box": json.loads(r["face_box"]),
                "sha256": r["sha256"],
                "created_at": r["created_at"],
            }
            for r in ex_rows
        ]
        return {
            "slug": p["slug"], "name": p["name"],
            "n_examples": p["n_examples"],
            "created_at": p["created_at"], "updated_at": p["updated_at"],
            "examples": examples,
        }

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT slug,name,n_examples,created_at,updated_at "
            "FROM persons ORDER BY name COLLATE NOCASE ASC, slug ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_name(self, name: str) -> list[str]:
        """Risolve un nome user-input in slug(s) presenti nel registro.

        Tre fasi (PR2, deterministiche §7.9):
          1. slugify dell'input. Se vuoto / non-slugifiable → [].
          2. exact match: se esiste un person con quel slug → [slug].
          3. token-anywhere: scorre i slug del registro, splitta su '_',
             ritorna i slug il cui set di token contiene il query slug.
             Sort alfabetico per output deterministico.

        Ambiguita' (≥2 slug match) e' segnalata via len(>=2): il caller
        decide se chiedere disambiguation via get_inputs (§ADR 0090) o
        ritornare error.
        """
        if name is None:
            return []
        try:
            qslug = slugify(name)
        except ValueError:
            return []
        if not qslug:
            return []
        # Fase 2: exact match
        row = self._conn.execute(
            "SELECT slug FROM persons WHERE slug=?", (qslug,)
        ).fetchone()
        if row is not None:
            return [qslug]
        # Fase 3: token-anywhere
        rows = self._conn.execute("SELECT slug FROM persons").fetchall()
        out: list[str] = []
        for r in rows:
            slug = r["slug"]
            if qslug in slug.split("_"):
                out.append(slug)
        out.sort()
        return out

    def lookup_embeddings(self, name: str) -> list[np.ndarray]:
        try:
            slug = slugify(name)
        except ValueError:
            return []
        rows = self._conn.execute(
            "SELECT embedding,embedding_dim FROM person_examples "
            "WHERE person_slug=? ORDER BY id",
            (slug,),
        ).fetchall()
        return [_bytes_to_embedding(r["embedding"], r["embedding_dim"]) for r in rows]

    # -- match -------------------------------------------------------------

    @staticmethod
    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    @staticmethod
    def _ensure_normalized(v: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(v))
        if norm <= 0.0:
            raise ValueError("zero-norm embedding")
        if abs(norm - 1.0) > 0.01:
            return (v / norm).astype(np.float32, copy=False)
        return v.astype(np.float32, copy=False)

    def top_k_match(
        self,
        query_embedding: np.ndarray,
        *,
        name: str | None = None,
        threshold: float = 0.55,
    ) -> list[dict]:
        q = np.asarray(query_embedding, dtype=np.float32)
        if q.ndim != 1 or q.size != EMBEDDING_DIM:
            raise ValueError(
                f"query must be 1-D dim {EMBEDDING_DIM}, got shape {q.shape}"
            )
        q = self._ensure_normalized(q)

        if name is not None:
            slugs = [slugify(name)]
        else:
            slugs = [r["slug"] for r in self._conn.execute(
                "SELECT slug FROM persons"
            ).fetchall()]

        results: list[dict] = []
        for slug in slugs:
            display_row = self._conn.execute(
                "SELECT name FROM persons WHERE slug=?", (slug,)
            ).fetchone()
            if display_row is None:
                continue
            ex = self._conn.execute(
                "SELECT embedding,embedding_dim FROM person_examples "
                "WHERE person_slug=? ORDER BY id",
                (slug,),
            ).fetchall()
            if not ex:
                continue
            best_score = -2.0
            best_idx = -1
            for i, r in enumerate(ex):
                v = _bytes_to_embedding(r["embedding"], r["embedding_dim"])
                # vectors are persisted as L2-normalized by caller; defensive renorm
                vn = self._ensure_normalized(v)
                score = float(np.dot(q, vn))
                if score > best_score:
                    best_score = score
                    best_idx = i
            if best_score >= threshold:
                results.append({
                    "slug": slug,
                    "name": display_row["name"],
                    "best_score": best_score,
                    "matched_example_idx": best_idx,
                })
        results.sort(key=lambda d: d["best_score"], reverse=True)
        return results

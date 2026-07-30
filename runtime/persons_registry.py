#!/usr/bin/env python3
"""persons_registry — named-person registry for face search by name.

Storage layer only (PR1). PR2 will glue this to `runtime/face_embedding.py`
and add the `*_persons_indices` executors.

Design fissato (Roberto):
- Slug case-insensitive per evitare sdoppiamenti ("Carol"/"carol"/"CAROL").
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
from pathlib import Path

import numpy as np

import config as _C  # §7.11

DEFAULT_DB_PATH = _C.PATH_USER_DATA / "persons.sqlite"
PERSISTENT_EXAMPLES_DIR = _C.PATH_USER_DATA / "persons_examples"


def _persist_example_image(image_path: str, slug: str, sha256: str) -> str:
    """Copia l'immagine in `PERSISTENT_EXAMPLES_DIR/<slug>/<sha256>.<ext>` se
    sorgente è in storage volatile (/tmp/...) o NON è già sotto la dir
    persistente. Ritorna il path persistente.

    Universal §7.3: enrollment storage deve sopravvivere a TTL upload_cleanup
    (default 1h). Sorgente in /tmp è rischio strutturale.
    Idempotente: se file destinazione esiste già (stesso sha256 + ext),
    nessuna copia.
    """
    src = Path(image_path)
    target_dir = PERSISTENT_EXAMPLES_DIR / slug
    # Se già sotto dir persistente, nessuna copia.
    try:
        src.resolve().relative_to(PERSISTENT_EXAMPLES_DIR.resolve())
        return str(src)
    except ValueError:
        pass
    target_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
    target = target_dir / f"{sha256}{ext}"
    if not target.is_file() and src.is_file():
        import shutil
        shutil.copy2(src, target)
    return str(target)
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


from timefmt import now_iso_z as _utc_now_iso  # nome storico: output Z-form


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

    def __init__(self, db_path: Path | None = None, *, read_only: bool = False):
        # Lookup DEFAULT_DB_PATH at call time (NOT at function-def time) so
        # tests can monkeypatch the module attribute and have it honored
        # by code paths that don't pass `db_path` explicitly.
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.read_only = bool(read_only)
        self._lock = threading.Lock()
        if self.read_only and self.db_path.exists():
            # URI mode=ro is an enforcement boundary, not merely intent.  It
            # also prevents schema bootstrap and journal-mode changes in a
            # reader sandbox where the exact DB/WAL/SHM files are ro-bound.
            self._conn = sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
        elif self.read_only:
            # A missing registry is the valid empty state.  Use an ephemeral
            # schema so list/resolve calls stay side-effect free.
            self._conn = sqlite3.connect(
                ":memory:", check_same_thread=False, isolation_level=None,
            )
            self._conn.executescript(_SCHEMA)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; explicit BEGIN/COMMIT
            )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON;")
        if not self.read_only:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_SCHEMA)

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("persons registry opened read-only")

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
        self._require_writable()
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

                # §7.3 persistenza: copia in storage stabile prima dell'INSERT.
                # Sorgente da /tmp/metnos_uploads/ ha TTL 1h, registry non puo'
                # tenere path orfani.
                persistent_path = _persist_example_image(image_path, slug, sha256)
                cur.execute(
                    "INSERT INTO person_examples(person_slug,image_path,face_box,"
                    "embedding,embedding_dim,sha256,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (slug, persistent_path, box_s, blob, dim, sha256, now),
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
        self._require_writable()
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

    def examples_dir(self, name: str) -> Path:
        """Dir delle crop persistite di una persona: `PERSISTENT_EXAMPLES_DIR/<slug>`.
        slugify e' idempotente → accetta indifferentemente il nome o lo slug."""
        return PERSISTENT_EXAMPLES_DIR / slugify(name)

    def purge_example_files(self, name: str) -> int:
        """Rimuove da disco le crop persistite della persona (leave-no-trace).

        `delete()` toglie le RIGHE (CASCADE su person_examples) ma NON i file
        immagine copiati in `PERSISTENT_EXAMPLES_DIR/<slug>/<sha256>.<ext>` da
        `_persist_example_image` → restavano orfani su disco (residuo biometrico
        dopo un un-enroll). Qui li rimuoviamo e togliamo la dir se vuota.

        Best-effort §2.8: ritorna il numero di FILE rimossi. DA CHIAMARE DOPO il
        backup-blob (i file servono a `reverse()` per un undo completo)."""
        self._require_writable()
        d = self.examples_dir(name)
        if not d.is_dir():
            return 0
        n = 0
        for f in list(d.iterdir()):
            try:
                if f.is_file():
                    f.unlink()
                    n += 1
            except OSError:
                pass
        try:
            d.rmdir()  # solo se vuota
        except OSError:
            pass
        return n

    # -- backup / restore (undo §2.3) -------------------------------------

    def export_person(self, name: str) -> dict | None:
        """Dump COMPLETO e verbatim di una persona per il backup di undo.

        Ritorna `{person: {row}, examples: [{...}]}` con gli embedding (BLOB)
        codificati base64 per la serializzazione JSON. None se inesistente.
        Usato da `delete_persons` PRIMA della cancellazione cosi' che
        `reverse()` possa ripristinare riga + esempi identici (stessa
        biometria), non un re-enroll approssimato.
        """
        import base64
        slug = slugify(name)
        cur = self._conn.cursor()
        prow = cur.execute(
            "SELECT slug,name,created_at,updated_at,n_examples,notes "
            "FROM persons WHERE slug=?", (slug,)
        ).fetchone()
        if prow is None:
            return None
        ex_rows = cur.execute(
            "SELECT image_path,face_box,embedding,embedding_dim,sha256,created_at "
            "FROM person_examples WHERE person_slug=? ORDER BY id", (slug,)
        ).fetchall()
        examples = []
        for r in ex_rows:
            examples.append({
                "image_path": r["image_path"],
                "face_box": r["face_box"],
                "embedding_b64": base64.b64encode(r["embedding"]).decode("ascii"),
                "embedding_dim": int(r["embedding_dim"]),
                "sha256": r["sha256"],
                "created_at": r["created_at"],
            })
        return {
            "person": {
                "slug": prow["slug"], "name": prow["name"],
                "created_at": prow["created_at"], "updated_at": prow["updated_at"],
                "n_examples": int(prow["n_examples"]), "notes": prow["notes"],
            },
            "examples": examples,
        }

    def restore_person(self, backup: dict) -> dict:
        """Ripristina una persona da un dump `export_person` (undo).

        INSERT verbatim di riga + esempi (embedding decodificati da base64).
        Idempotente: se lo slug esiste gia' → no-op `{restored: False}`.
        """
        self._require_writable()
        import base64
        if not isinstance(backup, dict) or "person" not in backup:
            raise ValueError("backup must be a dict from export_person")
        p = backup["person"]
        slug = p["slug"]
        examples = backup.get("examples") or []
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            try:
                exists = cur.execute(
                    "SELECT 1 FROM persons WHERE slug=?", (slug,)
                ).fetchone()
                if exists is not None:
                    cur.execute("COMMIT;")
                    return {"slug": slug, "restored": False,
                            "reason": "slug_already_exists"}
                cur.execute(
                    "INSERT INTO persons(slug,name,created_at,updated_at,"
                    "n_examples,notes) VALUES (?,?,?,?,?,?)",
                    (slug, p["name"], p["created_at"], p["updated_at"],
                     int(p.get("n_examples") or 0), p.get("notes") or ""),
                )
                for ex in examples:
                    cur.execute(
                        "INSERT INTO person_examples(person_slug,image_path,"
                        "face_box,embedding,embedding_dim,sha256,created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (slug, ex["image_path"], ex["face_box"],
                         base64.b64decode(ex["embedding_b64"]),
                         int(ex.get("embedding_dim") or 512),
                         ex["sha256"], ex["created_at"]),
                    )
                cur.execute("COMMIT;")
            except Exception:
                cur.execute("ROLLBACK;")
                raise
        return {"slug": slug, "restored": True,
                "restored_examples": len(examples)}

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
        # Exact slug match.
        rows = self._conn.execute(
            "SELECT embedding,embedding_dim FROM person_examples "
            "WHERE person_slug=? ORDER BY id",
            (slug,),
        ).fetchall()
        # Fallback (15/5/2026): se slug exact non matcha (es. name="alice"
        # vs slug="alice_brunialti"), usa `resolve_name` per token-anywhere.
        # Se multipli match, unisce embeddings di TUTTI (acceptable: face
        # recognition con stesso first-name dovrebbe essere disambiguato
        # con altri campi). Bug live (turn alice_mare): name=alice →
        # 0 embeddings → fallback query_text → 29k unfiltered.
        if not rows:
            slugs = self.resolve_name(name)
            if slugs:
                placeholders = ",".join("?" * len(slugs))
                rows = self._conn.execute(
                    f"SELECT embedding,embedding_dim FROM person_examples "
                    f"WHERE person_slug IN ({placeholders}) ORDER BY id",
                    slugs,
                ).fetchall()
        return [_bytes_to_embedding(r["embedding"], r["embedding_dim"]) for r in rows]

    # -- match -------------------------------------------------------------

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


# Module-level helper for cross-module convenience (used by
# find_images_indices). Returns list[np.ndarray].
def resolve_face_embeddings_for_name(name: str) -> list:
    """Module-level alias: PersonsRegistry().lookup_embeddings(name).
    Bug live 15/5/2026: find_images_indices importava questa funzione
    che NON era definita → ImportError silent → name filter saltato.
    """
    if not name:
        return []
    registry = None
    try:
        registry = PersonsRegistry(read_only=True)
        return registry.lookup_embeddings(name)
    except Exception:
        return []
    finally:
        if registry is not None:
            registry.close()

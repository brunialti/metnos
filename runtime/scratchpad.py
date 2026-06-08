#!/usr/bin/env python3
"""
scratchpad.py — archivio temporaneo per observation grandi (Metnos v1.1).

Concetto: quando un executor produce un'observation troppo grande per essere
utilmente messa nella history del LLM (oltre soglia, default 4 KB di JSON),
il runtime la salva in scratchpad e inserisce nella history un *riferimento*
+ un summary breve. Il LLM puo' poi chiamare un executor builtin
`scratchpad_read(id, mode, ...)` per accedere al contenuto pieno o a una porzione.

Decisione 26/4/2026 sera: scelta architetturale "scratchpad" preferita all'
alternativa "max_bytes hint inline" perche' separa la responsabilita' (executor
restituisce tutto, runtime decide come paginare) e perche' permette al LLM di
chiedere range diversi nello stesso turno senza ri-eseguire l'executor.

Storage: SQLite, file unico in `~/.local/share/metnos/scratchpad.db`.
Schema essenziale: (id, turn_id, step_num, executor_name, content_kind,
content, size_bytes, summary, created_at, expires_at).

API principali:
    Scratchpad.open()                         apre/crea il DB
    sp.put(turn_id, step, executor, obs)      memorizza un'observation,
                                              ritorna entry_dict per la history
    sp.get(scratchpad_id)                     recupera obs originale
    sp.read(scratchpad_id, mode, **kwargs)    porzione: 'full'|'head'|'tail'|'range'
    sp.gc(now)                                rimuove entries scadute
"""
import base64
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

import config as _C  # §7.11

DEFAULT_DB = _C.PATH_USER_DATA / "scratchpad.db"
DEFAULT_THRESHOLD_BYTES = 4096
DEFAULT_TTL_SECONDS = 3600  # 1 ora
SUMMARY_HEAD = 500
SUMMARY_TAIL = 500


SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            TEXT PRIMARY KEY,
    turn_id       TEXT NOT NULL,
    step_num      INTEGER,
    executor_name TEXT,
    content_kind  TEXT NOT NULL,    -- 'text' | 'binary'
    content       BLOB NOT NULL,    -- testo utf-8 o bytes raw
    size_bytes    INTEGER NOT NULL,
    summary       TEXT,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_turn ON entries(turn_id);
CREATE INDEX IF NOT EXISTS idx_entries_exp  ON entries(expires_at);
"""


def _smart_summary_text(text, head=SUMMARY_HEAD, tail=SUMMARY_TAIL):
    if len(text) <= head + tail + 80:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[... {omitted} caratteri omessi ...]\n\n{text[-tail:]}"


def _binary_summary(data):
    sha = hashlib.sha256(data).hexdigest()[:16]
    return f"[BINARY: {len(data)} bytes, sha256={sha}...]"


def _summarize_structured(observation, max_items=10):
    """Summary leggibile per observation senza `content` (es. fs_find -> matches).

    Cerca il primo campo-lista significativo, ne mostra fino a max_items elementi
    + il count totale. Include anche metadata se presente.
    """
    candidate_keys = ("matches", "items", "results", "entries", "files", "paths")
    chosen_list = None
    chosen_key = None
    for k in candidate_keys:
        v = observation.get(k)
        if isinstance(v, list):
            chosen_list = v
            chosen_key = k
            break

    parts = []
    md = observation.get("metadata") or {}
    if md:
        md_compact = ", ".join(f"{k}={v}" for k, v in md.items())
        parts.append(f"metadata: {md_compact}")

    if chosen_list is not None:
        # Solo schema + count, MAI il contenuto degli item: il LLM deve
        # passare il riferimento {{stepN.<list_field>}} al prossimo
        # tool, non ricopiare inline (vedi check_inline_data + ref_hint
        # nel synthetic handle). Mostrare `repr(item)` qui leakerebbe
        # i dati e tenta il LLM a copiarli — bug confermato 29/4/2026
        # (observed during dev on a large mail-summary observation).
        schema = sorted(chosen_list[0].keys()) if chosen_list and isinstance(chosen_list[0], dict) else None
        if schema:
            parts.append(f"{chosen_key}: {len(chosen_list)} elementi. Campi: {schema}")
        else:
            parts.append(f"{chosen_key}: {len(chosen_list)} elementi.")
    else:
        # Fallback: dump compatto del payload
        compact = json.dumps(observation, ensure_ascii=False)
        parts.append(_smart_summary_text(compact))

    return "\n".join(parts)


class Scratchpad:
    def __init__(self, conn):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def open(cls, db_path=None):
        # Lazy resolution: permette monkey-patching di DEFAULT_DB prima di open()
        if db_path is None:
            db_path = DEFAULT_DB
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        return cls(conn)

    def gc(self, now=None):
        now = now or time.time()
        cur = self.conn.execute("DELETE FROM entries WHERE expires_at < ?", (now,))
        self.conn.commit()
        return cur.rowcount

    def put(self, turn_id, step_num, executor_name, observation,
            ttl_seconds=DEFAULT_TTL_SECONDS):
        """
        Memorizza l'observation completa. Ritorna un dict 'sintetico' da inserire
        nella history del LLM al posto dell'observation grossa.

        Generalizzato (27/4): se l'observation non ha `content` (es. fs_find ha
        `matches`) salviamo l'observation intera serializzata JSON e costruiamo
        un summary informativo dai campi-lista o dal payload completo.
        """
        content = observation.get("content")

        if isinstance(content, bytes):
            kind = "binary"
            stored = content
            size = len(content)
            summary = _binary_summary(content)
        elif isinstance(content, str):
            kind = "text"
            stored = content.encode("utf-8")
            size = len(stored)
            summary = _smart_summary_text(content)
        else:
            # Nessun content testuale: salva l'observation intera come JSON.
            # Costruisci un summary che inglobi i campi-lista (matches, items, results, ...)
            # cosi' il LLM puo' vedere qualcosa di utile senza dover sempre fare scratchpad_read.
            kind = "text"
            full_json = json.dumps(observation, ensure_ascii=False, indent=2)
            stored = full_json.encode("utf-8")
            size = len(stored)
            summary = _summarize_structured(observation)

        eid = uuid.uuid4().hex[:16]
        now = time.time()
        self.conn.execute(
            "INSERT INTO entries (id, turn_id, step_num, executor_name, content_kind, content, size_bytes, summary, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, turn_id, step_num, executor_name, kind, stored, size, summary, now, now + ttl_seconds),
        )
        self.conn.commit()
        # Handle ricco: count, schema, list_field consentono al pianificatore
        # di sapere COSA c'e' (forma + dimensione) senza vederne il contenuto.
        list_field = None
        list_count = None
        list_schema = None
        for k in ("entries", "matches", "items", "results", "files", "paths"):
            v = observation.get(k)
            if isinstance(v, list):
                list_field = k
                list_count = len(v)
                if v and isinstance(v[0], dict):
                    list_schema = sorted(v[0].keys())
                break
        synthetic = {
            "ok": observation.get("ok", True),
            "scratchpad_id": eid,
            "step_num": step_num,
            "size_bytes": size,
            "kind": kind,
            "summary": summary,
            "metadata": observation.get("metadata", {}),
        }
        # Preserva top-level "leggeri" (scalari, dict piccoli, stats):
        # counts, ok_count, fail_count, truncated, available_total, used,
        # cap_field, cap_value, dimension, classes, total, message, error.
        # Sono i campi che il pianificatore deve VEDERE per chiudere il
        # turno (es. counts dopo classify_entries) senza scratchpad_read
        # che ritruncerebbe (the design guide 2.8 + 2.11). Liste pesanti restano
        # solo nello storage (entries/matches/items/...).
        _LIGHTWEIGHT_TOPLEVEL = (
            "counts", "ok_count", "fail_count", "truncated", "truncated_what",
            "available_total", "used", "cap_field", "cap_value",
            "dimension", "classes", "total", "message", "error",
            "pre_filtered", "llm_classified", "unclassified", "failed_batches",
        )
        for k in _LIGHTWEIGHT_TOPLEVEL:
            if k in observation and k not in synthetic:
                synthetic[k] = observation[k]
        if list_field is not None:
            synthetic["list_field"] = list_field
            synthetic["count"] = list_count
            if list_schema is not None:
                synthetic["schema"] = list_schema
            synthetic["ref_hint"] = (
                f"Per riusare in step successivi: passa {{{{step{step_num}.{list_field}}}}} "
                f"come arg di un altro tool. NON ripassare i dati inline."
            )
        else:
            synthetic["ref_hint"] = (
                f"Per riusare il contenuto in step successivi usa {{{{step{step_num}.<campo>}}}}. "
                f"Per leggere porzioni del payload usa scratchpad_read({eid})."
            )
        return synthetic

    def get(self, scratchpad_id):
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (scratchpad_id,)).fetchone()
        if not row:
            return None
        return dict(row)

    def read(self, scratchpad_id, mode="full", n=2000, start=0, end=None):
        """
        Restituisce porzione del contenuto.
            mode='full'   tutto
            mode='head'   primi n bytes/char
            mode='tail'   ultimi n bytes/char
            mode='range'  da start a end (esclusivo)
        """
        row = self.get(scratchpad_id)
        if not row:
            return {"ok": False, "error": f"scratchpad id non trovato: {scratchpad_id}"}
        kind = row["content_kind"]
        data = row["content"]  # bytes
        size = row["size_bytes"]
        if kind == "text":
            text = data.decode("utf-8", errors="replace")
            if mode == "full":
                slice_ = text
            elif mode == "head":
                slice_ = text[:n]
            elif mode == "tail":
                slice_ = text[-n:]
            elif mode == "range":
                e = end if end is not None else size
                slice_ = text[start:e]
            else:
                return {"ok": False, "error": f"mode sconosciuto: {mode}"}
            return {
                "ok": True,
                "content": slice_,
                "metadata": {
                    "scratchpad_id": scratchpad_id,
                    "kind": "text",
                    "size_full": size,
                    "size_returned": len(slice_),
                    "mode": mode,
                },
            }
        else:  # binary
            if mode == "full":
                slice_ = data
            elif mode == "head":
                slice_ = data[:n]
            elif mode == "tail":
                slice_ = data[-n:]
            elif mode == "range":
                e = end if end is not None else size
                slice_ = data[start:e]
            else:
                return {"ok": False, "error": f"mode sconosciuto: {mode}"}
            return {
                "ok": True,
                "content": base64.b64encode(slice_).decode("ascii"),
                "metadata": {
                    "scratchpad_id": scratchpad_id,
                    "kind": "binary-base64",
                    "size_full": size,
                    "size_returned": len(slice_),
                    "mode": mode,
                },
            }

    def list_for_turn(self, turn_id):
        rows = self.conn.execute(
            "SELECT id, step_num, executor_name, size_bytes, summary FROM entries WHERE turn_id = ? ORDER BY step_num",
            (turn_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        n = self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(size_bytes),0) bytes FROM entries").fetchone()
        return {"entries": n["c"], "total_bytes": n["bytes"]}


# --- Builtin executor: scratchpad_read ------------------------------------
# Esposto al LLM come tool quando ci sono entries scratchpad attive nel turno.
# A differenza degli executor tradizionali, vive nel runtime e non ha manifest
# su disk: il runtime lo costruisce dinamicamente.

SCRATCHPAD_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "scratchpad_read",
        "description": "Legge dallo scratchpad un'observation precedentemente salvata (perche' troppo grande per la history). Utile per consultare il contenuto pieno o porzioni di osservazioni grosse di passi precedenti.",
        "parameters": {
            "type": "object",
            "required": ["scratchpad_id"],
            "properties": {
                "scratchpad_id": {"type": "string", "description": "Id ottenuto dal campo 'scratchpad_id' di un'observation precedente."},
                "mode": {"type": "string", "enum": ["full", "head", "tail", "range"], "default": "head"},
                "n": {"type": "integer", "description": "Per mode head/tail: numero di caratteri da leggere. Default 2000.", "default": 2000},
                "start": {"type": "integer", "description": "Per mode range: indice di inizio.", "default": 0},
                "end": {"type": "integer", "description": "Per mode range: indice di fine (esclusivo)."},
            },
        },
    },
}


if __name__ == "__main__":
    sp = Scratchpad.open()
    print(f"DB: {DEFAULT_DB}")
    print(f"stats: {sp.stats()}")
    # Smoke test
    sample_obs = {"ok": True, "content": "x" * 8000, "metadata": {"path": "/tmp/foo"}}
    synth = sp.put("turn_test", 1, "read_files", sample_obs, ttl_seconds=10)
    print(f"synthetic: {synth}")
    full = sp.read(synth["scratchpad_id"], mode="full")
    print(f"full size returned: {full['metadata']['size_returned']}")
    head = sp.read(synth["scratchpad_id"], mode="head", n=100)
    print(f"head 100 size returned: {head['metadata']['size_returned']}")
    sp.gc()

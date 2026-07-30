"""Signed, immutable Tutor catalog built from reviewed and compiled sources.

The compiler writes a complete candidate beside the live catalog, verifies its
SQLite shape and detached Ed25519 signature, then swaps it under a filesystem
lock.  Readers take the same lock and open the admitted database read-only.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading

import numpy as np

import config
from logging_setup import get_logger

from .cards import Card, PUBLISHED_CARDS, REPO_ROOT, load_published
from .sources import (
    KnowledgeUnit,
    SOURCES_CONFIG,
    build_knowledge_units,
    declared_source_files,
    executor_catalog_stamp,
    snapshot_hash as knowledge_snapshot_hash,
)

log = get_logger(__name__)

CATALOG_PATH = config.PATH_USER_DATA / "tutor_catalog.sqlite"
SIGNATURE_PATH = config.PATH_USER_DATA / "tutor_catalog.sqlite.sig"
BACKUP_PATH = config.PATH_USER_DATA / "tutor_catalog.last_good.json"
LOCK_PATH = config.PATH_USER_STATE / "tutor_catalog.lock"
BUILD_LOCK_PATH = config.PATH_USER_STATE / "tutor_catalog.build.lock"
SCHEMA_VERSION = 5

_PROCESS_LOCK = threading.RLock()
_COMPILE_PROCESS_LOCK = threading.Lock()
_VERIFY_LOCK = threading.RLock()
_CACHE: tuple[str, tuple[Card, ...]] | None = None
_VECTOR_CACHE: tuple[str, "VectorIndex"] | None = None
_KNOWLEDGE_CACHE: tuple[str, tuple[KnowledgeUnit, ...]] | None = None
_KNOWLEDGE_VECTOR_CACHE: tuple[str, "VectorIndex"] | None = None
_VERIFY_CACHE: tuple[tuple[int, ...], bool] | None = None


def _compiler_implementation_digest() -> str:
    """Identity of Python modules whose imported code projects the corpus.

    A long-running HTTP or channel process may survive a worktree/deployment
    update.  Such a process must not stamp a catalog built by its old imported
    functions with the new files' input identity.
    """

    paths = (
        Path(__file__).resolve(),
        REPO_ROOT / "runtime" / "published_docs.py",
        REPO_ROOT / "runtime" / "tutor" / "probes.py",
        REPO_ROOT / "runtime" / "tutor" / "probe_worker.py",
        REPO_ROOT / "runtime" / "tutor" / "observation_views.py",
        REPO_ROOT / "runtime" / "tutor" / "sources.py",
        REPO_ROOT / "runtime" / "services_registry.py",
        REPO_ROOT / "runtime" / "ui_surfaces.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        encoded = str(path).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        try:
            data = path.read_bytes()
        except OSError:
            data = b"missing"
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


_LOADED_COMPILER_DIGEST = _compiler_implementation_digest()

_SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE cards (
    card_id TEXT PRIMARY KEY,
    audience TEXT NOT NULL,
    priority INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE card_vectors (
    card_id TEXT NOT NULL,
    lang TEXT NOT NULL,
    vector BLOB NOT NULL,
    dim INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    PRIMARY KEY(card_id, lang),
    FOREIGN KEY(card_id) REFERENCES cards(card_id)
);
CREATE TABLE knowledge_units (
    unit_id TEXT PRIMARY KEY,
    lang TEXT NOT NULL,
    audience TEXT NOT NULL,
    priority INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE knowledge_vectors (
    unit_id TEXT PRIMARY KEY,
    lang TEXT NOT NULL,
    vector BLOB NOT NULL,
    dim INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    FOREIGN KEY(unit_id) REFERENCES knowledge_units(unit_id)
);
"""


@dataclass(frozen=True, slots=True)
class VectorIndex:
    """Validated, normalized vectors admitted by the signed catalog."""

    refs: tuple[tuple[str, str], ...]
    matrix: np.ndarray
    dimension: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """One internally consistent admitted generation for a Tutor request."""

    version: str
    cards: tuple[Card, ...]
    units: tuple[KnowledgeUnit, ...]
    card_index: VectorIndex
    knowledge_index: VectorIndex


def _embedding_model_files() -> tuple[Path, ...]:
    """Resolve only the in-process text model used by ``get_local_embedder``.

    Provider-aware: the fingerprint must change when the ACTIVE model
    changes, or stored vectors would be reused across incompatible spaces.
    """

    from virt import tiers
    from virt import DEFAULT_EMBEDDERS

    spec = tiers.spec("embedding", "text", DEFAULT_EMBEDDERS)
    if spec.get("provider") == "qwen":
        from qwen_embedding import resolved_model_files

        return resolved_model_files(spec.get("model_dir"))
    configured = spec.get("model_dir") if spec.get("provider") == "bge" else None
    model_dir = Path(configured) if configured else (
        config.PATH_ROOT / "models" / "embedding-bge")
    return (
        model_dir / "onnx" / "sentence_transformers_int8.onnx",
        model_dir / "tokenizer.json",
    )


def embedding_fingerprint() -> str:
    """Cheap invalidation identity for the local model behind stored vectors.

    Catalog signatures protect the vector bytes.  File identity here ensures a
    model replacement invalidates and rebuilds the index without hashing a
    several-hundred-megabyte ONNX file on every Tutor request.
    """

    digest = hashlib.sha256()
    for path in _embedding_model_files():
        encoded = str(path.resolve()).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        try:
            stat = path.stat()
        except OSError:
            digest.update(b"missing")
        else:
            digest.update(stat.st_size.to_bytes(8, "big"))
            digest.update(stat.st_mtime_ns.to_bytes(8, "big"))
    return f"sha256:{digest.hexdigest()}"


def _source_files() -> tuple[Path, ...]:
    files = [path for path in PUBLISHED_CARDS.rglob("*") if path.is_file()]
    files.append(SOURCES_CONFIG)
    files.extend(declared_source_files())
    return tuple(sorted(set(files), key=lambda path: str(path)))


def input_stamp() -> str:
    """Content identity for every source admitted to the Tutor compiler.

    File size and mtime are deliberately insufficient here: generated docs,
    restored files, and reproducible builds can preserve both while changing
    the bytes.  Reading the complete source set (currently only a few MiB)
    makes every documentation change invalidate the signed catalog.
    """

    digest = hashlib.sha256()
    for path in _source_files():
        encoded = str(path.resolve()).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        try:
            data = path.read_bytes()
        except OSError:
            digest.update(b"missing")
        else:
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    # The projection code is part of the catalog input just as much as the
    # documents and manifests are. Without this identity, a new compiler could
    # admit an artifact produced by older projection rules merely because the
    # authored text did not change.
    for value in (
            embedding_fingerprint(), executor_catalog_stamp(),
            _compiler_implementation_digest()):
        encoded = value.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def source_hash(units: tuple[KnowledgeUnit, ...] | None = None) -> str:
    digest = hashlib.sha256()
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    fingerprint = embedding_fingerprint().encode("ascii")
    digest.update(len(fingerprint).to_bytes(4, "big"))
    digest.update(fingerprint)
    knowledge_hash = knowledge_snapshot_hash(
        units if units is not None else build_knowledge_units()).encode("ascii")
    digest.update(len(knowledge_hash).to_bytes(4, "big"))
    digest.update(knowledge_hash)
    return f"sha256:{digest.hexdigest()}"


@contextmanager
def _catalog_lock(*, exclusive: bool):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _build_lock():
    """Serialize compilers without excluding readers of the live artifact."""

    BUILD_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(BUILD_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _signature(data: bytes) -> bytes:
    import sign
    private_path = sign.KEYS_DIR / f"{sign.DEFAULT_AUTHOR_KEY}_priv.bin"
    public_path = sign.KEYS_DIR / f"{sign.DEFAULT_AUTHOR_KEY}_pub.bin"
    if not private_path.is_file() and not public_path.is_file():
        sign.generate_keypair(sign.DEFAULT_AUTHOR_KEY)
    if not private_path.is_file():
        raise RuntimeError("tutor catalog signing key unavailable")
    return sign.load_private(sign.DEFAULT_AUTHOR_KEY).sign(data)


def _verify_bytes(data: bytes, signature: bytes) -> bool:
    import sign
    try:
        # Catalogs and executor manifests are different trust domains.  The
        # catalog is authored only by the installation's named author key;
        # adding an executor publisher must not authorize Tutor knowledge.
        public = sign.load_public(sign.DEFAULT_AUTHOR_KEY)
        public.verify(signature, data)
        return True
    except (OSError, ValueError, TypeError):
        return False
    except Exception:
        return False


def _artifact_identity(path: Path, signature_path: Path) -> tuple[int, ...]:
    values: list[int] = []
    for candidate in (path, signature_path):
        stat = candidate.stat()
        values.extend((
            int(stat.st_dev), int(stat.st_ino), int(stat.st_size),
            int(stat.st_mtime_ns), int(stat.st_ctime_ns),
        ))
    return tuple(values)


def verify_catalog(path: Path | None = None,
                   signature_path: Path | None = None) -> bool:
    global _VERIFY_CACHE
    path = path or CATALOG_PATH
    signature_path = signature_path or SIGNATURE_PATH
    live = path == CATALOG_PATH and signature_path == SIGNATURE_PATH
    try:
        identity = _artifact_identity(path, signature_path)
        if live:
            with _VERIFY_LOCK:
                if _VERIFY_CACHE is not None and _VERIFY_CACHE[0] == identity:
                    return _VERIFY_CACHE[1]
        valid = _verify_bytes(path.read_bytes(), signature_path.read_bytes())
        # Do not cache a result across an in-place replacement observed while
        # reading. Normal callers also hold the catalog's shared file lock.
        if identity != _artifact_identity(path, signature_path):
            return False
        if live:
            with _VERIFY_LOCK:
                _VERIFY_CACHE = (identity, valid)
        return valid
    except OSError:
        return False


def _write_atomic(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _save_last_good() -> None:
    if not verify_catalog():
        return
    envelope = json.dumps({
        "schema": 1,
        "catalog": base64.b64encode(CATALOG_PATH.read_bytes()).decode("ascii"),
        "signature": base64.b64encode(
            SIGNATURE_PATH.read_bytes()).decode("ascii"),
    }, separators=(",", ":")).encode("ascii")
    _write_atomic(BACKUP_PATH, envelope)


def _restore_last_good() -> bool:
    try:
        envelope = json.loads(BACKUP_PATH.read_text(encoding="ascii"))
        if envelope.get("schema") != 1:
            return False
        data = base64.b64decode(envelope["catalog"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        if not _verify_bytes(data, signature):
            return False
        _write_atomic(CATALOG_PATH, data)
        _write_atomic(SIGNATURE_PATH, signature)
        return verify_catalog()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _serialize(card: Card) -> str:
    return json.dumps(asdict(card), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _deserialize(payload: str) -> Card:
    return Card(**json.loads(payload))


def _serialize_unit(unit: KnowledgeUnit) -> str:
    return json.dumps(asdict(unit), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _deserialize_unit(payload: str) -> KnowledgeUnit:
    return KnowledgeUnit(**json.loads(payload))


def _knowledge_embedding_text(unit: KnowledgeUnit) -> str:
    """Embed the authored title with the semantic description."""

    return f"{unit.title}. {unit.semantic}"


def _embed_items(items: list[tuple[str, str, str]]) -> tuple[
        list[tuple[str, str, bytes, int, str]], int, str]:
    """Embed a bounded corpus in batches to cap ONNX activation memory."""

    if not items:
        raise ValueError("tutor catalog has no semantic descriptions")
    from virt import get_local_embedder

    embedder = get_local_embedder("text")
    try:
        batch_size = int(os.environ.get("METNOS_TUTOR_EMBED_BATCH", "16"))
    except (TypeError, ValueError):
        batch_size = 16
    batch_size = max(1, min(32, batch_size))
    batches = []
    for start in range(0, len(items), batch_size):
        texts = [item[2] for item in items[start:start + batch_size]]
        batches.append(np.asarray(embedder.embed_texts(texts), dtype=np.float32))
    matrix = np.concatenate(batches, axis=0)
    if matrix.ndim != 2 or matrix.shape[0] != len(items) or matrix.shape[1] < 1:
        raise ValueError("invalid tutor embedding matrix shape")
    if not np.isfinite(matrix).all():
        raise ValueError("non-finite tutor embedding")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (norms <= 1e-8).any():
        raise ValueError("zero tutor embedding")
    matrix = np.ascontiguousarray(matrix / norms, dtype="<f4")
    dimension = int(matrix.shape[1])
    rows = []
    for (item_id, lang, semantic_text), vector in zip(items, matrix):
        rows.append((
            item_id,
            lang,
            vector.tobytes(order="C"),
            dimension,
            f"sha256:{hashlib.sha256(semantic_text.encode('utf-8')).hexdigest()}",
        ))
    return rows, dimension, embedding_fingerprint()


def _embed_cards(cards: tuple[Card, ...]) -> tuple[
        list[tuple[str, str, bytes, int, str]], int, str]:
    """Build and validate all localized semantic vectors in one local batch."""

    items = [
        (card.card_id, lang, text)
        for card in sorted(cards, key=lambda item: item.card_id)
        for lang, text in sorted(card.semantic.items())
    ]
    return _embed_items(items)


def _reusable_knowledge_vectors() -> tuple[dict[str, tuple[bytes, int]], int]:
    """Read signed vectors by semantic hash for an incremental F2 rebuild."""

    if not verify_catalog():
        return {}, 0
    try:
        connection = sqlite3.connect(
            f"file:{CATALOG_PATH}?mode=ro&immutable=1", uri=True)
        try:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if (metadata.get("schema_version") != str(SCHEMA_VERSION)
                    or metadata.get("embedding_fingerprint")
                    != embedding_fingerprint()):
                return {}, 0
            dimension = int(metadata["embedding_dim"])
            rows = connection.execute(
                "SELECT text_hash,vector,dim FROM knowledge_vectors").fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError):
        return {}, 0
    reusable = {}
    for text_hash, blob, row_dim in rows:
        if int(row_dim) == dimension and len(blob) == dimension * 4:
            reusable[str(text_hash)] = (bytes(blob), dimension)
    return reusable, dimension


def _embed_knowledge(units: tuple[KnowledgeUnit, ...]) -> tuple[
        list[tuple[str, str, bytes, int, str]], int, str]:
    items = [
        (unit.unit_id, unit.lang, _knowledge_embedding_text(unit))
        for unit in units
    ]
    reusable, reusable_dimension = _reusable_knowledge_vectors()
    admitted: dict[int, tuple[str, str, bytes, int, str]] = {}
    missing: list[tuple[int, tuple[str, str, str]]] = []
    for position, item in enumerate(items):
        item_id, lang, semantic = item
        text_hash = f"sha256:{hashlib.sha256(semantic.encode('utf-8')).hexdigest()}"
        cached = reusable.get(text_hash)
        if cached is None:
            missing.append((position, item))
            continue
        admitted[position] = (
            item_id, lang, cached[0], cached[1], text_hash)
    embedded_dimension = 0
    fingerprint = embedding_fingerprint()
    if missing:
        fresh, embedded_dimension, fingerprint = _embed_items(
            [item for _position, item in missing])
        for (position, _item), row in zip(missing, fresh):
            admitted[position] = row
    dimension = embedded_dimension or reusable_dimension
    if dimension < 1 or len(admitted) != len(items):
        raise ValueError("incomplete Tutor incremental embedding build")
    rows = [admitted[position] for position in range(len(items))]
    if any(row[3] != dimension for row in rows):
        raise ValueError("mixed Tutor embedding dimensions")
    return rows, dimension, fingerprint


def _build_candidate(path: Path, current_source_hash: str,
                     current_input_stamp: str,
                     knowledge: tuple[KnowledgeUnit, ...]) -> None:
    from ui_surfaces import validate_surfaces
    from .observation_views import registered_view_ids, validate_views

    surface_findings = validate_surfaces()
    if surface_findings:
        raise ValueError(
            "Tutor UI surface registry failed validation: "
            + ", ".join(surface_findings))
    view_findings = validate_views()
    if view_findings:
        raise ValueError(
            "Tutor observation-view registry failed validation: "
            + ", ".join(view_findings))
    admitted_views = registered_view_ids()
    unknown_views = sorted({
        unit.observation_ref
        for unit in knowledge
        if unit.observation_ref and unit.observation_ref not in admitted_views
    })
    if unknown_views:
        raise ValueError(
            "Tutor knowledge references unregistered observation views: "
            + ", ".join(unknown_views))
    invalid_view_units = sorted(
        unit.unit_id for unit in knowledge
        if ((unit.observation_ref and unit.source_kind != "live_observation")
            or (unit.source_kind == "live_observation"
                and not unit.observation_ref))
    )
    if invalid_view_units:
        raise ValueError(
            "Tutor observation authority appears on invalid knowledge units: "
            + ", ".join(invalid_view_units))
    for view_id in admitted_views:
        view_units = tuple(
            unit for unit in knowledge
            if unit.observation_ref == view_id
            and unit.source_kind == "live_observation"
        )
        if (len(view_units) != 2
                or {unit.lang for unit in view_units} != {"it", "en"}):
            raise ValueError(
                f"Tutor observation view has incomplete signed units: {view_id}")
    cards = load_published()
    # Le schede sono fonti curate ad alta autorita', non piu' un seed set
    # F1 a cardinalita' fissa: il ritiro ratificato (RM-0003, tranche 1
    # 25/7) le riduce senza rompere la compilazione. Ogni scheda resta
    # validata per integrita' in load_published(); l'unico vincolo di
    # insieme e' che una sola panoramica possa rivendicare quel ruolo.
    if sum(card.kind == "capability_overview" for card in cards) > 1:
        raise ValueError("tutor catalog admits at most one capability overview")
    vectors, dimension, fingerprint = _embed_cards(cards)
    knowledge_vectors, knowledge_dimension, knowledge_fingerprint = (
        _embed_knowledge(knowledge))
    if knowledge_dimension != dimension or knowledge_fingerprint != fingerprint:
        raise ValueError("Tutor embedding model changed during catalog build")
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(_SCHEMA)
        connection.executemany(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (("schema_version", str(SCHEMA_VERSION)),
             ("source_hash", current_source_hash),
             ("input_stamp", current_input_stamp),
             ("embedding_dim", str(dimension)),
             ("embedding_fingerprint", fingerprint),
             ("knowledge_count", str(len(knowledge)))),
        )
        connection.executemany(
            "INSERT INTO cards(card_id,audience,priority,payload_json) "
            "VALUES(?,?,?,?)",
            ((card.card_id, card.audience, card.priority, _serialize(card))
             for card in cards),
        )
        connection.executemany(
            "INSERT INTO card_vectors(card_id,lang,vector,dim,text_hash) "
            "VALUES(?,?,?,?,?)",
            vectors,
        )
        connection.executemany(
            "INSERT INTO knowledge_units(unit_id,lang,audience,priority,payload_json) "
            "VALUES(?,?,?,?,?)",
            ((unit.unit_id, unit.lang, unit.audience, unit.priority,
              _serialize_unit(unit)) for unit in knowledge),
        )
        connection.executemany(
            "INSERT INTO knowledge_vectors(unit_id,lang,vector,dim,text_hash) "
            "VALUES(?,?,?,?,?)",
            knowledge_vectors,
        )
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("candidate tutor catalog failed integrity_check")
    finally:
        connection.close()


def _metadata(path: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return dict(connection.execute("SELECT key,value FROM metadata"))
    finally:
        connection.close()


def _remove_stale_candidates() -> int:
    """Remove only interrupted candidates while holding the build lock."""
    removed = 0
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    for pattern in (
            ".tutor_catalog.*.sqlite",
            ".tutor_catalog.*.sqlite.sig"):
        for path in CATALOG_PATH.parent.glob(pattern):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed += 1
    return removed


def compile_catalog(*, force: bool = False) -> str:
    """Build if stale. A failed candidate leaves the admitted files intact."""

    global _CACHE, _VECTOR_CACHE, _KNOWLEDGE_CACHE, _KNOWLEDGE_VECTOR_CACHE
    global _VERIFY_CACHE
    if _compiler_implementation_digest() != _LOADED_COMPILER_DIGEST:
        # Read-only stale processes may continue serving the admitted signed
        # generation, but only a process importing the current implementation
        # is allowed to materialize a new one.
        with _PROCESS_LOCK, _catalog_lock(exclusive=False):
            if verify_catalog():
                metadata = _metadata(CATALOG_PATH)
                log.warning(
                    "Tutor compiler changed on disk; stale process remains "
                    "read-only until restart")
                return str(metadata["source_hash"])
        raise RuntimeError("stale Tutor compiler process has no valid catalog")
    current_input_stamp = input_stamp()
    # A dedicated compiler lock prevents duplicate builds while the catalog
    # lock remains available to request readers.  Only recovery and the final
    # atomic swap need to exclude readers.
    with _COMPILE_PROCESS_LOCK, _build_lock():
        stale_candidates = _remove_stale_candidates()
        if stale_candidates:
            log.info("removed %d interrupted Tutor catalog candidates",
                     stale_candidates)
        with _catalog_lock(exclusive=False):
            admitted_valid = verify_catalog()
            if admitted_valid:
                try:
                    admitted_metadata = _metadata(CATALOG_PATH)
                except (OSError, sqlite3.Error):
                    admitted_metadata = {}
            else:
                admitted_metadata = {}
        if not admitted_valid:
            with _catalog_lock(exclusive=True):
                if not verify_catalog():
                    _restore_last_good()
                admitted_valid = verify_catalog()
                admitted_metadata = (
                    _metadata(CATALOG_PATH) if admitted_valid else {})
        if not force and admitted_valid:
            try:
                if (admitted_metadata.get("schema_version")
                        == str(SCHEMA_VERSION)
                        and admitted_metadata.get("input_stamp")
                        == current_input_stamp):
                    return str(admitted_metadata["source_hash"])
            except (OSError, sqlite3.Error):
                pass

        knowledge = build_knowledge_units()
        current_source_hash = source_hash(knowledge)

        CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        descriptor, candidate_name = tempfile.mkstemp(
            prefix=".tutor_catalog.", suffix=".sqlite",
            dir=str(CATALOG_PATH.parent),
        )
        os.close(descriptor)
        candidate = Path(candidate_name)
        candidate_sig = candidate.with_suffix(candidate.suffix + ".sig")
        try:
            load_published.cache_clear()
            _build_candidate(
                candidate, current_source_hash, current_input_stamp, knowledge)
            # Compilation is intentionally long on a fresh CPU-only install.
            # Reject a candidate if either executable projection rules or any
            # admitted source changed while its vectors were being produced.
            if _compiler_implementation_digest() != _LOADED_COMPILER_DIGEST:
                raise RuntimeError(
                    "Tutor compiler changed during catalog build; "
                    "candidate rejected")
            if input_stamp() != current_input_stamp:
                raise RuntimeError(
                    "Tutor inputs changed during catalog build; "
                    "candidate rejected")
            data = candidate.read_bytes()
            candidate_sig.write_bytes(_signature(data))
            os.chmod(candidate, 0o600)
            os.chmod(candidate_sig, 0o600)
            if not verify_catalog(candidate, candidate_sig):
                raise ValueError("candidate tutor catalog signature invalid")
            with _catalog_lock(exclusive=True):
                _save_last_good()
                os.replace(candidate, CATALOG_PATH)
                os.replace(candidate_sig, SIGNATURE_PATH)
                directory_fd = os.open(CATALOG_PATH.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                # The admitted candidate has survived signature verification
                # and atomic replacement; it is now the recovery point.
                with _VERIFY_LOCK:
                    _VERIFY_CACHE = None
                _save_last_good()
            with _PROCESS_LOCK:
                _CACHE = None
                _VECTOR_CACHE = None
                _KNOWLEDGE_CACHE = None
                _KNOWLEDGE_VECTOR_CACHE = None
            with _VERIFY_LOCK:
                _VERIFY_CACHE = None
            return current_source_hash
        finally:
            for leftover in (candidate, candidate_sig):
                try:
                    leftover.unlink()
                except FileNotFoundError:
                    pass


def admitted_catalog_version() -> str:
    """Return the source hash of the already admitted generation.

    This accessor and all request-time loaders are deliberately read-only.
    Compilation belongs to install, deploy, and the explicit background build
    command.  An absent or invalid catalog therefore has no usable version.
    """

    with _PROCESS_LOCK, _catalog_lock(exclusive=False):
        if not verify_catalog():
            return ""
        try:
            metadata = _metadata(CATALOG_PATH)
        except (OSError, sqlite3.Error):
            return ""
    if metadata.get("schema_version") != str(SCHEMA_VERSION):
        return ""
    return str(metadata.get("source_hash") or "")


def load_cards() -> tuple[Card, ...]:
    """Return only cards admitted by the signed read-only catalog."""

    global _CACHE
    admitted_hash = admitted_catalog_version()
    if not admitted_hash:
        raise ValueError("no signed Tutor catalog is admitted")
    with _PROCESS_LOCK:
        if _CACHE is not None and _CACHE[0] == admitted_hash:
            return _CACHE[1]
        with _catalog_lock(exclusive=False):
            if not verify_catalog():
                raise ValueError("tutor catalog signature invalid")
            connection = sqlite3.connect(
                f"file:{CATALOG_PATH}?mode=ro&immutable=1", uri=True)
            try:
                metadata = dict(connection.execute(
                    "SELECT key,value FROM metadata"))
                if metadata.get("schema_version") != str(SCHEMA_VERSION):
                    raise ValueError("unsupported tutor catalog schema")
                rows = connection.execute(
                    "SELECT payload_json FROM cards ORDER BY card_id").fetchall()
            finally:
                connection.close()
            cards = tuple(_deserialize(row[0]) for row in rows)
            _CACHE = (admitted_hash, cards)
            return cards


def load_knowledge_units() -> tuple[KnowledgeUnit, ...]:
    """Return only F2 units admitted by the signed read-only catalog."""

    global _KNOWLEDGE_CACHE
    admitted_hash = admitted_catalog_version()
    if not admitted_hash:
        raise ValueError("no signed Tutor catalog is admitted")
    with _PROCESS_LOCK:
        if (_KNOWLEDGE_CACHE is not None
                and _KNOWLEDGE_CACHE[0] == admitted_hash):
            return _KNOWLEDGE_CACHE[1]
        with _catalog_lock(exclusive=False):
            if not verify_catalog():
                raise ValueError("tutor catalog signature invalid")
            connection = sqlite3.connect(
                f"file:{CATALOG_PATH}?mode=ro&immutable=1", uri=True)
            try:
                metadata = dict(connection.execute(
                    "SELECT key,value FROM metadata"))
                if metadata.get("schema_version") != str(SCHEMA_VERSION):
                    raise ValueError("unsupported tutor catalog schema")
                rows = connection.execute(
                    "SELECT payload_json FROM knowledge_units "
                    "ORDER BY unit_id").fetchall()
            finally:
                connection.close()
            units = tuple(_deserialize_unit(row[0]) for row in rows)
            if len(units) != int(metadata.get("knowledge_count") or -1):
                raise ValueError("incomplete Tutor knowledge corpus")
            _KNOWLEDGE_CACHE = (admitted_hash, units)
            return units


def load_vector_index() -> VectorIndex:
    """Return the signed semantic matrix after strict shape validation."""

    global _VECTOR_CACHE
    admitted_hash = admitted_catalog_version()
    if not admitted_hash:
        raise ValueError("no signed Tutor catalog is admitted")
    with _PROCESS_LOCK:
        if _VECTOR_CACHE is not None and _VECTOR_CACHE[0] == admitted_hash:
            return _VECTOR_CACHE[1]
        with _catalog_lock(exclusive=False):
            if not verify_catalog():
                raise ValueError("tutor catalog signature invalid")
            connection = sqlite3.connect(
                f"file:{CATALOG_PATH}?mode=ro&immutable=1", uri=True)
            try:
                metadata = dict(connection.execute(
                    "SELECT key,value FROM metadata"))
                if metadata.get("schema_version") != str(SCHEMA_VERSION):
                    raise ValueError("unsupported tutor catalog schema")
                try:
                    dimension = int(metadata["embedding_dim"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("invalid tutor embedding dimension") from exc
                rows = connection.execute(
                    "SELECT card_id,lang,vector,dim,text_hash "
                    "FROM card_vectors ORDER BY card_id,lang").fetchall()
                expected = {
                    (card_id, lang, f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}")
                    for card_id, payload in connection.execute(
                        "SELECT card_id,payload_json FROM cards")
                    for lang, text in _deserialize(payload).semantic.items()
                }
            finally:
                connection.close()
        refs: list[tuple[str, str]] = []
        vectors: list[np.ndarray] = []
        actual = set()
        for card_id, lang, blob, row_dim, text_hash in rows:
            if int(row_dim) != dimension or len(blob) != dimension * 4:
                raise ValueError("invalid tutor vector shape")
            vector = np.frombuffer(blob, dtype="<f4").astype(
                np.float32, copy=True)
            if not np.isfinite(vector).all():
                raise ValueError("non-finite tutor vector")
            norm = float(np.linalg.norm(vector))
            if not 0.999 <= norm <= 1.001:
                raise ValueError("unnormalized tutor vector")
            refs.append((str(card_id), str(lang)))
            vectors.append(vector)
            actual.add((str(card_id), str(lang), str(text_hash)))
        if not vectors or actual != expected:
            raise ValueError("incomplete tutor semantic index")
        matrix = np.ascontiguousarray(np.stack(vectors), dtype=np.float32)
        index = VectorIndex(
            refs=tuple(refs),
            matrix=matrix,
            dimension=dimension,
            fingerprint=str(metadata.get("embedding_fingerprint") or ""),
        )
        _VECTOR_CACHE = (admitted_hash, index)
        return index


def load_knowledge_vector_index() -> VectorIndex:
    """Return the signed F2 semantic matrix after strict validation."""

    global _KNOWLEDGE_VECTOR_CACHE
    admitted_hash = admitted_catalog_version()
    if not admitted_hash:
        raise ValueError("no signed Tutor catalog is admitted")
    with _PROCESS_LOCK:
        if (_KNOWLEDGE_VECTOR_CACHE is not None
                and _KNOWLEDGE_VECTOR_CACHE[0] == admitted_hash):
            return _KNOWLEDGE_VECTOR_CACHE[1]
        with _catalog_lock(exclusive=False):
            if not verify_catalog():
                raise ValueError("tutor catalog signature invalid")
            connection = sqlite3.connect(
                f"file:{CATALOG_PATH}?mode=ro&immutable=1", uri=True)
            try:
                metadata = dict(connection.execute(
                    "SELECT key,value FROM metadata"))
                if metadata.get("schema_version") != str(SCHEMA_VERSION):
                    raise ValueError("unsupported tutor catalog schema")
                try:
                    dimension = int(metadata["embedding_dim"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("invalid tutor embedding dimension") from exc
                rows = connection.execute(
                    "SELECT unit_id,lang,vector,dim,text_hash "
                    "FROM knowledge_vectors ORDER BY unit_id").fetchall()
                expected = {
                    (unit_id, lang,
                     f"sha256:{hashlib.sha256(_knowledge_embedding_text(_deserialize_unit(payload)).encode('utf-8')).hexdigest()}")
                    for unit_id, lang, payload in connection.execute(
                        "SELECT unit_id,lang,payload_json FROM knowledge_units")
                }
            finally:
                connection.close()
        refs: list[tuple[str, str]] = []
        vectors: list[np.ndarray] = []
        actual = set()
        for unit_id, lang, blob, row_dim, text_hash in rows:
            if int(row_dim) != dimension or len(blob) != dimension * 4:
                raise ValueError("invalid Tutor knowledge vector shape")
            vector = np.frombuffer(blob, dtype="<f4").astype(
                np.float32, copy=True)
            if not np.isfinite(vector).all():
                raise ValueError("non-finite Tutor knowledge vector")
            norm = float(np.linalg.norm(vector))
            if not 0.999 <= norm <= 1.001:
                raise ValueError("unnormalized Tutor knowledge vector")
            refs.append((str(unit_id), str(lang)))
            vectors.append(vector)
            actual.add((str(unit_id), str(lang), str(text_hash)))
        if not vectors or actual != expected:
            raise ValueError("incomplete Tutor knowledge semantic index")
        matrix = np.ascontiguousarray(np.stack(vectors), dtype=np.float32)
        index = VectorIndex(
            refs=tuple(refs),
            matrix=matrix,
            dimension=dimension,
            fingerprint=str(metadata.get("embedding_fingerprint") or ""),
        )
        _KNOWLEDGE_VECTOR_CACHE = (admitted_hash, index)
        return index


def load_request_snapshot() -> CatalogSnapshot:
    """Load all request-time views while one generation is read-locked.

    The individual accessors remain useful to tools/tests, but production
    retrieval must never combine cards/text/vectors across an atomic compile
    swap. The process lock also prevents cache invalidation halfway through
    the snapshot.
    """

    with _PROCESS_LOCK, _catalog_lock(exclusive=False):
        before = admitted_catalog_version()
        if not before:
            raise ValueError("no signed Tutor catalog is admitted")
        cards = load_cards()
        units = load_knowledge_units()
        card_index = load_vector_index()
        knowledge_index = load_knowledge_vector_index()
        after = admitted_catalog_version()
        if before != after:
            raise RuntimeError("Tutor catalog generation changed during read")
        return CatalogSnapshot(
            version=before,
            cards=cards,
            units=units,
            card_index=card_index,
            knowledge_index=knowledge_index,
        )

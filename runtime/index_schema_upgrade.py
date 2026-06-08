"""index_schema_upgrade — upgrade incrementale degli indici v1 -> v2 (PR4).

Boot hook: scansione di `~/.local/share/metnos/index/image/<sha8>/<idx>/`
e per ogni dir con `meta.json::schema_version < INDEX_SCHEMA_VERSION`,
spawna un task asincrono di upgrade.

Upgrade body: arricchisce le entries con i campi di `ENRICHMENTS` mancanti
(`fields_for_domain(idx)`), riusando vectors.npy (no re-embed). Atomic
tmp+rename di entries.jsonl + bump meta.json.

Determinismo §7.9: tutto in helper Python; i compute_fn vivono in
index_schema.py e sono deterministici.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).parent))

from index_schema import (
    INDEX_SCHEMA_VERSION,
    IDX_TYPES,
    fields_for_domain,
    needs_upgrade,
)

log = logging.getLogger(__name__)


def _index_image_root() -> Path:
    """Test isolation via env vars (8/5/2026): vedi runtime/config.py."""
    import config as _C  # §7.11
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    return _C.PATH_USER_DATA / "index" / "image"


# Backward-compat alias usato da chiamate `if base == _INDEX_BASE`
def _get_index_base() -> Path:
    return _index_image_root()


def _list_index_dirs() -> list[Path]:
    """Iter su `<index_image_root>/<sha>/<idx>/`."""
    out: list[Path] = []
    base = _index_image_root()
    if not base.exists():
        return out
    for sha_dir in sorted(base.iterdir()):
        if not sha_dir.is_dir():
            continue
        for idx_dir in sorted(sha_dir.iterdir()):
            if idx_dir.is_dir() and (idx_dir / "meta.json").exists():
                out.append(idx_dir)
    return out


def _load_meta(idx_dir: Path) -> Optional[dict]:
    meta_path = idx_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _missing_fields(entry: dict, idx: str) -> list:
    """Lista di EnrichmentField mancanti su `entry` per il dominio `idx`."""
    return [f for f in fields_for_domain(idx) if f.name not in entry]


def _compute_field(field, *, img, face, faces, exif) -> object:
    try:
        return field.compute_fn(img=img, face=face, faces=faces, exif=exif)
    except Exception as ex:
        log.debug("compute_fn %s fallito: %s", field.name, ex)
        return None


def _upgrade_entries(entries: list[dict], idx: str) -> tuple[list[dict], int]:
    """Arricchisce entries con i campi ENRICHMENTS mancanti per il dominio.

    Apre l'immagine UNA volta per path (cache), denormalizza face_count_
    in_photo per persons (gruppi tutte le entries dello stesso path).
    Ritorna (entries_upgraded, n_enriched).
    """
    try:
        from PIL import Image
    except Exception:
        log.warning("PIL non disponibile, skip upgrade")
        return entries, 0

    # Per persons, raggruppa per path: tutte le entries dello stesso path
    # condividono face_count_in_photo (denormalizzazione).
    by_path: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        p = e.get("path")
        if isinstance(p, str):
            by_path.setdefault(p, []).append(i)

    n_enriched = 0
    img_cache: dict[str, tuple[object, dict]] = {}

    for path, idx_list in by_path.items():
        # Decidi se serve aprire l'immagine: solo se almeno una entry ha
        # campi mancanti che richiedono img/exif.
        any_missing = any(_missing_fields(entries[i], idx) for i in idx_list)
        if not any_missing:
            continue

        img_obj = None
        exif = {}
        if path in img_cache:
            img_obj, exif = img_cache[path]
        else:
            try:
                img_obj = Image.open(path)
                img_obj.load()
                try:
                    exif_raw = img_obj._getexif() or {}
                    exif = dict(exif_raw)
                except Exception:
                    exif = {}
            except Exception as ex:
                log.debug("upgrade: apertura %s fallita: %s", path, ex)
                img_obj = None
            img_cache[path] = (img_obj, exif)

        # Denormalizzazione faces per idx=persons: lista di tutte le entries
        # dello stesso path (ognuna ha bbox/score/landmarks?).
        faces_in_photo = (
            [entries[j] for j in idx_list] if idx == "persons" else None
        )

        for i in idx_list:
            e = entries[i]
            missing = _missing_fields(e, idx)
            if not missing:
                continue
            face = e if idx == "persons" else None
            for field in missing:
                if img_obj is None and field.name not in (
                    "bbox_area_fraction", "face_count_in_photo", "frontal_score"
                ):
                    # senza img non possiamo calcolare campi che dipendono dall'immagine
                    continue
                val = _compute_field(
                    field, img=img_obj, face=face,
                    faces=faces_in_photo, exif=exif,
                )
                e[field.name] = val
            n_enriched += 1

    # Cleanup PIL
    for img_obj, _exif in img_cache.values():
        if img_obj is not None:
            try:
                img_obj.close()
            except Exception:
                pass

    return entries, n_enriched


def _atomic_write_jsonl(path: Path, entries: list[dict]) -> None:
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.replace(path)


def upgrade_one(idx_dir: Path) -> dict:
    """Upgrade singolo indice (sync). Idempotente.

    Ritorna `{ok, idx_dir, idx, from_version, to_version, n_entries,
    n_enriched, skipped?, error?}`.
    """
    meta = _load_meta(idx_dir)
    if meta is None:
        return {
            "ok": False, "idx_dir": str(idx_dir),
            "error": "meta.json missing or unreadable",
        }
    if not needs_upgrade(meta):
        return {
            "ok": True, "idx_dir": str(idx_dir),
            "skipped": True, "reason": "already at current schema",
            "from_version": meta.get("schema_version", meta.get("version", 1)),
            "to_version": INDEX_SCHEMA_VERSION,
        }
    idx = meta.get("idx")
    if idx not in IDX_TYPES:
        return {
            "ok": False, "idx_dir": str(idx_dir),
            "error": f"unknown idx in meta.json: {idx!r}",
        }

    entries_path = idx_dir / "entries.jsonl"
    if not entries_path.exists():
        # No entries: solo bump meta
        meta["schema_version"] = INDEX_SCHEMA_VERSION
        (idx_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return {
            "ok": True, "idx_dir": str(idx_dir), "idx": idx,
            "from_version": meta.get("schema_version", meta.get("version", 1)),
            "to_version": INDEX_SCHEMA_VERSION,
            "n_entries": 0, "n_enriched": 0,
        }

    entries: list[dict] = []
    with entries_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    entries, n_enriched = _upgrade_entries(entries, idx)

    try:
        _atomic_write_jsonl(entries_path, entries)
    except OSError as e:
        return {
            "ok": False, "idx_dir": str(idx_dir),
            "error": f"write failed: {e}",
        }

    from_v = meta.get("schema_version", meta.get("version", 1))
    meta["schema_version"] = INDEX_SCHEMA_VERSION
    meta["last_upgrade_at"] = time.time()
    (idx_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return {
        "ok": True, "idx_dir": str(idx_dir), "idx": idx,
        "from_version": from_v, "to_version": INDEX_SCHEMA_VERSION,
        "n_entries": len(entries), "n_enriched": n_enriched,
    }


def upgrade_existing_indices_at_boot(
    *, async_: bool = True, base: Optional[Path] = None,
) -> dict:
    """Boot hook. Itera tutti gli indici e spawna upgrade per i v1.

    `async_=True` (default): upgrade in background thread per non bloccare
    il boot del server. `async_=False`: sync (per i test).

    Ritorna `{found, needing_upgrade, scheduled, results?}`.
    """
    base = base or _index_image_root()
    if not base.exists():
        return {"found": 0, "needing_upgrade": 0, "scheduled": 0}

    # Override per consentire test deterministico anche con boot async.
    if os.environ.get("METNOS_INDEX_UPGRADE_SYNC", "").lower() in ("1", "true", "yes"):
        async_ = False

    dirs = _list_index_dirs() if base == _index_image_root() else [
        d for sha in sorted(base.iterdir()) if sha.is_dir()
        for d in sorted(sha.iterdir()) if d.is_dir() and (d / "meta.json").exists()
    ]
    found = len(dirs)
    targets: list[Path] = []
    for d in dirs:
        meta = _load_meta(d)
        if meta is not None and needs_upgrade(meta):
            targets.append(d)

    if not targets:
        return {"found": found, "needing_upgrade": 0, "scheduled": 0}

    if not async_:
        results = [upgrade_one(d) for d in targets]
        return {
            "found": found, "needing_upgrade": len(targets),
            "scheduled": len(targets), "results": results,
        }

    def _runner(dirs_to_upgrade: Iterable[Path]) -> None:
        for d in dirs_to_upgrade:
            try:
                res = upgrade_one(d)
                log.info("schema upgrade %s: %s", d, res)
            except Exception:
                log.exception("schema upgrade crash %s", d)

    t = threading.Thread(
        target=_runner, args=(targets,), name="index-schema-upgrade",
        daemon=True,
    )
    t.start()
    return {
        "found": found, "needing_upgrade": len(targets),
        "scheduled": len(targets),
    }


__all__ = ["upgrade_one", "upgrade_existing_indices_at_boot"]

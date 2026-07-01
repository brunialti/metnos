"""index_schema_upgrade_v4 — migration v3 → v4 (ADR 0117).

Per ogni `<index_image_root>/<sha8>/` con sub-dirs scene/persons/gps in
schema v3, costruisce un unified storage `unified/` aggregando le tre
fonti per path distinto, augmentando con VLM description/keywords e
text embedding (riusa il chiamabile `_call_vlm` + `BGEEmbeddingService`).

NON cancella scene/persons/gps in questa fase. La cleanup avviene in una
phase separata DOPO la verifica unified completo (vedi ADR 0117 phase 11).

Boot hook idempotente (chiamabile da `metnos_http_server.make_app`):
- Scansiona index_image_root.
- Per ogni dir con `unified/meta.json::schema_version<4` o non esistente,
  spawna systemd-run --user transient unit (riuso pattern ADR 0093) per
  eseguire `migrate_one(<sha8>)` in background.
- Se VLM server (`localhost:8081`) non e' raggiungibile, l'unit segnala
  fail e ri-tenta al prossimo boot (no retry loop interno).
- Idempotente: se `unified/meta.json::schema_version==4` skip.

Determinismo §7.9: tutto il merge legacy → unified e' deterministico (no
LLM nella migrazione strutturale; le call VLM sono il SOLO componente
non-deterministico, marcate come tali).
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
    is_unified_schema,
)

log = logging.getLogger(__name__)


def _virt_vlm_model() -> str:
    """Nome del modello VLM dalla config virtualizzata (vlm_tiers.toml), per
    etichettare i metadati dell'indice senza hardcodare il modello."""
    try:
        from virt import get_vlm
        return get_vlm().get("model", "qwen3vl-2b")
    except Exception:
        return "qwen3vl-2b"


def _index_image_root() -> Path:
    import config as _C  # §7.11
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    return _C.PATH_USER_DATA / "index" / "image"


def _list_corpus_dirs(base: Optional[Path] = None) -> list[Path]:
    """Iter su `<index_image_root>/<sha8>/` (livello corpus, NON idx)."""
    base = base or _index_image_root()
    if not base.exists():
        return []
    out: list[Path] = []
    for sha_dir in sorted(base.iterdir()):
        if not sha_dir.is_dir():
            continue
        out.append(sha_dir)
    return out


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _load_meta(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_npy(path: Path):
    if not path.exists():
        return None
    try:
        import numpy as np
        return np.load(str(path))
    except Exception:
        return None


def needs_v4_migration(corpus_dir: Path) -> bool:
    """True se la migration v3→v4 e' richiesta su `corpus_dir`."""
    unified_meta = _load_meta(corpus_dir / "unified" / "meta.json")
    if unified_meta is not None and is_unified_schema(unified_meta):
        return False
    # Se almeno uno dei 3 idx legacy esiste, c'e' qualcosa da migrare.
    for idx in IDX_TYPES:
        if (corpus_dir / idx / "meta.json").exists():
            return True
    return False


def _aggregate_legacy(corpus_dir: Path) -> dict:
    """Aggrega le entries dei 3 idx legacy per path comune.

    Ritorna {path: {"scene": entry?, "persons": [entries...], "gps": entry?,
                    "scene_vec": ndarray?, "face_vecs": [ndarray...]}}.
    """

    out: dict[str, dict] = {}

    # Scene: 1 riga per foto, vector (768,) in scene/vectors.npy
    scene_dir = corpus_dir / "scene"
    if (scene_dir / "meta.json").exists():
        scene_entries = _load_jsonl(scene_dir / "entries.jsonl")
        scene_vecs = _load_npy(scene_dir / "vectors.npy")
        for i, e in enumerate(scene_entries):
            p = e.get("path")
            if not isinstance(p, str):
                continue
            slot = out.setdefault(p, {"persons": []})
            slot["scene"] = e
            if scene_vecs is not None and 0 <= i < len(scene_vecs):
                slot["scene_vec"] = scene_vecs[i]

    # Persons: 1 riga per faccia, vector (512,) in persons/vectors.npy
    persons_dir = corpus_dir / "persons"
    if (persons_dir / "meta.json").exists():
        persons_entries = _load_jsonl(persons_dir / "entries.jsonl")
        face_vecs = _load_npy(persons_dir / "vectors.npy")
        for i, e in enumerate(persons_entries):
            p = e.get("path")
            if not isinstance(p, str):
                continue
            slot = out.setdefault(p, {"persons": []})
            slot.setdefault("persons", []).append(e)
            if face_vecs is not None and 0 <= i < len(face_vecs):
                slot.setdefault("face_vecs", []).append(face_vecs[i])

    # GPS: 1 riga per foto, no vectors
    gps_dir = corpus_dir / "gps"
    if (gps_dir / "meta.json").exists():
        gps_entries = _load_jsonl(gps_dir / "entries.jsonl")
        for e in gps_entries:
            p = e.get("path")
            if not isinstance(p, str):
                continue
            slot = out.setdefault(p, {"persons": []})
            slot["gps"] = e

    return out


def _legacy_to_unified_entry(
    path: str, slot: dict,
    *, vlm_caller, text_embedder,
    text_idx_counter: int, face_idx_counter: int,
    new_emb_text_list: list, new_emb_face_list: list,
) -> tuple[dict, int, int]:
    """Costruisce una unified entry da uno slot aggregato di legacy entries.

    Riusa: cheap fields (mtime/size/dims/EXIF) dalla scene entry se presente,
    altrimenti dalla prima persons entry, altrimenti dalla gps entry.
    Riusa: face embeddings da persons.
    NUOVO: chiama VLM su path → description/keywords/location/activity.
    NUOVO: chiama text embedder su description.

    Ritorna (entry, text_idx_counter_updated, face_idx_counter_updated).
    """
    base_e = slot.get("scene") or (slot.get("persons") or [{}])[0] or slot.get("gps") or {}
    if not base_e:
        return {}, text_idx_counter, face_idx_counter

    entry: dict = {
        "path": path,
        "sha256": base_e.get("sha256", ""),
        "name": base_e.get("name", Path(path).name),
        "mtime": float(base_e.get("mtime", 0.0)),
        "size": int(base_e.get("size", 0)),
        "image_w": int(base_e.get("image_w", 0)),
        "image_h": int(base_e.get("image_h", 0)),
        "taken_at_iso": base_e.get("taken_at_iso"),
        "exif_gps": None,
        "description": "",
        "keywords": [],
        "location_hint": "",
        "activity_hint": "",
        "faces": [],
    }

    # Migrate GPS
    gps_e = slot.get("gps")
    if gps_e:
        lat = gps_e.get("lat")
        lon = gps_e.get("lon")
        if lat is not None and lon is not None:
            entry["exif_gps"] = {"lat": float(lat), "lon": float(lon)}

    # Migrate faces from persons (denormalize: 1 entry per faccia → faces[])
    persons_entries = slot.get("persons", [])
    face_vecs = slot.get("face_vecs", [])
    for fi, pe in enumerate(persons_entries):
        face_d: dict = {
            "bbox": list(pe.get("bbox", [])),
            "detect_score": float(pe.get("score", 0.0)),
        }
        if "landmarks" in pe:
            face_d["landmarks"] = pe["landmarks"]
        if fi < len(face_vecs):
            new_emb_face_list.append(face_vecs[fi])
            face_d["embedding_face_idx"] = face_idx_counter
            face_idx_counter += 1
        entry["faces"].append(face_d)

    # NEW: VLM call (se disponibile)
    if vlm_caller is not None:
        try:
            vlm_out = vlm_caller(Path(path))
            entry["description"] = vlm_out.get("description", "")
            entry["keywords"] = list(vlm_out.get("keywords", []))
            entry["location_hint"] = vlm_out.get("location_hint", "")
            entry["activity_hint"] = vlm_out.get("activity_hint", "")
            if "_vlm_error" in vlm_out:
                entry["_vlm_error"] = vlm_out["_vlm_error"]
        except Exception as e:
            entry["_vlm_error"] = f"call_failed: {e!r}"

    # NEW: text embedding
    if text_embedder is not None and entry["description"]:
        try:
            vec = text_embedder.embed_texts([entry["description"]])
            if vec.ndim == 2 and vec.shape[0] == 1:
                new_emb_text_list.append(vec[0])
                entry["embedding_text_idx"] = text_idx_counter
                text_idx_counter += 1
        except Exception:
            pass

    return entry, text_idx_counter, face_idx_counter


def migrate_one(corpus_dir: Path, *,
                vlm_caller=None,
                text_embedder=None,
                dry_run: bool = False) -> dict:
    """Migrate `<corpus_dir>/{scene,persons,gps}/` → `<corpus_dir>/unified/`.

    Argomenti opt:
    - `vlm_caller`: callable(Path) → dict (description/keywords/...). Se
      None, le entries unified avranno description="" (degraded migration).
    - `text_embedder`: object con embed_texts. Se None, niente embedding_text.
    - `dry_run`: se True, non scrive nulla, ritorna stat.

    Idempotente: se unified/meta.json::schema_version==4 skip.

    Ritorna `{ok, corpus_dir, n_paths_aggregated, n_unified, n_skipped,
              n_vlm_failed?, error?}`.
    """
    import numpy as np

    unified_dir = corpus_dir / "unified"
    unified_meta_path = unified_dir / "meta.json"
    existing = _load_meta(unified_meta_path)
    if existing is not None and is_unified_schema(existing):
        return {
            "ok": True,
            "corpus_dir": str(corpus_dir),
            "skipped": True,
            "reason": "already at v4",
            "n_paths_aggregated": 0,
            "n_unified": int(existing.get("n_entries", 0)),
        }

    aggregate = _aggregate_legacy(corpus_dir)
    if not aggregate:
        return {
            "ok": False,
            "corpus_dir": str(corpus_dir),
            "error": "no legacy indices found",
        }

    n_paths = len(aggregate)
    if dry_run:
        return {
            "ok": True,
            "corpus_dir": str(corpus_dir),
            "dry_run": True,
            "n_paths_aggregated": n_paths,
        }

    new_entries: list[dict] = []
    new_emb_text_list: list = []
    new_emb_face_list: list = []
    text_idx_counter = 0
    face_idx_counter = 0
    n_vlm_failed = 0

    for path, slot in aggregate.items():
        entry, text_idx_counter, face_idx_counter = _legacy_to_unified_entry(
            path, slot,
            vlm_caller=vlm_caller, text_embedder=text_embedder,
            text_idx_counter=text_idx_counter, face_idx_counter=face_idx_counter,
            new_emb_text_list=new_emb_text_list,
            new_emb_face_list=new_emb_face_list,
        )
        if not entry:
            continue
        if "_vlm_error" in entry:
            n_vlm_failed += 1
        new_entries.append(entry)

    # Atomic write
    unified_dir.mkdir(parents=True, exist_ok=True)
    entries_path = unified_dir / "entries.jsonl"
    tmp = entries_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for e in new_entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.replace(entries_path)

    if new_emb_text_list:
        emb_text = np.stack(new_emb_text_list, axis=0).astype("float32")
        p = unified_dir / "embeddings_text.npy"
        tp = p.with_suffix(".tmp.npy")
        np.save(str(tp), emb_text)
        tp.replace(p)
        dim_text = int(emb_text.shape[1])
    else:
        dim_text = 0

    if new_emb_face_list:
        emb_face = np.stack(new_emb_face_list, axis=0).astype("float32")
        p = unified_dir / "embeddings_face.npy"
        tp = p.with_suffix(".tmp.npy")
        np.save(str(tp), emb_face)
        tp.replace(p)

    # meta.json
    meta = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "version": INDEX_SCHEMA_VERSION,
        "n_entries": len(new_entries),
        "n_faces": len(new_emb_face_list),
        "model_text": "bge-m3" if new_emb_text_list else "none",
        "dim_text": dim_text,
        "model_vlm": (_virt_vlm_model() if vlm_caller else "none"),
        "model_face": "buffalo_l",
        "last_refresh_at": time.time(),
        "migrated_from_v3_at": time.time(),
    }
    unified_meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "corpus_dir": str(corpus_dir),
        "n_paths_aggregated": n_paths,
        "n_unified": len(new_entries),
        "n_vlm_failed": n_vlm_failed,
    }


def migrate_existing_indices_at_boot(
    *, async_: bool = True, base: Optional[Path] = None,
) -> dict:
    """Boot hook. Itera tutti i corpus dirs e spawna migration v3→v4 dove serve.

    `async_=True` (default): migration in background thread (ZERO blocco boot).
    `async_=False` per i test sync.

    NB: nel boot live, la migration richiede VLM up. Se VLM down, la migration
    si esegue ma i field VLM restano vuoti (degraded; admin puo' rilanciare).
    Il flag `METNOS_MIGRATION_REQUIRE_VLM=1` fa fallire piuttosto che degradare.

    Ritorna `{found, needing_migration, scheduled, results?}`.
    """
    base = base or _index_image_root()
    if not base.exists():
        return {"found": 0, "needing_migration": 0, "scheduled": 0}

    if os.environ.get("METNOS_INDEX_MIGRATE_SYNC", "").lower() in ("1", "true", "yes"):
        async_ = False

    corpus_dirs = _list_corpus_dirs(base)
    found = len(corpus_dirs)
    targets = [d for d in corpus_dirs if needs_v4_migration(d)]

    if not targets:
        return {"found": found, "needing_migration": 0, "scheduled": 0}

    # Probe VLM/text once per migration batch
    vlm_caller = None
    text_embedder = None
    require_vlm = os.environ.get("METNOS_MIGRATION_REQUIRE_VLM", "0") == "1"
    try:
        from executors.create_images_indices.create_images_indices import _call_vlm
        # probe: lazy — non chiamiamo qui, ma il caller lo fara' per ogni foto
        vlm_caller = _call_vlm
    except Exception as e:
        log.warning("VLM caller import fallito: %r", e)
        if require_vlm:
            return {
                "found": found,
                "needing_migration": len(targets),
                "scheduled": 0,
                "error": "VLM required but caller import failed",
            }

    try:
        from virt import get_embedder
        text_embedder = get_embedder("text")
    except Exception as e:
        log.warning("BGE embedder init fallito: %r", e)

    if not async_:
        results = [
            migrate_one(d, vlm_caller=vlm_caller, text_embedder=text_embedder)
            for d in targets
        ]
        return {
            "found": found, "needing_migration": len(targets),
            "scheduled": len(targets), "results": results,
        }

    def _runner(dirs_to_migrate: Iterable[Path]) -> None:
        for d in dirs_to_migrate:
            try:
                res = migrate_one(
                    d, vlm_caller=vlm_caller, text_embedder=text_embedder,
                )
                log.info("v4 migration %s: %s", d, res)
            except Exception:
                log.exception("v4 migration crash %s", d)

    t = threading.Thread(
        target=_runner, args=(targets,), name="index-schema-upgrade-v4",
        daemon=True,
    )
    t.start()
    return {
        "found": found, "needing_migration": len(targets),
        "scheduled": len(targets),
    }


__all__ = [
    "migrate_one",
    "migrate_existing_indices_at_boot",
    "needs_v4_migration",
]

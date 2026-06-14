#!/usr/bin/env python3
"""index_image_embed_backfill — genera `embeddings_image.npy` (SigLIP) per
indici unified esistenti che ne sono privi.

Iterazione su `entries.jsonl`, batch embedding via `ClipEngine`, write
incrementale `embeddings_image.npy.partial` + atomic rename a checkpoint.
Idempotente: skip se `embeddings_image.npy` gia' presente E shape allineata
con n_entries.

Usage:
  python3 -m runtime.jobs.index_image_embed_backfill [INDEX_DIR]

Default: tutti gli indici sotto `~/.local/share/metnos/index/image/*/unified/`
senza `embeddings_image.npy`. Aggiorna `meta.json::model_image = "clip_siglip"`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as _C  # noqa: E402
from clip_embedding import get_clip_engine  # noqa: E402

CHECKPOINT_EVERY = 200
BATCH = 8


def _list_target_indices() -> list[Path]:
    root = _C.PATH_USER_DATA / "index" / "image"
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        unified = d / "unified"
        meta = unified / "meta.json"
        if not meta.is_file():
            continue
        ent = unified / "entries.jsonl"
        if not ent.is_file():
            continue
        emb = unified / "embeddings_image.npy"
        if emb.is_file():
            # Skip if shape matches entries count (idempotent)
            try:
                arr = np.load(emb, mmap_mode="r")
                n_lines = sum(1 for _ in ent.open("rb"))
                if arr.shape[0] == n_lines:
                    print(f"SKIP {unified.parent.name}: embeddings_image already aligned ({arr.shape[0]})")
                    continue
            except (OSError, ValueError):
                pass
        out.append(unified)
    return out


def _process_index(unified_dir: Path) -> None:
    print(f"\n[{time.strftime('%H:%M:%S')}] Processing {unified_dir.parent.name}")
    entries_file = unified_dir / "entries.jsonl"
    paths: list[str] = []
    with entries_file.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            p = e.get("path", "")
            if isinstance(p, str) and p:
                paths.append(p)
    n_total = len(paths)
    print(f"  entries: {n_total}")
    if n_total == 0:
        return

    engine = get_clip_engine()
    if not engine.available:
        print(f"  SKIP: ClipEngine not available ({engine.health()})")
        return
    dim = engine.dimension
    out_partial = unified_dir / "embeddings_image.npy.partial"
    out_final = unified_dir / "embeddings_image.npy"

    # Resume from checkpoint if .partial exists
    resume_from = 0
    if out_partial.is_file():
        try:
            existing = np.load(out_partial, mmap_mode="r")
            resume_from = existing.shape[0]
            print(f"  resume from checkpoint: {resume_from}/{n_total}")
        except (OSError, ValueError):
            resume_from = 0

    all_emb: list[np.ndarray] = []
    if resume_from > 0:
        all_emb.append(np.array(np.load(out_partial)))
    t_start = time.time()
    last_checkpoint = resume_from

    for batch_start in range(resume_from, n_total, BATCH):
        batch_paths = paths[batch_start:batch_start + BATCH]
        # Pre-filter: only existing files
        valid_paths = []
        valid_mask = []
        for p in batch_paths:
            ok = Path(p).is_file()
            valid_mask.append(ok)
            if ok:
                valid_paths.append(p)
        if valid_paths:
            try:
                emb_valid = engine.embed_images(valid_paths, batch_size=BATCH)
            except Exception as ex:
                print(f"  batch error at {batch_start}: {type(ex).__name__}: {ex}")
                emb_valid = np.zeros((len(valid_paths), dim), dtype=np.float32)
        else:
            emb_valid = np.zeros((0, dim), dtype=np.float32)

        # Reassemble batch in original order: zero for missing files
        batch_emb = np.zeros((len(batch_paths), dim), dtype=np.float32)
        vi = 0
        for i, ok in enumerate(valid_mask):
            if ok:
                batch_emb[i] = emb_valid[vi]
                vi += 1
        all_emb.append(batch_emb)

        # Checkpoint every CHECKPOINT_EVERY entries
        processed = batch_start + len(batch_paths)
        if processed - last_checkpoint >= CHECKPOINT_EVERY:
            arr = np.vstack(all_emb)
            np.save(out_partial, arr)
            elapsed = time.time() - t_start
            rate = (processed - resume_from) / max(elapsed, 0.1)
            eta = (n_total - processed) / max(rate, 0.01)
            print(f"  [{time.strftime('%H:%M:%S')}] {processed}/{n_total} "
                  f"({100*processed/n_total:.1f}%) {rate:.1f} img/s "
                  f"ETA {eta/60:.1f}min")
            last_checkpoint = processed

    # Final write + rename
    final_arr = np.vstack(all_emb) if all_emb else np.zeros((0, dim), dtype=np.float32)
    np.save(out_final.with_suffix(".npy.tmp"), final_arr)
    out_final.with_suffix(".npy.tmp").replace(out_final)
    if out_partial.is_file():
        out_partial.unlink()

    # Update meta.json
    meta_file = unified_dir / "meta.json"
    try:
        meta = json.loads(meta_file.read_text())
        meta["model_image"] = "clip_siglip"
        meta["dim_image"] = int(dim)
        meta["last_image_embed_at"] = time.time()
        meta_file.write_text(json.dumps(meta, indent=2))
    except (OSError, json.JSONDecodeError) as ex:
        print(f"  meta update failed: {ex}")

    elapsed = time.time() - t_start
    print(f"  DONE: {n_total} embeddings in {elapsed/60:.1f}min")


def main():
    targets = []
    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]
    else:
        targets = _list_target_indices()
    if not targets:
        print("No indices needing image embedding backfill.")
        return
    print(f"Targets: {len(targets)}")
    for d in targets:
        try:
            _process_index(d)
        except Exception as ex:
            print(f"FAIL {d}: {type(ex).__name__}: {ex}")
    print(f"\n[{time.strftime('%H:%M:%S')}] All done.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Re-embed retroattivo del testo con path_context (ADR 0166 — intelligent
indexing). Applica all'indice ESISTENTE l'arricchimento di cartella senza
re-eseguire il VLM: riusa le `description`, calcola `folder_path_context` (una
classificazione LLM per cartella-unica, cache su disco) e ri-embedda solo il
testo `path_context + ". " + description`. Backup + scrittura atomica.

Uso:
  python3 runtime/jobs/reembed_path_context.py [<unified_idx_dir>] [--lang it] [--dry]
Senza dir: usa l'indice unificato di default (~/.local/share/metnos/index/image).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

# In runtime/jobs/: parents[2] = repo root (parents[1] sarebbe runtime/).
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "runtime"))
sys.path.insert(0, str(_ROOT / "executors" / "create_images_indices"))

import create_images_indices as C  # noqa: E402
from virt import get_embedder  # noqa: E402

_BATCH = 256


def _find_default_unified() -> Path | None:
    base = os.environ.get("METNOS_INDEX_ROOT")
    root = Path(base) / "image" if base else (
        Path(os.environ.get("METNOS_USER_DATA",
                            str(Path.home() / ".local/share/metnos")))
        / "index" / "image")
    if not root.exists():
        return None
    cands = sorted(root.glob("*/unified"))
    return cands[0] if cands else None


def _load_cache(idx_dir: Path) -> None:
    f = idx_dir / "folder_context_cache.json"
    if f.exists():
        try:
            raw = json.loads(f.read_text("utf-8"))
            C._FOLDER_CTX_CACHE.update({k: tuple(v) for k, v in raw.items()})
            print(f"[cache] caricate {len(raw)} cartelle classificate")
        except Exception as ex:
            print(f"[cache] load fallito: {ex}")


def _save_cache(idx_dir: Path) -> None:
    f = idx_dir / "folder_context_cache.json"
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({k: list(v) for k, v in C._FOLDER_CTX_CACHE.items()},
                              ensure_ascii=False), "utf-8")
    os.replace(tmp, f)


def reembed(idx_dir: Path, lang: str, dry: bool) -> None:
    ents_path = idx_dir / "entries.jsonl"
    emb_path = idx_dir / "embeddings_text.npy"
    entries = [json.loads(ln) for ln in ents_path.read_text("utf-8").splitlines()
               if ln.strip()]
    print(f"[load] entries={len(entries)}  idx_dir={idx_dir}")

    _load_cache(idx_dir)
    # 1) classifica le cartelle uniche (popola cache; persisti ogni 25).
    folders = sorted({Path(e.get("path", "")).parent.name for e in entries})
    print(f"[folders] uniche={len(folders)}")
    t0 = time.time()
    for i, fn in enumerate(folders):
        C.folder_path_context(fn, lang)  # memoizza in _FOLDER_CTX_CACHE
        if (i + 1) % 25 == 0:
            _save_cache(idx_dir)
            print(f"  classificate {i+1}/{len(folders)} "
                  f"({(time.time()-t0):.0f}s)")
    _save_cache(idx_dir)
    print(f"[folders] classificazione completa ({(time.time()-t0):.0f}s)")

    # 2) costruisci gli input di embedding e riassegna gli idx contigui.
    inputs: list[str] = []
    for e in entries:
        desc = e.get("description") or ""
        ctx = C.folder_path_context(Path(e.get("path", "")).parent.name, lang)
        e["path_context"] = ctx
        emb_in = (ctx + ". " + desc).strip() if desc else ctx.strip()
        if emb_in:
            e["embedding_text_idx"] = len(inputs)
            inputs.append(emb_in)
        else:
            e.pop("embedding_text_idx", None)
    print(f"[embed] testi da embeddare={len(inputs)} (di {len(entries)} entries)")

    if dry:
        from collections import Counter
        cats = Counter(C._FOLDER_CTX_CACHE.get(
            __import__("re").sub(r"\b(19|20)\d\d\b", "",
                                 Path(e["path"]).parent.name).replace("-", " ").strip(),
            ("?", ""))[0] for e in entries)
        print(f"[dry] categorie entries: {dict(cats)}")
        print("[dry] nessuna scrittura")
        return

    # 3) embedding BGE-M3 in batch.
    te = get_embedder("text")
    vecs = np.zeros((len(inputs), 1024), dtype=np.float32)
    t0 = time.time()
    for s in range(0, len(inputs), _BATCH):
        chunk = inputs[s:s + _BATCH]
        v = te.embed_texts(chunk)
        vecs[s:s + len(chunk)] = v.astype(np.float32, copy=False)
        if (s // _BATCH) % 10 == 0:
            print(f"  embed {s+len(chunk)}/{len(inputs)} ({(time.time()-t0):.0f}s)")
    print(f"[embed] completo ({(time.time()-t0):.0f}s)")

    # 4) backup + scrittura atomica.
    ts = time.strftime("%Y%m%d-%H%M%S")
    for src in (ents_path, emb_path):
        if src.exists():
            bak = src.with_suffix(src.suffix + f".bak-{ts}")
            bak.write_bytes(src.read_bytes())
            print(f"[backup] {src.name} → {bak.name}")
    # np.save APPENDE '.npy' se il nome non termina in '.npy'; passando un
    # file-object il nome resta invariato → scrittura atomica corretta.
    tmp_npy = emb_path.with_name(emb_path.name + ".tmp")
    with tmp_npy.open("wb") as fh:
        np.save(fh, vecs)
    os.replace(tmp_npy, emb_path)
    tmp_jsonl = ents_path.with_suffix(".jsonl.tmp")
    with tmp_jsonl.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp_jsonl, ents_path)
    print(f"[write] {ents_path.name} + {emb_path.name} riscritti "
          f"(emb shape={vecs.shape})")


def main() -> int:
    args = sys.argv[1:]
    lang = "it"
    dry = False
    pos = []
    for a in args:
        if a == "--dry":
            dry = True
        elif a.startswith("--lang"):
            lang = a.split("=", 1)[1] if "=" in a else "it"
        else:
            pos.append(a)
    idx_dir = Path(pos[0]) if pos else _find_default_unified()
    if not idx_dir or not (idx_dir / "entries.jsonl").exists():
        print(f"ERRORE: indice unificato non trovato ({idx_dir})")
        return 2
    reembed(idx_dir, lang, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

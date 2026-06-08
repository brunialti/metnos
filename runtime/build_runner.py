"""build_runner — entry point per build asincrona di indici immagine
(ADR 0093, 6/5/2026).

Eseguito via systemd-run --user --transient. Lavora a batch (default
N=500 foto), scrive `progress.json` ogni batch + checkpoint in
`<idx_dir>/.tmp_<rand>/` (entries.jsonl + vectors.npy parziali). A fine
build atomic-rename `tmp_<rand>` → `<idx_dir>/`. Su SIGTERM salva
progress + flush e exit graceful (resume continua dal next_index).

Notification: a completamento scrive marker in
`/tmp/metnos_build_complete/<digest>_<idx>.json` con metadata. Il
notification dispatcher async del daemon HTTP legge i marker e invia
via send_messages all'actor.

Pattern (the design guide §7.2 semplicità):
  - una sola entry, una sola walk, batch loop.
  - riusa builders di create_images_indices (no duplicazione).
  - atomic write end-of-build, niente race condition.
  - resume robusto: se `.tmp_<rand>/` non esiste, riparte from scratch.

Storage layout:
  <idx_dir>/                       # final, atomic-renamed at end
  <idx_dir>/.tmp_<rand>/           # work-in-progress
    entries.jsonl                  # parziale, append per batch
    vectors.npy                    # parziale, riscritto per batch
    meta.json                      # finale solo a end
  <progress_dir>/<digest>_<idx>.json   # progress.json (state machine)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import signal
import string
import sys
import time
from pathlib import Path

# Permetti import dei moduli runtime + executor
_RUNTIME = Path(__file__).resolve().parent
_EXECUTORS = _RUNTIME.parent / "executors"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXECUTORS / "create_images_indices"))

import config as _C  # §7.11


_PROGRESS_DIR = _C.PATH_USER_STATE / "build_progress"
_COMPLETE_DIR = Path("/tmp/metnos_build_complete")
_VALID_IDX = ("scene", "persons", "gps")


_INDEX_BASE: Path | None = None  # test override via setattr


def _index_image_root() -> Path:
    """Test isolation via env vars (8/5/2026): vedi runtime/config.py."""
    if _INDEX_BASE is not None:
        return Path(_INDEX_BASE)
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    return _C.PATH_USER_DATA / "index" / "image"


def _digest_of(base_path: Path) -> str:
    # Path LOGICAL coerente con _index_dir delle altre componenti image-index.
    return hashlib.sha256(str(base_path).encode("utf-8")).hexdigest()[:16]


def _index_dir(base_path: Path, idx: str) -> Path:
    return _index_image_root() / _digest_of(base_path) / idx


def _progress_path(base_path: Path, idx: str) -> Path:
    return _PROGRESS_DIR / f"{_digest_of(base_path)}_{idx}.json"


def _complete_marker_path(base_path: Path, idx: str) -> Path:
    return _COMPLETE_DIR / f"{_digest_of(base_path)}_{idx}.json"


def _walk_images(base: Path, image_exts: set[str]) -> list[Path]:
    out: list[Path] = []
    for root, _dirs, files in os.walk(base):
        for f in files:
            if Path(f).suffix.lower() in image_exts:
                out.append(Path(root) / f)
    out.sort()  # determinismo per resume robusto
    return out


def _new_tmp_dir(idx_dir: Path) -> Path:
    """Crea o riusa un .tmp_<rand>/. Se ne esiste UNO solo, lo riusa per
    resume; altrimenti ne crea uno nuovo."""
    idx_dir.parent.mkdir(parents=True, exist_ok=True)
    existing = sorted(idx_dir.parent.glob(f"{idx_dir.name}.tmp_*"))
    if len(existing) == 1 and existing[0].is_dir():
        return existing[0]
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    tmp = idx_dir.parent / f"{idx_dir.name}.tmp_{rand}"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _write_progress(prog_path: Path, payload: dict) -> None:
    prog_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = prog_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    tmp.replace(prog_path)


def _read_progress(prog_path: Path) -> dict | None:
    if not prog_path.exists():
        return None
    try:
        return json.loads(prog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# --- SIGTERM graceful ---------------------------------------------------------

class _State:
    """Stato condiviso fra signal handler e loop principale."""
    stop_requested: bool = False


def _install_sigterm() -> None:
    def _handler(signum, frame):
        _State.stop_requested = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


# --- Build core ---------------------------------------------------------------

def _persist_partial(tmp_dir: Path, entries: list[dict], vectors) -> None:
    """Scrive entries.jsonl + vectors.npy parziali nel tmp_dir."""
    ents_path = tmp_dir / "entries.jsonl"
    ents_tmp = ents_path.with_suffix(".jsonl.tmp")
    with ents_tmp.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    ents_tmp.replace(ents_path)
    if vectors is not None and len(entries) > 0:
        import numpy as np
        vec_path = tmp_dir / "vectors.npy"
        # np.save aggiunge .npy se manca: passiamo path senza suffisso e
        # poi rinominiamo. Il file effettivo è "<base>.tmp.npy".
        vec_tmp_base = tmp_dir / "vectors.tmp"
        vec_tmp_actual = tmp_dir / "vectors.tmp.npy"
        np.save(str(vec_tmp_base), vectors)
        vec_tmp_actual.replace(vec_path)


def _atomic_finalize(tmp_dir: Path, idx_dir: Path, *, idx: str, model: str,
                      dim: int, base_path: Path, n_entries: int) -> None:
    """Scrive meta.json + os.rename(tmp_dir, idx_dir) atomicamente."""
    meta = {
        "version": 1,
        "idx": idx,
        "model": model,
        "dim": int(dim),
        "n_entries": int(n_entries),
        "base_path": str(base_path),
        "last_refresh_at": time.time(),
    }
    (tmp_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    # Se idx_dir esiste già (caso: rebuild force=true), spostalo in cestino.
    if idx_dir.exists():
        backup = idx_dir.with_name(f"{idx_dir.name}.old_{int(time.time())}")
        os.rename(idx_dir, backup)
    os.rename(tmp_dir, idx_dir)


def _emit_complete_marker(base_path: Path, idx: str, payload: dict) -> None:
    _COMPLETE_DIR.mkdir(parents=True, exist_ok=True)
    marker = _complete_marker_path(base_path, idx)
    tmp = marker.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    tmp.replace(marker)


def _build_batched(*, base_path: Path, idx: str, paths: list[Path],
                    next_index: int, batch_size: int, tmp_dir: Path,
                    prog_path: Path) -> dict:
    """Loop di build a batch, con checkpoint progress + persist parziale.

    Ritorna dict con esito (n_done, n_total, errors, finished).
    """
    import create_images_indices as cii
    import numpy as np

    builder = cii._BUILDERS[idx]
    image_exts = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
                   ".tiff", ".tif", ".gif", ".bmp"}
    n_total = len(paths)
    started_at = time.time()

    # Riusa entries esistenti dal tmp (resume) o vuoto
    existing_entries: list[dict] = []
    existing_vecs = None
    ents_path = tmp_dir / "entries.jsonl"
    if ents_path.exists():
        try:
            with ents_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        existing_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            existing_entries = []
    vec_path = tmp_dir / "vectors.npy"
    if vec_path.exists():
        try:
            existing_vecs = np.load(str(vec_path))
        except Exception:
            existing_vecs = None

    accumulated_entries = list(existing_entries)
    accumulated_vecs = existing_vecs
    errors_total = 0
    model_name = "unknown"
    dim = 0

    while next_index < n_total:
        if _State.stop_requested:
            # SIGTERM/SIGINT: salva e exit graceful (resume riprende qui)
            _write_progress(prog_path, {
                "state": "interrupted",
                "next_index": next_index,
                "n_done": next_index,
                "n_total": n_total,
                "errors": errors_total,
                "last_update": time.time(),
                "started_at": started_at,
                "tmp_dir": str(tmp_dir),
                "base_path": str(base_path),
                "idx": idx,
            })
            return {"finished": False, "interrupted": True,
                     "n_done": next_index, "n_total": n_total,
                     "errors": errors_total}

        end = min(next_index + batch_size, n_total)
        batch_paths = paths[next_index:end]

        # Invoca il builder per il batch — riusa la logica esistente di
        # create_images_indices (incremental, dedup per mtime/size)
        try:
            new_entries, new_vecs, ok_n, fail_n, model_name, dim = builder(
                batch_paths, accumulated_entries, accumulated_vecs, force=False,
            )
            errors_total += fail_n
            accumulated_entries = new_entries
            accumulated_vecs = new_vecs
        except Exception as e:
            errors_total += len(batch_paths)
            _write_progress(prog_path, {
                "state": "error",
                "next_index": next_index,
                "n_done": next_index,
                "n_total": n_total,
                "errors": errors_total,
                "last_update": time.time(),
                "started_at": started_at,
                "tmp_dir": str(tmp_dir),
                "base_path": str(base_path),
                "idx": idx,
                "error_msg": f"{type(e).__name__}: {e}",
            })
            return {"finished": False, "error": str(e),
                     "n_done": next_index, "n_total": n_total,
                     "errors": errors_total}

        next_index = end

        # Persist parziale + progress checkpoint
        try:
            _persist_partial(tmp_dir, accumulated_entries, accumulated_vecs)
        except OSError:
            pass

        elapsed = time.time() - started_at
        eta_s = 0.0
        if next_index > 0 and elapsed > 0:
            rate = next_index / elapsed
            eta_s = max(0.0, (n_total - next_index) / rate) if rate > 0 else 0.0
        _write_progress(prog_path, {
            "state": "running",
            "next_index": next_index,
            "n_done": next_index,
            "n_total": n_total,
            "errors": errors_total,
            "eta_s": eta_s,
            "last_update": time.time(),
            "started_at": started_at,
            "tmp_dir": str(tmp_dir),
            "base_path": str(base_path),
            "idx": idx,
            "model": model_name,
        })

    # Done: atomic finalize
    idx_dir = _index_dir(base_path, idx)
    n_entries_final = len(accumulated_entries)
    try:
        _atomic_finalize(tmp_dir, idx_dir, idx=idx, model=model_name,
                         dim=dim, base_path=base_path,
                         n_entries=n_entries_final)
    except OSError as e:
        _write_progress(prog_path, {
            "state": "error",
            "next_index": next_index,
            "n_done": next_index,
            "n_total": n_total,
            "errors": errors_total,
            "last_update": time.time(),
            "started_at": started_at,
            "tmp_dir": str(tmp_dir),
            "base_path": str(base_path),
            "idx": idx,
            "error_msg": f"finalize: {e}",
        })
        return {"finished": False, "error": str(e),
                 "n_done": next_index, "n_total": n_total,
                 "errors": errors_total}

    duration_s = time.time() - started_at
    _write_progress(prog_path, {
        "state": "done",
        "next_index": n_total,
        "n_done": n_total,
        "n_total": n_total,
        "errors": errors_total,
        "duration_s": duration_s,
        "last_update": time.time(),
        "started_at": started_at,
        "base_path": str(base_path),
        "idx": idx,
        "model": model_name,
        "n_entries": n_entries_final,
    })
    return {
        "finished": True,
        "n_done": n_total,
        "n_total": n_total,
        "errors": errors_total,
        "duration_s": duration_s,
        "n_entries": n_entries_final,
        "model": model_name,
        "dim": dim,
    }


# --- CLI / main ---------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Async build of image indices")
    p.add_argument("--base-path", required=True)
    p.add_argument("--idx", required=True, choices=_VALID_IDX)
    p.add_argument("--actor", default="host")
    p.add_argument("--channel", default="")
    p.add_argument("--chat-id", default="")
    p.add_argument("--resume", default="true")
    p.add_argument("--batch-size", type=int, default=500)
    args = p.parse_args(argv)

    base_path = Path(os.path.expanduser(args.base_path)).resolve()
    if not base_path.exists() or not base_path.is_dir():
        sys.stderr.write(f"base_path not found or not a dir: {base_path}\n")
        return 2

    idx = args.idx
    resume = str(args.resume).lower() in ("true", "1", "yes")
    batch_size = max(1, int(args.batch_size))

    _install_sigterm()

    idx_dir = _index_dir(base_path, idx)
    prog_path = _progress_path(base_path, idx)
    image_exts = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
                   ".tiff", ".tif", ".gif", ".bmp"}

    paths = _walk_images(base_path, image_exts)
    n_total = len(paths)

    # Resume: leggi progress.json esistente per next_index
    next_index = 0
    if resume:
        prev = _read_progress(prog_path)
        if prev and prev.get("base_path") == str(base_path) and prev.get("idx") == idx:
            ni = int(prev.get("next_index") or 0)
            if 0 <= ni <= n_total:
                next_index = ni

    tmp_dir = _new_tmp_dir(idx_dir)

    _write_progress(prog_path, {
        "state": "running",
        "next_index": next_index,
        "n_done": next_index,
        "n_total": n_total,
        "errors": 0,
        "last_update": time.time(),
        "started_at": time.time(),
        "tmp_dir": str(tmp_dir),
        "base_path": str(base_path),
        "idx": idx,
        "actor": args.actor,
        "channel": args.channel,
        "chat_id": args.chat_id,
    })

    if n_total == 0:
        # Nessuna foto: scrivi indice vuoto + done
        try:
            _atomic_finalize(tmp_dir, idx_dir, idx=idx, model="empty",
                             dim=0, base_path=base_path, n_entries=0)
        except OSError as e:
            sys.stderr.write(f"finalize empty: {e}\n")
            return 3
        _write_progress(prog_path, {
            "state": "done",
            "next_index": 0,
            "n_done": 0,
            "n_total": 0,
            "errors": 0,
            "duration_s": 0.0,
            "last_update": time.time(),
            "started_at": time.time(),
            "base_path": str(base_path),
            "idx": idx,
            "n_entries": 0,
        })
        _emit_complete_marker(base_path, idx, {
            "ok": True, "n_entries": 0, "duration_s": 0.0,
            "errors_count": 0, "actor": args.actor,
            "channel": args.channel, "chat_id": args.chat_id,
            "base_path": str(base_path), "idx": idx,
        })
        return 0

    res = _build_batched(
        base_path=base_path, idx=idx, paths=paths,
        next_index=next_index, batch_size=batch_size,
        tmp_dir=tmp_dir, prog_path=prog_path,
    )

    if res.get("interrupted"):
        # Exit code 0 per non far loggare a systemd come failure (è graceful)
        sys.stderr.write(f"build interrupted: {res['n_done']}/{res['n_total']}\n")
        return 0
    if not res.get("finished"):
        sys.stderr.write(f"build error: {res.get('error', '?')}\n")
        return 4

    _emit_complete_marker(base_path, idx, {
        "ok": True,
        "n_entries": res.get("n_entries", 0),
        "duration_s": res.get("duration_s", 0.0),
        "errors_count": res.get("errors", 0),
        "actor": args.actor,
        "channel": args.channel,
        "chat_id": args.chat_id,
        "base_path": str(base_path),
        "idx": idx,
        "model": res.get("model", "unknown"),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())

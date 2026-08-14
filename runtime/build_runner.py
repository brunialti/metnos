"""Runner asincrono dell'indice immagini unificato.

Viene avviato da :mod:`build_orchestrator` in una transient unit systemd.
Mantiene progress e marker di notifica compatibili con il daemon HTTP, ma
delega l'unica implementazione del build a ``create_images_indices``.

Il precedente runner conservava la pipeline v3 ``scene/persons/gps`` e
richiamava ``create_images_indices._BUILDERS``, rimosso con ADR 0117: quella
strada non poteva piu' completare. Questo adapter evita una seconda pipeline
di indicizzazione e quindi il drift fra build sincrono e asincrono.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent
_EXECUTOR_DIR = _RUNTIME.parent / "executors" / "create_images_indices"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXECUTOR_DIR))

import config as _C
from index_schema import corpus_digest, image_index_root

_PROGRESS_DIR = _C.PATH_USER_STATE / "build_progress"
_COMPLETE_DIR = Path("/tmp/metnos_build_complete")
_VALID_IDX = ("unified",)
_INDEX_BASE: Path | None = None  # test override
_HEARTBEAT_S = 30.0


def _index_image_root() -> Path:
    return image_index_root(_INDEX_BASE)


def _digest_of(base_path: Path) -> str:
    return corpus_digest(base_path)


def _index_dir(base_path: Path, idx: str = "unified") -> Path:
    return _index_image_root() / _digest_of(base_path) / idx


def _progress_path(base_path: Path, idx: str = "unified") -> Path:
    return _PROGRESS_DIR / f"{_digest_of(base_path)}_{idx}.json"


def _complete_marker_path(base_path: Path, idx: str = "unified") -> Path:
    return _COMPLETE_DIR / f"{_digest_of(base_path)}_{idx}.json"


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _heartbeat(stop: threading.Event, path: Path, payload: dict) -> None:
    """Mantiene vivo il record mentre il builder lavora senza callback."""
    while not stop.wait(_HEARTBEAT_S):
        current = dict(payload)
        current["last_update"] = time.time()
        try:
            _write_json_atomic(path, current)
        except OSError:
            # Il risultato finale resta autoritativo; un heartbeat non deve
            # far fallire un build gia' in corso.
            continue


def _public_result(result: dict) -> dict:
    return {
        key: value for key, value in result.items()
        if not str(key).startswith("_") and key != "entries"
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Async build of the unified image index")
    parser.add_argument("--base-path", required=True)
    parser.add_argument("--idx", default="unified", choices=_VALID_IDX)
    parser.add_argument("--actor", default="host")
    parser.add_argument("--channel", default="")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--resume", default="true")  # compat CLI v3
    parser.add_argument("--batch-size", type=int, default=500)  # compat v3
    parser.add_argument("--max-files", type=int, default=50000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    base_path = Path(os.path.expanduser(args.base_path)).resolve()
    if not base_path.is_dir():
        sys.stderr.write(f"base_path not found or not a dir: {base_path}\n")
        return 2

    progress_path = _progress_path(base_path)
    started_at = time.time()
    running = {
        "state": "running",
        "n_done": 0,
        "n_total": 0,
        "errors": 0,
        "last_update": started_at,
        "started_at": started_at,
        "base_path": str(base_path),
        "idx": "unified",
        "actor": args.actor,
        "channel": args.channel,
        "chat_id": args.chat_id,
        "index_dir": str(_index_dir(base_path)),
    }
    try:
        _write_json_atomic(progress_path, running)
    except OSError as exc:
        sys.stderr.write(f"cannot write build progress: {exc}\n")
        return 3

    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(stop_heartbeat, progress_path, running),
        name="metnos-index-heartbeat",
        daemon=True,
    )
    heartbeat.start()

    result: dict
    try:
        import create_images_indices as builder
        result = builder.invoke({
            "base_path": str(base_path),
            "force": bool(args.force),
            "max_files": max(1, int(args.max_files)),
        })
        if not isinstance(result, dict):
            result = {"ok": False, "error": "builder returned a non-object"}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=2.0)

    ended_at = time.time()
    ok = bool(result.get("ok"))
    n_entries = int(result.get("n_entries_total") or
                    result.get("ok_count") or 0)
    errors = int(result.get("fail_count") or (0 if ok else 1))
    final = {
        **running,
        **_public_result(result),
        "state": "done" if ok else "error",
        "n_done": n_entries,
        "n_total": n_entries,
        "n_entries": n_entries,
        "errors": errors,
        "duration_s": ended_at - started_at,
        "last_update": ended_at,
    }
    try:
        _write_json_atomic(progress_path, final)
        _write_json_atomic(_complete_marker_path(base_path), {
            "ok": ok,
            "n_entries": n_entries,
            "duration_s": final["duration_s"],
            "errors_count": errors,
            "actor": args.actor,
            "channel": args.channel,
            "chat_id": args.chat_id,
            "base_path": str(base_path),
            "idx": "unified",
            "error": result.get("error", "") if not ok else "",
        })
    except OSError as exc:
        sys.stderr.write(f"cannot persist build result: {exc}\n")
        return 3

    if not ok:
        sys.stderr.write(f"build error: {result.get('error', '?')}\n")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

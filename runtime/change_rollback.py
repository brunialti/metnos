"""change_rollback — rollback fisico per kind (ADR 0158, Fase 3.2).

Chiamato da `change_observer._physical_rollback` quando un APPLIED
fallisce le metriche di validation. Idempotente: re-run no-op se stato
gia' ripristinato.

Per kind:
  - create_executor    → archive synth dir + remove from catalog
  - extend_executor    → restore manifest da rollback_blob + re-sign
  - dedupe_executors   → remove alias + undeprecate B
  - materialize_pipeline → state = demoted
  - cache_pattern      → state = demoted
  - reject_pattern     → remove riga da rejected_patterns.jsonl
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable

import config as C
from change_intents import (
    KIND_CACHE_PATTERN,
    KIND_CREATE_EXECUTOR,
    KIND_DEDUPE_EXECUTORS,
    KIND_EXTEND_EXECUTOR,
    KIND_MATERIALIZE_PIPELINE,
    KIND_REJECT_PATTERN,
    ChangeIntent,
)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- Rollback handlers ---------------------------------------------------

def _rollback_create_executor(ci: ChangeIntent) -> dict:
    effect = ci.applied_effect or {}
    name = effect.get("executor_name") or ci.intent_target
    synth_dir = C.PATH_SYNTH_EXECUTORS / name
    if not synth_dir.is_dir():
        return {"executor_name": name, "note": "synth_dir already absent"}
    archive_root = C.PATH_USER_DATA / "executors_archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    dst = archive_root / f"{name}_{int(time.time())}"
    shutil.move(str(synth_dir), str(dst))
    # mark in executor_stats
    db = C.PATH_USER_STATE / "executor_stats.db"
    if db.exists():
        try:
            cn = sqlite3.connect(str(db), timeout=10.0)
            cn.execute(
                "UPDATE executor_stats SET archived_at=? WHERE name=? "
                "AND archived_at IS NULL",
                (_iso_now(), name),
            )
            cn.commit()
            cn.close()
        except sqlite3.Error:
            pass
    return {"executor_name": name, "archived_to": str(dst)}


def _rollback_extend_executor(ci: ChangeIntent) -> dict:
    effect = ci.applied_effect or {}
    name = effect.get("executor_name") or ci.intent_target
    rollback_blob = effect.get("rollback_blob_path")
    if not rollback_blob:
        return {"executor_name": name, "error": "no rollback_blob_path in effect"}
    blob = Path(rollback_blob)
    if not blob.exists():
        return {"executor_name": name, "error": f"rollback_blob missing: {blob}"}
    # Find manifest dir
    from runtime.change_applier_extend import _resolve_executor_dir
    mdir = _resolve_executor_dir(name)
    if mdir is None:
        return {"executor_name": name, "error": "manifest dir not found"}
    manifest_path = mdir / "manifest.toml"
    # Restore
    manifest_path.write_text(blob.read_text(encoding="utf-8"), encoding="utf-8")
    # Re-sign
    try:
        from sign import sign_executor
        digest, sig_path = sign_executor(mdir)
        return {"executor_name": name,
                "manifest_restored_from": str(blob),
                "new_digest": digest, "sig_path": str(sig_path)}
    except Exception as exc:
        return {"executor_name": name,
                "manifest_restored_from": str(blob),
                "re_sign_error": str(exc)[:200]}


def _rollback_dedupe_executors(ci: ChangeIntent) -> dict:
    effect = ci.applied_effect or {}
    b = effect.get("alias_from") or ci.intent_body.get("b") or ci.intent_target
    aliases_path = C.PATH_USER_DATA / "executor_aliases.json"
    removed = False
    if aliases_path.exists():
        try:
            aliases = json.loads(aliases_path.read_text())
            if b in aliases:
                del aliases[b]
                aliases_path.write_text(json.dumps(aliases, indent=2))
                removed = True
        except (json.JSONDecodeError, OSError):
            pass
    # Undeprecate B
    db = C.PATH_USER_STATE / "executor_stats.db"
    if db.exists():
        try:
            cn = sqlite3.connect(str(db), timeout=10.0)
            cn.execute(
                "UPDATE executor_stats SET deprecated_at=NULL WHERE name=?",
                (b,),
            )
            cn.commit()
            cn.close()
        except sqlite3.Error:
            pass
    return {"alias_removed": removed, "undeprecated": b}


def _rollback_materialize_pipeline(ci: ChangeIntent) -> dict:
    body = ci.intent_body or {}
    shape_hash = body.get("path_shape_hash")
    if not shape_hash:
        return {"error": "no path_shape_hash"}
    db = C.DB_MULTI_TOOL_PATHS
    if not db.exists():
        return {"note": "multi_tool_paths.sqlite missing"}
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        cur = cn.execute(
            "UPDATE multi_tool_paths SET state='demoted' "
            "WHERE path_shape_hash=?", (shape_hash,),
        )
        cn.commit()
    finally:
        cn.close()
    return {"shape_hash": shape_hash, "state_set_to": "demoted",
            "rows_updated": cur.rowcount}


def _rollback_cache_pattern(ci: ChangeIntent) -> dict:
    body = ci.intent_body or {}
    canonical = body.get("canonical_query")
    tool_name = body.get("tool_name") or ci.intent_target
    db = C.DB_MNESTOMA
    if not db.exists():
        return {"note": "mnest.sqlite missing"}
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        cur = cn.execute(
            "UPDATE canonical_query_log SET state='demoted' "
            "WHERE canonical_query=? AND tool_name=?",
            (canonical, tool_name),
        )
        cn.commit()
    finally:
        cn.close()
    return {"canonical_query": canonical, "tool_name": tool_name,
            "state_set_to": "demoted", "rows_updated": cur.rowcount}


def _rollback_reject_pattern(ci: ChangeIntent) -> dict:
    body = ci.intent_body or {}
    canonical = body.get("canonical_query") or ci.intent_target
    tools = body.get("tools_sequence") or []
    rp = C.PATH_USER_DATA / "rejected_patterns.jsonl"
    if not rp.exists():
        return {"note": "rejected_patterns.jsonl missing"}
    kept_lines: list[str] = []
    removed = 0
    try:
        with rp.open() as fp:
            for line in fp:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                if (rec.get("canonical_query") == canonical
                        and rec.get("tools_sequence") == tools):
                    removed += 1
                else:
                    kept_lines.append(line)
        rp.write_text("".join(kept_lines))
    except OSError as exc:
        return {"error": str(exc)[:200]}
    return {"canonical_query": canonical, "removed_lines": removed}


# --- Dispatcher ----------------------------------------------------------

_ROLLBACKERS: dict[str, Callable[[ChangeIntent], dict]] = {
    KIND_CREATE_EXECUTOR:      _rollback_create_executor,
    KIND_EXTEND_EXECUTOR:      _rollback_extend_executor,
    KIND_DEDUPE_EXECUTORS:     _rollback_dedupe_executors,
    KIND_MATERIALIZE_PIPELINE: _rollback_materialize_pipeline,
    KIND_CACHE_PATTERN:        _rollback_cache_pattern,
    KIND_REJECT_PATTERN:       _rollback_reject_pattern,
}


def rollback_for_kind(ci: ChangeIntent) -> dict:
    """Esegue rollback fisico in base a `ci.intent_kind`. Solleva
    se kind sconosciuto."""
    rb = _ROLLBACKERS.get(ci.intent_kind)
    if rb is None:
        raise ValueError(f"no rollback handler for kind={ci.intent_kind}")
    return rb(ci)

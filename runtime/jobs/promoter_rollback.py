"""Rollback di una promote synth.

Operazioni:
1. Read row dello state DB per il proposal_id.
2. Verifica state in ('promoted_grace', 'promoted_finalized').
3. Estrai blob_path; se mancante → fail-loud §2.8 con `error: 'no_blob'`.
4. Rimuovi `~/.local/share/metnos/executors/<name>/` (mai handcrafted).
5. Sposta blob in `~/.local/share/metnos/promoter_blobs/_rolled_back/<id>.tar.gz`.
6. UPDATE state DB: state='rolled_back', rolled_back_at=now.
7. Audit JSONL append.

§7.9 deterministico, §2.8 fail-loud su blob mancante (mai silenzio).
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .promoter_promote import _blob_dir, _handcrafted_dir, _synth_exec_dir
from .promoter_state import (
    audit_append,
    load_proposal_state,
    mark_rolled_back,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rollback_promotion(proposal_id: str) -> dict:
    """Esegue rollback per la promote di `proposal_id`.

    Ritorna dict con shape:
        {ok: bool, error: str | None, name: str, removed_path: str,
         rolled_back_blob: str}
    """
    if not proposal_id:
        return {"ok": False, "error": "proposal_id_empty"}

    state = load_proposal_state(proposal_id)
    if state is None:
        return {"ok": False, "error": "state_row_not_found",
                "proposal_id": proposal_id}
    current = state.get("state") or ""
    if current not in ("promoted_grace", "promoted_finalized"):
        return {"ok": False, "error": f"state_not_rollable: {current}",
                "proposal_id": proposal_id, "state": current}
    name = state.get("name") or ""
    blob_path_str = state.get("rollback_blob_path") or ""
    if not blob_path_str:
        return {"ok": False, "error": "no_blob",
                "proposal_id": proposal_id, "name": name}
    blob_path = Path(blob_path_str)
    if not blob_path.exists():
        return {"ok": False, "error": "no_blob",
                "proposal_id": proposal_id, "name": name,
                "blob_path": str(blob_path)}

    # Rimuovi dir executor. Refuse di toccare la dir handcrafted.
    target_dir = _synth_exec_dir() / name if name else None
    if target_dir is None:
        return {"ok": False, "error": "name_empty",
                "proposal_id": proposal_id}
    if str(target_dir).startswith(str(_handcrafted_dir()) + os.sep):
        return {"ok": False, "error": "target_dir_inside_handcrafted",
                "proposal_id": proposal_id, "name": name,
                "target_dir": str(target_dir)}
    removed = False
    if target_dir.exists():
        try:
            shutil.rmtree(str(target_dir))
            removed = True
        except OSError as ex:
            return {"ok": False, "error": f"rmtree_failed: {ex}",
                    "proposal_id": proposal_id, "name": name,
                    "target_dir": str(target_dir)}

    # Sposta blob in _rolled_back/.
    rolled_dir = _blob_dir() / "_rolled_back"
    rolled_dir.mkdir(parents=True, exist_ok=True)
    rolled_blob = rolled_dir / blob_path.name
    try:
        # Atomic rename se sullo stesso filesystem; copy+unlink come fallback.
        try:
            os.replace(str(blob_path), str(rolled_blob))
        except OSError:
            shutil.copy2(str(blob_path), str(rolled_blob))
            blob_path.unlink()
    except OSError as ex:
        return {"ok": False, "error": f"blob_move_failed: {ex}",
                "proposal_id": proposal_id, "name": name}

    # Update DB state.
    mark_rolled_back(proposal_id)

    # Audit.
    audit_append({
        "ts": _now_iso(),
        "proposal_id": proposal_id,
        "name": name,
        "action": "rolled_back",
        "removed_path": str(target_dir) if removed else None,
        "rolled_back_blob": str(rolled_blob),
        "prev_state": current,
    })

    return {
        "ok": True,
        "proposal_id": proposal_id,
        "name": name,
        "removed_path": str(target_dir) if removed else None,
        "rolled_back_blob": str(rolled_blob),
        "prev_state": current,
    }


__all__ = ["rollback_promotion"]

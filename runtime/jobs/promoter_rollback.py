"""Rollback di una promote synth.

Operazioni:
1. Read row dello state DB per il proposal_id.
2. Verifica state in ('promoted_grace', 'promoted_finalized').
3. Estrai blob_path; se mancante → fail-loud §2.8 con `error: 'no_blob'`.
4. Ripristina dal blob l'esatto candidato quarantinato precedente alla
   promozione (mai la directory handcrafted). Dopo il cutover sposta il
   binding fra le due identita' immutabili registrate al promote.
5. Sposta blob in `~/.local/share/metnos/promoter_blobs/_rolled_back/<id>.tar.gz`.
6. UPDATE state DB: state='rolled_back', rolled_back_at=now.
7. Audit JSONL append.

§7.9 deterministico, §2.8 fail-loud su blob mancante (mai silenzio).
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .promoter_promote import (
    _blob_dir, _handcrafted_dir, _restore_rollback_blob, _synth_exec_dir,
)
from .promoter_state import (
    audit_append,
    load_proposal_state,
    mark_rolled_back,
)


from timefmt import now_iso_z as _now_iso


@contextmanager
def _staged_rollback_candidate(parent: Path, name: str, blob_path: Path):
    """Expose a traversal-safe private rollback tree without touching authoring."""
    staging = Path(tempfile.mkdtemp(prefix=f".{name}.rollback-birth.", dir=str(parent)))
    try:
        with tarfile.open(str(blob_path), "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError("rollback_blob_empty")
            for member in members:
                path = Path(member.name)
                if (path.is_absolute() or ".." in path.parts or member.issym()
                        or member.islnk() or not member.isfile()):
                    raise ValueError("rollback_blob_unsafe_member")
            archive.extractall(str(staging), members=members, filter="data")
        if not (staging / "manifest.toml").is_file():
            raise ValueError("rollback_manifest_missing")
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
    from manifest_inventory import ManifestLayout, resolve_manifest_layout
    try:
        layout = resolve_manifest_layout()
    except Exception as ex:
        return {"ok": False, "error": f"publication_layout_invalid: {ex}",
                "proposal_id": proposal_id, "name": name}
    prepromotion_generation_id = state.get("prepromotion_generation_id")
    active_generation_id = state.get("active_generation_id")
    if layout is ManifestLayout.STORE_ONLY and (
        not prepromotion_generation_id or not active_generation_id
    ):
        return {
            "ok": False,
            "error": "promotion_generation_ids_missing",
            "proposal_id": proposal_id,
            "name": name,
        }
    if layout is ManifestLayout.STORE_ONLY:
        try:
            from executor_birth_intent import (
                BirthIntent, require_birth_intent_adapter,
                submit_promoter_rollback_birth,
            )
            from manifest_inventory import ContractId, ManifestOrigin
            require_birth_intent_adapter()
            with _staged_rollback_candidate(target_dir.parent, name, blob_path) as staging:
                birth = submit_promoter_rollback_birth(BirthIntent(
                    candidate_source_root=staging,
                    contract_id=ContractId(
                        ManifestOrigin.USER, f"{name}/manifest.toml",
                    ),
                    reason=f"rollback promotion proposal={proposal_id}",
                    approval_refs=(proposal_id,),
                ))
            if birth.error_code or birth.publication is None:
                raise RuntimeError(birth.error_code or "publication_missing")
            if str(birth.publication.current_generation_id) != str(prepromotion_generation_id):
                raise RuntimeError("rollback_generation_mismatch")
        except Exception as ex:
            return {
                "ok": False,
                "error": f"publication_rollback_requires_retry: {ex}",
                "proposal_id": proposal_id,
                "name": name,
                "target_dir": str(target_dir),
            }
    else:
        restored, restore_error = _restore_rollback_blob(target_dir, blob_path)
        if not restored:
            return {"ok": False, "error": restore_error,
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
        "restored_path": str(target_dir),
        "rolled_back_blob": str(rolled_blob),
        "prev_state": current,
    })

    return {
        "ok": True,
        "proposal_id": proposal_id,
        "name": name,
        "removed_path": None,
        "restored_path": str(target_dir),
        "rolled_back_blob": str(rolled_blob),
        "prev_state": current,
    }


__all__ = ["rollback_promotion"]

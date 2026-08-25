"""Promote di una proposta synth nel catalog live.

Step:
1. Verifica admission ADR 0114 dry-run (layer 2/3/5/6 pre-emptivo).
2. Verifica il candidate standard gia' materializzato da Synt e ne salva una
   copia esatta per il rollback.
3. Applica l'unica transizione ammessa `synthesized -> active` e attraversa
   il confine di ammissione del layout corrente.
4. Verifica l'artefatto con il loader autoritativo. Nel layout legacy un
   errore ripristina il candidato; dopo il cutover non riscrive mai la
   sorgente alle spalle della generazione pubblicata.

§7.9 deterministico ovunque tranne layer 6 (LLM verifier, gia' chiuso in
proposal_evaluator). §2.8 fail-loud: ogni admission fail ritorna esplicito
`{ok:false, error:...}`.
"""
from __future__ import annotations

import os
import shutil
import sys as _sys
import tarfile
import tempfile
import time
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11

# Dir canoniche, env-overridable per i test.
_DEFAULT_SYNTH_EXEC_DIR = _C.PATH_USER_DATA / "executors"
_DEFAULT_BLOB_DIR = _C.PATH_USER_DATA / "promoter_blobs"
# ADR 0148 rename-resilient — derive from this module's location (runtime/jobs/file.py)
_DEFAULT_HANDCRAFTED_DIR = Path(__file__).resolve().parents[2] / "executors"


def _synth_exec_dir() -> Path:
    env = os.environ.get("METNOS_PROMOTER_SYNTH_DIR")
    return Path(env) if env else _DEFAULT_SYNTH_EXEC_DIR


def _blob_dir() -> Path:
    env = os.environ.get("METNOS_PROMOTER_BLOB_DIR")
    return Path(env) if env else _DEFAULT_BLOB_DIR


def _handcrafted_dir() -> Path:
    """Dir handcrafted canonico. Promoter NON deve mai toccarla (read-only)."""
    return _DEFAULT_HANDCRAFTED_DIR


def _extract_code(proposal: dict) -> str:
    """Estrae il codice Python dallo stage 5 della proposta."""
    stages = proposal.get("stages") or []
    if len(stages) < 5:
        return ""
    s5 = stages[4]
    if not isinstance(s5, dict):
        return ""
    out = s5.get("output") or {}
    if not isinstance(out, dict):
        return ""
    return out.get("code") or ""


def _atomic_write(target: Path, content: str | bytes) -> None:
    """Write atomico via tmp+rename nella stessa dir per garantire atomic."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent),
    )
    try:
        mode = "wb" if isinstance(content, bytes) else "w"
        with os.fdopen(tmp_fd, mode) as f:
            f.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_rollback_blob(executor_dir: Path, blob_path: Path) -> None:
    """Crea tar.gz dei file appena scritti. Atomic via tmp+rename."""
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{blob_path.name}.", suffix=".tmp",
        dir=str(blob_path.parent),
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        with tarfile.open(str(tmp_path), "w:gz") as tf:
            for child in sorted(executor_dir.iterdir()):
                tf.add(str(child), arcname=child.name)
        os.replace(tmp_path_str, str(blob_path))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _restore_rollback_blob(
        executor_dir: Path, blob_path: Path,
) -> tuple[bool, str]:
    """Restore an exact candidate tree with traversal-safe atomic switching."""

    if not blob_path.is_file():
        return False, "rollback_blob_missing"
    executor_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{executor_dir.name}.restore.", dir=str(executor_dir.parent)))
    displaced = executor_dir.parent / (
        f".{executor_dir.name}.displaced.{time.time_ns()}")
    try:
        with tarfile.open(str(blob_path), "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                return False, "rollback_blob_empty"
            for member in members:
                path = Path(member.name)
                if (path.is_absolute() or ".." in path.parts
                        or member.issym() or member.islnk()
                        or not member.isfile()):
                    return False, "rollback_blob_unsafe_member"
            archive.extractall(str(staging), members=members, filter="data")
        if not (staging / "manifest.toml").is_file():
            return False, "rollback_manifest_missing"
        if executor_dir.exists():
            os.replace(str(executor_dir), str(displaced))
        try:
            os.replace(str(staging), str(executor_dir))
        except Exception:
            if displaced.exists() and not executor_dir.exists():
                os.replace(str(displaced), str(executor_dir))
            raise
        if displaced.exists():
            shutil.rmtree(displaced)
        return True, ""
    except (OSError, tarfile.TarError) as exc:
        return False, f"rollback_restore_failed:{type(exc).__name__}"
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _rollback_blob_generation_id(blob_path: Path) -> str:
    """Derive the immutable candidate ID from the exact rollback payload."""
    from contract_store import GENERATION_FILES, generation_id

    payloads: dict[str, bytes] = {}
    with tarfile.open(str(blob_path), "r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or len(path.parts) != 1
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("rollback blob contains an unsafe member")
            if member.name not in GENERATION_FILES:
                continue
            if not member.isfile():
                raise ValueError("rollback generation member is not a file")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("rollback generation member is unreadable")
            payloads[member.name] = stream.read()
    if set(payloads) != set(GENERATION_FILES):
        raise ValueError("rollback blob lacks the immutable generation payload")
    return generation_id(payloads)


def _loader_admission(executor_dir: Path, name: str) -> tuple[bool, str]:
    """Exercise the verified loader against the authoritative layout."""

    from manifest_inventory import ManifestLayout, resolve_manifest_layout
    from loader import Catalog, _load_dir_into_catalog, load_catalog

    if resolve_manifest_layout() is ManifestLayout.STORE_ONLY:
        catalog = load_catalog(include_synth=True, include_verb_unique=False)
    else:
        with tempfile.TemporaryDirectory(prefix="metnos-promoter-admit-") as raw:
            root = Path(raw)
            os.symlink(str(executor_dir), str(root / executor_dir.name),
                       target_is_directory=True)
            catalog = Catalog()
            _load_dir_into_catalog(
                root, catalog, verify=True, is_synthesized=True,
                current_lang="it",
            )

    if name not in catalog.executors:
        reason = next((reason for path, reason in catalog.rejected
                       if Path(path).name == executor_dir.name),
                      "executor_not_admitted")
        return False, reason
    executor = catalog.executors[name]
    if executor.lifecycle != "active" or executor.standard_state != "declared":
        return False, "executor_not_conformant_active"
    return True, ""


def _dry_run_admission_layer2(proposal: dict) -> tuple[bool, str]:
    """Layer 2 — Affinity overlap dry-run.

    Riusa la stessa logica di `proposal_evaluator._check_affinity_overlap`
    (soglia 0.4 stretta vs 0.5 al catalog load). Se overlap >= 0.4 verso
    handcrafted o synth piu' vecchio → reject.
    """
    try:
        from proposal_evaluator import _check_affinity_overlap
    except ImportError as ex:
        return True, f"affinity_check_unavailable ({ex})"
    triggered, reason, _info = _check_affinity_overlap(proposal, catalog=None)
    return not triggered, reason


def _dry_run_admission_layer3(proposal: dict) -> tuple[bool, str]:
    """Layer 3 — Efficacy ager dry-run.

    Sui synth nuovi (mai invocati live) non si applica: skip = pass.
    Re-check post-promote sara' eseguito dal task `apply_executor_ager`
    sul catalog vero quando l'executor accumula invocations.
    """
    return True, ""


def _dry_run_admission_layer5(proposal: dict) -> tuple[bool, str]:
    """Layer 5 — Smoke battery: il nuovo executor deve non rompere alcun
    routing critico definito in `runtime/smoke.py::BATTERY`. Pre-promote
    dry-run: se la proposta dichiara una affinity che catch-all su una
    query smoke nota, reject.

    Implementazione semplificata §7.2: ricarica BATTERY e check che il
    name della proposta NON sia in conflitto con expected_first_tool di
    nessun case (es. proposta `find_texts` mentre un case attende
    `find_urls`). Se conflitto, layer 5 fail.
    """
    try:
        from smoke import BATTERY  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return True, "smoke battery unavailable; layer 5 skipped"
    name = proposal.get("name") or ""
    if not name:
        return True, ""
    affinity = set()
    stages = proposal.get("stages") or []
    if len(stages) >= 4 and isinstance(stages[3], dict):
        out = stages[3].get("output") or {}
        if isinstance(out, dict):
            for t in (out.get("affinity") or []):
                affinity.add(str(t).strip().lower())
    if not affinity:
        return True, ""
    for case in BATTERY:
        if not isinstance(case, dict):
            continue
        expected = case.get("expected_first_tool")
        if not expected or expected == name:
            continue
        # Query case-insensitive token overlap >= 3 con affinity?
        q = (case.get("query") or "").lower()
        q_tokens = {t for t in q.replace(",", " ").split() if len(t) >= 3}
        overlap = affinity & q_tokens
        if len(overlap) >= 3:
            return False, (
                f"layer 5: la proposta '{name}' rischia di hijackare "
                f"il routing della smoke '{q[:50]}' "
                f"(expected={expected}, overlap={sorted(overlap)})"
            )
    return True, ""


def _dry_run_admission_layer6(proposal: dict) -> tuple[bool, str]:
    """Layer 6 — Semantic verifier LLM stage 6.

    Per proposte gia' synthesized, il check e' stato eseguito durante
    la synt pipeline. Se `final_state == "synthesized"` consideriamo
    layer 6 passato (audit gia' presente in `synth_audit/`).
    Re-eseguirlo qui spreca GPU (ricontrollerebbe lo stesso payload).
    """
    final_state = proposal.get("final_state") or ""
    if final_state == "synthesized":
        return True, ""
    return False, f"layer 6: final_state '{final_state}' non e' synthesized"


def promote_to_catalog(proposal: dict) -> dict:
    """Promote di una proposta synth nel synth catalog dir.

    Ritorna dict con shape:
        {ok: bool, path: str (dir), blob_path: str, error: str | None,
         admission_layer_failed: str | None}

    §2.8 fail-loud: ogni step fallito ritorna dict con `ok=False` + reason.
    """
    proposal_id = proposal.get("id") or "?"
    name = proposal.get("name") or proposal.get("expected_name") or ""
    if not name:
        return {"ok": False, "error": "proposal_name_empty",
                "proposal_id": proposal_id}

    # Refuse di toccare la dir handcrafted (mai).
    target_dir = _synth_exec_dir() / name
    if str(target_dir).startswith(str(_handcrafted_dir()) + os.sep):
        return {"ok": False, "error": "target_dir_inside_handcrafted",
                "proposal_id": proposal_id, "target_dir": str(target_dir)}

    from manifest_inventory import ManifestLayout, resolve_manifest_layout
    try:
        layout = resolve_manifest_layout()
    except Exception as ex:
        return {"ok": False, "error": "publication_layout_invalid",
                "reason": str(ex)[:500], "proposal_id": proposal_id,
                "name": name}

    # Admission dry-run prima di toccare il filesystem.
    for layer_name, fn in (
        ("layer_2", _dry_run_admission_layer2),
        ("layer_3", _dry_run_admission_layer3),
        ("layer_5", _dry_run_admission_layer5),
        ("layer_6", _dry_run_admission_layer6),
    ):
        ok, reason = fn(proposal)
        if not ok:
            return {
                "ok": False,
                "error": f"admission_failed_{layer_name}",
                "admission_layer_failed": layer_name,
                "reason": reason,
                "proposal_id": proposal_id,
                "name": name,
            }

    # Do not transition the authoring lifecycle unless the sealed operational
    # Birth service is available to commit that exact candidate.
    try:
        from executor_birth_intent import require_birth_intent_adapter
        require_birth_intent_adapter()
    except Exception as ex:
        return {"ok": False, "error": f"birth_unavailable: {ex}",
                "proposal_id": proposal_id, "name": name}

    # Synt already created and admitted the candidate. Promotion is one state
    # transition of that exact artifact, never a second manifest generator.
    manifest_path = target_dir / "manifest.toml"
    if not manifest_path.is_file():
        return {"ok": False, "error": "candidate_not_found",
                "proposal_id": proposal_id, "name": name}
    blob_path = _blob_dir() / f"{proposal_id}.tar.gz"
    try:
        candidate_text = manifest_path.read_text(encoding="utf-8")
        from generated_executor_contract import (
            GeneratedContractError,
            transition_generated_manifest_text,
            validate_generated_manifest_text,
        )
    except OSError as ex:
        return {"ok": False, "error": "candidate_not_conformant",
                "reason": str(ex)[:500], "proposal_id": proposal_id,
                "name": name}
    already_active = False
    try:
        active_text, candidate_manifest = transition_generated_manifest_text(
            candidate_text, expected_lifecycle="synthesized",
            target_lifecycle="active")
    except GeneratedContractError as transition_error:
        # Store publication can commit before a final registry callback fails.
        # In that case authoring is already reconciled to ``active`` and the
        # next call must be an idempotent publication retry, not a dead end.
        try:
            candidate_manifest = validate_generated_manifest_text(
                candidate_text, expected_lifecycle="active",
            )
        except GeneratedContractError:
            return {"ok": False, "error": "candidate_not_conformant",
                    "reason": str(transition_error)[:500],
                    "proposal_id": proposal_id, "name": name}
        if not blob_path.is_file():
            return {"ok": False,
                    "error": "active_candidate_without_rollback_blob",
                    "proposal_id": proposal_id, "name": name}
        active_text = candidate_text
        already_active = True
    if str(candidate_manifest.get("name") or "") != name:
        return {"ok": False, "error": "candidate_identity_mismatch",
                "proposal_id": proposal_id, "name": name}
    code_files = (candidate_manifest.get("code") or {}).get("files") or []
    proposal_code = _extract_code(proposal)
    if (len(code_files) != 1 or not proposal_code
            or not (target_dir / str(code_files[0])).is_file()):
        return {"ok": False, "error": "candidate_code_missing",
                "proposal_id": proposal_id, "name": name}
    try:
        installed_code = (target_dir / str(code_files[0])).read_text(
            encoding="utf-8")
    except OSError:
        installed_code = ""
    if installed_code != proposal_code:
        return {"ok": False, "error": "candidate_proposal_mismatch",
                "proposal_id": proposal_id, "name": name}

    # Snapshot the exact quarantined state before the activation commit.
    prepromotion_generation_id = None
    try:
        if not already_active:
            _write_rollback_blob(target_dir, blob_path)
        if layout is ManifestLayout.STORE_ONLY:
            prepromotion_generation_id = _rollback_blob_generation_id(blob_path)
    except (OSError, tarfile.TarError, ValueError) as ex:
        return {"ok": False, "error": f"transition_write_failed: {ex}",
                "proposal_id": proposal_id, "name": name}

    # Executor Birth is the only publication and reactivation boundary.
    publication = None
    birth_staging = Path(tempfile.mkdtemp(
        prefix=f".{name}.birth.", dir=str(target_dir.parent),
    ))
    try:
        for child in target_dir.iterdir():
            destination = birth_staging / child.name
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)
        _atomic_write(birth_staging / "manifest.toml", active_text)
        from executor_birth_intent import BirthIntent, submit_birth_intent
        from manifest_inventory import ContractId, ManifestOrigin
        birth = submit_birth_intent(BirthIntent(
            candidate_source_root=birth_staging,
            contract_id=ContractId(
                ManifestOrigin.USER, f"{name}/manifest.toml",
            ),
            actor="promoter",
            reason=f"promote synthesized proposal={proposal_id}",
            operation="promote",
            approval_refs=(str(proposal_id),),
        ))
        if birth.error_code or birth.publication is None:
            raise RuntimeError(birth.error_code or "publication_missing")
        publication = birth.publication
    except Exception as ex:
        if layout is ManifestLayout.AUTHORING:
            _restore_rollback_blob(target_dir, blob_path)
            error = f"birth_failed: {ex}"
        else:
            # Il pointer puo' essere gia' avanzato. Conservare la sorgente
            # active consente al retry di riconciliare in modo idempotente.
            error = f"publication_outcome_requires_retry: {ex}"
        return {"ok": False, "error": error,
                "proposal_id": proposal_id, "name": name,
                "path": str(target_dir)}
    finally:
        shutil.rmtree(birth_staging, ignore_errors=True)
    active_generation_id = None
    if layout is ManifestLayout.STORE_ONLY:
        if publication is None:
            return {"ok": False, "error": "publisher_returned_no_generation",
                    "proposal_id": proposal_id, "name": name}
        active_generation_id = str(publication.current_generation_id)
        previous = publication.previous_generation_id
        if (
            not publication.repeated
            and previous != prepromotion_generation_id
        ):
            return {
                "ok": False,
                "error": "prepromotion_generation_mismatch",
                "expected": prepromotion_generation_id,
                "observed": previous,
                "proposal_id": proposal_id,
                "name": name,
            }
    admitted, admission_reason = _loader_admission(target_dir, name)
    if not admitted:
        if layout is ManifestLayout.AUTHORING:
            restored, restore_reason = _restore_rollback_blob(
                target_dir, blob_path)
        else:
            # Non alterare authoring dopo una pubblicazione riuscita: sarebbe
            # diverso dalla generazione viva. La diagnosi resta recuperabile
            # e un rollback esplicito dovra' attraversare lo stesso publisher.
            restored, restore_reason = False, "published_generation_retained"
        return {
            "ok": False,
            "error": "admission_failed_standard",
            "reason": admission_reason,
            "rollback_restored": restored,
            "rollback_error": restore_reason,
            "proposal_id": proposal_id,
            "name": name,
        }

    return {
        "ok": True,
        "path": str(target_dir),
        "blob_path": str(blob_path),
        "proposal_id": proposal_id,
        "name": name,
        "promoted_at": time.time(),
        "prepromotion_generation_id": prepromotion_generation_id,
        "active_generation_id": active_generation_id,
    }


__all__ = ["promote_to_catalog"]

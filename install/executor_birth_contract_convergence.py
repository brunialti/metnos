#!/usr/bin/env python3
"""Converge a first-transition store through the installed Executor Birth.

The V1 receipt namespace is historical.  When an already stored generation
uses that namespace, the transition publishes an equivalent packaging
revision instead of overwriting the historical receipt.  Every mutation still
travels through the installer Producer, Admission and atomic Birth publisher.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

import tomlkit


_REPOSITORY = Path(__file__).resolve().parents[1]
_RUNTIME = _REPOSITORY / "runtime"
for _root in (_REPOSITORY, _RUNTIME):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


class ContractConvergenceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _fail(code: str, detail: object = "") -> ContractConvergenceError:
    return ContractConvergenceError(code, str(detail))


def _source_generation_has_historical_receipt(
    source_ref, *, store_root: Path, trusted_publics: tuple,
    admission_verifiers,
) -> bool:
    import contract_store
    from executor_birth_receipts import (
        _parse_admission, verify_admission_receipt,
    )

    source_payloads = {
        name: contract_store._read_regular_file(
            source_ref.manifest_dir / name, code="source_file_invalid",
        )
        for name in contract_store.GENERATION_FILES
    }
    source_generation = contract_store.generation_id(source_payloads)
    contract_dir = contract_store._existing_contract_directory(
        source_ref.contract_id, store_root=store_root,
    )
    historical_receipt = contract_store._birth_receipt_path(
        contract_dir, source_generation,
    )
    if contract_store._is_link_like(historical_receipt):
        raise _fail("birth_transition_historical_receipt_invalid")
    if not historical_receipt.exists():
        return False
    generation_path = (
        contract_dir / "generations"
        / contract_store.generation_directory_name(source_generation)
    )
    if not generation_path.exists():
        # Birth writes the receipt before the generation and current pointer.
        # Replaying the exact candidate is the only crash-safe action here.
        return False
    installed = contract_store._load_generation_for_commit(
        source_ref, source_generation,
        trusted_publics=trusted_publics, store_root=store_root,
    )
    if installed != source_payloads:
        raise _fail("birth_transition_historical_generation_invalid")
    encoded = contract_store._read_regular_file(
        historical_receipt, code="birth_receipt_invalid",
    )
    receipt, _unsigned = _parse_admission(encoded)
    if (
        receipt.contract_id != source_ref.contract_id.value
        or receipt.generation_id != source_generation
    ):
        raise _fail("birth_transition_historical_receipt_invalid")
    if receipt.authentication.key_id in admission_verifiers:
        verify_admission_receipt(
            encoded, verifier_keys=admission_verifiers,
        )
        return False
    # A canonical receipt bound to this exact, author-authenticated immutable
    # generation belongs to a preceding admission context.  It stays intact;
    # the new context receives a separate packaging generation and later V2
    # reattestation.
    return True


def _candidate_for_transition(
    source_ref, destination: Path, *, packaging_revision: bool,
):
    from executor_birth_snapshot import materialize_birth_candidate_from_authoring
    from i18n_materializer import migrate_language_state_bytes

    candidate = materialize_birth_candidate_from_authoring(
        source_ref.manifest_dir, destination,
    )
    if not packaging_revision:
        return candidate
    manifest_path = candidate / "manifest.toml"
    document = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    if document.get("version") != "1.0.0":
        raise _fail(
            "birth_transition_contract_version_invalid",
            source_ref.contract_id.value,
        )
    document["version"] = "1.0.1"
    rendered = tomlkit.dumps(document).encode("utf-8")
    manifest_path.write_bytes(rendered)
    state_path = candidate / "manifest.lang_state.json"
    state_path.write_bytes(migrate_language_state_bytes(
        state_path.read_bytes(),
        manifest=tomllib.loads(rendered.decode("utf-8")),
    ).state_bytes)
    return candidate


def _initial_shadow_root(source_inventory) -> Path | None:
    """Use the existing candidate-bound shadow until the complete catalog is verified."""
    from contract_store import ProductionStoreMode, production_store_mode
    from executor_birth_bootstrap import (
        _initial_candidate_payloads_v1, _initial_shadow_root_v1,
    )
    from executor_birth_prepared_root import load_sealed_authorities_v1

    mode = production_store_mode()
    if mode is ProductionStoreMode.ACTIVE:
        return None
    if mode not in {ProductionStoreMode.LEGACY, ProductionStoreMode.RECOVERY_REQUIRED}:
        raise _fail("birth_transition_initial_catalog_changed")
    sealed = load_sealed_authorities_v1()
    candidates = tuple(
        (ref, _initial_candidate_payloads_v1(ref))
        for ref in sorted(source_inventory.manifests, key=lambda item: item.contract_id.value)
    )
    return _initial_shadow_root_v1(sealed.prepared.set_id, candidates)


def _finish_initial_catalog(sources, *, shadow_root, store_root, trusted, admission_verifiers):
    """Verify every Birth receipt before the existing atomic catalog activation.

    The administrative parent retains deployment/startup exclusion and proves
    maintenance before and after this service-owned child. The child is usable
    only through the sealed installer on the still-empty ownership chain.
    """
    import contract_store
    from executor_birth_bootstrap import (
        _require_initial_transition_v1, _verified_initial_receipt_v1,
    )

    _require_initial_transition_v1()
    _container, production_root, _marker = contract_store._production_paths()
    with contract_store.catalog_admission_lock(store_root=production_root):
        with contract_store.catalog_admission_lock(store_root=store_root):
            expected = {}
            for contract_id, ref in sources.items():
                generation = contract_store.current_revision_id(ref, store_root=store_root)
                _verified_initial_receipt_v1(
                    ref, generation, store_root=store_root,
                    trusted_publics=trusted, admission_verifiers=admission_verifiers,
                )
                expected[contract_id] = generation
            if shadow_root is not None:
                shadow_v1, shadow_container = contract_store._canonical_activation_shadow(shadow_root)
                contract_store._activate_store_locked(
                    expected, shadow_v1=shadow_v1, shadow_container=shadow_container,
                    trusted=trusted, production_container=_container,
                    production_root=production_root, marker=_marker,
                    mode=contract_store.production_store_mode(),
                )


def converge(*, new_instance: bool = False) -> dict[str, int]:
    """Converge the catalog selected by the administrative transition.

    The parent proves a fresh initial journal before selecting ``new_instance``.
    An empty store alone never selects it. Every new or resumed admission still
    requires the sealed initial-chain installer Producer below.
    """
    if type(new_instance) is not bool:
        raise _fail("birth_transition_contract_catalog_mismatch")
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        raise _fail("birth_transition_service_identity_required")
    if Path(os.path.abspath(os.environ.get("METNOS_INSTALL_ROOT", ""))) != _REPOSITORY:
        raise _fail("birth_transition_distribution_changed")

    import contract_store
    from executor_birth_intent import BirthIntent
    from executor_birth_bootstrap import (
        _build_initial_transition_installer_runtime_v1,
    )
    from manifest_inventory import (
        ManifestInventory, ManifestOrigin, inventory_authoring_manifests,
        inventory_store_manifests,
    )
    from executor_birth_prepared_root import (
        _load_historical_transition_verifiers_v1,
    )

    source_inventory = inventory_authoring_manifests()
    if source_inventory.problems:
        raise _fail("birth_transition_contract_inventory_invalid")
    _container, store_root, _marker = contract_store._production_paths()
    shadow_root = _initial_shadow_root(source_inventory) if new_instance else None
    if shadow_root is not None:
        store_root = shadow_root
    try:
        store_root.lstat()
    except FileNotFoundError:
        if not new_instance:
            raise _fail("birth_transition_contract_catalog_mismatch")
        # Only the authenticated initial transition may start without a store.
        # Birth populates a separate shadow; a dangling link is not absence.
        store_inventory = ManifestInventory((), ())
    else:
        store_inventory = inventory_store_manifests(store_root=store_root)
    if source_inventory.problems or store_inventory.problems:
        raise _fail("birth_transition_contract_inventory_invalid")
    sources = source_inventory.by_id()
    stored = store_inventory.by_id()
    if not sources or not set(stored).issubset(sources) or (not stored and not new_instance):
        raise _fail("birth_transition_contract_catalog_mismatch")

    admitted_origins = {
        ManifestOrigin.CORE, ManifestOrigin.BUILTIN, ManifestOrigin.BUILTIN_SKILL,
    }
    if new_instance and any(item.origin not in admitted_origins for item in sources):
        raise _fail("birth_transition_external_contract_changed")

    historical = _load_historical_transition_verifiers_v1()
    trusted = tuple(sorted(historical.author_verifier_keys.items()))
    admission_verifiers = historical.admission_verifier_keys
    transition_runtime = (
        _build_initial_transition_installer_runtime_v1(store_root=shadow_root)
        if new_instance else None
    )
    examined = 0
    changed = 0
    current = 0
    selected = sources if new_instance else stored
    for contract_id in sorted(selected, key=lambda item: item.value):
        examined += 1
        source_ref = sources[contract_id]
        with tempfile.TemporaryDirectory(
            prefix="metnos-transition-contract-",
        ) as temporary:
            candidate = _candidate_for_transition(
                source_ref, Path(temporary) / "candidate",
                packaging_revision=False,
            )
            candidate_manifest = (candidate / "manifest.toml").read_bytes()
            candidate_state = (
                candidate / "manifest.lang_state.json"
            ).read_bytes()
            installed = None
            if contract_id in stored:
                try:
                    generation_id = contract_store.current_revision_id(
                        source_ref, store_root=store_root,
                    )
                    installed = contract_store._load_generation(
                        source_ref,
                        generation_id,
                        trusted_publics=trusted,
                        store_root=store_root,
                    )
                except contract_store.ContractStoreError as error:
                    if new_instance or error.code != "code_digest_mismatch":
                        raise
            if (
                installed is not None
                and installed.manifest_bytes == candidate_manifest
                and installed.language_state_bytes == candidate_state
            ):
                current += 1
                continue
            if new_instance and contract_id in stored:
                raise _fail("birth_transition_initial_catalog_changed", contract_id.value)
            if contract_id.origin not in admitted_origins:
                raise _fail("birth_transition_external_contract_changed")
            if not new_instance and _source_generation_has_historical_receipt(
                source_ref, store_root=store_root,
                trusted_publics=trusted,
                admission_verifiers=admission_verifiers,
            ):
                candidate = _candidate_for_transition(
                    source_ref, Path(temporary) / "packaging-revision",
                    packaging_revision=True,
                )
                candidate_manifest = (candidate / "manifest.toml").read_bytes()
                candidate_state = (
                    candidate / "manifest.lang_state.json"
                ).read_bytes()
                if (
                    installed is not None
                    and installed.manifest_bytes == candidate_manifest
                    and installed.language_state_bytes == candidate_state
                ):
                    current += 1
                    continue
            if transition_runtime is None:
                # Verification alone does not select historical authority for
                # execution.  A changed contract still needs a strict runtime.
                transition_runtime = _build_initial_transition_installer_runtime_v1()
            birth = transition_runtime.submit(BirthIntent(
                candidate_source_root=candidate,
                contract_id=contract_id,
                reason="converge first-transition repository contract",
            ))
            if birth.error_code or birth.publication is None:
                raise _fail(
                    birth.error_code or "birth_transition_contract_publication_missing",
                    contract_id.value,
                )
            changed += 1

    if new_instance:
        _finish_initial_catalog(
            sources, shadow_root=shadow_root, store_root=store_root,
            trusted=trusted, admission_verifiers=admission_verifiers,
        )
    contract_store.materialize_repository_authoring_for_transition_v1(
        trusted_publics=trusted,
    )
    return {"changed": changed, "current": current, "examined": examined}


def main() -> int:
    try:
        arguments = sys.argv[1:]
        if arguments not in ([], ["--new-instance"]):
            raise _fail("birth_transition_contract_catalog_mismatch")
        result = converge(new_instance=bool(arguments))
    except BaseException as error:
        code = getattr(error, "code", "birth_transition_contract_convergence_failed")
        sys.stderr.write(str(code) + "\n")
        return 78
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

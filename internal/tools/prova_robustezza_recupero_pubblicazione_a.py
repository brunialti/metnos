"""Independent checks for a publication-recovery candidate checkpoint."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


def load_candidate(candidate_root: Path):
    """Import the recovery module and its identity type from one immutable tree."""
    runtime = candidate_root.resolve() / "runtime"
    sys.path.insert(0, str(runtime))
    import executor_birth_publication_recovery as recovery
    from manifest_inventory import ContractId, ManifestOrigin

    return recovery, ContractId, ManifestOrigin


def exact_container(path: Path) -> None:
    """Create only the incomplete shape admitted by the recovery primitive."""
    (path / "generations").mkdir(parents=True)
    (path / "writer.lock").write_bytes(b"\0")


class FakeInventory:
    """Minimal authoring inventory fixture for the productive issuer."""

    def __init__(self, contract_id) -> None:
        self.manifests = (type("ManifestRef", (), {"contract_id": contract_id})(),)
        self.problems = ()


def authorization(recovery, contract_id, root: Path):
    """Use the productive issuer while substituting only its inventory input."""
    import manifest_inventory

    original = manifest_inventory.inventory_authoring_manifests
    manifest_inventory.inventory_authoring_manifests = (
        lambda *args, **kwargs: FakeInventory(contract_id)
    )
    try:
        return recovery.autorizza_dall_inventario(
            contract_id.value, store_root=root
        )
    finally:
        manifest_inventory.inventory_authoring_manifests = original


def remove_exact_container(path: Path) -> None:
    """Remove a fixture container without traversing any unknown entries."""
    lock = path / "writer.lock"
    if lock.exists():
        lock.unlink()
    generations = path / "generations"
    if generations.exists():
        generations.rmdir()
    path.rmdir()


def check_authorization_cannot_be_minted(recovery, ContractId, ManifestOrigin) -> list[str]:
    """An importer must not be able to construct an inventory authorization."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = ContractId(ManifestOrigin.BUILTIN, "outside/manifest.toml")
        exact_container(root / contract_id.storage_key)
        emitter = getattr(recovery, "_emetti_sigillo", None)
        root_identity = recovery._identita_radice(root)
        if not callable(emitter):
            return []
        authorization = recovery.AutorizzazioneRecupero(
            contract_id, contract_id.storage_key, root_identity, emitter()
        )
        try:
            recovery.ispeziona_contenitore_incompleto(
                authorization, store_root=root
            )
        except recovery.RecuperoPubblicazioneError:
            return []
        return ["the module-level seal emitter minted an authorization"]


def check_retry_after_generations_removal(recovery, ContractId, ManifestOrigin) -> list[str]:
    """A stop after removing generations must leave a resumable state."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = ContractId(ManifestOrigin.BUILTIN, "cleanup-gap/manifest.toml")
        exact_container(root / contract_id.storage_key)
        issued = authorization(recovery, contract_id, root)
        original_rmdir = recovery.os.rmdir
        stopped = False

        def stop_after_generations(name, *args, **kwargs):
            nonlocal stopped
            result = original_rmdir(name, *args, **kwargs)
            if not stopped and os.fspath(name) == recovery.NOME_GENERAZIONI:
                stopped = True
                raise recovery.RecuperoPubblicazioneError("injected_stop")
            return result

        try:
            with mock.patch.object(recovery.os, "rmdir", stop_after_generations):
                recovery.rimuovi_contenitore_incompleto(
                    issued, store_root=root
                )
        except recovery.RecuperoPubblicazioneError as exc:
            if exc.code != "injected_stop":
                return [f"unexpected first-stop result: {exc.code}"]

        try:
            outcome = recovery.rimuovi_contenitore_incompleto(
                issued, store_root=root
            )
        except Exception as exc:  # The candidate currently leaks FileNotFoundError.
            code = getattr(exc, "code", type(exc).__name__)
            return [f"retry did not resume after partial cleanup: {code}"]
        return [] if outcome.rimosso else ["retry did not complete recovery"]


def prepare_rename_collision(recovery, root: Path):
    """Return a rename wrapper that creates one exact occupied destination."""
    original = recovery._rinomina_senza_sostituzione

    def collide(source: str, destination: str, *, dir_fd: int) -> None:
        exact_container(root / destination)
        original(source, destination, dir_fd=dir_fd)

    return collide


def check_receipt_binds_exact_container(recovery, ContractId, ManifestOrigin) -> list[str]:
    """A prepared receipt must not authorize a different retired directory."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = ContractId(ManifestOrigin.BUILTIN, "receipt-object/manifest.toml")
        original_path = root / contract_id.storage_key
        retired_path = root / (recovery.PREFISSO_RITIRO + contract_id.storage_key)
        exact_container(original_path)
        issued = authorization(recovery, contract_id, root)

        try:
            with mock.patch.object(
                recovery,
                "_rinomina_senza_sostituzione",
                prepare_rename_collision(recovery, root),
            ):
                recovery.rimuovi_contenitore_incompleto(
                    issued, store_root=root
                )
        except recovery.RecuperoPubblicazioneError as exc:
            if exc.code != "nome_di_ritiro_occupato":
                return [f"unexpected collision result: {exc.code}"]
        else:
            return ["the occupied retired name was not rejected"]

        occupied_identity = retired_path.stat().st_dev, retired_path.stat().st_ino
        remove_exact_container(original_path)
        try:
            recovery.rimuovi_contenitore_incompleto(
                issued, store_root=root
            )
        except recovery.RecuperoPubblicazioneError:
            return []
        if not retired_path.exists():
            return [
                "a receipt prepared for the original container removed a different "
                f"retired directory {occupied_identity}"
            ]
        return []


def check_receipt_records_commit(recovery, ContractId, ManifestOrigin) -> list[str]:
    """A receipt written before a failed rename must not prove completion."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = ContractId(ManifestOrigin.BUILTIN, "receipt-stage/manifest.toml")
        original_path = root / contract_id.storage_key
        retired_path = root / (recovery.PREFISSO_RITIRO + contract_id.storage_key)
        exact_container(original_path)
        issued = authorization(recovery, contract_id, root)

        try:
            with mock.patch.object(
                recovery,
                "_rinomina_senza_sostituzione",
                prepare_rename_collision(recovery, root),
            ):
                recovery.rimuovi_contenitore_incompleto(
                    issued, store_root=root
                )
        except recovery.RecuperoPubblicazioneError as exc:
            if exc.code != "nome_di_ritiro_occupato":
                return [f"unexpected collision result: {exc.code}"]

        remove_exact_container(original_path)
        remove_exact_container(retired_path)
        try:
            outcome = recovery.rimuovi_contenitore_incompleto(
                issued, store_root=root
            )
        except recovery.RecuperoPubblicazioneError:
            return []
        return [
            "a pre-rename receipt reported completion after the rename had failed"
        ] if outcome.rimosso else []


def check_idempotent_retry_syncs_removal(recovery, ContractId, ManifestOrigin) -> list[str]:
    """A retry after the final removal must make the parent durable."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = ContractId(ManifestOrigin.BUILTIN, "final-sync/manifest.toml")
        original_path = root / contract_id.storage_key
        retired_path = root / (recovery.PREFISSO_RITIRO + contract_id.storage_key)
        exact_container(original_path)
        issued = authorization(recovery, contract_id, root)
        original_fsync = recovery.os.fsync
        stopped = False

        def stop_before_final_sync(descriptor: int) -> None:
            nonlocal stopped
            if not stopped and not original_path.exists() and not retired_path.exists():
                stopped = True
                raise recovery.RecuperoPubblicazioneError("injected_stop")
            original_fsync(descriptor)

        try:
            with mock.patch.object(recovery.os, "fsync", stop_before_final_sync):
                recovery.rimuovi_contenitore_incompleto(issued, store_root=root)
        except recovery.RecuperoPubblicazioneError as exc:
            if exc.code != "injected_stop":
                return [f"unexpected final-stop result: {exc.code}"]
        if not stopped:
            return ["the proof did not reach the final parent sync"]

        synced: list[int] = []

        def observe_sync(descriptor: int) -> None:
            synced.append(descriptor)
            original_fsync(descriptor)

        with mock.patch.object(recovery.os, "fsync", observe_sync):
            outcome = recovery.rimuovi_contenitore_incompleto(
                issued, store_root=root
            )
        if not outcome.rimosso:
            return ["idempotent retry did not preserve the completed outcome"]
        return [] if synced else [
            "idempotent retry returned before syncing the parent directory"
        ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "candidate_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    candidate = parser.parse_args().candidate_root
    recovery, ContractId, ManifestOrigin = load_candidate(candidate)
    cases = (
        (
            "authorization is inventory-only",
            check_authorization_cannot_be_minted,
        ),
        (
            "partial cleanup is resumable",
            check_retry_after_generations_removal,
        ),
        (
            "receipt binds the exact container",
            check_receipt_binds_exact_container,
        ),
        (
            "receipt distinguishes preparation from commit",
            check_receipt_records_commit,
        ),
        (
            "idempotent retry makes the final removal durable",
            check_idempotent_retry_syncs_removal,
        ),
    )
    failures = 0
    for name, case in cases:
        errors = case(recovery, ContractId, ManifestOrigin)
        failures += bool(errors)
        print(("PASS" if not errors else "FAIL"), name, "; ".join(errors))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

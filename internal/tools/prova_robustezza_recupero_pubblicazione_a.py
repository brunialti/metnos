"""Independent checks for the second publication-recovery checkpoint."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

import executor_birth_publication_recovery as recovery  # noqa: E402
from manifest_inventory import ContractId, ManifestOrigin  # noqa: E402


def identity(name: str) -> ContractId:
    return ContractId(ManifestOrigin.BUILTIN, f"{name}/manifest.toml")


def authorization(contract_id: ContractId):
    return recovery.AutorizzazioneRecupero(
        contract_id, contract_id.storage_key, recovery._TOKEN,
    )


def incomplete(root: Path, contract_id: ContractId) -> Path:
    container = root / contract_id.storage_key
    (container / "generations").mkdir(parents=True)
    (container / "writer.lock").write_bytes(b"\0")
    return container


def check_rename_does_not_replace() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = identity("no-replace")
        incomplete(root, contract_id)
        original = os.rename
        replaced = []

        def racing_rename(source, destination, *args, **kwargs):
            occupied = root / os.fspath(destination)
            occupied.mkdir()
            before = occupied.stat().st_ino
            result = original(source, destination, *args, **kwargs)
            replaced.append((before, occupied.stat().st_ino))
            return result

        with mock.patch.object(os, "rename", racing_rename):
            recovery.rimuovi_contenitore_incompleto(
                authorization(contract_id), store_root=root,
            )
        return [] if replaced and replaced[0][0] == replaced[0][1] else [
            f"occupied destination replaced: {replaced!r}",
        ]


def check_commit_is_synced_before_cleanup() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = identity("sync")
        incomplete(root, contract_id)
        original_open = recovery._apri_directory
        original_rename = os.rename
        original_fsync = os.fsync
        renamed = False
        synced_after_rename = []

        def observing_rename(*args, **kwargs):
            nonlocal renamed
            result = original_rename(*args, **kwargs)
            renamed = True
            return result

        def observing_fsync(fd):
            if renamed:
                synced_after_rename.append(fd)
            return original_fsync(fd)

        def stop_before_cleanup(name, *, dir_fd=None):
            if os.fspath(name).startswith(recovery.PREFISSO_RITIRO):
                raise recovery.RecuperoPubblicazioneError("injected_stop")
            return original_open(name, dir_fd=dir_fd)

        try:
            with (
                mock.patch.object(os, "rename", observing_rename),
                mock.patch.object(os, "fsync", observing_fsync),
                mock.patch.object(recovery, "_apri_directory", stop_before_cleanup),
            ):
                recovery.rimuovi_contenitore_incompleto(
                    authorization(contract_id), store_root=root,
                )
        except recovery.RecuperoPubblicazioneError:
            pass
        return [] if synced_after_rename else [
            "rename commit was not synced before cleanup",
        ]


def check_retry_resumes_retired_name() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = identity("resume")
        incomplete(root, contract_id)
        original_open = recovery._apri_directory
        stopped = False

        def stop_once(name, *, dir_fd=None):
            nonlocal stopped
            if (
                not stopped
                and os.fspath(name).startswith(recovery.PREFISSO_RITIRO)
            ):
                stopped = True
                raise recovery.RecuperoPubblicazioneError("injected_stop")
            return original_open(name, dir_fd=dir_fd)

        try:
            with mock.patch.object(recovery, "_apri_directory", stop_once):
                recovery.rimuovi_contenitore_incompleto(
                    authorization(contract_id), store_root=root,
                )
        except recovery.RecuperoPubblicazioneError:
            pass
        try:
            outcome = recovery.rimuovi_contenitore_incompleto(
                authorization(contract_id), store_root=root,
            )
        except recovery.RecuperoPubblicazioneError as exc:
            return [f"retry did not resume: {exc.code}"]
        return [] if outcome.rimosso else ["retry did not complete removal"]


def check_unexpected_entry_is_preserved() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = identity("late-entry")
        incomplete(root, contract_id)
        original = os.rename
        late_path = root / (
            recovery.PREFISSO_RITIRO + contract_id.storage_key
        ) / "late-entry.txt"

        def rename_then_add(source, destination, *args, **kwargs):
            result = original(source, destination, *args, **kwargs)
            late_path.write_text("must remain unmodified")
            return result

        with mock.patch.object(os, "rename", rename_then_add):
            recovery.rimuovi_contenitore_incompleto(
                authorization(contract_id), store_root=root,
            )
        return [] if late_path.exists() else [
            "entry added after verification was removed",
        ]


def check_authorization_origin_is_enforced() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = identity("not-in-inventory")
        incomplete(root, contract_id)
        constructed = authorization(contract_id)
        recovery.ispeziona_contenitore_incompleto(constructed, store_root=root)
        return ["module token allowed construction outside inventory"]


def check_authorization_is_bound_to_root() -> list[str]:
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        first_root = Path(first)
        second_root = Path(second)
        contract_id = identity("root-binding")
        incomplete(first_root, contract_id)
        incomplete(second_root, contract_id)
        issued = authorization(contract_id)
        recovery.ispeziona_contenitore_incompleto(issued, store_root=first_root)
        recovery.ispeziona_contenitore_incompleto(issued, store_root=second_root)
        return ["one authorization selected the same key in two caller roots"]


def check_completed_retry_is_idempotent() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_id = identity("completed-retry")
        incomplete(root, contract_id)
        issued = authorization(contract_id)
        recovery.rimuovi_contenitore_incompleto(issued, store_root=root)
        try:
            outcome = recovery.rimuovi_contenitore_incompleto(
                issued, store_root=root,
            )
        except recovery.RecuperoPubblicazioneError as exc:
            return [f"completed retry failed: {exc.code}"]
        return [] if outcome.rimosso else ["completed retry lost its outcome"]


def main() -> int:
    cases = (
        ("rename cannot replace an occupied name", check_rename_does_not_replace),
        ("commit is synced before cleanup", check_commit_is_synced_before_cleanup),
        ("retry resumes the retired name", check_retry_resumes_retired_name),
        ("late unexpected entries are preserved", check_unexpected_entry_is_preserved),
        ("authorization origin is enforced", check_authorization_origin_is_enforced),
        ("authorization is bound to one root", check_authorization_is_bound_to_root),
        ("completed retry is idempotent", check_completed_retry_is_idempotent),
    )
    failures = 0
    for name, case in cases:
        errors = case()
        failures += bool(errors)
        print(("PASS" if not errors else "FAIL"), name, "; ".join(errors))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Independent robustness checks for publication-container recovery."""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

import contract_store  # noqa: E402
import executor_birth_publication_recovery as recovery  # noqa: E402
from manifest_inventory import ContractId, ManifestOrigin  # noqa: E402


@contextlib.contextmanager
def isolated_catalog_lock(*, store_root=None, timeout=None):
    """Avoid reaching the configured store while retaining writer-lock behavior."""
    del store_root, timeout
    yield


def identity(name: str) -> ContractId:
    return ContractId(ManifestOrigin.BUILTIN, f"{name}/manifest.toml")


def incomplete(root: Path, contract_id: ContractId, *, lock: bool = True) -> Path:
    container = root / contract_id.storage_key
    (container / "generations").mkdir(parents=True)
    if lock:
        (container / "writer.lock").write_bytes(b"\0")
    return container


def check_lock_scope() -> list[str]:
    calls = []

    @contextlib.contextmanager
    def observing_lock(*, store_root=None, timeout=None):
        calls.append(store_root)
        del timeout
        yield

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cid = identity("scope")
        incomplete(root, cid)
        with mock.patch.object(contract_store, "catalog_admission_lock", observing_lock):
            recovery.recupera_contenitore_incompleto(cid, store_root=root)
        return [] if calls == [root] else [f"catalog lock roots: {calls!r}"]


def check_observation_is_read_only() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cid = identity("observe")
        container = incomplete(root, cid, lock=False)
        with mock.patch.object(
            contract_store, "catalog_admission_lock", isolated_catalog_lock,
        ):
            recovery.recupera_contenitore_incompleto(cid, store_root=root)
        return [] if not (container / "writer.lock").exists() else [
            "observation created writer.lock",
        ]


def check_identity_is_authorized() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cid = identity("not-in-inventory")
        container = incomplete(root, cid)
        with mock.patch.object(
            contract_store, "catalog_admission_lock", isolated_catalog_lock,
        ):
            recovery.recupera_contenitore_incompleto(
                cid, store_root=root, applica=True,
            )
        return ["unverified ContractId removed its container"] if not container.exists() else []


def check_late_failure_preserves_retry_shape() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cid = identity("late-failure")
        container = incomplete(root, cid)
        path_type = type(container)
        original_rmdir = path_type.rmdir

        def failing_rmdir(path):
            if path == container:
                raise OSError("injected final removal failure")
            return original_rmdir(path)

        try:
            with (
                mock.patch.object(
                    contract_store, "catalog_admission_lock", isolated_catalog_lock,
                ),
                mock.patch.object(path_type, "rmdir", failing_rmdir),
            ):
                recovery.recupera_contenitore_incompleto(
                    cid, store_root=root, applica=True,
                )
        except OSError:
            pass
        else:
            return ["injected failure was not observed"]
        missing = [
            name for name in ("generations", "writer.lock")
            if not (container / name).exists()
        ]
        return [] if not missing else ["late failure removed: " + ", ".join(missing)]


def check_component_replacement_is_confined() -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cid = identity("replacement")
        container = incomplete(root, cid)
        held = root / "held"
        outside = root / "outside"
        (outside / "generations").mkdir(parents=True)
        (outside / "writer.lock").write_bytes(b"\0")
        path_type = type(container)
        original_iterdir = path_type.iterdir
        container_reads = 0

        def replacing_iterdir(path):
            nonlocal container_reads
            if path == container:
                container_reads += 1
                if container_reads == 2:
                    os.rename(container, held)
                    os.symlink(outside, container)
            return original_iterdir(path)

        try:
            with (
                mock.patch.object(
                    contract_store, "catalog_admission_lock", isolated_catalog_lock,
                ),
                mock.patch.object(path_type, "iterdir", replacing_iterdir),
            ):
                recovery.recupera_contenitore_incompleto(
                    cid, store_root=root, applica=True,
                )
        except OSError:
            pass
        damaged = [
            name for name in ("generations", "writer.lock")
            if not (outside / name).exists()
        ]
        return [] if not damaged else ["replacement target lost: " + ", ".join(damaged)]


def main() -> int:
    cases = (
        ("catalog lock follows requested store", check_lock_scope),
        ("observation is read-only", check_observation_is_read_only),
        ("contract identity carries inventory authority", check_identity_is_authorized),
        ("late failure preserves a retryable shape", check_late_failure_preserves_retry_shape),
        ("component replacement cannot affect another container", check_component_replacement_is_confined),
    )
    failures = 0
    for name, case in cases:
        errors = case()
        failures += bool(errors)
        print(("PASS" if not errors else "FAIL"), name, "; ".join(errors))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

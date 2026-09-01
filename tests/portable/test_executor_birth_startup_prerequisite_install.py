from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import stat
import sys

import pytest

from executor_birth_distribution_assembler import (
    DistributionAssemblerError,
    build_startup_prerequisite_v1,
    encode_startup_prerequisite_v1,
)
from executor_birth_ownership_coordinator import (
    OwnershipCoordinatorError, _deployment_lock_for_test_v1,
)
from executor_birth_startup_gate import _exclusive_startup_gate_for_test_v1
from install.executor_birth_startup_gate import (
    _install_startup_gate_for_test_v1,
)
from install.executor_birth_startup_prerequisite import (
    STARTUP_PREREQUISITES_DIRECTORY_V1,
    _publish_startup_prerequisite_for_test_v2,
)


LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux startup prerequisite",
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def prerequisite(character: str):
    return build_startup_prerequisite_v1(
        request_id=D(character),
        closed_build_id=D("a"),
        release_sequence=1,
        deployment_descriptor_id=D("b"),
        predecessor_id=D("c"),
        administrative_bundle_hash=D("d"),
        python_binary_hash=D("e"),
        openssl_binary_hash=D("f"),
        openssl_tcb_hash=D("0"),
        systemctl_binary_hash=D("1"),
        systemd_analyze_binary_hash=D("2"),
        service_catalog_id=D("3"),
        service_coverage_hash=D("4"),
        systemd_manager_version="255.4",
        candidate_units_hash=D("5"),
        effective_units_hash=D("6"),
    )


@contextmanager
def held_cell(tmp_path: Path):
    ownership_root = tmp_path / "ownership"
    runtime_parent = tmp_path / "run"
    runtime_parent.mkdir(mode=0o755)
    runtime_root = runtime_parent / "metnos-executor-birth-v1"
    with _deployment_lock_for_test_v1(ownership_root) as deployment:
        installed = _install_startup_gate_for_test_v1(
            deployment, ownership_root, runtime_root,
        )
        with _exclusive_startup_gate_for_test_v1(
            installed.gate_path,
        ) as startup:
            yield ownership_root, installed.gate_path, deployment, startup


def publish(cell, record, *, seam=None):
    ownership_root, gate_path, deployment, startup = cell
    return _publish_startup_prerequisite_for_test_v2(
        record,
        deployment_session=deployment,
        startup_session=startup,
        ownership_root=ownership_root,
        gate_path=gate_path,
        _crash_seam=seam,
    )


@LINUX_ONLY
def test_prerequisite_is_published_once_and_reread_exactly(tmp_path) -> None:
    record = prerequisite("7")
    encoded = encode_startup_prerequisite_v1(record)
    with held_cell(tmp_path) as cell:
        first = publish(cell, record)
        second = publish(cell, record)
        target = (
            cell[0] / STARTUP_PREREQUISITES_DIRECTORY_V1
            / f"{record.request_id}.json"
        )
        assert target.read_bytes() == encoded
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert first.prerequisite_id == second.prerequisite_id
        assert first.evidence_digest == (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        )


@LINUX_ONLY
@pytest.mark.parametrize("interruption_stage", (
    "startup_prerequisite_created",
    "startup_prerequisite_written",
    "startup_prerequisite_temporary",
))
def test_temporary_survives_each_interruption_and_resumes(
    tmp_path, interruption_stage,
) -> None:
    record = prerequisite("8")

    class Interrupted(Exception):
        pass

    def seam(stage: str) -> None:
        if stage == interruption_stage:
            raise Interrupted

    with held_cell(tmp_path) as cell:
        with pytest.raises(Interrupted):
            publish(cell, record, seam=seam)
        directory = cell[0] / STARTUP_PREREQUISITES_DIRECTORY_V1
        temporary = directory / f".{record.request_id[7:]}.json.tmp"
        final = directory / f"{record.request_id}.json"
        observed = temporary.read_bytes()
        expected = encode_startup_prerequisite_v1(record)
        assert expected.startswith(observed)
        if interruption_stage != "startup_prerequisite_created":
            assert observed == expected
        assert not final.exists()

        publish(cell, record)
        assert final.read_bytes() == expected
        assert not temporary.exists()


@LINUX_ONLY
def test_previous_prerequisite_remains_while_the_next_is_appended(tmp_path) -> None:
    first = prerequisite("7")
    second = prerequisite("8")
    with held_cell(tmp_path) as cell:
        publish(cell, first)
        before = encode_startup_prerequisite_v1(first)
        publish(cell, second)
        directory = cell[0] / STARTUP_PREREQUISITES_DIRECTORY_V1
        assert (directory / f"{first.request_id}.json").read_bytes() == before
        assert (
            directory / f"{second.request_id}.json"
        ).read_bytes() == encode_startup_prerequisite_v1(second)


@LINUX_ONLY
@pytest.mark.parametrize("foreign", ("unknown", "conflicting-final"))
def test_foreign_inventory_is_refused_without_replacement(
    tmp_path, foreign,
) -> None:
    record = prerequisite("9")
    with held_cell(tmp_path) as cell:
        directory = cell[0] / STARTUP_PREREQUISITES_DIRECTORY_V1
        directory.mkdir(mode=0o755)
        if foreign == "unknown":
            target = directory / "other"
            target.write_bytes(b"unchanged")
        else:
            target = directory / f"{record.request_id}.json"
            target.write_bytes(b"{}")
        target.chmod(0o644)
        before = target.read_bytes()

        with pytest.raises(DistributionAssemblerError):
            publish(cell, record)
        assert target.read_bytes() == before


@LINUX_ONLY
def test_test_publisher_requires_both_live_sessions(tmp_path) -> None:
    record = prerequisite("a")
    ownership_root = tmp_path / "ownership"
    gate_path = tmp_path / "missing" / "startup-v1.lock"
    with pytest.raises(OwnershipCoordinatorError):
        _publish_startup_prerequisite_for_test_v2(
            record,
            deployment_session=object(),
            startup_session=object(),
            ownership_root=ownership_root,
            gate_path=gate_path,
        )

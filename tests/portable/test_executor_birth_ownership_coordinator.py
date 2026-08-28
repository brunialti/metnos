from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import pickle
import stat
import sys
from copy import copy, deepcopy
from contextlib import contextmanager

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_distribution_manifest as distribution
import executor_birth_ownership_coordinator as coordinator_module
from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS, BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_GUARD_VERSION, BIRTH_CLOSED_OWNER, BIRTH_CLOSED_SCHEMA,
    BIRTH_CLOSED_SEALED_MODULES, SCAN_ROOTS,
    SCHEMA as BOUNDARY_INVENTORY_SCHEMA,
)
from install.birth_ownership_authority_provisioner import (
    _provision_ownership_authorities_at_v1,
)
from executor_birth_cutover import CurrentReceiptProof
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_ownership_authorities import (
    _load_private_at_v1, _root_ownership_authorities_for_test,
)
from executor_birth_ownership_coordinator import (
    OwnershipCoordinatorError, OwnershipCoordinatorJournalV1,
    OwnershipCoordinatorStateV1, _prepare_under_maintenance_v1,
    _deployment_lock_for_test_v1, _deployment_lock_v1, _prepared_record,
    _publish_certificate_with_prerequisite_v1,
    _require_deployment_lock_session_v1,
    _require_test_deployment_lock_session_v1,
    _startup_prerequisite_for_test,
)
from executor_birth_ownership_preflight import (
    canonical_maintenance_proof,
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _distribution(tmp_path, name="current"):
    root = tmp_path / ("distribution-" + name)
    root.mkdir(mode=0o755, exist_ok=True)
    private = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(name.encode("ascii")).digest()
    )
    key_id = distribution.distribution_key_id(private.public_key())
    registry = distribution.DistributionRegistry({
        key_id: distribution.DistributionKey(
            key_id, private.public_key(), frozenset({distribution.PURPOSE}),
        ),
    })
    inventory = _canonical({
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": "signed-release", "scan_roots": list(SCAN_ROOTS),
        "entries": [],
        "birth_closed": {
            "schema": BIRTH_CLOSED_SCHEMA,
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "coordinator_store_owners": sorted(
                BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
            ),
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception in sorted(
                    BIRTH_CLOSED_EXCEPTION_SCOPES.items(),
                )
            ],
        },
    })
    contents = {
        "requirements.lock": ("dependency_lock", b"cryptography==47.0.0\n"),
        "runtime/__version__.py": ("product_version", b'__version__ = "1.2.3"\n'),
        "runtime/contract_boundary_guard.py": ("boundary_guard", b"GUARD = 1\n"),
        "runtime/contract_store.py": ("runtime_code", b"STORE = 1\n"),
        "runtime/executor_birth.py": ("runtime_code", b"BIRTH = 1\n"),
        "runtime/executor_birth_distribution_manifest.py": ("preflight", b"VERIFY = 1\n"),
        "runtime/executor_birth_ownership_preflight.py": ("preflight", b"PREFLIGHT = 1\n"),
        "runtime/sign.py": ("runtime_code", b"SIGN = 1\n"),
        "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json": (
            "boundary_inventory", inventory,
        ),
        "systemd/metnos-http-birth-closed.conf": ("service_unit", b"[Service]\n"),
    }
    files = []
    for path, (role, content) in contents.items():
        destination = root.joinpath(*path.split("/"))
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        destination.write_bytes(content)
        if os.name != "nt":
            destination.chmod(0o644)
        files.append({
            "path": path, "size": len(content), "role": role,
            "content_hash": distribution.file_content_hash(path, content),
        })
    target = "windows" if os.name == "nt" else "linux"
    value = {
        "schema_version": 1, "closed_build_id": None,
        "previous_closed_build_id": D("0"), "release_sequence": 2,
        "product_version": "1.2.3", "platform": target,
        "architecture": "x86_64", "signing_key_id": key_id,
        "installation_root": str(root),
        "certificate_directory": (
            r"C:\ProgramData\Metnos\ExecutorBirth"
            if target == "windows" else "/var/lib/metnos/executor-birth"
        ),
        "boundary_inventory_path": (
            "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
        ),
        "boundary_inventory_hash": "sha256:" + hashlib.sha256(
            distribution.BOUNDARY_INVENTORY_DOMAIN + inventory,
        ).hexdigest(),
        "boundary_guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "preflight_entrypoint": "runtime/executor_birth_distribution_manifest.py",
        "files": sorted(files, key=lambda item: item["path"].encode("utf-8")),
    }
    value["closed_build_id"] = "sha256:" + hashlib.sha256(
        distribution.BUILD_ID_DOMAIN + _canonical({
            key: item for key, item in value.items() if key != "closed_build_id"
        }),
    ).hexdigest()
    encoded = _canonical(value)
    signature = private.sign(distribution.SIGNATURE_DOMAIN + encoded)
    return distribution._verify_distribution_manifest_for_test(
        encoded, signature, registry=registry,
        _environment=distribution._environment_for_test(
            target, "x86_64", root,
        ),
    )


def _maintenance(source="inactive_http_and_inactive_sidecar") -> bytes:
    return canonical_maintenance_proof(
        source=source,
        units=tuple({
            "scope": scope, "unit": unit, "load_state": "loaded",
            "active_state": "inactive", "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )


def _proof() -> CurrentReceiptProof:
    identities = (("executor:alpha", D("3")),)
    return CurrentReceiptProof(identities, {identities[0]: D("4")})


def _prepared(tmp_path):
    journal = OwnershipCoordinatorJournalV1(
        tmp_path / "journal", root_owned=False,
    )
    maintenance = _maintenance()
    result = _prepare_under_maintenance_v1(
        _distribution(tmp_path), journal=journal,
        initial_maintenance=maintenance,
        observe_maintenance=lambda: maintenance,
        prepare_receipts=_proof,
    )
    return journal, result


def _disk_authorities(tmp_path):
    root = tmp_path / "authority-root"
    root.mkdir(mode=0o755)
    _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    return _load_private_at_v1(root / "authorities-v1", root_owned=False)


def _portable_authorities():
    names = (
        "coordinator-distribution", "coordinator-cutover",
        "coordinator-head",
    )
    return _root_ownership_authorities_for_test(*(
        Ed25519PrivateKey.from_private_bytes(
            hashlib.sha256(name.encode("ascii")).digest()
        )
        for name in names
    ))


def test_productive_core_stops_at_receipts_complete_and_replays(tmp_path):
    journal, result = _prepared(tmp_path)
    assert result.state is OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
    assert result.current_count == 1
    calls = []
    repeated = _prepare_under_maintenance_v1(
        _distribution(tmp_path), journal=journal,
        initial_maintenance=_maintenance(),
        observe_maintenance=lambda: calls.append("maintenance") or _maintenance(),
        prepare_receipts=lambda: calls.append("receipts") or _proof(),
    )
    assert repeated == result
    assert calls == ["receipts", "maintenance"]
    assert [record.state for record in journal.load()] == [
        OwnershipCoordinatorStateV1.PREPARED,
        OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
    ]
    changed = CurrentReceiptProof(
        (("executor:changed", D("7")),),
        {("executor:changed", D("7")): D("8")},
    )
    with pytest.raises(OwnershipCoordinatorError, match="recovery_required"):
        _prepare_under_maintenance_v1(
            _distribution(tmp_path), journal=journal,
            initial_maintenance=_maintenance(),
            observe_maintenance=_maintenance, prepare_receipts=lambda: changed,
        )


def test_productive_entry_rechecks_live_files_before_journal(tmp_path, monkeypatch):
    verified = _distribution(tmp_path)
    root = tmp_path / "distribution-current"
    private = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"current").digest()
    )
    key_id = distribution.distribution_key_id(private.public_key())
    registry = distribution.DistributionRegistry({
        key_id: distribution.DistributionKey(
            key_id, private.public_key(), frozenset({distribution.PURPOSE}),
        ),
    })
    record = distribution._authenticate_distribution_record_for_test(
        verified.encoded, verified.signature, registry=registry,
    )

    @contextmanager
    def deployment_lock():
        yield

    def verify_live(_record):
        return distribution._verify_authenticated_distribution_record_for_test(
            record, environment=distribution._environment_for_test(
                "windows" if os.name == "nt" else "linux",
                "x86_64", root,
            ),
        )

    def unexpected_journal(*_args, **_kwargs):
        raise AssertionError("journal reached before live verification")

    monkeypatch.setattr(coordinator_module, "_deployment_lock_v1", deployment_lock)
    monkeypatch.setattr(
        coordinator_module, "verify_current_installation_distribution_v1",
        lambda _encoded, _signature: verify_live(record),
    )
    monkeypatch.setattr(
        coordinator_module, "OwnershipCoordinatorJournalV1", unexpected_journal,
    )
    (root / "runtime" / "sign.py").write_bytes(b"tampered")
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        coordinator_module.prepare_ownership_cutover_v1(verified)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux journal")
@pytest.mark.parametrize("disposition", ["partial", "complete"])
def test_journal_recovers_only_one_nominal_temporary(tmp_path, disposition):
    journal = OwnershipCoordinatorJournalV1(
        tmp_path / "journal", root_owned=False,
    )
    record = _prepared_record(
        _distribution(tmp_path), previous_cutover_id=None,
    )
    temporary = journal.directory / (
        ".record-000-v1.json." + record.request_id[7:] + ".tmp"
    )
    temporary.write_bytes(
        b'{"partial"' if disposition == "partial" else record.encode()
    )
    temporary.chmod(0o600 if disposition == "partial" else 0o644)
    loaded = journal.load()
    assert loaded == (() if disposition == "partial" else (record,))
    assert not temporary.exists()


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "gap", "rewind"])
def test_journal_rejects_open_schema_gaps_and_rewind(tmp_path, mutation):
    journal, _result = _prepared(tmp_path)
    first = journal.directory / "record-000-v1.json"
    second = journal.directory / "record-001-v1.json"
    if mutation == "extra":
        value = json.loads(first.read_bytes())
        value["extra"] = True
        first.write_bytes(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
        ).encode("ascii"))
    elif mutation == "duplicate":
        first.write_bytes(first.read_bytes().replace(
            b'{"boundary_guard_version"',
            b'{"request_id":"' + D("9").encode("ascii")
            + b'","boundary_guard_version"',
        ))
    elif mutation == "gap":
        second.rename(journal.directory / "record-002-v1.json")
    else:
        value = json.loads(second.read_bytes())
        value["sequence"] = 0
        second.write_bytes(json.dumps(
            value, sort_keys=True, separators=(",", ":"),
        ).encode("ascii"))
    with pytest.raises(OwnershipCoordinatorError, match="journal_invalid"):
        journal.load()


def _crash_publish(journal_path, certificate_path, authority_path, point):
    journal = OwnershipCoordinatorJournalV1(journal_path, root_owned=False)
    authorities = _load_private_at_v1(authority_path, root_owned=False)
    prerequisite = _startup_prerequisite_for_test(D("5"), D("6"))

    def crash(observed):
        if observed == point:
            os._exit(91)

    _publish_certificate_with_prerequisite_v1(
        journal=journal, certificate_directory=certificate_path,
        authorities=authorities, prerequisite=prerequisite,
        observe_maintenance=_maintenance,
        _crash_seam=crash,
    )


def _resume_publish(journal_path, certificate_path, authority_path, queue):
    try:
        result = _publish_certificate_with_prerequisite_v1(
            journal=OwnershipCoordinatorJournalV1(
                journal_path, root_owned=False,
            ),
            certificate_directory=certificate_path,
            authorities=_load_private_at_v1(authority_path, root_owned=False),
            prerequisite=_startup_prerequisite_for_test(D("5"), D("6")),
            observe_maintenance=_maintenance,
        )
        queue.put(result.state.value)
    except Exception as exc:
        queue.put("error:" + getattr(exc, "code", type(exc).__name__))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
@pytest.mark.parametrize("point", [
    "certificate_ready", "certificate_signature", "certificate_payload",
    "certificate_verified",
])
def test_process_death_resumes_from_every_certificate_boundary(tmp_path, point):
    journal, _result = _prepared(tmp_path)
    authorities = _disk_authorities(tmp_path)
    authority_path = tmp_path / "authority-root" / "authorities-v1"
    certificate = tmp_path / "certificate"
    certificate.mkdir(mode=0o755)
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_publish,
        args=(journal.directory, certificate, authority_path, point),
    )
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 91
    queue = context.Queue()
    recovery = context.Process(
        target=_resume_publish,
        args=(journal.directory, certificate, authority_path, queue),
    )
    recovery.start()
    recovery.join(timeout=15)
    assert recovery.exitcode == 0
    assert queue.get(timeout=2) == "CERTIFICATE_PUBLISHED"
    reopened = OwnershipCoordinatorJournalV1(
        journal.directory, root_owned=False,
    )
    assert len(reopened.load()) == 4


def test_signature_without_certificate_ready_is_never_adopted(tmp_path):
    journal, _result = _prepared(tmp_path)
    authorities = _portable_authorities()
    certificate = tmp_path / "certificate"
    certificate.mkdir(mode=0o755)
    (certificate / "ownership-cutover-v1.sig").write_bytes(b"x" * 64)
    with pytest.raises(OwnershipCoordinatorError, match="recovery_required"):
        _publish_certificate_with_prerequisite_v1(
            journal=journal, certificate_directory=certificate,
            authorities=authorities,
            prerequisite=_startup_prerequisite_for_test(D("5"), D("6")),
            observe_maintenance=_maintenance,
        )


def test_fresh_maintenance_is_required_before_certificate_ready(tmp_path):
    journal, _result = _prepared(tmp_path)
    authorities = _portable_authorities()
    certificate = tmp_path / "certificate"
    certificate.mkdir(mode=0o755)
    with pytest.raises(OwnershipCoordinatorError, match="maintenance drift"):
        _publish_certificate_with_prerequisite_v1(
            journal=journal, certificate_directory=certificate,
            authorities=authorities,
            prerequisite=_startup_prerequisite_for_test(D("5"), D("6")),
            observe_maintenance=lambda: _maintenance(
                "inactive_http_and_sidecar_broker",
            ),
        )
    assert [record.state for record in journal.load()] == [
        OwnershipCoordinatorStateV1.PREPARED,
        OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
    ]


def test_partial_certificate_temporary_is_rebuilt_before_publication(tmp_path):
    from executor_birth_ownership_cutover import issue_ownership_cutover_certificate

    journal, result = _prepared(tmp_path)
    authorities = _portable_authorities()
    latest = journal.load()[-1]
    key_id = next(iter(authorities.public.cutover.keys))
    _payload, signature = issue_ownership_cutover_certificate(
        proof=latest.current_proof,
        previous_cutover_id=latest.previous_cutover_id,
        request_id=latest.request_id, signing_key_id=key_id,
        maintenance_evidence_hash=latest.maintenance_after_hash,
        boundary_inventory_hash=latest.boundary_inventory_hash,
        boundary_guard_version=latest.boundary_guard_version,
        closed_build_id=latest.closed_build_id,
        private_key=authorities.cutover_private,
    )
    certificate = tmp_path / "certificate"
    certificate.mkdir(mode=0o755)
    temporary = certificate / (
        ".ownership-cutover-v1.sig." + result.request_id[7:] + ".tmp"
    )
    temporary.write_bytes(signature[:11])
    temporary.chmod(0o600)
    published = _publish_certificate_with_prerequisite_v1(
        journal=journal, certificate_directory=certificate,
        authorities=authorities,
        prerequisite=_startup_prerequisite_for_test(D("5"), D("6")),
        observe_maintenance=_maintenance,
    )
    assert published.state is OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED


def _locked_prepare_worker(root, distribution_value, barrier, queue):
    try:
        barrier.wait()
        with _deployment_lock_for_test_v1(root) as session:
            _require_test_deployment_lock_session_v1(session, root)
            journal = OwnershipCoordinatorJournalV1(
                root / "journal", root_owned=False,
            )
            result = _prepare_under_maintenance_v1(
                distribution_value, journal=journal,
                initial_maintenance=_maintenance(),
                observe_maintenance=_maintenance, prepare_receipts=_proof,
            )
        queue.put(result.state.value)
    except Exception as exc:
        queue.put("error:" + getattr(exc, "code", type(exc).__name__))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
def test_deployment_lock_serializes_same_request_and_rejects_a_different_one(tmp_path):
    root = tmp_path / "deployment"
    root.mkdir(mode=0o755)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    queue = context.Queue()
    distribution_value = _distribution(tmp_path)
    workers = [
        context.Process(
            target=_locked_prepare_worker,
            args=(root, distribution_value, barrier, queue),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
    assert all(worker.exitcode == 0 for worker in workers)
    assert [queue.get(timeout=2) for _ in workers] == [
        "RECEIPTS_COMPLETE", "RECEIPTS_COMPLETE",
    ]
    other = _distribution(tmp_path, "other")
    with _deployment_lock_for_test_v1(root) as session:
        _require_test_deployment_lock_session_v1(session, root)
        with pytest.raises(OwnershipCoordinatorError, match="request_conflict"):
            _prepare_under_maintenance_v1(
                other,
                journal=OwnershipCoordinatorJournalV1(
                    root / "journal", root_owned=False,
                ),
                initial_maintenance=_maintenance(),
                observe_maintenance=_maintenance, prepare_receipts=_proof,
            )


def _inherited_session_worker(session, root, queue):
    try:
        _require_test_deployment_lock_session_v1(session, root)
        queue.put("accepted")
    except Exception as exc:
        queue.put(getattr(exc, "code", type(exc).__name__))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
def test_deployment_lock_session_is_live_local_and_nontransferable(tmp_path):
    root = tmp_path / "deployment-session"
    root.mkdir(mode=0o755)
    assert tuple(inspect.signature(_deployment_lock_v1).parameters) == ()
    with _deployment_lock_for_test_v1(root) as session:
        _require_test_deployment_lock_session_v1(session, root)
        assert set(type(session).__slots__) == {"_seal", "_token"}
        assert not hasattr(session, "_lease")
        assert not hasattr(session, "descriptor")
        with pytest.raises(OwnershipCoordinatorError):
            _require_test_deployment_lock_session_v1(session, root / "other")
        with pytest.raises(OwnershipCoordinatorError):
            _require_deployment_lock_session_v1(session)
        for operation in (copy, deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                operation(session)
        forged = object.__new__(type(session))
        with pytest.raises(OwnershipCoordinatorError):
            _require_test_deployment_lock_session_v1(forged, root)
        clone = object.__new__(type(session))
        clone._seal = session._seal
        clone._token = session._token
        with pytest.raises(OwnershipCoordinatorError):
            _require_test_deployment_lock_session_v1(clone, root)
        malformed = object.__new__(type(session))
        malformed._seal = session._seal

        class HostileToken:
            def __hash__(self):
                raise RuntimeError("hostile token hash")

            def __eq__(self, _other):
                raise RuntimeError("hostile token equality")

        malformed._token = HostileToken()
        with pytest.raises(OwnershipCoordinatorError) as failure:
            _require_test_deployment_lock_session_v1(malformed, root)
        assert failure.value.code == "birth_ownership_deployment_lock_invalid"

        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        child = context.Process(
            target=_inherited_session_worker, args=(session, root, queue),
        )
        child.start()
        child.join(timeout=10)
        assert child.exitcode == 0
        assert queue.get(timeout=2) == "birth_ownership_deployment_lock_invalid"
    with pytest.raises(OwnershipCoordinatorError):
        _require_test_deployment_lock_session_v1(session, root)


def test_deployment_lock_rejects_unregistered_nominal_session(tmp_path):
    root = tmp_path / "deployment-unregistered-session"
    session = coordinator_module._DeploymentLockSessionForTestV1(
        object(), coordinator_module._TEST_DEPLOYMENT_LOCK_SESSION_SEAL,
    )
    with pytest.raises(OwnershipCoordinatorError) as failure:
        _require_test_deployment_lock_session_v1(session, root)
    assert failure.value.code == "birth_ownership_deployment_lock_invalid"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
def test_deployment_lock_session_rejects_replaced_nominal_file(tmp_path):
    root = tmp_path / "deployment-replaced-lock"
    root.mkdir(mode=0o755)
    lock_path = root / coordinator_module.DEPLOYMENT_LOCK_BASENAME_V1
    displaced = root / "displaced.lock"

    with _deployment_lock_for_test_v1(root) as original_session:
        lock_path.replace(displaced)
        lock_path.write_bytes(b"\0")
        lock_path.chmod(0o600)
        with pytest.raises(OwnershipCoordinatorError):
            _require_test_deployment_lock_session_v1(original_session, root)
        with _deployment_lock_for_test_v1(root) as replacement_session:
            _require_test_deployment_lock_session_v1(replacement_session, root)
            with pytest.raises(OwnershipCoordinatorError):
                _require_test_deployment_lock_session_v1(original_session, root)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
def test_fork_child_does_not_retain_deployment_lock(tmp_path):
    import fcntl

    root = tmp_path / "deployment-fork-release"
    root.mkdir(mode=0o755)
    status_read, status_write = os.pipe()
    release_read, release_write = os.pipe()
    holder = os.fork()
    if holder == 0:
        os.close(status_read)
        os.close(release_write)
        with _deployment_lock_for_test_v1(root):
            child = os.fork()
            if child == 0:
                os.close(status_write)
                os.read(release_read, 1)
                os._exit(0)
            os.close(release_read)
            os.write(status_write, (str(child) + "\n").encode("ascii"))
            os.close(status_write)
            os._exit(0)

    os.close(status_write)
    os.close(release_read)
    try:
        child_report = os.read(status_read, 64)
        os.close(status_read)
        assert child_report.strip().isdigit()
        waited, status_value = os.waitpid(holder, 0)
        assert waited == holder and os.waitstatus_to_exitcode(status_value) == 0

        lock_path = root / coordinator_module.DEPLOYMENT_LOCK_BASENAME_V1
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
        with _deployment_lock_for_test_v1(root) as session:
            _require_test_deployment_lock_session_v1(session, root)
    finally:
        try:
            os.write(release_write, b"x")
        except OSError:
            pass
        os.close(release_write)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
@pytest.mark.parametrize("failure_stage", ("target", "parent"))
def test_deployment_lock_retries_each_durability_boundary(
    tmp_path, monkeypatch, failure_stage,
):
    root = tmp_path / ("deployment-sync-" + failure_stage)
    root.mkdir(mode=0o755)
    calls = []
    if failure_stage == "target":
        real_fsync = os.fsync

        def fail_target_once(fd):
            lock_path = root / coordinator_module.DEPLOYMENT_LOCK_BASENAME_V1
            lock_inode = lock_path.stat().st_ino if lock_path.exists() else None
            if os.fstat(fd).st_ino == lock_inode:
                calls.append("target")
                if len(calls) == 1:
                    raise OSError("injected target fsync failure")
            real_fsync(fd)

        monkeypatch.setattr(coordinator_module.os, "fsync", fail_target_once)
    else:
        real_sync = coordinator_module._sync_directory

        def fail_parent_once(path):
            calls.append("parent")
            if len(calls) == 1:
                raise OSError("injected parent fsync failure")
            real_sync(path)

        monkeypatch.setattr(coordinator_module, "_sync_directory", fail_parent_once)

    with pytest.raises(OwnershipCoordinatorError) as failure:
        with _deployment_lock_for_test_v1(root):
            pass
    assert failure.value.code == "birth_ownership_deployment_lock_invalid"
    with _deployment_lock_for_test_v1(root) as session:
        _require_test_deployment_lock_session_v1(session, root)
    assert calls == [failure_stage, failure_stage]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux coordinator")
def test_deployment_lock_repairs_safe_restrictive_empty_residue(
    tmp_path, monkeypatch,
):
    root = tmp_path / "deployment-restrictive-residue"
    root.mkdir(mode=0o755)
    lock_path = root / coordinator_module.DEPLOYMENT_LOCK_BASENAME_V1
    seed_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(seed_descriptor, 0o000)
    real_open = os.open

    def privileged_reopen(path, flags, mode=0o777):
        if os.fspath(path) == os.fspath(lock_path) and not flags & os.O_CREAT:
            return os.dup(seed_descriptor)
        return real_open(path, flags, mode)

    monkeypatch.setattr(coordinator_module.os, "open", privileged_reopen)
    try:
        with _deployment_lock_for_test_v1(root) as session:
            _require_test_deployment_lock_session_v1(session, root)
    finally:
        os.close(seed_descriptor)

    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert lock_path.read_bytes() == b"\0"


def test_product_deployment_lock_fails_off_linux_before_io(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("deployment lock performed I/O")

    monkeypatch.setattr(coordinator_module.sys, "platform", "win32")
    monkeypatch.setattr(coordinator_module.os, "open", unexpected)
    monkeypatch.setattr(coordinator_module.Path, "mkdir", unexpected)
    with pytest.raises(OwnershipCoordinatorError) as failure:
        with _deployment_lock_v1():
            pass
    assert failure.value.code == "birth_ownership_platform_unsupported"

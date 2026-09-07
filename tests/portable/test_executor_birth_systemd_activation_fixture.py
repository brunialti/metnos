"""Validate the synthetic G6 ownership history without root or systemd."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler
import executor_birth_legacy_state as legacy


@pytest.mark.parametrize("mutation", (None, "missing-record", "wrong-binding"))
def test_g6_fixture_history_is_complete_and_bound(tmp_path, monkeypatch, mutation):
    portable = Path(__file__).resolve().parent
    monkeypatch.syspath_prepend(str(portable))
    import test_executor_birth_admin_preflight as support
    import test_executor_birth_admin_preflight_materials as materials
    import test_executor_birth_systemd_activation as activation

    openssl = shutil.which("openssl")
    if sys.platform != "linux" or openssl is None:
        pytest.skip("requires POSIX snapshots and OpenSSL signature verification")
    root = tmp_path / "ownership"
    root.mkdir(mode=0o755)
    monkeypatch.setattr(activation, "OWNERSHIP_ROOT", root)

    def no_live_call(*_args, **_kwargs):
        pytest.fail("portable fixture test must not enter the live G6 setup")

    monkeypatch.setattr(activation, "_activation_fixture", no_live_call)
    monkeypatch.setattr(activation, "_systemctl", no_live_call)
    keys = {name: Ed25519PrivateKey.generate()
            for name in ("distribution", "cutover", "head")}
    _release, manifest, signature, registry, _temporary = (
        support._distribution_fixture(tmp_path, private_key=keys["distribution"])
    )
    # These are already-valid portable codec inputs. Only the G6 graph builder
    # is under test here, not installed-file or effective-systemd attestation.
    descriptor = materials._deployment_record()
    fixture = SimpleNamespace(
        namespace="0123456789abcdef", manifest=manifest, signature=signature,
        distribution_registry=registry, private_keys=keys,
        account=SimpleNamespace(uid=991, gid=991),
        descriptor=descriptor,
        descriptor_bytes=assembler.encode_deployment_descriptor_v1(descriptor),
        catalog_bytes=materials._catalog_bytes(),
    )
    digest = materials.D("a")
    tcb = SimpleNamespace(
        executables=SimpleNamespace(**{
            name: digest for name in (
                "python_binary_hash", "openssl_binary_hash",
                "systemctl_binary_hash", "systemd_analyze_binary_hash",
            )
        }),
        openssl_tcb=SimpleNamespace(openssl_tcb_hash=digest),
    )
    effective = SimpleNamespace(
        manager_version="255.4-1ubuntu8.17",
        snapshot=SimpleNamespace(effective_units_hash=digest),
    )
    if mutation == "wrong-binding":
        install_journal = activation._install_legacy_state_journal

        def wrong_binding(value):
            install_journal(value)
            return "sha256:" + "0" * 64

        monkeypatch.setattr(activation, "_install_legacy_state_journal", wrong_binding)
    # Match G6's 0755 parent-directory contract: pathlib's intermediate
    # parents use the process mask, not the leaf directory's explicit mode.
    previous_umask = os.umask(0o022)
    try:
        prerequisite, request_id, head = activation._build_prerequisite_and_graph(
            fixture, tcb, effective, digest,
        )
    finally:
        os.umask(previous_umask)
    journal = root / "legacy-state-adoption-v1"
    assert sorted(path.name for path in journal.iterdir()) == list(
        preflight._LEGACY_STATE_JOURNAL_NAMES_V1,
    )
    for path, mode in ((journal, 0o700), (journal / "journal.lock", 0o600)):
        info = path.lstat()
        assert (info.st_uid, info.st_gid) == (os.getuid(), os.getgid())
        assert stat.S_IMODE(info.st_mode) == mode
    assert (journal / "journal.lock").read_bytes() == b""
    paths = [journal / name for name in preflight._LEGACY_STATE_RECORD_NAMES_V1]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o644 for path in paths)
    raw = tuple(path.read_bytes() for path in paths)
    canonical = legacy.decode_legacy_state_chain_v1(raw)
    autonomous = preflight._decode_legacy_state_chain_v1(raw)
    assert tuple(record.state for record in canonical) == tuple(legacy.LegacyStateV1)
    assert canonical[-1].record_sha256 == autonomous[-1].record_sha256
    records = tuple(preflight._decode_coordinator_record_v2(path.read_bytes())
                    for path in sorted((root / "coordinator-v1" / "transactions-v2"
                                        / request_id).iterdir()))
    assert len(records) == 6 and records[-1].sequence == head.sequence == 5
    assert all(record.legacy_state_record_sha256 == head.legacy_state_record_sha256
               for record in records)
    assert records[-1].startup_prerequisite_digest == activation._raw_digest(prerequisite)
    if mutation == "missing-record":
        paths[-1].rename(tmp_path / "withheld-ready.json")
    if mutation is not None:
        expected = ("legacy state journal inventory" if mutation == "missing-record"
                    else "claim transaction binding")
        with pytest.raises(preflight.PreflightError, match=expected):
            preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
                root, openssl_executable=Path(openssl).resolve(),
            )
        return
    assert head.legacy_state_record_sha256 == canonical[-1].record_sha256
    snapshot = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path(openssl).resolve(),
    ).snapshot
    assert len(snapshot.transactions) == 1 and not snapshot.pending_claims
    assert snapshot.transactions[0].prefix.records[-1].head_id == head.head_id
    selected = preflight._select_ownership_epoch_v1(snapshot)
    assert selected.required_head.head_id == head.head_id
    assert selected.transaction.prefix.records[-1].request_id == request_id

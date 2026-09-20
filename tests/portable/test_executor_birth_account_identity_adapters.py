"""Error translation and snapshot binding for account-identity adapters."""
from __future__ import annotations

import pytest

import executor_birth_account_identity as identity
from install import birth_authority_provisioner as provisioner
from install import executor_birth_source_receiver as receiver
from install import executor_birth_transition as transition


_ERROR_MATRIX = (
    ("transition", identity.PosixAccountFailureKindV1.platform_unsupported,
     "birth_ownership_platform_unsupported"),
    ("transition", identity.PosixAccountFailureKindV1.account_lookup_failed,
     "birth_ownership_deployment_invalid"),
    ("transition", identity.PosixAccountFailureKindV1.group_lookup_failed,
     "birth_ownership_deployment_invalid"),
    ("source_receiver", identity.PosixAccountFailureKindV1.platform_unsupported,
     "birth_ownership_deployment_invalid"),
    ("source_receiver", identity.PosixAccountFailureKindV1.account_lookup_failed,
     "birth_ownership_deployment_invalid"),
    ("source_receiver", identity.PosixAccountFailureKindV1.group_lookup_failed,
     "birth_ownership_deployment_invalid"),
    ("provisioner", identity.PosixAccountFailureKindV1.platform_unsupported,
     "birth_transition_service_identity_changed"),
    ("provisioner", identity.PosixAccountFailureKindV1.account_lookup_failed,
     "birth_transition_service_identity_changed"),
    ("provisioner", identity.PosixAccountFailureKindV1.group_lookup_failed,
     "birth_transition_service_identity_changed"),
)


@pytest.mark.parametrize("adapter,kind,expected_code", _ERROR_MATRIX)
def test_account_adapters_translate_typed_failures(
    monkeypatch, adapter, kind, expected_code,
) -> None:
    def fail(_name):
        raise identity.PosixAccountResolutionError(kind)

    monkeypatch.setattr(identity, "resolve_posix_account_v1", fail)
    monkeypatch.setattr(identity, "resolve_posix_account_snapshot_v1", fail)
    selected = {
        "transition": transition._service_environment_v1,
        "source_receiver": receiver._service_account_snapshot_v1,
        "provisioner": provisioner._resolve_legacy_service_identity_v2,
    }[adapter]

    with pytest.raises(RuntimeError) as captured:
        selected("metnos")
    assert captured.value.code == expected_code


def test_receiver_snapshot_binds_the_resolved_shell_target(monkeypatch) -> None:
    record = identity.PosixAccountRecordV1(
        "metnos", 991, 992, "/srv/metnos", "/usr/sbin/nologin",
    )
    snapshot = identity.PosixAccountSnapshotV1(record, (992, 1001))
    monkeypatch.setattr(
        identity, "resolve_posix_account_snapshot_v1", lambda _name: snapshot,
    )
    targets = iter(("/usr/sbin/nologin", "/usr/bin/false"))
    monkeypatch.setattr(
        receiver, "_resolve_root_owned_shell_v1", lambda _shell: next(targets),
    )
    monkeypatch.setattr(
        receiver, "_require_closed_user_authority_v1", lambda _account: None,
    )

    before = receiver._service_account_snapshot_v1("metnos")
    after = receiver._service_account_snapshot_v1("metnos")

    with pytest.raises(identity.PosixAccountSnapshotChangedError):
        before._identity_snapshot_v1().assert_unchanged(
            after._identity_snapshot_v1(),
        )


def test_transition_rejects_a_resolver_identity_alias(monkeypatch) -> None:
    account = identity.PosixAccountRecordV1(
        "different", 991, 992, "/srv/metnos", "/usr/sbin/nologin",
    )
    monkeypatch.setattr(
        identity, "resolve_posix_account_v1", lambda _name: account,
    )

    with pytest.raises(transition.TransitionEntryError) as captured:
        transition._service_environment_v1("metnos")
    assert captured.value.code == "birth_ownership_deployment_invalid"

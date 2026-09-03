from __future__ import annotations

from pathlib import Path

import pytest

import contract_store
import executor_birth_authority_gate as gate
import sign


LEGACY_STORE_APIS = (
    "activate_store",
    "publish_technical_update",
    "publish_signed_source",
    "reactivate_technical_update",
    "rollback",
)
OFFLINE_STORE_APIS = tuple(
    name for name in LEGACY_STORE_APIS if name != "activate_store"
)

LEGACY_SIGN_APIS = (
    "sign_executor",
    "publish_executor",
    "publish_authoring_update",
    "reactivate_executor_contract",
    "rollback_executor_contract",
)


def _store_call(operation, name: str, *, store_root=None):
    if name == "activate_store":
        return operation(
            None, shadow_root=None, trusted_publics=None,
            quiescence_guard=None,
        )
    common = {"store_root": store_root}
    if name == "publish_technical_update":
        return operation(
            None, expected_generation_id=None, draft=None, private_key=None,
            trusted_publics=None, **common,
        )
    if name == "publish_signed_source":
        return operation(
            None, expected_generation_id=None, trusted_publics=None, **common,
        )
    if name == "reactivate_technical_update":
        return operation(
            None, expected_retirement_id=None, draft=None, actor=None,
            reason=None, private_key=None, trusted_publics=None, **common,
        )
    return operation(
        None, expected_generation_id=None, target_generation_id=None,
        actor=None, reason=None, trusted_publics=None, **common,
    )


def _sign_call(operation, name: str):
    if name == "reactivate_executor_contract":
        return operation(object(), actor=None, reason=None)
    if name == "rollback_executor_contract":
        return operation(
            object(), expected_generation_id=None, target_generation_id=None,
            actor=None, reason=None,
        )
    return operation(object())


@pytest.fixture
def closed_build(monkeypatch):
    # This replaces the signed source literal solely inside the test process;
    # the production API exposes no flag, environment or state switch.
    monkeypatch.setattr(gate, "closed_build_enforcement", lambda: True)


@pytest.mark.parametrize("name", LEGACY_STORE_APIS)
def test_closed_build_denies_productive_store_before_touching_inputs(
    name: str, closed_build,
) -> None:
    operation = getattr(contract_store, name)
    with pytest.raises(contract_store.ContractStoreError) as caught:
        _store_call(operation, name)
    assert caught.value.code == gate.LEGACY_API_CLOSED
    assert caught.value.detail == name


@pytest.mark.parametrize("name", LEGACY_STORE_APIS)
def test_imported_alias_remains_denied(name: str, closed_build) -> None:
    alias = getattr(contract_store, name)
    with pytest.raises(contract_store.ContractStoreError) as caught:
        _store_call(alias, name)
    assert caught.value.code == gate.LEGACY_API_CLOSED


@pytest.mark.parametrize("name", LEGACY_SIGN_APIS)
def test_closed_build_denies_signing_before_path_or_key_access(
    name: str, closed_build,
) -> None:
    operation = getattr(sign, name)
    with pytest.raises(gate.BirthAuthorityGateClosed) as caught:
        _sign_call(operation, name)
    assert caught.value.code == gate.LEGACY_API_CLOSED
    assert caught.value.operation == name


@pytest.mark.parametrize("name", OFFLINE_STORE_APIS)
def test_explicit_nonproductive_root_preserves_offline_exception(
    name: str, closed_build, tmp_path: Path,
) -> None:
    operation = getattr(contract_store, name)
    # It passes the F4 denial and reaches ordinary argument validation.  The
    # isolated root is still subject to contract_store's overlap/link checks.
    with pytest.raises((TypeError, AttributeError, contract_store.ContractStoreError)) as caught:
        _store_call(operation, name, store_root=tmp_path)
    if isinstance(caught.value, contract_store.ContractStoreError):
        assert caught.value.code != gate.LEGACY_API_CLOSED


def test_environment_and_user_state_cannot_change_authority_gate_policy(
    monkeypatch, tmp_path: Path,
) -> None:
    compiled = gate.closed_build_enforcement()
    monkeypatch.setenv("METNOS_BIRTH_CLOSED", "1")
    monkeypatch.setenv("METNOS_USER_STATE", str(tmp_path))
    assert gate.closed_build_enforcement() is compiled


def test_private_signing_helper_cannot_bypass_public_alias(closed_build) -> None:
    alias = sign._sign_executor_under_catalog_lock
    with pytest.raises(gate.BirthAuthorityGateClosed) as caught:
        alias(object())
    assert caught.value.operation == "sign_executor"


def test_pure_signature_primitive_remains_available_to_birth_and_localization(
    closed_build,
) -> None:
    class Key:
        def sign(self, value: bytes) -> bytes:
            return b"signed:" + value

    assert sign.sign_manifest_bytes(b"canonical", private_key=Key()) == b"signed:canonical"

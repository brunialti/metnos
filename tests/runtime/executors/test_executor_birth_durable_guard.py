"""Exact durable attempt guard; its authority comes from the sealed publisher."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_durable_guard as guard_module
from executor_birth_durable_guard import DurableBirthAttemptGuard, DurableBirthGuardError
from executor_birth_epoch_store import (
    BirthLifecycle, EpochState, ExecutionEpochAttestation,
)
from manifest_inventory import ContractId, ManifestOrigin


CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
GENERATION = "sha256:" + "1" * 64


def _guard(tmp_path, authenticate=None):
    return DurableBirthAttemptGuard(
        authenticate=authenticate or (
            lambda *_args: SimpleNamespace(executor_name="demo")),
        epoch_db_path=tmp_path / "epochs.sqlite",
    )


def test_guard_binds_authenticated_generation_to_exact_epoch(monkeypatch, tmp_path):
    calls = []

    def authenticate(contract_id, generation_id):
        calls.append(("generation", contract_id, generation_id))
        return SimpleNamespace(executor_name="demo")

    def epoch(**kwargs):
        calls.append(("epoch", kwargs["contract_id"], kwargs["generation_id"], kwargs["name"]))
        assert kwargs["db_path"] == tmp_path / "epochs.sqlite"
        return ExecutionEpochAttestation(
            CID, GENERATION, "demo", EpochState.CURRENT,
            BirthLifecycle.ACTIVE, 7,
        )

    monkeypatch.setattr(guard_module, "attest_execution_epoch", epoch)
    result = _guard(tmp_path, authenticate)(SimpleNamespace(
        name="demo", contract_id=CID.value, generation_id=GENERATION,
    ))
    assert result.state_version == 7
    assert calls == [
        ("generation", CID, GENERATION),
        ("epoch", CID, GENERATION, "demo"),
    ]


def test_guard_never_falls_back_from_missing_exact_identity(tmp_path):
    authenticate = lambda *_args: pytest.fail("generation lookup reached")
    with pytest.raises(DurableBirthGuardError) as raised:
        _guard(tmp_path, authenticate)(SimpleNamespace(
            name="demo", digest=GENERATION, lifecycle="active",
        ))
    assert raised.value.code == "execution.runner_absent"


def test_guard_refuses_a_binding_for_another_executor_name(tmp_path):
    authenticate = lambda *_args: SimpleNamespace(executor_name="another")
    with pytest.raises(DurableBirthGuardError) as raised:
        _guard(tmp_path, authenticate)(SimpleNamespace(
            name="demo", contract_id=CID.value, generation_id=GENERATION,
        ))
    assert raised.value.detail == "name_mismatch"


@pytest.mark.parametrize("code", [
    "execution.runner_absent", "execution.dormant",
    "execution.retired", "execution.quarantined",
])
def test_guard_preserves_closed_epoch_failure_vocabulary(
    monkeypatch, tmp_path, code,
):
    class EpochFailure(RuntimeError):
        pass

    failure = EpochFailure(code)
    failure.code = code
    monkeypatch.setattr(
        guard_module, "attest_execution_epoch",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(DurableBirthGuardError) as raised:
        _guard(tmp_path)(SimpleNamespace(
            name="demo", contract_id=CID.value, generation_id=GENERATION,
        ))
    assert raised.value.code == code


def test_guard_rejects_a_caller_supplied_verifier_mapping(tmp_path):
    """The productive guard holds no trust material of its own."""
    with pytest.raises(TypeError):
        DurableBirthAttemptGuard(
            epoch_db_path=tmp_path / "epochs.sqlite",
            trusted_publics=(object(),),
            admission_verifier_keys={"key": object()},
        )


def test_legacy_installation_composes_without_a_guard(monkeypatch):
    """An unmigrated installation keeps the durable service exactly as it was."""
    import executor_birth_activation_mode as mode

    monkeypatch.setattr(
        mode, "read_birth_activation_state",
        lambda: mode.BirthActivationState(mode.BirthStateOwner.LEGACY, None, None, None),
    )
    assert guard_module.productive_birth_attempt_guard() is None


def test_migrated_installation_without_epoch_store_refuses_to_run_unguarded(
    monkeypatch, tmp_path,
):
    import config
    import executor_birth_activation_mode as mode

    monkeypatch.setattr(
        mode, "read_birth_activation_state",
        lambda: mode.BirthActivationState(
            mode.BirthStateOwner.EPOCH, "sha256:" + "2" * 64, None, "unavailable"),
    )
    monkeypatch.setattr(config, "PATH_USER_STATE", Path(tmp_path))
    with pytest.raises(DurableBirthGuardError) as raised:
        guard_module.productive_birth_attempt_guard()
    assert raised.value.detail == "f5_epoch_migration_required"

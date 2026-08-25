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


def _guard(tmp_path):
    return DurableBirthAttemptGuard(
        epoch_db_path=tmp_path / "epochs.sqlite",
        trusted_publics=(object(),),
        admission_verifier_keys={"key": object()},
        store_root=tmp_path / "store",
    )


def test_guard_binds_authenticated_generation_to_exact_epoch(monkeypatch, tmp_path):
    calls = []

    def authenticate(contract_id, generation_id, **kwargs):
        calls.append(("generation", contract_id, generation_id, kwargs["store_root"]))
        return SimpleNamespace(executor_name="demo")

    def epoch(**kwargs):
        calls.append(("epoch", kwargs["contract_id"], kwargs["generation_id"], kwargs["name"]))
        return ExecutionEpochAttestation(
            CID, GENERATION, "demo", EpochState.CURRENT,
            BirthLifecycle.ACTIVE, 7,
        )

    monkeypatch.setattr(guard_module, "authenticate_execution_binding", authenticate)
    monkeypatch.setattr(guard_module, "attest_execution_epoch", epoch)
    result = _guard(tmp_path)(SimpleNamespace(
        name="demo", contract_id=CID.value, generation_id=GENERATION,
    ))
    assert result.state_version == 7
    assert calls == [
        ("generation", CID, GENERATION, tmp_path / "store"),
        ("epoch", CID, GENERATION, "demo"),
    ]


def test_guard_never_falls_back_from_missing_exact_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(
        guard_module, "authenticate_execution_binding",
        lambda *_args, **_kwargs: pytest.fail("generation lookup reached"),
    )
    with pytest.raises(DurableBirthGuardError) as raised:
        _guard(tmp_path)(SimpleNamespace(
            name="demo", digest=GENERATION, lifecycle="active",
        ))
    assert raised.value.code == "execution.runner_absent"


@pytest.mark.parametrize("code", [
    "execution.runner_absent", "execution.dormant",
    "execution.retired", "execution.quarantined",
])
def test_guard_preserves_closed_epoch_failure_vocabulary(
    monkeypatch, tmp_path, code,
):
    monkeypatch.setattr(
        guard_module, "authenticate_execution_binding",
        lambda *_args, **_kwargs: SimpleNamespace(executor_name="demo"),
    )

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

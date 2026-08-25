from pathlib import Path

import pytest

import executor_birth_intent as intent
from manifest_inventory import ContractId, ManifestOrigin


CONTRACT_ID = ContractId(ManifestOrigin.USER, "sample/manifest.toml")


def test_intent_rejects_empty_authority_data(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="birth_intent_invalid: actor"):
        intent.BirthIntent(tmp_path, CONTRACT_ID, "", "reason", "promote")


def test_productive_adapter_is_fail_closed_before_bootstrap(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(intent, "_PRODUCTION_ADAPTER", None)
    value = intent.BirthIntent(
        tmp_path, CONTRACT_ID, "promoter", "approved", "promote",
    )
    with pytest.raises(RuntimeError, match="birth_intent_adapter_unavailable"):
        intent.submit_birth_intent(value)


def test_adapter_requires_a_real_birth_request(monkeypatch, tmp_path: Path) -> None:
    import executor_birth_operational as operational

    monkeypatch.setattr(
        operational, "birth_executor",
        lambda _request: (_ for _ in ()).throw(AssertionError("must not execute")),
    )
    value = intent.BirthIntent(
        tmp_path, CONTRACT_ID, "skills_cli", "import", "skill_import",
    )
    with pytest.raises(ValueError, match="birth_request_invalid"):
        intent._submit_birth_intent_for_test(
            value, request_factory=lambda _intent: object(),
        )

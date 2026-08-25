from __future__ import annotations

import inspect
from pathlib import Path

from contract_store import PublicationResult
from executor_birth_operational import BirthResult
from executor_birth_shadow import BirthOutcome, BirthReport
from executor_birth_synth import (
    SynthBirthData, _as_intent, submit_synth_birth,
)
from manifest_inventory import ContractId, ManifestOrigin


REQUEST_ID = "sha256:" + "1" * 64
CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")


def _result(*, admitted: bool) -> BirthResult:
    outcome = BirthOutcome.ADMITTED if admitted else BirthOutcome.REJECTED
    error = None if admitted else "producer_receipt_replay"
    report = BirthReport(1, "user:demo/manifest.toml", None, None, None,
                         None, (), (), outcome, error)
    publication = (
        PublicationResult(
            "user:demo/manifest.toml", None, "sha256:" + "2" * 64,
            "commit_birth_snapshot", False,
        ) if admitted else None
    )
    return BirthResult(REQUEST_ID, report, publication, error)


def test_public_synth_adapter_accepts_data_not_trust_authorities(tmp_path: Path) -> None:
    assert set(inspect.signature(submit_synth_birth).parameters) == {"data"}
    assert {"publisher", "registry", "private_key", "checks"}.isdisjoint(
        SynthBirthData.__dataclass_fields__
    )
    data = SynthBirthData(
        tmp_path, CID, "synt_multistage", "create", "new synthesized executor",
    )
    intent = _as_intent(data)
    assert intent.actor == "synt_multistage"
    assert intent.operation == "create"


def test_adapter_preserves_specialize_identity_and_admits(tmp_path: Path, monkeypatch) -> None:
    seen = []
    data = SynthBirthData(
        tmp_path, CID, "synt_specialize", "specialize", "fixed argument variant",
    )
    monkeypatch.setattr(
        "executor_birth_synth.submit_birth_intent",
        lambda intent: seen.append(intent) or _result(admitted=True),
    )
    result = submit_synth_birth(data)
    assert result.publication is not None
    assert seen[0].actor == "synt_specialize"
    assert seen[0].operation == "specialize"


def test_rejected_and_replayed_approval_never_claim_publication(tmp_path: Path, monkeypatch) -> None:
    calls = 0
    def one_use(intent):
        nonlocal calls
        assert intent.actor == "synt_approve"
        calls += 1
        return _result(admitted=calls == 1)
    monkeypatch.setattr("executor_birth_synth.submit_birth_intent", one_use)

    approval = SynthBirthData(
        tmp_path, CID, "synt_approve", "approve", "human approval",
        ("proposal-17",),
    )
    first = submit_synth_birth(approval)
    replay = submit_synth_birth(SynthBirthData(
        tmp_path, CID, "synt_approve", "replay", "approval replay", ("proposal-17",),
    ))
    assert first.publication is not None
    assert replay.publication is None
    assert replay.error_code == "producer_receipt_replay"

"""Historical publication joins use real signatures, not current policy.

The Producer fixture supplies authenticated-wire inputs; historical public
context/source custody and filesystem acquisition have separate owner tests.
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store as contracts
import executor_birth_operational as operational
import executor_birth_receipts as receipts
import executor_birth_producer_store as producers
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_shadow import BirthOutcome, BirthReport, CheckResult, CheckStatus, RevisionClass
from manifest_inventory import ContractId, ManifestOrigin
from tests.runtime.executors.test_executor_birth_historical_producer_binding import (
    _binding, _replace_public, digest,
)


def _fixture(*, embedded=True, preexercise=False, checks=None,
             admission_changes=None, payload_changes=None, revision=RevisionClass.FIRST_BIRTH):
    contract = ContractId(ManifestOrigin.USER, "sample/manifest.toml")
    author = Ed25519PrivateKey.generate()
    terminal_key = Ed25519PrivateKey.generate()
    # Deliberately not a current executable contract/language-state schema.
    manifest = b'name = "historical-sample"\n'
    payloads = {
        "manifest.toml": manifest, "manifest.toml.sig": author.sign(manifest),
        "manifest.lang_state.json": b'{"historical_language_schema":1}\n',
    }
    payloads.update(payload_changes or {})
    generation = contracts.generation_id(payloads)
    if checks is None:
        checks = (
            CheckResult("old_standard", "older-rule", CheckStatus.PASSED, None, digest(8), ""),
            CheckResult("semantic_review", "v1", CheckStatus.PASSED, None, digest(9), ""),
            CheckResult("approval", "v1", CheckStatus.NOT_APPLICABLE, None, digest(1), ""),
        )
    check_map = {c.check_id: receipts.AdmissionCheck(
        c.rule_version, receipts.AdmittedCheckStatus(c.status.value), c.evidence_hash,
    ) for c in checks}
    check_map["authoring_install_journal_v1"] = receipts.AdmissionCheck(
        "1", receipts.AdmittedCheckStatus.PASSED, digest(7),
    )
    semantic_hash = next((c.evidence_hash for c in checks
                          if c.check_id == "semantic_review" and c.status is CheckStatus.PASSED), None)
    approval_hash = next((c.evidence_hash for c in checks
                          if c.check_id == "approval" and c.status is CheckStatus.PASSED), None)
    admission = dict(
        generation_id=generation, check_results=check_map,
        semantic_review_hash=semantic_hash, approval_hash=approval_hash,
        revision_class=receipts.RevisionClass(revision.value),
        approved_lifecycle=(receipts.ApprovedLifecycle.PREEXERCISE if preexercise
                            else receipts.ApprovedLifecycle.ACTIVE),
    )
    admission.update(admission_changes or {})
    binding = _binding(admission_changes=admission)
    public = binding["declarations"].context.public_set
    _replace_public(binding, admission_verifier_keys={
        **public.admission_verifier_keys, "terminal-after-rotation": terminal_key.public_key(),
    }, author_verifier_keys={
        "author-old": author.public_key(), "author-new": Ed25519PrivateKey.generate().public_key(),
    })
    durable = contracts.HistoricalBirthEvidenceV1(
        contract, generation, digest(6), contracts.encode_binding(contract),
        binding["admission_encoded"], payloads["manifest.toml"],
        payloads["manifest.toml.sig"], payloads["manifest.lang_state.json"],
    )
    report = BirthReport(
        1, contract, digest(4), digest(5), digest(6), revision, (), tuple(checks),
        BirthOutcome.PREEXERCISE if preexercise else BirthOutcome.ADMITTED, None,
    )
    terminal = operational.BirthResult(
        binding["receipt_row"].request_id, report,
        contracts.PublicationResult(contract, None, generation, "commit_birth_snapshot", False),
        None,
    )
    raw = operational._terminal_envelope(
        SimpleNamespace(admission_key_id="terminal-after-rotation"), terminal,
        durable.receipt_bytes if embedded else None,
    )
    fixture = SimpleNamespace(
        inputs=dict(evidence=durable, receipt_row=binding["receipt_row"],
                    issuance_row=binding["issuance_row"], declarations=binding["declarations"]),
        document=json.loads(raw), key=terminal_key,
    )
    _sign_terminal(fixture)
    return fixture


def _sign_terminal(fixture):
    raw = encode_canonical_ascii_v1(fixture.document)
    signature = fixture.key.sign(b"metnos.executor-birth.terminal/v1\0" + raw)
    fixture.inputs["receipt_row"] = replace(
        fixture.inputs["receipt_row"], terminal_envelope=raw, terminal_auth=signature,
        result_binding=operational._terminal_binding(raw),
    )


def _verify(fixture):
    return operational.verify_historical_publication_v1(**fixture.inputs)


@pytest.mark.parametrize("embedded,preexercise,layout_v1", (
    (True, False, False), (False, False, False), (False, True, False), (True, False, True),
))
def test_historical_publication_accepts_exact_reuse_rotation_and_explicit_layout(
    monkeypatch, embedded, preexercise, layout_v1,
):
    fixture = _fixture(embedded=embedded, preexercise=preexercise)
    if layout_v1:
        fixture.inputs["evidence"] = replace(fixture.inputs["evidence"], admission_context_id=None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("historical verification attempted current operational access")

    monkeypatch.setattr(contracts, "_authenticate_payloads", forbidden)
    monkeypatch.setattr(contracts, "_store_root", forbidden)
    monkeypatch.setattr(operational, "BirthRequest", forbidden)
    monkeypatch.setattr(producers.sqlite3, "connect", forbidden)
    result = _verify(fixture)
    assert result.producer_binding.namespace == "writer:create"
    assert result.terminal.report.outcome is (
        BirthOutcome.PREEXERCISE if preexercise else BirthOutcome.ADMITTED
    )
    assert result.producer_binding.admission.authentication.key_id == "admission-old"
    assert not hasattr(result, "qualified")
    with pytest.raises(FrozenInstanceError):
        result.terminal = None


@pytest.mark.parametrize("path,value", (
    (("publication",), None),
    (("publication", "previous_generation_id"), digest(2)),
    (("publication", "current_generation_id"), digest(2)),
    (("publication", "operation"), "retire"),
    (("report", "candidate_id"), digest(2)),
    (("report", "semantic_core_id"), digest(2)),
    (("report", "admission_context_id"), digest(2)),
    (("report", "revision_class"), "code_revision"),
    (("report", "outcome"), "preexercise"),
    (("report", "error_code"), "unexpected_failure"),
    (("error_code",), "unexpected_failure"),
    (("admission_receipt",), "eA=="),
))
def test_historical_publication_rejects_authentic_but_inconsistent_terminal(path, value):
    fixture = _fixture()
    target = fixture.document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    _sign_terminal(fixture)
    with pytest.raises(ValueError, match="birth_history_terminal"):
        _verify(fixture)


@pytest.mark.parametrize("changes", (
    {"state": "in_progress"}, {"state": "rejected"},
    {"rejection_code": "stored_error"}, {"result_binding": digest(2)},
    {"terminal_envelope": None}, {"terminal_auth": None},
))
def test_historical_publication_does_not_infer_success_from_database_claims(changes):
    fixture = _fixture()
    fixture.inputs["receipt_row"] = replace(fixture.inputs["receipt_row"], **changes)
    with pytest.raises(ValueError):
        _verify(fixture)


@pytest.mark.parametrize("field,value", (
    ("manifest_bytes", b"different manifest"),
    ("language_state_bytes", b"different language bytes"),
    ("signature_bytes", b"x" * 64),
    ("generation_id", digest(2)), ("admission_context_id", digest(2)),
    ("binding_bytes", contracts.encode_binding(ContractId(ManifestOrigin.USER, "other/manifest.toml"))),
    ("receipt_bytes", b"{}"),
))
def test_historical_publication_rejects_durable_identity_or_payload_drift(field, value):
    fixture = _fixture()
    fixture.inputs["evidence"] = replace(fixture.inputs["evidence"], **{field: value})
    with pytest.raises((contracts.ContractStoreError, receipts.ReceiptError)):
        _verify(fixture)


def test_historical_generation_requires_author_signature_even_when_admission_binds_digest():
    fixture = _fixture(payload_changes={"manifest.toml.sig": b"x" * 64})
    with pytest.raises(contracts.ManifestSignatureError):
        _verify(fixture)


def test_historical_publication_requires_terminal_signature_independently():
    fixture = _fixture()
    fixture.inputs["receipt_row"] = replace(fixture.inputs["receipt_row"], terminal_auth=b"x" * 64)
    with pytest.raises(InvalidSignature):
        _verify(fixture)


@pytest.mark.parametrize("field", (
    "candidate_id", "semantic_core_id", "predecessor_id", "semantic_review_hash", "authoring_journal_hash",
))
def test_historical_publication_rejects_inconsistent_signed_admission_facts(field):
    fixture = _fixture(admission_changes={field: digest(2)})
    with pytest.raises(ValueError, match="birth_history_terminal"):
        _verify(fixture)


@pytest.mark.parametrize("case", ("extra", "missing", "rule", "hash", "status", "reserved"))
def test_historical_checks_match_the_exact_publisher_projection(case):
    fixture = _fixture()
    checks = fixture.document["report"]["checks"]
    if case == "extra":
        checks.append({**checks[0], "check_id": "unknown_historical_check"})
    elif case == "missing":
        checks.pop(0)
    elif case == "reserved":
        checks.append({**checks[0], "check_id": "authoring_install_journal_v1"})
    elif case == "rule":
        checks[0]["rule_version"] = "different-rule"
    elif case == "hash":
        checks[0]["evidence_hash"] = digest(2)
    else:
        checks[0]["status"] = "failed"
    _sign_terminal(fixture)
    with pytest.raises(ValueError):
        _verify(fixture)


@pytest.mark.parametrize("approval_present", (False, True))
def test_historical_not_applicable_check_hash_is_not_approval_evidence(approval_present):
    fixture = _fixture(admission_changes={"approval_hash": digest(2) if approval_present else None})
    assert _verify(fixture)


@pytest.mark.parametrize("mismatch", (False, True))
def test_historical_passed_approval_hash_must_match(mismatch):
    checks = (CheckResult("approval", "v1", CheckStatus.PASSED, None, digest(2), ""),)
    fixture = _fixture(checks=checks, admission_changes={"approval_hash": digest(3)} if mismatch else {})
    if mismatch:
        with pytest.raises(ValueError, match="birth_history_terminal"):
            _verify(fixture)
    else:
        assert _verify(fixture)


@pytest.mark.parametrize("revision", (RevisionClass.LOCALIZATION, RevisionClass.EQUIVALENT,
                                      RevisionClass.PROMOTION, RevisionClass.REACTIVATION))
def test_historical_authentic_nontechnical_revision_is_not_corruption(revision):
    assert _verify(_fixture(revision=revision)).terminal.report.revision_class is revision


@pytest.mark.parametrize("field,maximum", (
    ("binding_bytes", 65536), ("receipt_bytes", 1024 * 1024),
    ("manifest_bytes", 1024 * 1024), ("signature_bytes", 64),
    ("language_state_bytes", 1024 * 1024),
))
def test_historical_publication_applies_owner_byte_limits_before_parsing(field, maximum):
    fixture = _fixture()
    fixture.inputs["evidence"] = replace(fixture.inputs["evidence"], **{field: b"x" * (maximum + 1)})
    with pytest.raises(contracts.ContractStoreError, match="birth_history_file_invalid"):
        _verify(fixture)

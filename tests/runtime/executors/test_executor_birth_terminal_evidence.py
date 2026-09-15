"""Public terminal verification shares the operational wire format."""
from __future__ import annotations

import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_operational as operational
from contract_store import PublicationResult
from executor_birth_shadow import (
    BirthOutcome, BirthReport, CheckResult, CheckStatus, RevisionClass,
)
from manifest_inventory import ContractId, ManifestOrigin


DIGEST = "sha256:" + "a" * 64
REQUEST = "sha256:" + "b" * 64
CONTRACT = ContractId(ManifestOrigin.USER, "example/manifest.toml")
SIGNATURE_DOMAIN = b"metnos.executor-birth.terminal/v1\0"


def _document(*, publication=True, admission=b"opaque receipt bytes", outcome=BirthOutcome.ADMITTED):
    private = Ed25519PrivateKey.generate()
    core = SimpleNamespace(admission_key_id="historical-public-key")
    negative = outcome in {BirthOutcome.REJECTED, BirthOutcome.NEEDS_HUMAN, BirthOutcome.QUARANTINED}
    error = "birth_not_admitted" if negative else None
    report = BirthReport(
        1, CONTRACT, DIGEST, DIGEST, DIGEST, RevisionClass.CODE,
        ("code",), (CheckResult("historical_check", "older-rule", CheckStatus.PASSED, None, DIGEST, ""),),
        outcome, error,
    )
    result = operational.BirthResult(
        REQUEST, report,
        PublicationResult(CONTRACT, None, DIGEST, "commit_birth_snapshot", False) if publication else None,
        error,
    )
    encoded = operational._terminal_envelope(core, result, admission)
    return private, encoded, result


def _verify(private, encoded, **overrides):
    values = dict(
        expected_request_id=REQUEST, expected_contract_id=CONTRACT,
        verifier_keys={"historical-public-key": private.public_key()},
    )
    values.update(overrides)
    return operational.verify_terminal_evidence_v1(
        encoded, private.sign(SIGNATURE_DOMAIN + encoded), **values,
    )


@pytest.mark.parametrize("publication,admission,outcome", (
    (True, b"opaque receipt bytes", BirthOutcome.ADMITTED),
    (True, None, BirthOutcome.ADMITTED),
    (True, None, BirthOutcome.PREEXERCISE),
    (False, None, BirthOutcome.ADMITTED),
    (False, None, BirthOutcome.REJECTED),
    (False, None, BirthOutcome.NEEDS_HUMAN),
    (False, None, BirthOutcome.QUARANTINED),
))
def test_public_terminal_evidence_requires_no_current_request_or_private_core(
    monkeypatch, publication, admission, outcome,
):
    private, encoded, expected = _document(publication=publication, admission=admission, outcome=outcome)

    def forbidden(*_args, **_kwargs):
        pytest.fail("public terminal verification attempted runtime selection")

    monkeypatch.setattr(operational, "_runtime_bundle_snapshot", forbidden)
    monkeypatch.setattr(operational, "BirthRequest", forbidden)
    result, received = _verify(private, encoded)
    assert received == admission
    assert result.report == expected.report
    assert result.request_id == REQUEST
    assert result.error_code == expected.error_code
    assert (result.publication is not None) is publication
    if publication:
        # Repeated is a decoding default, not a signed assertion of retry.
        assert result.publication == replace(expected.publication, repeated=True)


def test_unpublished_terminal_does_not_claim_to_sign_the_expected_contract():
    private, encoded, _expected = _document(publication=False, admission=None)
    other = ContractId(ManifestOrigin.USER, "other/manifest.toml")
    result, _ = _verify(private, encoded, expected_contract_id=other)
    assert result.publication is None
    assert result.report.contract_id == other  # Caller context only, not on the wire.


@pytest.mark.parametrize("case", (
    "extra-root", "missing-root", "schema-type", "request", "signing-key-type",
    "extra-report", "candidate-digest", "semantic-digest", "context-digest",
    "revision-type", "outcome", "dimensions-type", "dimension-type", "duplicate-dimension",
    "checks-type", "extra-check", "check-id-type", "rule-type", "check-error-type",
    "check-detail-type", "check-evidence", "duplicate-check", "check-status",
    "extra-publication", "publication-contract", "generation", "predecessor", "operation-type",
    "error-type", "report-error-type", "admission-type", "bad-base64", "noncanonical-base64",
    "diagnostic-null", "extra-diagnostic", "nan", "unpaired-surrogate",
))
def test_public_terminal_rejects_malformed_but_correctly_signed_documents(case):
    private, encoded, _expected = _document()
    value = json.loads(encoded)
    report = value["report"]
    check = report["checks"][0]
    publication = value["publication"]
    if case == "extra-root":
        value["unexpected"] = True
    elif case == "missing-root":
        del value["admission_receipt"]
    elif case == "schema-type":
        value["schema_version"] = 2.0
    elif case == "request":
        value["request_id"] = DIGEST
    elif case == "signing-key-type":
        value["signing_key_id"] = ["historical-public-key"]
    elif case == "extra-report":
        report["unexpected"] = True
    elif case in {"candidate-digest", "semantic-digest", "context-digest"}:
        field = {"candidate-digest": "candidate_id", "semantic-digest": "semantic_core_id", "context-digest": "admission_context_id"}[case]
        report[field] = "not-a-digest"
    elif case == "revision-type":
        report["revision_class"] = ""
    elif case == "outcome":
        report["outcome"] = "unknown"
    elif case == "dimensions-type":
        report["changed_dimensions"] = "code"
    elif case == "dimension-type":
        report["changed_dimensions"] = [True]
    elif case == "duplicate-dimension":
        report["changed_dimensions"] *= 2
    elif case == "checks-type":
        report["checks"] = {}
    elif case == "extra-check":
        check["unexpected"] = True
    elif case == "check-id-type":
        check["check_id"] = ["historical_check"]
    elif case == "rule-type":
        check["rule_version"] = 1
    elif case == "check-error-type":
        check.update(status="failed", error_code=True)
    elif case == "check-detail-type":
        check["redacted_detail"] = {}
    elif case == "check-evidence":
        check["evidence_hash"] = "not-a-digest"
    elif case == "duplicate-check":
        report["checks"] *= 2
    elif case == "check-status":
        check["status"] = "unknown"
    elif case == "extra-publication":
        publication["unexpected"] = True
    elif case == "publication-contract":
        publication["contract_id"] = ContractId(ManifestOrigin.USER, "other/manifest.toml").value
    elif case == "generation":
        publication["current_generation_id"] = "not-a-digest"
    elif case == "predecessor":
        publication["previous_generation_id"] = False
    elif case == "operation-type":
        publication["operation"] = ["commit_birth_snapshot"]
    elif case == "error-type":
        value["error_code"] = True
    elif case == "report-error-type":
        report["error_code"] = []
    elif case == "admission-type":
        value["admission_receipt"] = []
    elif case == "bad-base64":
        value["admission_receipt"] = "!not base64!"
    elif case == "noncanonical-base64":
        value["admission_receipt"] = "Zh=="
        assert base64.b64decode("Zh==") == b"f"
    elif case == "diagnostic-null":
        value["diagnostic"] = None
    elif case == "extra-diagnostic":
        value["diagnostic"] = {"phase": "checks", "code": "unavailable", "cause": "", "unexpected": True}
    elif case == "nan":
        value["error_code"] = float("nan")
    elif case == "unpaired-surrogate":
        check["redacted_detail"] = "\ud800"
    malformed = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(ValueError, match="birth_terminal_envelope_invalid"):
        _verify(private, malformed)


@pytest.mark.parametrize("case", ("signature", "short-signature", "unknown-key", "wrong-key", "duplicate-json", "oversized"))
def test_public_terminal_requires_exact_authentication_and_bounded_unique_bytes(case):
    private, encoded, _expected = _document()
    keys = {"historical-public-key": private.public_key()}
    if case == "duplicate-json":
        encoded = b'{"schema_version":2,' + encoded[1:]
    elif case == "oversized":
        encoded = b" " * (operational.MAX_TERMINAL_ENVELOPE_BYTES + 1)
    signature = private.sign(SIGNATURE_DOMAIN + encoded)
    if case == "signature":
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    elif case == "short-signature":
        signature = signature[:-1]
    elif case == "unknown-key":
        keys = {}
    elif case == "wrong-key":
        keys = {"historical-public-key": Ed25519PrivateKey.generate().public_key()}
    with pytest.raises((ValueError, InvalidSignature)):
        operational.verify_terminal_evidence_v1(
            encoded, signature, expected_request_id=REQUEST,
            expected_contract_id=CONTRACT, verifier_keys=keys,
        )


def test_terminal_emission_obeys_the_same_payload_limit():
    _private, _encoded, result = _document()
    check = replace(result.report.checks[0], redacted_detail="x" * operational.MAX_TERMINAL_ENVELOPE_BYTES)
    result = replace(result, report=replace(result.report, checks=(check,)))
    with pytest.raises(ValueError, match="birth_terminal_envelope_invalid"):
        operational._terminal_envelope(SimpleNamespace(admission_key_id="key"), result)

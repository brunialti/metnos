"""Synthetic authenticated history, shared without importing test modules.

Bytes and signatures are real; historical context/source custody is a seam.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store as contracts
import executor_birth_operational as operational
import executor_birth_prepared_root as root
import executor_birth_producer_context as context
import executor_birth_producer_store as producers
import executor_birth_reattestation as reattestation
import executor_birth_receipts as receipts
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_prepared_root import (
    HistoricalContextVerifiersV1, HistoricalProducerDeclarationsV1,
)
from executor_birth_prepared_set import HistoricalProducerVerifiersV1, HistoricalPublicSetV1
from executor_birth_shadow import BirthOutcome, BirthReport, CheckResult, CheckStatus, RevisionClass
from manifest_inventory import ContractId, ManifestOrigin


def digest(number):
    return "sha256:" + str(number) * 64


def _binding(*, producer_changes=None, admission_changes=None, producer_transform=None):
    """Only the context/source owner is a seam; bytes and signatures are real."""
    admission_key = Ed25519PrivateKey.generate()
    producer_key = Ed25519PrivateKey.generate()
    contract = ContractId(ManifestOrigin.USER, "sample/manifest.toml")
    request = receipts.producer_request_id_v1(
        issuer_id="writer", operation="create", contract_id=contract.value,
        objective_hash=digest(1), candidate_source_id=digest(2),
    )
    producer_fields = dict(
        issuer_id="writer", executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.MODEL, objective_hash=digest(1),
        candidate_source_id=digest(2), issued_at="2026-08-25T12:00:00Z",
        expires_at="2026-08-25T13:00:00Z",
        nonce=hashlib.sha256(request.encode()).hexdigest()[:32],
        key_id="producer-old", private_key=producer_key,
    )
    producer_fields.update(producer_changes or {})
    producer_bytes = receipts.issue_producer_receipt(**producer_fields)
    producer, _ = receipts._parse_producer(producer_bytes, temporal_now=None)
    if producer_transform is not None:
        producer_bytes = producer_transform(producer_bytes)
    producer_hash = producers.producer_receipt_hash(producer_bytes)
    admission_fields = dict(
        policy_version="birth-policy/v1", contract_id=contract,
        generation_id=digest(3), candidate_id=digest(4), semantic_core_id=digest(5),
        admission_context_id=digest(6), birth_request_id=request,
        authoring_journal_hash=digest(7), predecessor_id=None,
        producer_receipt_hash=producer_hash, revision_class=receipts.RevisionClass.FIRST_BIRTH,
        check_results={"manifest": receipts.AdmissionCheck(
            "1", receipts.AdmittedCheckStatus.PASSED, digest(8),
        )},
        semantic_review_hash=None, approval_hash=None,
        approved_lifecycle=receipts.ApprovedLifecycle.ACTIVE,
        kind=receipts.AdmissionKind.ADMISSION, issued_at="2026-08-25T12:00:10Z",
        key_id="admission-old", private_key=admission_key,
    )
    admission_fields.update(admission_changes or {})
    admission_bytes = receipts.issue_admission_receipt(**admission_fields)
    public = HistoricalPublicSetV1(
        "a" * 64, "b" * 64, "c" * 32, "fixture-provisioner",
        SimpleNamespace(pin=SimpleNamespace(admission_context_id=digest(6))),
        {}, {"admission-old": admission_key.public_key()},
        {"writer:create": HistoricalProducerVerifiersV1(
            "fixture-store", {"producer-old": producer_key.public_key()},
        )},
    )
    declarations = HistoricalProducerDeclarationsV1(
        HistoricalContextVerifiersV1(digest(7), digest(8), public),
        digest(9), "fixture-source.py", digest(1),
        {"writer:create": "model"}, {"user": "human"},
    )
    row = producers.ProducerReceiptRowV1(
        row_id=2, receipt_id=producer.receipt_id, receipt_hash=producer_hash,
        encoded=producer_bytes, issuer_id=producer.issuer_id,
        objective_hash=producer.objective_hash, candidate_source_id=producer.candidate_source_id,
        executor_origin=producer.executor_origin.value,
        revision_authorship=producer.revision_authorship.value,
        expires_at=producer.expires_at, state="committed",
        registered_at="2099-01-01T00:00:00Z", request_id=request,
        claimed_at="2099-01-01T00:00:00Z", finalized_at="2099-01-01T00:00:01Z",
        result_binding="unsigned fixture, not proof of terminal success",
    )
    issuance = producers.ProducerIssuanceRowV1(
        4, request, "writer", "writer:create", contract.value,
        producer.objective_hash, producer.candidate_source_id, producer.receipt_id,
        producer_bytes,
    )
    return dict(admission_encoded=admission_bytes, receipt_row=row,
                issuance_row=issuance, declarations=declarations)


def _replace_public(binding, **changes):
    declarations = binding["declarations"]
    binding["declarations"] = replace(declarations, context=replace(
        declarations.context, public_set=replace(declarations.context.public_set, **changes),
    ))


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


def _v2(*, producer_changes=None, admission_changes=None, adoption=False, continuity=None,
        base=None, context_id=digest(6), source_id=digest(2)):
    """The same stored wire protocol as the writer, with actual signatures."""
    base = _fixture() if base is None else base
    durable = base.inputs["evidence"]
    old = base.inputs["declarations"]
    issuer = Ed25519PrivateKey.generate()
    admission_key = Ed25519PrivateKey.generate()
    namespace = "installer_phase3:install"
    alias = "installer_phase4:ownership_reattest_current_v2"
    request, objective = context.producer_request_identity_v2(
        contract_id=durable.contract_id.value, generation_id=durable.generation_id,
        admission_context_id=context_id, transition_id=digest(8),
        set_id="a" * 64, context_epoch=digest(9), candidate_source_id=source_id,
    )
    fields = dict(
        issuer_id="installer_phase3", executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.MAINTENANCE, objective_hash=objective,
        candidate_source_id=source_id, issued_at="2026-08-25T12:00:00Z",
        expires_at="2026-08-25T13:00:00Z",
        nonce=hashlib.sha256(request.encode()).hexdigest()[:32],
        key_id="producer-old", private_key=issuer,
    )
    fields.update(producer_changes or {})
    producer_bytes = receipts.issue_producer_receipt(**fields)
    producer, _ = receipts._parse_producer(producer_bytes, temporal_now=None)
    snapshot = reattestation._hash(
        b"metnos.executor-birth.reattestation-snapshot/v1\0",
        durable.contract_id.value.encode(), durable.generation_id.encode(), digest(4).encode(),
    )
    checks = {
        "reattestation_current_generation_v1": receipts.AdmissionCheck(
            "1", receipts.AdmittedCheckStatus.PASSED, snapshot,
        ),
        "properties": receipts.AdmissionCheck("1", receipts.AdmittedCheckStatus.NOT_APPLICABLE, digest(1)),
        "semantic_review": receipts.AdmissionCheck("1", receipts.AdmittedCheckStatus.NOT_APPLICABLE, digest(2)),
    }
    if adoption:
        checks["initial_current_generation_adoption_v1"] = receipts.AdmissionCheck(
            "1", receipts.AdmittedCheckStatus.PASSED, reattestation._hash(
                b"metnos.executor-birth.initial-current-adoption/v1\0",
                digest(8).encode(), durable.contract_id.value.encode(), durable.generation_id.encode(),
                source_id.encode(), digest(4).encode(), context_id.encode(),
            ),
        )
    if continuity:
        checks["unchanged_current_continuity_v1"] = receipts.AdmissionCheck(
            "1", receipts.AdmittedCheckStatus.NOT_APPLICABLE, continuity,
        )
    fields = dict(
        policy_version="birth-policy/v1", contract_id=durable.contract_id,
        generation_id=durable.generation_id, candidate_id=digest(4), semantic_core_id=digest(5),
        admission_context_id=context_id, birth_request_id=request,
        authoring_journal_hash=snapshot, predecessor_id=durable.generation_id,
        producer_receipt_hash=producers.producer_receipt_hash(producer_bytes),
        revision_class=receipts.RevisionClass.REATTESTATION, check_results=checks,
        semantic_review_hash=None, approval_hash=None,
        approved_lifecycle=receipts.ApprovedLifecycle.ACTIVE,
        kind=receipts.AdmissionKind.REATTESTATION, issued_at="2026-08-25T12:00:10Z",
        key_id="admission-old", private_key=admission_key,
    )
    fields.update(admission_changes or {})
    encoded = receipts.issue_admission_receipt(**fields)
    public = replace(old.context.public_set,
        admission_verifier_keys={"admission-old": admission_key.public_key()},
        producers={namespace: HistoricalProducerVerifiersV1(
            "installer-store", {"producer-old": issuer.public_key()},
        )},
        material=SimpleNamespace(pin=SimpleNamespace(
            admission_context_id=context_id, context_epoch=digest(9),
        )),
    )
    declarations = replace(old,
        context=replace(old.context, public_set=public, initial_transition=adoption,
                        previous_admission_context_id=old.context.public_set.material.pin.admission_context_id),
        authors={namespace: "maintenance"},
        reattestation_scope=root.HistoricalReattestationScopeV2(namespace, alias, ()),
    )
    row = replace(base.inputs["receipt_row"],
        receipt_id=producer.receipt_id, receipt_hash=producers.producer_receipt_hash(producer_bytes),
        encoded=producer_bytes, issuer_id=producer.issuer_id,
        objective_hash=producer.objective_hash, candidate_source_id=producer.candidate_source_id,
        revision_authorship=producer.revision_authorship.value, request_id=request,
        terminal_envelope=None, terminal_auth=None,
        result_binding=reattestation._hash(b"metnos.executor-birth.reattestation-result/v1\0", encoded),
    )
    issuance = replace(base.inputs["issuance_row"], request_id=request,
        issuer_id=producer.issuer_id, capability_id=alias, objective_hash=producer.objective_hash,
        candidate_source_id=producer.candidate_source_id, receipt_id=producer.receipt_id,
        encoded=producer_bytes,
    )
    return dict(evidence=replace(durable, receipt_bytes=encoded, admission_context_id=context_id), receipt_row=row,
                issuance_row=issuance, declarations=declarations)

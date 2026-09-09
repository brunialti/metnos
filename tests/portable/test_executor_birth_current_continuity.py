"""Successor maintenance reuses only authenticated, byte-identical currents."""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store
import executor_birth_reattestation as reattestation
import executor_birth_shadow as shadow
from executor_birth import ObservedCandidate
from executor_birth_context_selection import (
    _STAGED_REATTESTATION_MODE_V1, _context_selection_from_required_chain_v1,
)
from executor_birth_cutover import CurrentGeneration
from executor_birth_identity import (
    AdmissionContextV1, CandidateIdentityInput, ContextComponent, ExecutorOrigin,
    admission_context_id, compute_candidate_identities,
)
from executor_birth_intent import _INSTALLER
from executor_birth_operational import _candidate_source_id_from_snapshot
from executor_birth_prepared_root import PreviousContextRuntimeV1, SealedAuthoritiesV1
from executor_birth_producer_context import build_producer_request_v2
from executor_birth_producer_store import ProducerReceiptBinding
from executor_birth_producer_table_v1 import producer_author_v1
from executor_birth_receipts import (
    AdmissionCheck, AdmissionKind, AdmittedCheckStatus, ApprovedLifecycle,
    ReceiptError, RevisionClass, issue_admission_receipt,
)
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus
from tests.portable.test_executor_birth_context_selection import D, _evidence, _prepared_with


def _context(character):
    return AdmissionContextV1(**{
        name: ContextComponent("v1", D(character))
        for name in AdmissionContextV1.__dataclass_fields__
    })


@pytest.fixture
def continuity(monkeypatch, tmp_path):
    transition, prepared, distribution = _evidence()
    old_context, new_context = _context("a"), _context("b")
    old_selection = replace(
        _context_selection_from_required_chain_v1(transition, prepared, distribution),
        admission_context_id=admission_context_id(old_context),
    )
    selection = replace(
        old_selection, _mode=_STAGED_REATTESTATION_MODE_V1,
        admission_context_id=admission_context_id(new_context),
        context_epoch=D("c"), transition_id=D("d"), set_id="e" * 64,
        distribution=replace(distribution, release_sequence=2,
                             previous_closed_build_id=distribution.identity.closed_build_id),
    )
    key = Ed25519PrivateKey.generate()
    keys = {"admission": key.public_key()}
    previous = PreviousContextRuntimeV1(
        old_selection,
        SealedAuthoritiesV1(
            _prepared_with(prepared, prepared_admission_context_id=old_selection.admission_context_id),
            SimpleNamespace(verifier_keys={"author": key.public_key()}),
            SimpleNamespace(verifier_keys=keys), {}, None, None, None,
            prepared.prepared_context_epoch, SimpleNamespace(context=old_context),
        ),
        D("f"),
    )
    source = Path(__file__).resolve().parents[2] / "executors" / "consult_frontier"
    manifest = (source / "manifest.toml").read_bytes()
    snapshot = SimpleNamespace(
        manifest_bytes=manifest,
        language_state_bytes=(source / "manifest.lang_state.json").read_bytes(),
        code_files={name: (source / name).read_bytes()
                    for name in tomllib.loads(manifest.decode())["code"]["files"]},
        close=lambda: None,
    )
    contract = ContractId(ManifestOrigin.CORE, "consult_frontier/manifest.toml")
    ref = ManifestRef(contract, ManifestOrigin.CORE, ManifestStatus.ADMITTED,
                      source.parent, source / "manifest.toml", "consult_frontier/manifest.toml",
                      (source,))
    current = CurrentGeneration(ref, D("3"))
    source_id = _candidate_source_id_from_snapshot(snapshot)
    author = producer_author_v1(_INSTALLER.producer_id, _INSTALLER.operation)
    requests = [build_producer_request_v2(
        item, contract_id=contract, generation_id=current.generation_id,
        candidate_source_id=source_id,
    ) for item in (old_selection, selection)]
    identities = [compute_candidate_identities(CandidateIdentityInput(
        contract, manifest, snapshot.language_state_bytes, snapshot.code_files,
        ExecutorOrigin.CORE, author, request.objective_hash,
    ), context) for request, context in zip(requests, (old_context, new_context))]
    request = reattestation._reattestation_request_for_test(
        requests[1].request_id, current, b"not-consumed-by-read-only-proof",
        "installer", "successor current", ProducerReceiptBinding(
            requests[1].objective_hash, source_id, ExecutorOrigin.CORE, author,
        ), requests[1],
    )
    fields = dict(
        policy_version="birth-policy-v1", contract_id=contract,
        generation_id=current.generation_id, candidate_id=identities[0].candidate_id,
        semantic_core_id=identities[0].semantic_core_id,
        admission_context_id=old_selection.admission_context_id,
        birth_request_id=requests[0].request_id,
        authoring_journal_hash=D("4"), predecessor_id=current.generation_id,
        producer_receipt_hash=D("5"), revision_class=RevisionClass.REATTESTATION,
        check_results={
            "properties": AdmissionCheck("1", AdmittedCheckStatus.NOT_APPLICABLE, D("6")),
            "semantic_review": AdmissionCheck("1", AdmittedCheckStatus.NOT_APPLICABLE, D("7")),
            "reattestation_current_generation_v1": AdmissionCheck("1", AdmittedCheckStatus.PASSED, D("8")),
        },
        semantic_review_hash=None, approval_hash=None,
        approved_lifecycle=ApprovedLifecycle.ACTIVE,
        kind=AdmissionKind.REATTESTATION, issued_at="2026-09-09T12:00:00Z",
        key_id="admission", private_key=key,
    )
    holder = SimpleNamespace(encoded=issue_admission_receipt(**fields), calls=[])

    def read(read_ref, **kwargs):
        assert read_ref == ref
        assert kwargs["request"] == requests[0]
        assert kwargs["trusted_publics"] == tuple(previous.authorities.author.verifier_keys.items())
        assert kwargs["store_root"] == tmp_path / "store"
        holder.calls.append(kwargs)
        return holder.encoded

    monkeypatch.setattr(contract_store, "read_current_birth_receipt_v2", read)
    return SimpleNamespace(
        core=SimpleNamespace(previous_context=previous, selection=selection,
                             store_root=tmp_path / "store"),
        request=request,
        observed=ObservedCandidate(contract, snapshot, identities[1],
                                   ExecutorOrigin.CORE, author, requests[1].objective_hash),
        holder=holder, fields=fields, new_context=new_context,
    )


def test_exact_prior_receipt_proves_continuity_without_a_new_test_run(continuity):
    rig = continuity
    proof = reattestation._current_continuity_v1(rig.core, rig.request, rig.observed)
    assert proof.previous_receipt_hash == "sha256:" + hashlib.sha256(rig.holder.encoded).hexdigest()
    assert proof.previous_head_id == rig.core.previous_context.required_head_id
    assert proof.approved_lifecycle is ApprovedLifecycle.ACTIVE
    deps = shadow._sealed_dependencies_for_test(current_continuity=proof)
    decision = shadow.classify_revision(shadow.RevisionFacts(reattestation=True))
    for check in (shadow._property_check, shadow._semantic_check):
        result = check(rig.observed, decision, deps)
        assert result.status is shadow.CheckStatus.NOT_APPLICABLE
        assert "continuity" in result.redacted_detail
        assert proof.previous_receipt_hash in result.redacted_detail
        assert "initial" not in result.redacted_detail
    assert len(rig.holder.calls) == 1


@pytest.mark.parametrize("field,value", [
    ("generation_id", D("0")), ("predecessor_id", D("0")),
    ("candidate_id", D("0")), ("semantic_core_id", D("0")),
    ("birth_request_id", D("0")), ("admission_context_id", D("0")),
    ("contract_id", ContractId(ManifestOrigin.CORE, "other/manifest.toml")),
])
def test_signed_but_foreign_previous_receipt_is_refused(continuity, field, value):
    rig = continuity
    rig.holder.encoded = issue_admission_receipt(**(rig.fields | {field: value}))
    with pytest.raises(reattestation.BirthReattestationError, match="continuity_receipt_invalid"):
        reattestation._current_continuity_v1(rig.core, rig.request, rig.observed)


@pytest.mark.parametrize("payload", [b"broken", b"{}", b""])
def test_malformed_previous_receipt_never_selects_continuity(continuity, payload):
    continuity.holder.encoded = payload
    with pytest.raises(ReceiptError):
        reattestation._current_continuity_v1(continuity.core, continuity.request, continuity.observed)


def test_missing_previous_receipt_selects_ordinary_checks(continuity):
    continuity.holder.encoded = None
    assert reattestation._current_continuity_v1(
        continuity.core, continuity.request, continuity.observed,
    ) is None


def test_other_signing_authority_is_not_continuity(continuity):
    rig = continuity
    rig.holder.encoded = issue_admission_receipt(**(
        rig.fields | {"private_key": Ed25519PrivateKey.generate()}
    ))
    with pytest.raises(ReceiptError):
        reattestation._current_continuity_v1(rig.core, rig.request, rig.observed)


@pytest.mark.parametrize("field,value", [
    ("request_id", D("0")), ("admission_context_id", D("0")),
    ("candidate_source_id", D("0")), ("transition_id", D("0")),
])
def test_swapped_new_request_cannot_use_old_evidence(continuity, field, value):
    rig = continuity
    swapped = replace(rig.request.producer_request, **{field: value})
    # Deliberate object at the downstream helper seam, not a productive request.
    request = SimpleNamespace(current=rig.request.current, producer_request=swapped)
    with pytest.raises(reattestation.BirthReattestationError, match="continuity_request_invalid"):
        reattestation._current_continuity_v1(rig.core, request, rig.observed)
    assert rig.holder.calls == []


@pytest.mark.parametrize("field", ["candidate_id", "admission_context_id"])
def test_proof_cannot_be_replayed_for_other_observation(continuity, field):
    rig = continuity
    proof = reattestation._current_continuity_v1(rig.core, rig.request, rig.observed)
    observed = replace(rig.observed, identities=replace(rig.observed.identities, **{field: D("0")}))
    decision = shadow.classify_revision(shadow.RevisionFacts(reattestation=True))
    with pytest.raises(ValueError, match="continuity_binding_invalid"):
        proof.check(observed, decision, "properties")


def test_proof_cannot_skip_checks_for_a_new_birth(continuity):
    rig = continuity
    proof = reattestation._current_continuity_v1(rig.core, rig.request, rig.observed)
    decision = shadow.classify_revision(shadow.RevisionFacts(first_birth=True))
    with pytest.raises(ValueError, match="continuity_binding_invalid"):
        proof.check(rig.observed, decision, "properties")


@pytest.mark.parametrize("component", ["manifest", "language", "code"])
def test_changed_bytes_cannot_inherit_previous_admission(continuity, monkeypatch, component):
    rig = continuity
    snapshot = SimpleNamespace(**vars(rig.observed.snapshot))
    if component == "manifest":
        snapshot.manifest_bytes += b"\n# changed exact source\n"
    elif component == "language":
        snapshot.language_state_bytes += b"\n"
    else:
        snapshot.code_files = dict(snapshot.code_files)
        name = next(iter(snapshot.code_files))
        snapshot.code_files[name] += b"\n# changed code\n"
    source_id = _candidate_source_id_from_snapshot(snapshot)
    changed_request = build_producer_request_v2(
        rig.core.selection, contract_id=rig.request.current.ref.contract_id,
        generation_id=rig.request.current.generation_id, candidate_source_id=source_id,
    )
    observed = replace(rig.observed, snapshot=snapshot)
    request = SimpleNamespace(current=rig.request.current, producer_request=changed_request)
    monkeypatch.setattr(contract_store, "read_current_birth_receipt_v2",
                        lambda *_args, **_kwargs: rig.holder.encoded)
    with pytest.raises(reattestation.BirthReattestationError, match="continuity_receipt_invalid"):
        reattestation._current_continuity_v1(rig.core, request, observed)


@pytest.mark.parametrize("field,value", [
    ("release_sequence", 1), ("release_sequence", 3),
    ("previous_closed_build_id", D("0")),
])
def test_only_immediate_successor_can_use_continuity(continuity, field, value):
    rig = continuity
    selection = replace(rig.core.selection, distribution=replace(
        rig.core.selection.distribution, **{field: value},
    ))
    with pytest.raises(reattestation.BirthReattestationError, match="continuity_context_invalid"):
        reattestation._require_continuity_context_v1(rig.core.previous_context, selection)


@pytest.mark.parametrize("check_name", ["properties", "semantic_review", "reattestation_current_generation_v1"])
def test_signed_incomplete_receipt_is_not_continuity(continuity, check_name):
    rig = continuity
    checks = dict(rig.fields["check_results"])
    del checks[check_name]
    rig.holder.encoded = issue_admission_receipt(**(rig.fields | {"check_results": checks}))
    with pytest.raises(reattestation.BirthReattestationError, match="continuity_receipt_invalid"):
        reattestation._current_continuity_v1(rig.core, rig.request, rig.observed)


def test_unsealed_dependency_hint_cannot_skip_checks():
    with pytest.raises(ValueError, match="birth_dependencies_untrusted"):
        shadow._sealed_dependencies_for_test(current_continuity=True)


@pytest.mark.parametrize("lifecycle", [ApprovedLifecycle.ACTIVE, ApprovedLifecycle.PREEXERCISE])
def test_successor_issues_real_receipt_and_replays_without_retesting(
    continuity, tmp_path, lifecycle,
):
    from executor_birth_operational import _sealed_core_for_test
    from executor_birth_predecessor import AdmissionContextPin, predecessor_snapshot
    from executor_birth_producer_store import register_producer_receipt
    from executor_birth_receipts import (
        IssuerKey, IssuerRegistry, issue_producer_receipt, verify_admission_receipt,
    )

    rig = continuity
    rig.holder.encoded = issue_admission_receipt(**(rig.fields | {"approved_lifecycle": lifecycle}))
    original_receipt = rig.holder.encoded
    instant = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    issuer, admission = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    registry = IssuerRegistry({"installer": (IssuerKey(
        "producer", issuer.public_key(), frozenset({rig.observed.executor_origin}),
        frozenset({rig.observed.revision_authorship}),
    ),)})
    producer = issue_producer_receipt(
        issuer_id="installer", executor_origin=rig.observed.executor_origin,
        revision_authorship=rig.observed.revision_authorship,
        objective_hash=rig.request.producer_binding.objective_hash,
        candidate_source_id=rig.request.producer_binding.candidate_source_id,
        issued_at="2026-09-09T12:00:00Z", expires_at="2026-09-09T13:00:00Z",
        nonce="1234567890abcdef1234567890abcdef", key_id="producer", private_key=issuer,
    )
    db = tmp_path / "new-producer.sqlite"
    register_producer_receipt(producer, registry=registry, now=instant, db_path=db)
    request = replace(rig.request, producer_receipt=producer)

    class NoDynamicChecks:
        def run(self, *_args, **_kwargs):
            raise AssertionError("unchanged current was dynamically retested")

        def inputs_for(self, *_args, **_kwargs):
            raise AssertionError("unchanged current was semantically rereviewed")

    birth = _sealed_core_for_test(
        producer_registry=registry, producer_db=db,
        context_resolver=lambda _request: (rig.new_context, AdmissionContextPin(
            rig.core.selection.admission_context_id, rig.core.selection.context_epoch,
        )),
        predecessor_resolver=lambda _request: (predecessor_snapshot(None, "absent", None), None),
        context_epoch_resolver=lambda: rig.core.selection.context_epoch,
        shadow_dependencies=shadow._sealed_dependencies_for_test(
            property_runner=NoDynamicChecks(), semantic_authority=NoDynamicChecks(),
        ),
        admission_private_key=admission, admission_public_key=admission.public_key(),
        admission_key_id="new-admission", policy_version="birth-policy-v1",
        now=lambda: instant, commit_publisher=object(),
    )
    written = []

    def persist(_current, encoded, expected, _request):
        decoded = verify_admission_receipt(encoded, verifier_keys=birth.admission_verifier_keys)
        assert all(getattr(decoded, field) == value for field, value in expected.items())
        written.append(encoded)
        return encoded

    core = reattestation._sealed_reattestation_core_for_test(
        birth=birth, capture=lambda _current: rig.observed.snapshot,
        read_receipt=lambda _current: None, persist=lambda *_args: pytest.fail("V1 write"),
        read_v2=lambda *_args: written[-1] if written else None, persist_v2=persist,
        previous_context=rig.core.previous_context, selection=rig.core.selection,
        store_root=rig.core.store_root,
    )
    first = reattestation._execute(request, core)
    receipt = verify_admission_receipt(first.receipt, verifier_keys=birth.admission_verifier_keys)
    assert first.receipt != original_receipt
    assert receipt.admission_context_id == rig.core.selection.admission_context_id
    assert receipt.approved_lifecycle is lifecycle
    assert receipt.check_results["properties"].status is AdmittedCheckStatus.NOT_APPLICABLE
    assert receipt.check_results["semantic_review"].status is AdmittedCheckStatus.NOT_APPLICABLE
    continuity_check = receipt.check_results["unchanged_current_continuity_v1"]
    assert continuity_check.status is AdmittedCheckStatus.NOT_APPLICABLE
    assert continuity_check.evidence_hash == "sha256:" + hashlib.sha256(original_receipt).hexdigest()
    assert "initial_current_generation_adoption_v1" not in receipt.check_results
    assert receipt.semantic_review_hash is None
    assert reattestation._execute(request, core).repeated
    assert len(written) == 1
    assert rig.holder.encoded == original_receipt

"""One consolidated family for V2 authentication and complete history joins."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store as contracts
import executor_birth_history as history
import executor_birth_prepared_root as root
import executor_birth_producer_context as context
import executor_birth_producer_store as producers
import executor_birth_reattestation as reattestation
import executor_birth_receipts as receipts
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_prepared_set import HistoricalProducerVerifiersV1
from tests.runtime.executors.test_executor_birth_historical_publication import _fixture as ordinary
from tests.runtime.executors.test_executor_birth_historical_producer_binding import digest


def _v2(*, producer_changes=None, admission_changes=None, adoption=False, continuity=None,
        base=None, context_id=digest(6), source_id=digest(2)):
    """The same stored wire protocol as the writer, with actual signatures."""
    base = ordinary() if base is None else base
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


@pytest.mark.parametrize("adoption,continuity", ((False, None), (True, None), (False, digest(3))))
def test_v2_history_accepts_its_terminal_less_protocol_without_operational_access(monkeypatch, adoption, continuity):
    inputs = _v2(adoption=adoption, continuity=continuity)

    def forbidden(*args, **kwargs):
        pytest.fail("historical verification attempted an operational request or store")

    monkeypatch.setattr(context, "build_producer_request_v2", forbidden)
    monkeypatch.setattr(producers, "_open", forbidden)
    monkeypatch.setattr(reattestation, "_execute", forbidden)
    result = reattestation.verify_historical_reattestation_v2(**inputs)
    assert result.initial_adoption == adoption
    assert result.continuity_receipt_hash == continuity
    assert result.producer_binding.namespace == "installer_phase3:install"
    assert not hasattr(result, "qualified")


@pytest.mark.parametrize("field,value", (
    ("state", "in_progress"), ("result_binding", digest(1)),
    ("terminal_envelope", b"{}"), ("terminal_auth", b"x" * 64),
    ("rejection_code", "rejected"), ("request_id", digest(9)),
))
def test_v2_history_rejects_row_drift(field, value):
    inputs = _v2()
    inputs["receipt_row"] = replace(inputs["receipt_row"], **{field: value})
    with pytest.raises((receipts.ReceiptError, reattestation.BirthReattestationError)):
        reattestation.verify_historical_reattestation_v2(**inputs)


@pytest.mark.parametrize("changes", (
    {"predecessor_id": digest(1)}, {"candidate_id": digest(1)},
    {"authoring_journal_hash": digest(1)}, {"birth_request_id": digest(1)},
    {"semantic_review_hash": digest(1)}, {"approval_hash": digest(1)},
    {"revision_class": receipts.RevisionClass.CODE_REVISION},
    {"kind": receipts.AdmissionKind.ADMISSION},
))
def test_v2_history_rejects_signed_contradictions(changes):
    with pytest.raises((receipts.ReceiptError, reattestation.BirthReattestationError)):
        reattestation.verify_historical_reattestation_v2(**_v2(admission_changes=changes))


@pytest.mark.parametrize("changes", (
    {"objective_hash": digest(1)}, {"candidate_source_id": digest(1)},
    {"nonce": "a" * 32}, {"revision_authorship": RevisionAuthor.MODEL},
    {"expires_at": "2026-08-25T12:00:01Z"},
))
def test_v2_history_checks_producer_bindings_at_the_signed_act(changes):
    with pytest.raises(receipts.ReceiptError):
        reattestation.verify_historical_reattestation_v2(**_v2(producer_changes=changes))


def test_v2_alias_does_not_grant_reattestation_to_another_authenticated_producer():
    inputs = _v2(producer_changes={"issuer_id": "writer"})
    declarations = inputs["declarations"]
    public = declarations.context.public_set
    inputs["declarations"] = replace(declarations,
        context=replace(declarations.context, public_set=replace(public, producers={
            "writer:create": next(iter(public.producers.values())),
        })), authors={"writer:create": "maintenance"},
    )
    with pytest.raises(receipts.ReceiptError, match="history_capability_binding"):
        reattestation.verify_historical_reattestation_v2(**inputs)


@pytest.mark.parametrize("change", ("scope_missing", "scope_error", "alias", "epoch", "transition", "initial"))
def test_v2_history_uses_historical_scope_and_context(change):
    inputs = _v2(adoption=change == "initial")
    declarations = inputs["declarations"]
    if change == "scope_missing":
        declarations = replace(declarations, reattestation_scope=None)
    elif change == "scope_error":
        declarations = replace(declarations, reattestation_scope_error="unavailable")
    elif change == "alias":
        inputs["issuance_row"] = replace(inputs["issuance_row"], capability_id="writer:create")
    elif change == "epoch":
        public = declarations.context.public_set
        declarations = replace(declarations, context=replace(declarations.context,
            public_set=replace(public, material=SimpleNamespace(pin=SimpleNamespace(
                admission_context_id=digest(6), context_epoch=digest(1),
            ))),
        ))
    else:
        declarations = replace(declarations, context=replace(declarations.context,
            **({"transition_id": digest(1)} if change == "transition" else {"initial_transition": False}),
        ))
    inputs["declarations"] = declarations
    with pytest.raises((receipts.ReceiptError, reattestation.BirthReattestationError)):
        reattestation.verify_historical_reattestation_v2(**inputs)


def _inventory(inputs, *, copies=1):
    durable = inputs["evidence"]
    contract = contracts.HistoricalContractHistoryV1(
        durable.contract_id, durable.binding_bytes, (durable.generation_id,), (),
        (contracts.HistoricalBirthReceiptV1(durable.generation_id, durable.admission_context_id,
                                          durable.receipt_bytes),) * copies,
    )
    inventory = contracts.HistoricalBirthInventoryV1(Path("fixture"), (contract,), (), copies, 0)
    producer_history = producers.ProducerHistoryV1(
        Path("fixture.sqlite"), 5, (), (inputs["receipt_row"],), (inputs["issuance_row"],), 0,
    )
    return inventory, producer_history


def _reconcile(inputs, *, copies=1, inventory_changes=None, producer_changes=None):
    inventory, producer_history = _inventory(inputs, copies=copies)
    return history.reconcile_historical_birth_v1(
        replace(inventory, **(inventory_changes or {})),
        replace(producer_history, **(producer_changes or {})), (inputs["declarations"],),
        read_evidence=lambda *args, **kwargs: inputs["evidence"],
    )


@pytest.mark.parametrize("kind,expected", (
    ("ordinary", "technical_candidate"), ("preexercise", "preexercise"),
    ("nontechnical", "nontechnical_revision"), ("reattestation", "reattestation"),
))
def test_complete_reconciliation_classifies_and_deduplicates_without_activation(kind, expected):
    inputs = (_v2() if kind == "reattestation" else ordinary(
        preexercise=kind == "preexercise",
        revision=(receipts.RevisionClass.LOCALIZATION_REVISION if kind == "nontechnical"
                  else receipts.RevisionClass.FIRST_BIRTH),
    ).inputs)
    result = _reconcile(inputs, copies=2)
    assert result.physical_receipts == 2
    assert not result.issues
    assert len(result.acts) == 1
    assert result.acts[0].classification == expected
    assert len(result.technical_acts) == (kind == "ordinary")
    assert not hasattr(result, "activation")


def test_complete_reconciliation_retains_all_unjoined_rows_and_unbound_namespaces():
    inputs = ordinary().inputs
    result = _reconcile(inputs, inventory_changes={
        "contracts": (), "unbound_empty_namespaces": (
            contracts.HistoricalUnboundNamespaceV1("a" * 64, b"\0"),
        ),
    })
    assert len(result.issues) == 3
    assert {issue.source for issue in result.issues} == {"namespace", "producer", "issuance"}
    assert not result.technical_acts


def test_complete_reconciliation_does_not_silently_discard_missing_policy():
    inputs = ordinary().inputs
    inventory, producer_history = _inventory(inputs)
    result = history.reconcile_historical_birth_v1(inventory, producer_history, (),
        read_evidence=lambda *args, **kwargs: pytest.fail("unselected context read"))
    assert "historical_context_policy_unavailable" in {issue.code for issue in result.issues}
    assert result.physical_receipts == 1
    assert not result.technical_acts


def test_complete_reconciliation_requires_exact_independent_reread():
    inputs = ordinary().inputs
    inventory, producer_history = _inventory(inputs)
    result = history.reconcile_historical_birth_v1(inventory, producer_history,
        (inputs["declarations"],), read_evidence=lambda *args, **kwargs: replace(
            inputs["evidence"], binding_bytes=b"different",
        ))
    assert "durable_inventory_reread_mismatch" in {issue.code for issue in result.issues}
    assert not result.technical_acts


def test_complete_reconciliation_records_unresolved_continuity():
    result = _reconcile(_v2(continuity=digest(3)))
    assert result.acts[0].classification == "unreconciled_continuity"
    assert "continuity_predecessor_not_reconciled" in {issue.code for issue in result.issues}


@pytest.mark.parametrize("reverse", (False, True))
def test_complete_reconciliation_links_continuity_without_counting_another_admission(reverse):
    base = ordinary()
    initial = base.inputs
    old_hash = "sha256:" + hashlib.sha256(initial["evidence"].receipt_bytes).hexdigest()
    newer = _v2(base=base, context_id=digest(7), continuity=old_hash)
    first, rows = _inventory(initial)
    second, other_rows = _inventory(newer)
    physical = first.contracts[0].receipts + second.contracts[0].receipts
    contract = replace(first.contracts[0], receipts=physical[::-1] if reverse else physical)
    combined = replace(rows,
        receipts=rows.receipts + (replace(other_rows.receipts[0], row_id=3),),
        issuances=rows.issuances + (replace(other_rows.issuances[0], row_id=5),),
    )
    evidence = {item["evidence"].admission_context_id: item["evidence"] for item in (initial, newer)}
    result = history.reconcile_historical_birth_v1(replace(first, contracts=(contract,)), combined,
        (initial["declarations"], newer["declarations"]),
        read_evidence=lambda *args, admission_context_id: evidence[admission_context_id],
    )
    assert not result.issues
    assert len(result.acts) == 2
    assert len(result.technical_acts) == 1


@pytest.mark.parametrize("changed_source,missing_parent", ((False, False), (True, False), (False, True)))
def test_complete_reconciliation_checks_v2_source_and_propagates_invalid_continuity(changed_source, missing_parent):
    base = ordinary()
    initial = _v2(base=base, continuity=digest(9) if missing_parent else None)
    initial_hash = "sha256:" + hashlib.sha256(initial["evidence"].receipt_bytes).hexdigest()
    newer = _v2(base=base, context_id=digest(7), continuity=initial_hash,
                source_id=digest(3) if changed_source else digest(2))
    first, rows = _inventory(initial)
    second, others = _inventory(newer)
    contract = replace(first.contracts[0], receipts=second.contracts[0].receipts + first.contracts[0].receipts)
    declarations = (initial["declarations"], newer["declarations"])
    combined = replace(rows, receipts=rows.receipts + (replace(others.receipts[0], row_id=3),),
                       issuances=rows.issuances + (replace(others.issuances[0], row_id=5),))
    evidence = {item["evidence"].admission_context_id: item["evidence"] for item in (initial, newer)}
    result = history.reconcile_historical_birth_v1(replace(first, contracts=(contract,)), combined,
        declarations, read_evidence=lambda *args, admission_context_id: evidence[admission_context_id])
    if changed_source or missing_parent:
        assert result.acts[0].classification == "unreconciled_continuity"
        assert "continuity_predecessor_not_reconciled" in {item.code for item in result.issues}
    else:
        assert not result.issues
        assert [act.classification for act in result.acts] == ["reattestation", "reattestation"]


@pytest.mark.parametrize("field", ("receipt_hash", "receipt_id"))
def test_complete_reconciliation_retains_ambiguous_row_identity(field):
    inputs = ordinary().inputs
    inventory, rows = _inventory(inputs)
    changes = ({"receipts": rows.receipts + (replace(rows.receipts[0], row_id=3),)}
               if field == "receipt_hash" else
               {"issuances": rows.issuances + (replace(rows.issuances[0], row_id=5),)})
    result = history.reconcile_historical_birth_v1(inventory, replace(rows, **changes),
        (inputs["declarations"],), read_evidence=lambda *args, **kwargs: inputs["evidence"],
    )
    assert "duplicate_stored_identity" in {issue.code for issue in result.issues}
    assert not result.technical_acts


def test_scope_projection_reads_source_without_importing_a_historical_module():
    runtime = Path(root.__file__).parent
    assert root._project_reattestation_scope_v2(
        (runtime / "executor_birth_intent.py").read_bytes(),
        (runtime / "executor_birth_bootstrap.py").read_bytes(),
    ) == ("installer_phase3:install", "installer_phase4:ownership_reattest_current_v2")


@pytest.mark.parametrize("old,new", (
    ("authority=assembly.authorities[_INSTALLER]", "authority=assembly.authorities[_PROMOTE]"),
    ("capability_id=_REATTESTATION_CAPABILITY_V2", "capability_id='forged'"),
    ("issuer_id=authority.issuer_id", "issuer_id='forged'"),
    ("    if selection is None:\n", "    options['authority'] = assembly.authorities[_PROMOTE]\n    if selection is None:\n"),
    ("authority=authority,\n", "authority=other,\n"),
    ("class _CutoverReattestationFactoryV2(_CutoverReattestationFactoryV1)",
     "class _CutoverReattestationFactoryV2(other)"),
))
def test_scope_projection_rejects_changed_protocol_binding(old, new):
    runtime = Path(root.__file__).parent
    with pytest.raises(root.PreparedRootError, match="reattestation_policy_unsupported"):
        root._project_reattestation_scope_v2(
            (runtime / "executor_birth_intent.py").read_bytes(),
            (runtime / "executor_birth_bootstrap.py").read_bytes().replace(old.encode(), new.encode()),
        )


@pytest.mark.parametrize("suffix", (
    b"\n_CutoverReattestationFactoryV2 = object()\n",
    b"\nclass _CutoverReattestationFactoryV2: pass\n",
    b"\n_CutoverReattestationFactoryV2.prepare = other\n",
    b"\nfrom elsewhere import _CutoverReattestationFactoryV2\n",
))
def test_scope_projection_rejects_profile_rebinding(suffix):
    runtime = Path(root.__file__).parent
    with pytest.raises(root.PreparedRootError, match="reattestation_policy_unsupported"):
        root._project_reattestation_scope_v2(
            (runtime / "executor_birth_intent.py").read_bytes(),
            (runtime / "executor_birth_bootstrap.py").read_bytes() + suffix,
        )


def test_scope_profile_ignores_comments_but_not_authority_flow():
    runtime = Path(root.__file__).parent
    assert root._project_reattestation_scope_v2(
        b"# Formatting only.\n" + (runtime / "executor_birth_intent.py").read_bytes(),
        b"# Formatting only.\n" + (runtime / "executor_birth_bootstrap.py").read_bytes(),
    )[0] == "installer_phase3:install"


def test_v2_identity_codec_has_no_seal_and_matches_the_original_framing():
    fields = dict(contract_id="user:sample/manifest.toml", generation_id=digest(1),
                  admission_context_id=digest(2), transition_id=digest(3),
                  set_id="a" * 64, context_epoch=digest(4), candidate_source_id=digest(5))
    payload = b"".join(len(value.encode()).to_bytes(8, "big") + value.encode() for value in fields.values())
    expected = tuple("sha256:" + hashlib.sha256(domain + payload).hexdigest() for domain in (
        b"metnos.executor-birth.producer-request/v2\0", b"metnos.executor-birth.producer-objective/v2\0",
    ))
    assert context.producer_request_identity_v2(**fields) == expected

"""Operational, single-authority Executor Birth boundary (RM-0008 F4).

The public request is data only.  Trust registries, the F3 check catalog,
receipt keys/verifiers and the publisher are assembled behind a module seal.
"""
from __future__ import annotations

import hashlib
import base64
import json
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

if TYPE_CHECKING:
    from executor_birth_intent import BirthIntent, _ProducerCapability

from contract_store import BirthCommitAuthorization, ManifestRef, PublicationResult
from executor_birth import ObservedCandidate, observe_candidate
from executor_birth_approval import ApprovalEvidence, ApprovalSubject, approval_evidence_hash
from executor_birth_identity import AdmissionContextV1, ExecutorOrigin, admission_context_id
from executor_birth_predecessor import (
    AdmissionContextPin, AuthenticatedPredecessorSnapshot,
    derive_revision_facts, revision_facts_id,
)
from executor_birth_property_runner import ObservedPropertyRunner
from executor_birth_producer_store import (
    ProducerReceiptBinding, claim_producer_receipt,
    finalize_producer_receipt, producer_receipt_hash,
    record_producer_receipt_terminal_hint,
)
from executor_birth_receipts import (
    AdmissionCheck, AdmissionKind, AdmittedCheckStatus, ApprovedLifecycle,
    IssuerRegistry, RevisionClass as ReceiptRevisionClass,
    issue_admission_receipt, verify_admission_receipt,
)
from executor_birth_shadow import (
    BirthOutcome, BirthReport, CheckResult, CheckStatus, RevisionClass, RevisionFacts, _BirthDependencies,
    _observe_birth_for_test, classify_revision,
)


def _digest(domain: bytes, fields: Mapping[str, bytes]) -> str:
    framed = bytearray(domain)
    for name, value in sorted(fields.items(), key=lambda item: item[0].encode()):
        key = name.encode()
        framed.extend(len(key).to_bytes(8, "big")); framed.extend(key)
        framed.extend(len(value).to_bytes(8, "big")); framed.extend(value)
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def candidate_source_id(observed: ObservedCandidate) -> str:
    """Identify the complete, closed source envelope copied by F1."""
    return _candidate_source_id_from_snapshot(observed.snapshot)


def _candidate_source_id_from_snapshot(snapshot: object) -> str:
    """Identify an already-owned snapshot without reopening its source."""
    fields = {
        "manifest.toml": snapshot.manifest_bytes,
        "manifest.lang_state.json": snapshot.language_state_bytes,
        **dict(snapshot.code_files),
    }
    return _digest(b"metnos.executor-birth.candidate-source/v1\0", fields)


def approval_scope(observed: ObservedCandidate, revision: RevisionClass) -> str | None:
    """Derive the only permissible approval scope from observed core facts."""
    if observed.executor_origin is ExecutorOrigin.SYNTHESIZED:
        return "preexercise"
    scopes = {
        RevisionClass.AUTHORITY: "authority",
        RevisionClass.PROMOTION: "promotion",
        RevisionClass.REACTIVATION: "reactivation",
    }
    if revision in scopes:
        return scopes[revision]
    return None


@dataclass(frozen=True, slots=True)
class BirthRequest:
    request_id: str
    manifest_ref: ManifestRef
    producer_receipt: bytes
    actor: str
    reason: str
    approval_refs: tuple[str, ...]
    operation_hint: str
    candidate_source_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_ref, ManifestRef):
            raise ValueError("birth_request_invalid: manifest_ref")
        if not isinstance(self.candidate_source_root, Path):
            raise ValueError("birth_request_invalid: candidate_source_root")
        if not isinstance(self.producer_receipt, bytes):
            raise ValueError("birth_request_invalid: producer_receipt")
        if not all(isinstance(value, str) and value and "\x00" not in value for value in
                   (self.actor, self.reason, self.operation_hint)):
            raise ValueError("birth_request_invalid: text")
        if not isinstance(self.approval_refs, tuple) or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in self.approval_refs
        ):
            raise ValueError("birth_request_invalid: approval_refs")
        _require_digest(self.request_id, "request_id")


@dataclass(frozen=True, slots=True)
class BirthResult:
    request_id: str
    report: BirthReport
    publication: PublicationResult | None
    error_code: str | None


def _terminal_envelope(
    core: "_BirthCore", result: BirthResult, admission_receipt: bytes | None = None,
) -> bytes:
    report = result.report
    publication = result.publication
    value = {
        "admission_receipt": (base64.b64encode(admission_receipt).decode("ascii")
                              if admission_receipt is not None else None),
        "error_code": result.error_code,
        "publication": None if publication is None else {
            "contract_id": publication.contract_id.value,
            "current_generation_id": publication.current_generation_id,
            "operation": publication.operation,
            "previous_generation_id": publication.previous_generation_id,
        },
        "report": {
            "admission_context_id": report.admission_context_id,
            "candidate_id": report.candidate_id,
            "changed_dimensions": list(report.changed_dimensions),
            "checks": [{
                "check_id": check.check_id, "error_code": check.error_code,
                "evidence_hash": check.evidence_hash, "redacted_detail": check.redacted_detail,
                "rule_version": check.rule_version, "status": check.status.value,
            } for check in report.checks],
            "error_code": report.error_code, "outcome": report.outcome.value,
            "revision_class": report.revision_class.value if report.revision_class else None,
            "semantic_core_id": report.semantic_core_id,
        },
        # This identifier is emitted only by the sealed core.  It is signed as
        # part of the canonical envelope and is never request/caller input.
        "signing_key_id": core.admission_key_id,
        "request_id": result.request_id, "schema_version": 2,
    }
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _decode_terminal_envelope(encoded: bytes, request: BirthRequest) -> tuple[BirthResult, bytes | None, str]:
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=lambda pairs: _unique_object(pairs))
        if json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") != encoded:
            raise ValueError("noncanonical")
        if value["schema_version"] != 2 or value["request_id"] != request.request_id:
            raise ValueError("binding")
        signing_key_id = value["signing_key_id"]
        if not isinstance(signing_key_id, str) or not signing_key_id or "\x00" in signing_key_id:
            raise ValueError("signing key")
        item = value["report"]
        checks = tuple(CheckResult(
            check["check_id"], check["rule_version"], CheckStatus(check["status"]),
            check["error_code"], check["evidence_hash"], check["redacted_detail"],
        ) for check in item["checks"])
        report = BirthReport(
            1, request.manifest_ref.contract_id, item["candidate_id"], item["semantic_core_id"],
            item["admission_context_id"],
            RevisionClass(item["revision_class"]) if item["revision_class"] else None,
            tuple(item["changed_dimensions"]), checks, BirthOutcome(item["outcome"]), item["error_code"],
        )
        pub = value["publication"]
        publication = None if pub is None else PublicationResult(
            request.manifest_ref.contract_id, pub["previous_generation_id"],
            pub["current_generation_id"], pub["operation"], True,
        )
        admission = (base64.b64decode(value["admission_receipt"], validate=True)
                     if value["admission_receipt"] is not None else None)
        return (BirthResult(request.request_id, report, publication, value["error_code"]),
                admission, signing_key_id)
    except Exception as exc:
        raise ValueError("birth_terminal_envelope_invalid") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _sign_terminal(core: "_BirthCore", encoded: bytes) -> bytes:
    """Sign with the sealed Birth admission key; no envelope-selectable key exists."""
    return core.admission_private_key.sign(b"metnos.executor-birth.terminal/v1\0" + encoded)


def _verify_terminal(core: "_BirthCore", encoded: bytes, signature: bytes,
                     request: BirthRequest) -> tuple[BirthResult, bytes | None]:
    """Select a historical verifier only from the core-owned signed key id."""
    result, admission, key_id = _decode_terminal_envelope(encoded, request)
    verifier = core.admission_verifier_keys.get(key_id)
    if verifier is None:
        raise ValueError("birth_terminal_key_untrusted")
    verifier.verify(signature, b"metnos.executor-birth.terminal/v1\0" + encoded)
    return result, admission


def _terminal_binding(encoded: bytes) -> str:
    return _digest(b"metnos.executor-birth.terminal-binding/v1\0", {"envelope": encoded})


def _replay_terminal(core: "_BirthCore", request: BirthRequest, claim: object) -> BirthResult:
    encoded = getattr(claim, "terminal_envelope", None)
    signature = getattr(claim, "terminal_auth", None)
    if encoded is None or signature is None:
        raise ValueError("birth_terminal_envelope_missing")
    result, admission = _verify_terminal(core, encoded, signature, request)
    if result.publication is not None:
        _publication_binding(request, result.publication)
        verified, verified_admission = _verified_postcondition(
            core.postcondition_verifier(request, result.publication, admission)
        )
        if verified != result.publication:
            raise ValueError("birth_publication_replay_mismatch")
        if admission is not None and verified_admission not in {None, admission}:
            raise ValueError("birth_admission_replay_mismatch")
        result = BirthResult(result.request_id, result.report, verified, result.error_code)
    return result


def _verified_postcondition(value: object) -> tuple[PublicationResult | None, bytes | None]:
    if value is None:
        return None, None
    if isinstance(value, PublicationResult):
        return value, None
    if (isinstance(value, tuple) and len(value) == 2
            and isinstance(value[0], PublicationResult)
            and (value[1] is None or isinstance(value[1], bytes))):
        return value
    raise ValueError("birth_postcondition_verifier_invalid")


def _require_digest(value: object, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 71 or
            not value.startswith("sha256:") or
            any(char not in "0123456789abcdef" for char in value[7:])):
        raise ValueError(f"birth_request_invalid: {field}")
    return value


ContextResolver = Callable[[BirthRequest], tuple[AdmissionContextV1, AdmissionContextPin]]
PredecessorResolver = Callable[
    [BirthRequest], tuple[AuthenticatedPredecessorSnapshot, Mapping[str, bytes] | None]
]
Publisher = Callable[..., PublicationResult]
PostconditionVerifier = Callable[
    [BirthRequest, PublicationResult | None, bytes | None],
    PublicationResult | tuple[PublicationResult, bytes | None] | None,
]
ApprovalResolver = Callable[
    [BirthRequest, ObservedCandidate, RevisionClass, datetime],
    tuple[ApprovalSubject | None, ApprovalEvidence | None],
]

_CORE_SEAL = object()


@dataclass(frozen=True, slots=True)
class _BirthCore:
    producer_registry: IssuerRegistry
    producer_db: Path
    context_resolver: ContextResolver
    predecessor_resolver: PredecessorResolver
    context_epoch_resolver: Callable[[], str]
    approval_resolver: ApprovalResolver
    shadow_dependencies: _BirthDependencies
    admission_private_key: object
    admission_verifier_keys: Mapping[str, object]
    admission_key_id: str
    policy_version: str
    now: Callable[[], datetime]
    commit_publisher: object
    postcondition_verifier: PostconditionVerifier
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _CORE_SEAL:
            raise ValueError("birth_core_untrusted")
        if not callable(self.approval_resolver):
            raise ValueError("birth_core_approval_resolver_invalid")
        verifiers = dict(self.admission_verifier_keys)
        if (not isinstance(self.admission_private_key, Ed25519PrivateKey)
                or self.admission_key_id not in verifiers or any(
            not isinstance(key_id, str) or not key_id
            or not isinstance(verifier, Ed25519PublicKey)
            for key_id, verifier in verifiers.items()
        )):
            raise ValueError("birth_core_admission_keyring_invalid")
        active_public = self.admission_private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        configured_public = verifiers[self.admission_key_id].public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        if active_public != configured_public:
            raise ValueError("birth_core_admission_keyring_invalid")
        object.__setattr__(self, "admission_verifier_keys", MappingProxyType(verifiers))



def _sealed_core_for_test(**values: object) -> _BirthCore:
    """Test-only trust-core constructor; the public API never accepts it."""
    values.setdefault("postcondition_verifier", lambda _request, expected, _receipt: expected)
    values.setdefault("approval_resolver", lambda _request, _observed, _revision, _now: (None, None))
    # Compatibility belongs exclusively to this explicitly test-only seam.
    if "admission_verifier_keys" not in values and "admission_public_key" in values:
        public = values.pop("admission_public_key")
        values["admission_verifier_keys"] = {values["admission_key_id"]: public}
    # The productive core takes a sealed publisher; a test that wants to drive
    # the publication supplies a plain callable, and this seam — and only this
    # seam — wraps it so the productive shape is the one under test.
    if "commit_publisher" not in values and "publisher" in values:
        publisher = values.pop("publisher")
        options = dict(values.pop("publisher_options", {}) or {})
        predecessor = values.get("predecessor_resolver")
        values["commit_publisher"] = _TestCommitPublisher(
            publisher, options, predecessor, values,
        )
    values["_seal"] = _CORE_SEAL
    return _BirthCore(**values)  # type: ignore[arg-type]


class _TestCommitPublisher:
    """Test-only adapter: the productive publisher with a driven primitive.

    It builds the real sealed publisher and substitutes only the store call,
    so what the test exercises is the productive issuer, verifier and epoch
    resolver rather than a stand-in that could disagree with them.
    """

    __slots__ = ("_inner", "_options", "_predecessor")

    def __init__(self, publisher, options, predecessor, values) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from i18n_pipeline import reconcile_published_contract_registry
        from executor_birth_commit_publisher import (
            _BirthCommitPublisher, _PUBLISHER_TOKEN,
        )

        self._options = dict(options)
        self._predecessor = predecessor
        resolver = values.get("context_epoch_resolver")
        self._inner = _BirthCommitPublisher(
            _PUBLISHER_TOKEN,
            author_private=Ed25519PrivateKey.generate(),
            author_ring=tuple(self._options.get("trusted_publics", ())),
            admission_private=values["admission_private_key"],
            admission_key_id=values["admission_key_id"],
            admission_verifiers=values["admission_verifier_keys"],
            prepared_context_epoch=resolver() if resolver else "",
            primitive=lambda ref, **kwargs: publisher(ref, **kwargs),
            store_root=self._options.get("store_root"),
            # The productive reconciler, not a stand-in: this seam exists so
            # that what the test exercises is the productive shape, and a
            # different reconciler here would quietly test something else.
            registry_reconciler=reconcile_published_contract_registry,
        )

    def admission_lock(self):
        return self._inner.admission_lock()

    def resolve_predecessor(self, request):
        if self._predecessor is None:
            raise ValueError("birth_predecessor_resolver_missing")
        return self._predecessor(request)

    def commit(self, facts):
        return self._inner.commit(facts)

    def reattestation_port(self):
        return self._inner.reattestation_port()


def _assemble_birth_core(
    *, producer_registry: IssuerRegistry, producer_db: Path,
    context_resolver: ContextResolver,
    context_epoch_resolver: Callable[[], str],
    approval_resolver: ApprovalResolver,
    shadow_dependencies: _BirthDependencies, admission_private_key: object,
    admission_verifier_keys: Mapping[str, object], admission_key_id: str, policy_version: str,
    now: Callable[[], datetime], commit_publisher: object,
    postcondition_verifier: PostconditionVerifier,
) -> _BirthCore:
    """Core bootstrap assembler; productive publication is not selectable.

    The publisher is sealed: it owns the author key, the trusted ring, the
    Admission identity and the single store primitive.  The core can hand it
    facts and nothing else, so no option and no caller can substitute an
    authority (section 5.3).
    """
    for name in ("admission_lock", "commit", "resolve_predecessor", "reattestation_port"):
        if not callable(getattr(commit_publisher, name, None)):
            raise ValueError("birth_commit_publisher_invalid")

    return _BirthCore(
        producer_registry, producer_db, context_resolver,
        commit_publisher.resolve_predecessor,
        context_epoch_resolver, approval_resolver,
        shadow_dependencies, admission_private_key, admission_verifier_keys,
        admission_key_id, policy_version, now, commit_publisher,
        postcondition_verifier, _CORE_SEAL,
    )


class _BorrowedObserved:
    """Let F3 inspect the one owned snapshot without transferring its lifetime."""
    def __init__(self, observed: ObservedCandidate) -> None:
        self._observed = observed

    def __getattr__(self, name: str) -> object:
        return getattr(self._observed, name)

    def close(self) -> None:
        pass


def _receipt_revision(report: BirthReport) -> ReceiptRevisionClass:
    if report.revision_class is None:
        raise ValueError("birth_report_invalid")
    return ReceiptRevisionClass(report.revision_class.value)


def _receipt_checks(report: BirthReport) -> Mapping[str, AdmissionCheck]:
    result: dict[str, AdmissionCheck] = {}
    for check in report.checks:
        if check.status not in {CheckStatus.PASSED, CheckStatus.NOT_APPLICABLE}:
            raise ValueError("birth_report_not_admitted")
        result[check.check_id] = AdmissionCheck(
            check.rule_version, AdmittedCheckStatus(check.status.value), check.evidence_hash,
        )
    return MappingProxyType(result)


_PREDECESSOR_UNSET = object()


def _publication_binding(request: BirthRequest, publication: PublicationResult,
                         predecessor_id: str | None | object = _PREDECESSOR_UNSET) -> str:
    """Bind the complete replayable publication postcondition."""
    if publication.contract_id != request.manifest_ref.contract_id:
        raise ValueError("birth_publication_invalid: contract_id")
    if (predecessor_id is not _PREDECESSOR_UNSET
            and publication.previous_generation_id != predecessor_id):
        raise ValueError("birth_publication_invalid: previous_generation_id")
    _require_digest(publication.current_generation_id, "current_generation_id")
    if publication.operation != "commit_birth_snapshot":
        raise ValueError("birth_publication_invalid: operation")
    return _digest(b"metnos.executor-birth.publication-result/v1\0", {
        "contract_id": publication.contract_id.value.encode(),
        "current_generation_id": publication.current_generation_id.encode(),
        "operation": publication.operation.encode(),
        "previous_generation_id": (publication.previous_generation_id or "").encode(),
        "request_id": request.request_id.encode(),
    })


def _rejected_report(request: BirthRequest, *, observed: ObservedCandidate | None,
                     facts: RevisionFacts | None, error_code: str) -> BirthReport:
    decision = classify_revision(facts) if facts is not None else None
    return BirthReport(
        1, request.manifest_ref.contract_id,
        observed.identities.candidate_id if observed is not None else None,
        observed.identities.semantic_core_id if observed is not None else None,
        observed.identities.admission_context_id if observed is not None else None,
        decision.revision_class if decision is not None else None,
        decision.changed_dimensions if decision is not None else (), (),
        BirthOutcome.REJECTED, error_code,
    )


def _execute(request: BirthRequest, core: _BirthCore) -> BirthResult:
    observed: ObservedCandidate | None = None
    report: BirthReport | None = None
    facts: RevisionFacts | None = None
    receipt_binding: ProducerReceiptBinding | None = None
    claimed = False
    publication_started = False
    try:
        if not isinstance(core, _BirthCore) or core._seal is not _CORE_SEAL:
            raise ValueError("birth_core_untrusted")
        instant = core.now().astimezone(timezone.utc)
        context, context_pin = core.context_resolver(request)
        if (not isinstance(context_pin, AdmissionContextPin)
                or context_pin.admission_context_id != admission_context_id(context)
                or core.context_epoch_resolver() != context_pin.context_epoch):
            # A refusal that reports itself as a generic unavailability sends
            # the diagnosis the wrong way: the code travels.
            from executor_birth_commit_publisher import BirthCommitLinkError

            raise BirthCommitLinkError("birth_context_pin_invalid")
        # The admission lock serializes receipt consumption and acquisition of
        # the only source snapshot.  All expensive checks run after release.
        with core.commit_publisher.admission_lock():
            producer_preview = _peek_receipt(core, request, instant)
            observed = observe_candidate(
                request.candidate_source_root, contract_id=request.manifest_ref.contract_id,
                executor_origin=producer_preview.executor_origin,
                revision_authorship=producer_preview.revision_authorship,
                objective_hash=producer_preview.objective_hash,
                admission_context=context,
            )
            receipt_binding = ProducerReceiptBinding(
                observed.objective_hash, candidate_source_id(observed),
                observed.executor_origin, observed.revision_authorship,
            )
            claim = claim_producer_receipt(
                request.producer_receipt, registry=core.producer_registry,
                binding=receipt_binding, request_id=request.request_id,
                now=instant, db_path=core.producer_db,
            )
            producer = claim.receipt
            claimed = True
        if claim.state == "rejected":
            return _replay_terminal(core, request, claim)
        if claim.state == "committed":
            return _replay_terminal(core, request, claim)
        if claim.terminal_envelope is not None:
            hinted, admission = _verify_terminal(
                core, claim.terminal_envelope, claim.terminal_auth, request,
            )
            reconciled, reconciled_admission = _verified_postcondition(
                core.postcondition_verifier(request, None, admission)
            )
            if reconciled is not None:
                _publication_binding(request, reconciled)
                recovered = BirthResult(request.request_id, hinted.report, reconciled, None)
                envelope = _terminal_envelope(core, recovered, reconciled_admission or admission)
                auth = _sign_terminal(core, envelope)
                finalize_producer_receipt(
                    request.producer_receipt, registry=core.producer_registry,
                    binding=receipt_binding, request_id=request.request_id, now=instant,
                    db_path=core.producer_db, result_binding=_terminal_binding(envelope),
                    terminal_envelope=envelope, terminal_auth=auth,
                )
                return recovered
        predecessor_snapshot, predecessor_payloads = core.predecessor_resolver(request)
        if not isinstance(predecessor_snapshot, AuthenticatedPredecessorSnapshot):
            raise ValueError("birth_predecessor_snapshot_invalid")
        facts = derive_revision_facts(
            predecessor_snapshot, predecessor_payloads, observed.snapshot,
        )
        revision = classify_revision(facts).revision_class
        approval_subject, approval_evidence = core.approval_resolver(
            request, observed, revision, instant,
        )
        shadow = core.shadow_dependencies
        property_runner = shadow.property_runner or ObservedPropertyRunner(
            observed, windows_registry=shadow.windows_sandbox_registry,
            linux_registry=shadow.linux_sandbox_registry,
        )
        # Derived from the shadow container, never re-listed field by field:
        # a new dependency would otherwise be silently dropped here.
        borrowed_dependencies = replace(
            shadow,
            observer=lambda *_args, **_kwargs: _BorrowedObserved(observed),
            property_runner=property_runner,
            approval_subject=approval_subject,
            approval_evidence=approval_evidence, now=instant,
        )
        report = _observe_birth_for_test(
            request.candidate_source_root, contract_id=request.manifest_ref.contract_id,
            executor_origin=producer.executor_origin,
            revision_authorship=producer.revision_authorship,
            objective_hash=producer.objective_hash, admission_context=context,
            revision_facts=facts, _dependencies=borrowed_dependencies,
        )
        if report.outcome not in {BirthOutcome.ADMITTED, BirthOutcome.PREEXERCISE}:
            rejected_result = BirthResult(request.request_id, report, None, report.error_code)
            envelope = _terminal_envelope(core, rejected_result)
            finalize_producer_receipt(
                request.producer_receipt, registry=core.producer_registry,
                binding=receipt_binding, request_id=request.request_id, now=instant,
                db_path=core.producer_db,
                rejection_code=report.error_code or "birth_not_admitted",
                terminal_envelope=envelope, terminal_auth=_sign_terminal(core, envelope),
            )
            return rejected_result

        checks = dict(_receipt_checks(report))
        semantic_hash = next((c.evidence_hash for c in report.checks if c.check_id == "semantic_review" and c.status is CheckStatus.PASSED), None)
        approval_hash = (
            approval_evidence_hash(approval_evidence)
            if approval_evidence is not None else None
        )
        lifecycle = ApprovedLifecycle.PREEXERCISE if report.outcome is BirthOutcome.PREEXERCISE else ApprovedLifecycle.ACTIVE
        predecessor = predecessor_snapshot.revision_id

        # The core hands over facts; the sealed publisher owns the keys, the
        # issuer, the verifier, the epoch resolver and the store primitive.
        from executor_birth_commit_publisher import BirthCommitFactsV1

        commit_facts = BirthCommitFactsV1(
            manifest_ref=request.manifest_ref,
            snapshot=observed.snapshot,
            request_id=request.request_id,
            policy_version=core.policy_version,
            contract_id=request.manifest_ref.contract_id,
            candidate_id=observed.identities.candidate_id,
            semantic_core_id=observed.identities.semantic_core_id,
            admission_context_id=observed.identities.admission_context_id,
            expected_generation_id=predecessor,
            predecessor_id=predecessor,
            predecessor_snapshot_id=predecessor_snapshot.snapshot_id,
            revision_facts_id=revision_facts_id(facts),
            observed_context_epoch=context_pin.context_epoch,
            producer_receipt_hash=producer_receipt_hash(request.producer_receipt),
            revision_class=_receipt_revision(report),
            approved_lifecycle=lifecycle,
            check_results=dict(checks),
            semantic_review_hash=semantic_hash,
            approval_hash=approval_hash,
            issued_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        # The signed admitted report is sufficient for a read-only recovery
        # verifier to reconcile a crash after the publisher's durable point.
        hint = _terminal_envelope(core, BirthResult(request.request_id, report, None, None))
        record_producer_receipt_terminal_hint(
            request.producer_receipt, registry=core.producer_registry,
            binding=receipt_binding, request_id=request.request_id, now=instant,
            db_path=core.producer_db, terminal_envelope=hint,
            terminal_auth=_sign_terminal(core, hint),
        )
        publication_started = True
        outcome = core.commit_publisher.commit(commit_facts)
        publication = outcome.publication
        issued_receipts = (
            [outcome.admission_receipt] if outcome.admission_receipt else []
        )
        _publication_binding(request, publication, predecessor)
        successful = BirthResult(request.request_id, report, publication, None)
        envelope = _terminal_envelope(core, successful, issued_receipts[-1] if issued_receipts else None)
        finalize_producer_receipt(
            request.producer_receipt, registry=core.producer_registry,
            binding=receipt_binding, request_id=request.request_id, now=instant,
            db_path=core.producer_db, result_binding=_terminal_binding(envelope),
            terminal_envelope=envelope, terminal_auth=_sign_terminal(core, envelope),
        )
        return successful
    except Exception as exc:
        error_code = getattr(exc, "code", "birth_unavailable")
        if report is None:
            report = _rejected_report(
                request, observed=observed, facts=facts, error_code=error_code,
            )
        elif report.outcome in {BirthOutcome.ADMITTED, BirthOutcome.PREEXERCISE}:
            # Admission is not an operational success until the atomic commit
            # returns its verified postcondition.
            report = BirthReport(
                report.schema_version, report.contract_id, report.candidate_id,
                report.semantic_core_id, report.admission_context_id,
                report.revision_class, report.changed_dimensions, report.checks,
                BirthOutcome.REJECTED, error_code,
            )
        # Once publication starts, failure is ambiguous: the durable store may
        # already expose the postcondition.  Keep the claim recoverable so an
        # exact retry can make the publisher prove (or reject) that state.
        if claimed and not publication_started and receipt_binding is not None:
            try:
                rejected_result = BirthResult(request.request_id, report, None, error_code)
                envelope = _terminal_envelope(core, rejected_result)
                finalize_producer_receipt(
                    request.producer_receipt, registry=core.producer_registry,
                    binding=receipt_binding, request_id=request.request_id,
                    now=instant, db_path=core.producer_db,
                    rejection_code=str(error_code),
                    terminal_envelope=envelope, terminal_auth=_sign_terminal(core, envelope),
                )
            except Exception:
                # Never replace the original failure with bookkeeping noise.
                pass
        return BirthResult(request.request_id, report, None, error_code)
    finally:
        if observed is not None:
            observed.close()


def _peek_receipt(core: _BirthCore, request: BirthRequest, instant: datetime):
    from executor_birth_receipts import verify_producer_receipt
    return verify_producer_receipt(request.producer_receipt, registry=core.producer_registry, now=instant)


@dataclass(frozen=True, slots=True)
class BirthRuntimeBundle:
    """One immutable publication unit for every productive Birth dependency."""

    core: _BirthCore
    producer_factories: Mapping[object, Callable[["BirthIntent"], BirthRequest]]
    reattestation_factory: Callable[[object], object]
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _RUNTIME_SEAL or self.core._seal is not _CORE_SEAL:
            raise ValueError("birth_runtime_bundle_untrusted")
        factories = dict(self.producer_factories)
        if (
            not factories
            or any(not callable(value) for value in factories.values())
            or not callable(self.reattestation_factory)
        ):
            raise ValueError("birth_runtime_bundle_invalid")
        object.__setattr__(self, "producer_factories", MappingProxyType(factories))


_RUNTIME_SEAL = object()
_RUNTIME_LOCK = threading.Lock()
_RUNTIME_BUNDLE: BirthRuntimeBundle | None = None


def _assemble_birth_runtime_bundle(
    core: _BirthCore,
    producer_factories: Mapping["_ProducerCapability", Callable[["BirthIntent"], BirthRequest]],
    reattestation_factory: Callable[[object], object],
) -> BirthRuntimeBundle:
    """Bootstrap primitive; its inputs must already be fully validated."""
    from executor_birth_intent import _is_producer_capability
    if not producer_factories or any(
        not _is_producer_capability(capability) for capability in producer_factories
    ):
        raise ValueError("birth_producer_capability_untrusted")
    return BirthRuntimeBundle(
        core, producer_factories, reattestation_factory, _RUNTIME_SEAL,
    )


def _install_birth_runtime_bundle(bundle: BirthRuntimeBundle) -> None:
    """Publish the complete runtime exactly once, with no partial state."""
    global _RUNTIME_BUNDLE
    if not isinstance(bundle, BirthRuntimeBundle) or bundle._seal is not _RUNTIME_SEAL:
        raise ValueError("birth_runtime_bundle_untrusted")
    with _RUNTIME_LOCK:
        if _RUNTIME_BUNDLE is not None:
            raise ValueError("birth_runtime_bundle_already_installed")
        _RUNTIME_BUNDLE = bundle


def _runtime_bundle_snapshot() -> BirthRuntimeBundle | None:
    # Assignment is atomic in supported CPython runtimes. The lock supplies a
    # language-level happens-before edge for alternate Python implementations.
    with _RUNTIME_LOCK:
        return _RUNTIME_BUNDLE


def _execute_intent_with_capability(
    intent: "BirthIntent", capability: "_ProducerCapability",
) -> BirthResult:
    from executor_birth_intent import BirthIntent, _is_producer_capability
    if not isinstance(intent, BirthIntent):
        raise ValueError("birth_intent_invalid")
    if not _is_producer_capability(capability):
        raise ValueError("birth_producer_capability_untrusted")
    bundle = _runtime_bundle_snapshot()
    if bundle is None:
        # Every mutating CLI/job facade crosses this same lazy boot gate.  A
        # missing pre-provisioned key or incomplete recovery fails before any
        # producer worker can observe a partial runtime.
        from executor_birth_bootstrap import bootstrap_birth_runtime
        try:
            bundle = bootstrap_birth_runtime()
        except Exception as exc:
            raise RuntimeError("birth_runtime_bundle_unavailable") from exc
    factory = bundle.producer_factories.get(capability)
    if factory is None:
        raise ValueError("birth_producer_capability_unavailable")
    request = factory(intent)
    if not isinstance(request, BirthRequest):
        raise ValueError("birth_request_invalid")
    # Use the core from the same bundle snapshot as the producer factory.
    return _execute(request, bundle.core)


def birth_executor(request: BirthRequest) -> BirthResult:
    """Execute the sealed productive Birth pipeline."""
    if not isinstance(request, BirthRequest):
        raise ValueError("birth_request_invalid")
    bundle = _runtime_bundle_snapshot()
    if bundle is None:
        report = BirthReport(1, request.manifest_ref.contract_id, None, None, None,
                             None, (), (), BirthOutcome.REJECTED,
                             "birth_core_unavailable")
        return BirthResult(request.request_id, report, None, "birth_core_unavailable")
    return _execute(request, bundle.core)


def _birth_executor_for_test(request: BirthRequest, *, _core: _BirthCore) -> BirthResult:
    return _execute(request, _core)

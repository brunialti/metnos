"""Operational, single-authority Executor Birth boundary (RM-0008 F4).

The public request is data only.  Trust registries, the F3 check catalog,
receipt keys/verifiers and the publisher are assembled behind a module seal.
"""
from __future__ import annotations

import hashlib
import base64
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping

if TYPE_CHECKING:
    from executor_birth_intent import BirthIntent, _ProducerCapability

from contract_store import (
    BirthCommitAuthorization, ManifestRef, PublicationResult,
    catalog_admission_lock,
)
from executor_birth import ObservedCandidate, observe_candidate
from executor_birth_approval import approval_evidence_hash
from executor_birth_identity import AdmissionContextV1
from executor_birth_producer_store import (
    ProducerReceiptBinding, claim_producer_receipt,
    finalize_producer_receipt, producer_receipt_hash,
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
    fields = {
        "manifest.toml": observed.snapshot.manifest_bytes,
        "manifest.lang_state.json": observed.snapshot.language_state_bytes,
        **dict(observed.snapshot.code_files),
    }
    return _digest(b"metnos.executor-birth.candidate-source/v1\0", fields)


@dataclass(frozen=True, slots=True)
class BirthRequest:
    request_id: str
    manifest_ref: ManifestRef
    expected_revision_id: str | None
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


def _terminal_envelope(result: BirthResult, admission_receipt: bytes | None = None) -> bytes:
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
        "request_id": result.request_id, "schema_version": 1,
    }
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _decode_terminal_envelope(encoded: bytes, request: BirthRequest) -> tuple[BirthResult, bytes | None]:
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=lambda pairs: _unique_object(pairs))
        if json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") != encoded:
            raise ValueError("noncanonical")
        if value["schema_version"] != 1 or value["request_id"] != request.request_id:
            raise ValueError("binding")
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
        return BirthResult(request.request_id, report, publication, value["error_code"]), admission
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
    return core.admission_private_key.sign(b"metnos.executor-birth.terminal/v1\0" + encoded)


def _replay_terminal(core: "_BirthCore", request: BirthRequest, claim: object) -> BirthResult:
    encoded = getattr(claim, "terminal_envelope", None)
    signature = getattr(claim, "terminal_auth", None)
    if encoded is None or signature is None:
        raise ValueError("birth_terminal_envelope_missing")
    core.admission_public_key.verify(signature, b"metnos.executor-birth.terminal/v1\0" + encoded)
    result, admission = _decode_terminal_envelope(encoded, request)
    if result.publication is not None:
        verified = core.postcondition_verifier(request, result.publication, admission)
        if verified != result.publication:
            raise ValueError("birth_publication_replay_mismatch")
        result = BirthResult(result.request_id, result.report, verified, result.error_code)
    return result


def _require_digest(value: object, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 71 or
            not value.startswith("sha256:") or
            any(char not in "0123456789abcdef" for char in value[7:])):
        raise ValueError(f"birth_request_invalid: {field}")
    return value


ContextResolver = Callable[[BirthRequest], AdmissionContextV1]
FactsResolver = Callable[[BirthRequest], RevisionFacts]
Publisher = Callable[..., PublicationResult]
PostconditionVerifier = Callable[[BirthRequest, PublicationResult, bytes | None], PublicationResult]

_CORE_SEAL = object()


@dataclass(frozen=True, slots=True)
class _BirthCore:
    producer_registry: IssuerRegistry
    producer_db: Path
    context_resolver: ContextResolver
    facts_resolver: FactsResolver
    shadow_dependencies: _BirthDependencies
    admission_private_key: object
    admission_public_key: object
    admission_key_id: str
    policy_version: str
    now: Callable[[], datetime]
    publisher: Publisher
    postcondition_verifier: PostconditionVerifier
    publisher_options: Mapping[str, object]
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _CORE_SEAL:
            raise ValueError("birth_core_untrusted")
        object.__setattr__(self, "publisher_options", MappingProxyType(dict(self.publisher_options)))


def _sealed_core_for_test(**values: object) -> _BirthCore:
    """Test-only trust-core constructor; the public API never accepts it."""
    values["_seal"] = _CORE_SEAL
    return _BirthCore(**values)  # type: ignore[arg-type]


def _assemble_birth_core(
    *, producer_registry: IssuerRegistry, producer_db: Path,
    context_resolver: ContextResolver, facts_resolver: FactsResolver,
    shadow_dependencies: _BirthDependencies, admission_private_key: object,
    admission_public_key: object, admission_key_id: str, policy_version: str,
    now: Callable[[], datetime], publisher_options: Mapping[str, object],
    postcondition_verifier: PostconditionVerifier,
) -> _BirthCore:
    """Core bootstrap assembler; productive publication is not selectable."""
    from contract_store import commit_birth_snapshot
    return _BirthCore(
        producer_registry, producer_db, context_resolver, facts_resolver,
        shadow_dependencies, admission_private_key, admission_public_key,
        admission_key_id, policy_version, now, commit_birth_snapshot, postcondition_verifier,
        publisher_options, _CORE_SEAL,
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


def _publication_binding(request: BirthRequest, publication: PublicationResult) -> str:
    """Bind the complete replayable publication postcondition."""
    if publication.contract_id != request.manifest_ref.contract_id:
        raise ValueError("birth_publication_invalid: contract_id")
    if publication.previous_generation_id != request.expected_revision_id:
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
        context = core.context_resolver(request)
        facts = core.facts_resolver(request)
        # The admission lock serializes receipt consumption and acquisition of
        # the only source snapshot.  All expensive checks run after release.
        lock_root = core.publisher_options.get("store_root")
        with catalog_admission_lock(store_root=lock_root):
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
            error_code = claim.rejection_code or "birth_rejected"
            return BirthResult(
                request.request_id,
                _rejected_report(request, observed=observed, facts=facts,
                                 error_code=error_code),
                None, error_code,
            )
        committed_binding = claim.result_binding if claim.state == "committed" else None
        shadow = core.shadow_dependencies
        borrowed_dependencies = _BirthDependencies(
            observer=lambda *_args, **_kwargs: _BorrowedObserved(observed),
            property_runner=shadow.property_runner, semantic_policy=shadow.semantic_policy,
            semantic_risk=shadow.semantic_risk,
            independent_evidence=shadow.independent_evidence,
            approval_subject=shadow.approval_subject,
            approval_evidence=shadow.approval_evidence, now=shadow.now,
            _seal=shadow._seal,
        )
        report = _observe_birth_for_test(
            request.candidate_source_root, contract_id=request.manifest_ref.contract_id,
            executor_origin=producer.executor_origin,
            revision_authorship=producer.revision_authorship,
            objective_hash=producer.objective_hash, admission_context=context,
            revision_facts=facts, _dependencies=borrowed_dependencies,
        )
        if report.outcome not in {BirthOutcome.ADMITTED, BirthOutcome.PREEXERCISE}:
            finalize_producer_receipt(
                request.producer_receipt, registry=core.producer_registry,
                binding=receipt_binding, request_id=request.request_id, now=instant,
                db_path=core.producer_db,
                rejection_code=report.error_code or "birth_not_admitted",
            )
            return BirthResult(request.request_id, report, None, report.error_code)

        checks = dict(_receipt_checks(report))
        semantic_hash = next((c.evidence_hash for c in report.checks if c.check_id == "semantic_review" and c.status is CheckStatus.PASSED), None)
        approval_hash = (
            approval_evidence_hash(shadow.approval_evidence)
            if shadow.approval_evidence is not None else None
        )
        lifecycle = ApprovedLifecycle.PREEXERCISE if report.outcome is BirthOutcome.PREEXERCISE else ApprovedLifecycle.ACTIVE
        predecessor = request.expected_revision_id

        def issuer(generation_id: str, _payload_hashes: Mapping[str, str],
                   birth_request_id: str, journal_hash: str) -> bytes:
            receipt_checks = dict(checks)
            receipt_checks["authoring_install_journal_v1"] = AdmissionCheck(
                "1", AdmittedCheckStatus.PASSED, journal_hash,
            )
            return issue_admission_receipt(
                policy_version=core.policy_version, contract_id=request.manifest_ref.contract_id,
                generation_id=generation_id, candidate_id=observed.identities.candidate_id,
                semantic_core_id=observed.identities.semantic_core_id,
                admission_context_id=observed.identities.admission_context_id,
                birth_request_id=birth_request_id, authoring_journal_hash=journal_hash,
                predecessor_id=predecessor,
                producer_receipt_hash=producer_receipt_hash(request.producer_receipt),
                revision_class=_receipt_revision(report), check_results=receipt_checks,
                semantic_review_hash=semantic_hash, approval_hash=approval_hash,
                approved_lifecycle=lifecycle, kind=AdmissionKind.ADMISSION,
                issued_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                key_id=core.admission_key_id, private_key=core.admission_private_key,
            )

        authorization = BirthCommitAuthorization(
            observed.identities.candidate_id, observed.identities.semantic_core_id,
            observed.identities.admission_context_id, predecessor, issuer,
            lambda encoded: verify_admission_receipt(
                encoded, public_key=core.admission_public_key,
                expected_key_id=core.admission_key_id,
            ),
        )
        publication_started = True
        publication = core.publisher(
            request.manifest_ref, expected_generation_id=predecessor,
            snapshot=observed.snapshot, request_id=request.request_id,
            birth_authorization=authorization, **dict(core.publisher_options),
        )
        result_binding = _publication_binding(request, publication)
        if committed_binding is not None and result_binding != committed_binding:
            raise ValueError("birth_publication_replay_mismatch")
        finalize_producer_receipt(
            request.producer_receipt, registry=core.producer_registry,
            binding=receipt_binding, request_id=request.request_id, now=instant,
            db_path=core.producer_db, result_binding=result_binding,
        )
        return BirthResult(request.request_id, report, publication, None)
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
                finalize_producer_receipt(
                    request.producer_receipt, registry=core.producer_registry,
                    binding=receipt_binding, request_id=request.request_id,
                    now=instant, db_path=core.producer_db,
                    rejection_code=str(error_code),
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
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _RUNTIME_SEAL or self.core._seal is not _CORE_SEAL:
            raise ValueError("birth_runtime_bundle_untrusted")
        factories = dict(self.producer_factories)
        if not factories or any(not callable(value) for value in factories.values()):
            raise ValueError("birth_runtime_bundle_invalid")
        object.__setattr__(self, "producer_factories", MappingProxyType(factories))


_RUNTIME_SEAL = object()
_RUNTIME_LOCK = threading.Lock()
_RUNTIME_BUNDLE: BirthRuntimeBundle | None = None


def _assemble_birth_runtime_bundle(
    core: _BirthCore,
    producer_factories: Mapping["_ProducerCapability", Callable[["BirthIntent"], BirthRequest]],
) -> BirthRuntimeBundle:
    """Bootstrap primitive; its inputs must already be fully validated."""
    from executor_birth_intent import _is_producer_capability
    if not producer_factories or any(
        not _is_producer_capability(capability) for capability in producer_factories
    ):
        raise ValueError("birth_producer_capability_untrusted")
    return BirthRuntimeBundle(core, producer_factories, _RUNTIME_SEAL)


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
        raise RuntimeError("birth_runtime_bundle_unavailable")
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

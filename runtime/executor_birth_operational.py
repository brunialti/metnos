"""Operational, single-authority Executor Birth boundary (RM-0008 F4).

The public request is data only.  Trust registries, the F3 check catalog,
receipt keys/verifiers and the publisher are assembled behind a module seal.
"""
from __future__ import annotations

import hashlib
import base64
import re
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
    from executor_birth_prepared_root import HistoricalProducerDeclarationsV1
    from executor_birth_producer_store import (
        HistoricalProducerBindingV1, ProducerReceiptRowV1, ProducerIssuanceRowV1,
    )
    from contract_store import HistoricalBirthEvidenceV1

from contract_store import BirthCommitAuthorization, ManifestRef, PublicationResult
from manifest_inventory import ContractId
from executor_birth import ObservedCandidate, observe_candidate
from executor_birth_approval import ApprovalEvidence, ApprovalSubject, approval_evidence_hash
from executor_birth_canonical import decode_canonical_ascii_v1, encode_canonical_ascii_v1
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


MAX_TERMINAL_ENVELOPE_BYTES = 4 * 1024 * 1024
_TERMINAL_SIGNATURE_DOMAIN = b"metnos.executor-birth.terminal/v1\0"
_PUBLISHED_OUTCOMES = frozenset({
    BirthOutcome.ADMITTED, BirthOutcome.PREEXERCISE, BirthOutcome.QUARANTINED,
})


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
class BirthDiagnostic:
    """Bounded technical evidence, never exception messages or request data."""

    phase: str
    code: str
    cause: str

    def __post_init__(self) -> None:
        for value in (self.phase, self.code):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", value):
                raise ValueError("birth_diagnostic_invalid")
        if (not isinstance(self.cause, str) or len(self.cause) > 1024
                or not re.fullmatch(r"[A-Za-z0-9_.: >-]*", self.cause)):
            raise ValueError("birth_diagnostic_invalid")


def birth_failure_diagnostic(exc: Exception, phase: str) -> BirthDiagnostic:
    """Keep codes and reviewed source locations instead of unsafe free text.

    The location distinguishes e.g. an untrusted admission key from a bad
    signature even when both exceptions use receipt_invalid. No detail,
    filename from a request, argv, output or exception message is retained.
    """
    def code(error):
        value = getattr(error, "code", None)
        return value if isinstance(value, str) and re.fullmatch(
            r"[a-z][a-z0-9_]{0,95}", value) else "birth_unavailable"

    causes, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(causes) < 4:
        seen.add(id(current))
        location = ""
        frame = current.__traceback__
        while frame is not None:
            path = Path(frame.tb_frame.f_code.co_filename)
            if (path.parent == Path(__file__).parent and path.is_file()
                    and re.fullmatch(r"[a-z][a-z0-9_]*\.py", path.name)):
                location = f" {path.name}:{frame.tb_lineno}"
            frame = frame.tb_next
        causes.append(code(current) + location)
        current = current.__cause__ or current.__context__
    return BirthDiagnostic(phase, code(exc), " > ".join(causes)[:1024])


@dataclass(frozen=True, slots=True)
class BirthResult:
    request_id: str
    report: BirthReport
    publication: PublicationResult | None
    error_code: str | None
    diagnostic: BirthDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class HistoricalPublicationV1:
    """Authenticated ordinary publication facts, not a grant or F5 count."""

    producer_binding: HistoricalProducerBindingV1
    terminal: BirthResult


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
    if result.diagnostic is not None:
        value["diagnostic"] = {
            "phase": result.diagnostic.phase, "code": result.diagnostic.code,
            "cause": result.diagnostic.cause,
        }
    encoded = encode_canonical_ascii_v1(value)
    if len(encoded) > MAX_TERMINAL_ENVELOPE_BYTES:
        raise ValueError("birth_terminal_envelope_invalid")
    return encoded


def _terminal_text(value: object, *, nullable: bool = False, empty: bool = False):
    if nullable and value is None:
        return None
    if type(value) is not str or (not empty and not value) or "\x00" in value:
        raise ValueError("terminal text")
    value.encode("utf-8")
    return value


def _terminal_fields(value: object, fields: set[str]) -> None:
    if type(value) is not dict or set(value) != fields:
        raise ValueError("terminal fields")


def _decode_terminal_envelope(
    encoded: bytes, *, expected_request_id: str, expected_contract_id: ContractId,
) -> tuple[BirthResult, bytes | None, str]:
    """Decode signed wire facts without inventing a current operational request.

    The expected contract supplies report context. Only a publication carries
    that contract on the wire. Report defaults and publication.repeated are
    reconstruction details, never additional signed facts.
    """
    try:
        _require_digest(expected_request_id, "request_id")
        if not isinstance(expected_contract_id, ContractId):
            raise ValueError("expected contract")
        value = decode_canonical_ascii_v1(encoded, maximum=MAX_TERMINAL_ENVELOPE_BYTES)
        if type(value) is not dict or set(value) - {"diagnostic"} != {
            "schema_version", "request_id", "signing_key_id", "report",
            "publication", "admission_receipt", "error_code",
        }:
            raise ValueError("terminal fields")
        if (type(value["schema_version"]) is not int or value["schema_version"] != 2
                or value["request_id"] != expected_request_id):
            raise ValueError("binding")
        signing_key_id = _terminal_text(value["signing_key_id"])
        _terminal_text(value["error_code"], nullable=True)
        item = value["report"]
        _terminal_fields(item, {
            "admission_context_id", "candidate_id", "changed_dimensions", "checks",
            "error_code", "outcome", "revision_class", "semantic_core_id",
        })
        for field in ("candidate_id", "semantic_core_id", "admission_context_id"):
            if item[field] is not None:
                _require_digest(item[field], field)
        _terminal_text(item["error_code"], nullable=True)
        dimensions = item["changed_dimensions"]
        if type(dimensions) is not list or type(item["checks"]) is not list:
            raise ValueError("terminal lists")
        for dimension in dimensions:
            _terminal_text(dimension)
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("duplicate dimensions")
        checks = []
        for check in item["checks"]:
            _terminal_fields(check, {
                "check_id", "rule_version", "status", "error_code", "evidence_hash", "redacted_detail",
            })
            checks.append(CheckResult(
                _terminal_text(check["check_id"]), _terminal_text(check["rule_version"]),
                CheckStatus(check["status"]), _terminal_text(check["error_code"], nullable=True),
                _require_digest(check["evidence_hash"], "evidence_hash"),
                _terminal_text(check["redacted_detail"], empty=True),
            ))
        if len({check.check_id for check in checks}) != len(checks):
            raise ValueError("duplicate checks")
        report = BirthReport(
            1, expected_contract_id, item["candidate_id"], item["semantic_core_id"],
            item["admission_context_id"],
            RevisionClass(item["revision_class"]) if item["revision_class"] is not None else None,
            tuple(dimensions), tuple(checks), BirthOutcome(item["outcome"]), item["error_code"],
        )
        pub = value["publication"]
        if pub is not None:
            _terminal_fields(pub, {
                "contract_id", "previous_generation_id", "current_generation_id", "operation",
            })
            # Compare the signed fact before reconstructing its typed identity.
            if pub["contract_id"] != expected_contract_id.value:
                raise ValueError("publication contract binding")
            _require_digest(pub["current_generation_id"], "current_generation_id")
            if pub["previous_generation_id"] is not None:
                _require_digest(pub["previous_generation_id"], "previous_generation_id")
            _terminal_text(pub["operation"])
        publication = None if pub is None else PublicationResult(
            expected_contract_id, pub["previous_generation_id"],
            pub["current_generation_id"], pub["operation"], True,
        )
        admission = None
        if value["admission_receipt"] is not None:
            text = _terminal_text(value["admission_receipt"])
            admission = base64.b64decode(text, validate=True)
            if base64.b64encode(admission).decode("ascii") != text:
                raise ValueError("noncanonical admission bytes")
        diagnostic = None
        if "diagnostic" in value:
            _terminal_fields(value["diagnostic"], {"phase", "code", "cause"})
            diagnostic = BirthDiagnostic(**value["diagnostic"])
        return (BirthResult(expected_request_id, report, publication, value["error_code"], diagnostic),
                admission, signing_key_id)
    except Exception as exc:
        raise ValueError("birth_terminal_envelope_invalid") from exc


def _sign_terminal(core: "_BirthCore", encoded: bytes) -> bytes:
    """Sign with the sealed Birth admission key; no envelope-selectable key exists."""
    return core.admission_private_key.sign(_TERMINAL_SIGNATURE_DOMAIN + encoded)


def verify_terminal_evidence_v1(
    encoded: bytes, signature: bytes, *, expected_request_id: str,
    expected_contract_id: ContractId, verifier_keys: Mapping[str, Ed25519PublicKey],
) -> tuple[BirthResult, bytes | None]:
    """Verify wire evidence using public keys selected by the owning caller.

    This does not establish the ring's authority, authenticate an embedded
    Admission or prove a durable success. A report's contract is expected
    context; only publication carries it on the wire. Reconstructed defaults
    such as publication.repeated are not authenticated evidence.
    """
    if type(signature) is not bytes or len(signature) != 64:
        raise ValueError("birth_terminal_signature_invalid")
    result, admission, key_id = _decode_terminal_envelope(
        encoded, expected_request_id=expected_request_id,
        expected_contract_id=expected_contract_id,
    )
    if not isinstance(verifier_keys, Mapping):
        raise ValueError("birth_terminal_key_untrusted")
    verifier = verifier_keys.get(key_id)
    if not isinstance(verifier, Ed25519PublicKey):
        raise ValueError("birth_terminal_key_untrusted")
    verifier.verify(signature, _TERMINAL_SIGNATURE_DOMAIN + encoded)
    return result, admission


def _verify_terminal(core: "_BirthCore", encoded: bytes, signature: bytes,
                     request: BirthRequest) -> tuple[BirthResult, bytes | None]:
    """Select a historical verifier only from the core-owned signed key id."""
    return verify_terminal_evidence_v1(
        encoded, signature, expected_request_id=request.request_id,
        expected_contract_id=request.manifest_ref.contract_id,
        verifier_keys=core.admission_verifier_keys,
    )


def _terminal_binding(encoded: bytes) -> str:
    return _digest(b"metnos.executor-birth.terminal-binding/v1\0", {"envelope": encoded})


def _replay_terminal(core: "_BirthCore", request: BirthRequest, claim: object) -> BirthResult:
    encoded = getattr(claim, "terminal_envelope", None)
    signature = getattr(claim, "terminal_auth", None)
    if encoded is None or signature is None:
        raise ValueError("birth_terminal_envelope_missing")
    result, admission = _verify_terminal(core, encoded, signature, request)
    committed = claim.state == "committed"
    if committed != (result.publication is not None):
        raise ValueError("birth_terminal_state_mismatch")
    if committed:
        if (result.report.outcome not in _PUBLISHED_OUTCOMES
                or result.error_code is not None):
            raise ValueError("birth_terminal_state_mismatch")
        if claim.result_binding != _terminal_binding(encoded):
            raise ValueError("birth_terminal_binding_mismatch")
    elif (claim.state != "rejected"
            or result.report.outcome not in {
                BirthOutcome.REJECTED, BirthOutcome.NEEDS_HUMAN, BirthOutcome.QUARANTINED,
            }
            or not isinstance(result.error_code, str) or not result.error_code
            or result.error_code != claim.rejection_code):
        # An admitted recovery hint is not a terminal rejection. The operational
        # error can differ from the original check error after finalization fails.
        raise ValueError("birth_terminal_state_mismatch")
    if result.publication is not None:
        _publication_binding(request, result.publication)
        verified, verified_admission = _verified_postcondition(
            core.postcondition_verifier(request, result.publication, admission)
        )
        if verified != result.publication:
            raise ValueError("birth_publication_replay_mismatch")
        if admission is not None and verified_admission not in {None, admission}:
            raise ValueError("birth_admission_replay_mismatch")
        result = replace(result, publication=verified)
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
    quarantine_key_ids: frozenset[str] = frozenset()

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
        object.__setattr__(self, "quarantine_key_ids", frozenset(self.quarantine_key_ids))


def _is_birth_core(value: object) -> bool:
    """Recognize only the exact core built behind this module's seal."""
    return type(value) is _BirthCore and value._seal is _CORE_SEAL


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
    quarantine_key_ids: frozenset[str] = frozenset(),
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
        postcondition_verifier, _CORE_SEAL, quarantine_key_ids,
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


def verify_historical_publication_v1(
    *, evidence: HistoricalBirthEvidenceV1, receipt_row: ProducerReceiptRowV1,
    issuance_row: ProducerIssuanceRowV1, declarations: HistoricalProducerDeclarationsV1,
) -> HistoricalPublicationV1:
    """Join durable, Producer and terminal evidence without operational access.

    Public-ring provenance and a common complete frontier belong to the
    acquiring owner. Valid nontechnical/preexercise publications remain facts,
    not qualifying technical admissions. Missing original source/journal bytes
    are not reconstructed from their signed cross-bindings.
    """
    from contract_store import HistoricalBirthEvidenceV1, verify_historical_birth_evidence_v1
    from executor_birth_prepared_root import HistoricalProducerDeclarationsV1
    from executor_birth_producer_store import verify_historical_producer_binding_v1

    if (type(evidence) is not HistoricalBirthEvidenceV1
            or type(declarations) is not HistoricalProducerDeclarationsV1):
        raise ValueError("birth_history_terminal_input_invalid")
    public = declarations.context.public_set
    admission = verify_historical_birth_evidence_v1(
        evidence, admission_verifier_keys=public.admission_verifier_keys,
        author_verifier_keys=public.author_verifier_keys,
    )
    binding = verify_historical_producer_binding_v1(
        evidence.receipt_bytes, receipt_row=receipt_row, issuance_row=issuance_row,
        declarations=declarations,
    )
    if receipt_row.state != "committed" or receipt_row.rejection_code is not None:
        raise ValueError("birth_history_terminal_state_invalid")
    terminal, embedded = verify_terminal_evidence_v1(
        receipt_row.terminal_envelope, receipt_row.terminal_auth,
        expected_request_id=admission.birth_request_id,
        expected_contract_id=evidence.contract_id,
        verifier_keys=public.admission_verifier_keys,
    )
    if receipt_row.result_binding != _terminal_binding(receipt_row.terminal_envelope):
        raise ValueError("birth_history_terminal_binding_invalid")
    if embedded is not None and embedded != evidence.receipt_bytes:
        raise ValueError("birth_history_terminal_receipt_mismatch")
    report, publication = terminal.report, terminal.publication
    lifecycles = {
        BirthOutcome.ADMITTED: ApprovedLifecycle.ACTIVE,
        BirthOutcome.PREEXERCISE: ApprovedLifecycle.PREEXERCISE,
        BirthOutcome.QUARANTINED: ApprovedLifecycle.QUARANTINED,
    }
    if (publication is None or terminal.error_code is not None or report.error_code is not None
            or lifecycles.get(report.outcome) != admission.approved_lifecycle
            or publication.operation != "commit_birth_snapshot"
            or publication.current_generation_id != admission.generation_id
            or publication.previous_generation_id != admission.predecessor_id
            or report.candidate_id != admission.candidate_id
            or report.semantic_core_id != admission.semantic_core_id
            or report.admission_context_id != admission.admission_context_id
            or report.revision_class is None
            or report.revision_class.value != admission.revision_class.value):
        raise ValueError("birth_history_terminal_facts_mismatch")
    checks = dict(_receipt_checks(report))
    if "authoring_install_journal_v1" in checks:
        raise ValueError("birth_history_terminal_reserved_check")
    checks["authoring_install_journal_v1"] = AdmissionCheck(
        "1", AdmittedCheckStatus.PASSED, admission.authoring_journal_hash,
    )
    semantic_hash = next((c.evidence_hash for c in report.checks
                          if c.check_id == "semantic_review" and c.status is CheckStatus.PASSED), None)
    approval = checks.get("approval")
    if (checks != admission.check_results or semantic_hash != admission.semantic_review_hash
            or (approval is not None and approval.status is AdmittedCheckStatus.PASSED
                and approval.evidence_hash != admission.approval_hash)):
        raise ValueError("birth_history_terminal_checks_mismatch")
    return HistoricalPublicationV1(binding, terminal)


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


def _execute(request: BirthRequest, core: _BirthCore, *, quarantine_execution=None) -> BirthResult:
    observed: ObservedCandidate | None = None
    report: BirthReport | None = None
    facts: RevisionFacts | None = None
    receipt_binding: ProducerReceiptBinding | None = None
    claimed = False
    publication_started = False
    phase = "context"
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
            phase = "candidate"
            producer_preview = _peek_receipt(core, request, instant)
            if quarantine_execution is not None:
                from executor_birth_quarantine import _validate_quarantine_request
                _validate_quarantine_request(
                    request, producer_preview, quarantine_execution, core.quarantine_key_ids,
                )
            elif producer_preview.authentication.key_id in core.quarantine_key_ids:
                raise ValueError("birth_quarantine_execution_required")
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
            phase = "producer_claim"
            from executor_birth_receipts import ReceiptError
            try:
                claim = claim_producer_receipt(
                    request.producer_receipt, registry=core.producer_registry,
                    binding=receipt_binding, request_id=request.request_id,
                    now=instant, db_path=core.producer_db,
                )
            except ReceiptError as exc:
                if quarantine_execution is None or exc.code != "producer_receipt_lease_expired":
                    raise
                from executor_birth_producer_store import renew_producer_receipt_claim
                claim = renew_producer_receipt_claim(
                    request.producer_receipt, registry=core.producer_registry,
                    binding=receipt_binding, request_id=request.request_id,
                    now=instant, db_path=core.producer_db,
                )
            producer = claim.receipt
            claimed = True
        phase = "recovery"
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
        phase = "predecessor"
        predecessor_snapshot, predecessor_payloads = core.predecessor_resolver(request)
        if not isinstance(predecessor_snapshot, AuthenticatedPredecessorSnapshot):
            raise ValueError("birth_predecessor_snapshot_invalid")
        facts = derive_revision_facts(
            predecessor_snapshot, predecessor_payloads, observed.snapshot,
        )
        revision = classify_revision(facts).revision_class
        phase = "approval"
        approval_evidence = None
        if quarantine_execution is not None:
            from executor_birth_quarantine import _quarantine_report
            phase = "checks"
            report = _quarantine_report(
                request, quarantine_execution, observed, predecessor_snapshot,
                predecessor_payloads, facts, core.commit_publisher,
            )
        else:
            approval_subject, approval_evidence = core.approval_resolver(request, observed, revision, instant)
            phase = "checks"
            report = _ordinary_admission_report(
                request, core, observed, producer, context, facts, instant,
                approval_subject, approval_evidence,
            )
        phase = "checks"
        if (report.outcome not in _PUBLISHED_OUTCOMES or report.error_code is not None
                or (report.outcome is BirthOutcome.QUARANTINED and quarantine_execution is None)):
            rejected_result = BirthResult(
                request.request_id, report, None, report.error_code,
                BirthDiagnostic("checks", report.error_code or "birth_not_admitted", ""),
            )
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
        lifecycle = {
            BirthOutcome.ADMITTED: ApprovedLifecycle.ACTIVE,
            BirthOutcome.PREEXERCISE: ApprovedLifecycle.PREEXERCISE,
            BirthOutcome.QUARANTINED: ApprovedLifecycle.QUARANTINED,
        }[report.outcome]
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
        phase = "publication"
        outcome = core.commit_publisher.commit(commit_facts)
        publication = outcome.publication
        issued_receipts = (
            [outcome.admission_receipt] if outcome.admission_receipt else []
        )
        _publication_binding(request, publication, predecessor)
        successful = BirthResult(request.request_id, report, publication, None)
        envelope = _terminal_envelope(core, successful, issued_receipts[-1] if issued_receipts else None)
        phase = "finalization"
        finalize_producer_receipt(
            request.producer_receipt, registry=core.producer_registry,
            binding=receipt_binding, request_id=request.request_id, now=instant,
            db_path=core.producer_db, result_binding=_terminal_binding(envelope),
            terminal_envelope=envelope, terminal_auth=_sign_terminal(core, envelope),
        )
        return successful
    except Exception as exc:
        diagnostic = birth_failure_diagnostic(exc, phase)
        error_code = diagnostic.code
        if report is None:
            report = _rejected_report(
                request, observed=observed, facts=facts, error_code=error_code,
            )
        elif report.outcome in _PUBLISHED_OUTCOMES and report.error_code is None:
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
                rejected_result = BirthResult(request.request_id, report, None, error_code, diagnostic)
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
        # An in-progress publication retains its immutable admitted recovery
        # hint. Its failure diagnostic travels in the result/release report;
        # it must not overwrite the proof needed to recover a durable commit.
        return BirthResult(request.request_id, report, None, error_code, diagnostic)
    finally:
        if observed is not None:
            observed.close()


def _ordinary_admission_report(request, core, observed, producer, context, facts, instant,
                               approval_subject, approval_evidence):
    shadow = core.shadow_dependencies
    property_runner = shadow.property_runner or ObservedPropertyRunner(
        observed, windows_registry=shadow.windows_sandbox_registry,
        linux_registry=shadow.linux_sandbox_registry,
    )
    # Preserve every dependency when lending the already-owned observation.
    borrowed = replace(
        shadow, observer=lambda *_args, **_kwargs: _BorrowedObserved(observed),
        property_runner=property_runner, approval_subject=approval_subject,
        approval_evidence=approval_evidence, now=instant,
    )
    report = _observe_birth_for_test(
        request.candidate_source_root, contract_id=request.manifest_ref.contract_id,
        executor_origin=producer.executor_origin, revision_authorship=producer.revision_authorship,
        objective_hash=producer.objective_hash, admission_context=context,
        revision_facts=facts, _dependencies=borrowed,
    )
    return report


def _peek_receipt(core: _BirthCore, request: BirthRequest, instant: datetime):
    from executor_birth_producer_store import _verify_claimed
    return _verify_claimed(request.producer_receipt, registry=core.producer_registry,
                           now=instant, db_path=core.producer_db)


@dataclass(frozen=True, slots=True)
class BirthRuntimeBundle:
    """One immutable publication unit for every productive Birth dependency."""

    core: _BirthCore
    producer_factories: Mapping[object, Callable[["BirthIntent"], BirthRequest]]
    reattestation_factory: Callable[[object], object]
    author_verifier_keys: Mapping[str, Ed25519PublicKey]
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _RUNTIME_SEAL or self.core._seal is not _CORE_SEAL:
            raise ValueError("birth_runtime_bundle_untrusted")
        factories = dict(self.producer_factories)
        author_verifiers = dict(self.author_verifier_keys)
        if (
            not factories
            or any(not callable(value) for value in factories.values())
            or not callable(self.reattestation_factory)
            or not author_verifiers
            or any(
                not isinstance(key_id, str) or not key_id
                or not isinstance(verifier, Ed25519PublicKey)
                for key_id, verifier in author_verifiers.items()
            )
        ):
            raise ValueError("birth_runtime_bundle_invalid")
        object.__setattr__(self, "producer_factories", MappingProxyType(factories))
        object.__setattr__(
            self, "author_verifier_keys", MappingProxyType(author_verifiers),
        )


_RUNTIME_SEAL = object()
_RUNTIME_LOCK = threading.Lock()
_RUNTIME_BUNDLE: BirthRuntimeBundle | None = None


def _assemble_birth_runtime_bundle(
    core: _BirthCore,
    producer_factories: Mapping["_ProducerCapability", Callable[["BirthIntent"], BirthRequest]],
    reattestation_factory: Callable[[object], object],
    *,
    author_verifier_keys: Mapping[str, Ed25519PublicKey],
) -> BirthRuntimeBundle:
    """Bootstrap primitive; its inputs must already be fully validated."""
    from executor_birth_intent import _is_producer_capability
    if not producer_factories or any(
        not _is_producer_capability(capability) for capability in producer_factories
    ):
        raise ValueError("birth_producer_capability_untrusted")
    return BirthRuntimeBundle(
        core, producer_factories, reattestation_factory,
        author_verifier_keys, _RUNTIME_SEAL,
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


def _runtime_author_trusted_publics_v1() -> tuple | None:
    """Expose only the public author ring of the installed sealed runtime."""
    bundle = _runtime_bundle_snapshot()
    if bundle is None:
        return None
    return tuple(sorted(bundle.author_verifier_keys.items()))


def _validate_synth_tests(data):
    """Use the sealed backend without exporting the bundle to a producer."""
    from executor_birth_functional import SynthTestData, SynthTestReport, _run_synth_tests
    if type(data) is not SynthTestData:
        raise ValueError("synth_test_data_invalid")
    bundle = _runtime_bundle_snapshot()
    if bundle is None:
        return SynthTestReport(error_code="test_environment_unavailable")
    shadow = bundle.core.shadow_dependencies
    return _run_synth_tests(
        data, linux_registry=shadow.linux_sandbox_registry,
        windows_registry=shadow.windows_sandbox_registry,
    )


def _execute_intent_with_capability(
    intent: "BirthIntent", capability: "_ProducerCapability",
) -> BirthResult:
    from executor_birth_intent import BirthIntent, _is_producer_capability, _PROMOTER_QUARANTINE
    if not isinstance(intent, BirthIntent):
        raise ValueError("birth_intent_invalid")
    if not _is_producer_capability(capability):
        raise ValueError("birth_producer_capability_untrusted")
    if capability is _PROMOTER_QUARANTINE:
        raise ValueError("birth_quarantine_execution_required")
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


def _quarantine_execution_with_bundle(execution, bundle):
    """Fixed-owner composition; a private seam for isolated integration tests."""
    from executor_birth_intent import BirthIntent, _PROMOTER_QUARANTINE
    from executor_birth_lifecycle import LifecycleError, LifecyclePublication
    from executor_birth_quarantine import quarantine_reason

    reason = quarantine_reason(execution)
    factory = bundle.producer_factories.get(_PROMOTER_QUARANTINE)
    if factory is None:
        raise LifecycleError("birth_quarantine_capability_unavailable")
    core = bundle.core
    with core.commit_publisher.quarantine_candidate(execution) as (ref, stage):
        request = factory(BirthIntent(stage, ref.contract_id, reason))
        result = _execute(request, core, quarantine_execution=execution)
        if result.error_code is not None or result.publication is None:
            raise LifecycleError(result.error_code or "birth_quarantine_not_published")
        publication, encoded = _verified_postcondition(
            core.postcondition_verifier(request, result.publication, None),
        )
        if publication != result.publication or encoded is None:
            raise LifecycleError("birth_quarantine_reread_invalid")
        admission = verify_admission_receipt(encoded, verifier_keys=core.admission_verifier_keys)
        if (admission.approved_lifecycle is not ApprovedLifecycle.QUARANTINED
                or admission.predecessor_id != execution.generation_id
                or admission.check_results["quarantine_execution"].evidence_hash != execution.receipt_id):
            raise LifecycleError("birth_quarantine_reread_invalid")
        return LifecyclePublication(admission, encoded, publication.current_generation_id,
                                    publication.previous_generation_id, admission.approved_lifecycle)


def _quarantine_execution_with_runtime(execution):
    from executor_birth_lifecycle import load_f5_activation
    from executor_birth_bootstrap import bootstrap_birth_runtime

    load_f5_activation()
    bundle = _runtime_bundle_snapshot() or bootstrap_birth_runtime()
    return _quarantine_execution_with_bundle(execution, bundle)


def _retire_with_runtime(contract_id, expected_generation_id, reason):
    """Administrative retirement reuses the installed authority, not F5."""
    from executor_birth_bootstrap import bootstrap_birth_runtime

    bundle = _runtime_bundle_snapshot() or bootstrap_birth_runtime()
    return bundle.core.commit_publisher.retire(contract_id, expected_generation_id, reason)


def _birth_executor_for_test(request: BirthRequest, *, _core: _BirthCore) -> BirthResult:
    return _execute(request, _core)


# ---------------------------------------------------------------------------
# V2 reattestation postcondition.
#
# One reattestation produces two objects: the V2 admission receipt in the
# contract store and the terminal Producer registration.  They are two
# representations of a single act, so the postcondition is not "both exist" but
# "both name the same act": same contract, same generation, same admission
# context, read back from the durable stores rather than from what the caller
# believed it wrote.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReattestationPostconditionV2:
    contract_id: str
    generation_id: str
    admission_context_id: str
    admission_receipt_hash: str
    producer_receipt_hash: str


def verify_reattestation_postcondition_v2(
    ref,
    *,
    request: object,
    producer_receipt: bytes,
    verify_admission: Callable[[bytes], object],
    authenticate_terminal: Callable[[bytes, bytes], str],
    registry: object,
    binding: object,
    now: datetime,
    producer_db_path: Path,
    trusted_publics,
    store_root=None,
    lock_timeout: float | None = None,
) -> ReattestationPostconditionV2:
    """Read both representations back and require them to name one act.

    Nothing here trusts what the writer reported.  The admission receipt is
    re-read from the store and re-verified, the Producer registration is
    re-read from its durable transaction and its terminal envelope
    re-authenticated, and only then are the two compared against the sealed
    request and against each other.
    """
    from contract_store import (
        admission_receipt_hash, read_current_birth_receipt_v2,
    )
    from executor_birth_producer_context import ProducerRequestV2
    from executor_birth_producer_store import (
        producer_receipt_hash, verify_terminal_registration_v2,
    )

    # Exact type: a subclass would skip the sealed constructor entirely.
    if type(request) is not ProducerRequestV2:
        raise ValueError("birth_postcondition_request_untrusted")
    if not callable(verify_admission):
        raise ValueError("birth_postcondition_verifier_invalid")

    read_kwargs = {"trusted_publics": trusted_publics, "store_root": store_root}
    if lock_timeout is not None:
        read_kwargs["lock_timeout"] = lock_timeout
    encoded = read_current_birth_receipt_v2(ref, request=request, **read_kwargs)
    if encoded is None:
        raise ValueError("birth_postcondition_admission_missing")

    try:
        receipt = verify_admission(encoded)
    except Exception as exc:
        raise ValueError("birth_postcondition_admission_unauthenticated") from exc
    for field, wanted in (
        ("contract_id", request.contract_id),
        ("generation_id", request.generation_id),
        ("admission_context_id", request.admission_context_id),
    ):
        actual = getattr(receipt, field, None)
        if getattr(actual, "value", actual) != wanted:
            raise ValueError(f"birth_postcondition_admission_binding: {field}")

    claim = verify_terminal_registration_v2(
        producer_receipt, request=request, registry=registry, binding=binding,
        now=now, db_path=producer_db_path,
        authenticate_terminal=authenticate_terminal,
    )
    if claim.request_id != request.request_id:
        raise ValueError("birth_postcondition_producer_binding")

    return ReattestationPostconditionV2(
        request.contract_id,
        request.generation_id,
        request.admission_context_id,
        admission_receipt_hash(encoded),
        producer_receipt_hash(producer_receipt),
    )

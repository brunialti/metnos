"""Fail-closed observational Birth orchestration for RM-0008 F3.

This module intentionally has no publisher, signer, receipt-store or lifecycle
writer dependency.  It owns the candidate snapshot while checks run and emits
only an immutable report describing the decision that a later commit phase
could act on.
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, TYPE_CHECKING

from executor_birth import ObservedCandidate, observe_candidate
from executor_birth_identity import AdmissionContextV1, ExecutorOrigin, RevisionAuthor
from manifest_inventory import ContractId
from executor_birth_approval import ApprovalEvidence, ApprovalSubject, approval_evidence_hash, validate_approval
from executor_birth_properties import PropertyStatus
from executor_birth_property_runner import (
    ObservedPropertyRunner, PropertyCandidateProfile, PropertyRunner,
    run_applicable_properties,
)
from executor_birth_runner import WindowsSandboxRegistry
from executor_birth_semantic_review import (
    IndependentEvidence, ReviewPolicyV1, ReviewRiskFacts, SemanticReviewRequest,
    SemanticVerdict, review_candidate_semantics,
)
from executor_standard import validate_for_lifecycle

if TYPE_CHECKING:
    from executor_birth_semantic_authority import SemanticAuthorityProvider


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class RevisionClass(str, Enum):
    FIRST_BIRTH = "first_birth"
    CODE = "code_revision"
    AUTHORITY = "authority_revision"
    CONTRACT = "contract_revision"
    LOCALIZATION = "localization_revision"
    EQUIVALENT = "equivalent_republish"
    PROMOTION = "promotion_revision"
    REACTIVATION = "reactivation_revision"
    REATTESTATION = "reattestation"


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class BirthOutcome(str, Enum):
    ADMITTED = "admitted"
    PREEXERCISE = "preexercise"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True, slots=True)
class RevisionFacts:
    """Trusted comparison with the authenticated predecessor, not caller hints."""

    first_birth: bool = False
    promotion: bool = False
    reactivation: bool = False
    reattestation: bool = False
    code_changed: bool = False
    authority_changed: bool = False
    contract_changed: bool = False
    linguistic_surface_changed: bool = False
    localization_proof_valid: bool = False
    semantic_core_unchanged: bool = False
    exact_republish: bool = False

    def __post_init__(self) -> None:
        state_modes = sum((self.first_birth, self.promotion, self.reactivation, self.reattestation))
        if state_modes > 1:
            raise ValueError("revision_state_ambiguous")


@dataclass(frozen=True, slots=True)
class RevisionDecision:
    revision_class: RevisionClass
    changed_dimensions: tuple[str, ...]


def classify_revision(facts: RevisionFacts) -> RevisionDecision:
    """Apply the conservative RM precedence and retain the complete union."""
    dimensions = tuple(name for name, changed in (
        ("code", facts.code_changed),
        ("authority", facts.authority_changed),
        ("contract", facts.contract_changed),
        ("linguistic_surface", facts.linguistic_surface_changed),
    ) if changed)
    if facts.first_birth:
        kind = RevisionClass.FIRST_BIRTH
    elif facts.reactivation:
        kind = RevisionClass.REACTIVATION
    elif facts.promotion:
        kind = RevisionClass.PROMOTION
    elif facts.reattestation:
        kind = RevisionClass.REATTESTATION
    elif facts.exact_republish and not dimensions:
        kind = RevisionClass.EQUIVALENT
    elif facts.code_changed:
        kind = RevisionClass.CODE
    elif facts.authority_changed:
        kind = RevisionClass.AUTHORITY
    elif facts.contract_changed:
        kind = RevisionClass.CONTRACT
    elif (
        facts.linguistic_surface_changed
        and facts.localization_proof_valid
        and facts.semantic_core_unchanged
    ):
        kind = RevisionClass.LOCALIZATION
    else:
        # No positive proof of equivalence/localization is never classified as
        # the less restrictive class.
        kind = RevisionClass.CONTRACT
    return RevisionDecision(kind, dimensions)


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    rule_version: str
    status: CheckStatus
    error_code: str | None
    evidence_hash: str
    redacted_detail: str

    def __post_init__(self) -> None:
        if not self.check_id or not self.rule_version:
            raise ValueError("check_result_invalid")
        if not isinstance(self.status, CheckStatus):
            raise ValueError("check_status_invalid")
        if not isinstance(self.evidence_hash, str) or not _DIGEST_RE.fullmatch(self.evidence_hash):
            raise ValueError("check_evidence_invalid")
        if self.status is CheckStatus.PASSED and self.error_code is not None:
            raise ValueError("check_result_invalid")
        if self.status in {CheckStatus.FAILED, CheckStatus.UNAVAILABLE} and not self.error_code:
            raise ValueError("check_result_invalid")


Applicability = Callable[[RevisionDecision, ObservedCandidate], bool]
CheckRunner = Callable[[ObservedCandidate, RevisionDecision], CheckResult]


@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str
    rule_version: str
    mandatory: bool
    applicable: Applicability
    run: CheckRunner

    def __post_init__(self) -> None:
        if not self.check_id or not self.rule_version or type(self.mandatory) is not bool:
            raise ValueError("check_spec_invalid")
        if not callable(self.applicable) or not callable(self.run):
            raise ValueError("check_spec_invalid")


@dataclass(frozen=True, slots=True)
class BirthReport:
    schema_version: int
    contract_id: ContractId
    candidate_id: str | None
    semantic_core_id: str | None
    admission_context_id: str | None
    revision_class: RevisionClass | None
    changed_dimensions: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    outcome: BirthOutcome
    error_code: str | None
    publisher_call_count: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.publisher_call_count != 0:
            raise ValueError("birth_report_invalid")


Observer = Callable[..., ObservedCandidate]


_DEPENDENCY_SEAL = object()


@dataclass(frozen=True, slots=True)
class _BirthDependencies:
    """Trusted runtime services. Construction is guarded by the module seal."""
    observer: Observer
    property_runner: PropertyRunner | None
    windows_sandbox_registry: WindowsSandboxRegistry | None
    semantic_policy: ReviewPolicyV1 | None
    semantic_risk: ReviewRiskFacts | None
    independent_evidence: tuple[IndependentEvidence, ...]
    semantic_authority: "SemanticAuthorityProvider | None"
    approval_subject: ApprovalSubject | None
    approval_evidence: ApprovalEvidence | None
    now: datetime | None
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _DEPENDENCY_SEAL:
            raise ValueError("birth_dependencies_untrusted")


def _sealed_dependencies_for_test(**overrides: object) -> _BirthDependencies:
    """Internal test seam; production callers never provide a check catalog."""
    values: dict[str, object] = {
        "observer": observe_candidate, "property_runner": None,
        "windows_sandbox_registry": None,
        "semantic_policy": None, "semantic_risk": None,
        "independent_evidence": (), "semantic_authority": None, "approval_subject": None,
        "approval_evidence": None, "now": None, "_seal": _DEPENDENCY_SEAL,
    }
    if set(overrides) - set(values):
        raise ValueError("birth_dependencies_invalid")
    values.update(overrides)
    return _BirthDependencies(**values)  # type: ignore[arg-type]


def _assemble_production_dependencies(*, semantic_authority=None,
                                      windows_sandbox_registry=None) -> _BirthDependencies:
    """Single core-owned assembler; it cannot alter the fixed check catalog."""
    # The runner is constructed only after Birth owns the observation.  Keeping
    # it out of this process-global dependency object prevents an unbound or
    # caller-selected runner from exercising different candidate bytes.
    return _sealed_dependencies_for_test(
        property_runner=None, semantic_authority=semantic_authority,
        windows_sandbox_registry=windows_sandbox_registry,
    )


_PRODUCTION_DEPENDENCIES = _assemble_production_dependencies()


def _manifest(observed: ObservedCandidate) -> dict[str, object]:
    value = tomllib.loads(observed.snapshot.manifest_bytes.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest_root_invalid")
    return value


def _profile(manifest: Mapping[str, object]) -> PropertyCandidateProfile:
    output = manifest.get("output")
    output_map = output if isinstance(output, Mapping) else {}
    properties = output_map.get("properties")
    schema: list[tuple[str, str]] = []
    if isinstance(properties, Mapping):
        for key, declaration in sorted(properties.items()):
            if isinstance(key, str) and isinstance(declaration, Mapping) and isinstance(declaration.get("type"), str):
                schema.append((key, declaration["type"]))
    args = manifest.get("args")
    args_map = args if isinstance(args, Mapping) else {}
    arg_properties = args_map.get("properties")
    arg_properties = arg_properties if isinstance(arg_properties, Mapping) else {}
    execution = manifest.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    names = {name for name, _ in schema}
    return PropertyCandidateProfile(
        output_schema=tuple(schema), collection_output=bool(names & {"entries", "results"}),
        limit_input="limit" in arg_properties, truncation_declared="truncated" in names,
        revertible=manifest.get("revertible") is True or manifest.get("reversible") is True,
        destructive_with_undo=(manifest.get("revertible") is True and execution.get("effect") == "mutating"),
        entries_and_results={"entries", "results"}.issubset(names),
    )


def _standard_check(observed: ObservedCandidate, _decision: RevisionDecision, _deps: _BirthDependencies) -> CheckResult:
    findings = validate_for_lifecycle(_manifest(observed), require_declaration=True)
    evidence = _shadow_evidence("manifest-standard", observed.identities.candidate_id,
                                *(f"{item.code}:{item.message}" for item in findings))
    if findings:
        return CheckResult("manifest_standard", "v1", CheckStatus.FAILED,
                           "contract_nonconformant", evidence, findings[0].code)
    return CheckResult("manifest_standard", "v1", CheckStatus.PASSED, None, evidence, "valid")


def _property_check(observed: ObservedCandidate, _decision: RevisionDecision, deps: _BirthDependencies) -> CheckResult:
    runner = deps.property_runner or ObservedPropertyRunner(
        observed, windows_registry=deps.windows_sandbox_registry,
    )
    evidence = run_applicable_properties(_profile(_manifest(observed)), _runner=runner)
    digest = _shadow_evidence("properties", observed.identities.candidate_id,
                              *(f"{item.property_id}:{item.case_id}:{item.status.value}:{item.output_hash}" for item in evidence))
    failed = next((item for item in evidence if item.status in {PropertyStatus.FAILED, PropertyStatus.UNAVAILABLE}), None)
    if failed:
        return CheckResult("properties", "v1", CheckStatus.FAILED, failed.error_code, digest, failed.property_id)
    return CheckResult("properties", "v1", CheckStatus.PASSED, None, digest, f"{len(evidence)} cases")


def _semantic_check(observed: ObservedCandidate, _decision: RevisionDecision, deps: _BirthDependencies) -> CheckResult:
    request = SemanticReviewRequest(
        observed.identities.candidate_id, observed.identities.admission_context_id,
        f"{observed.executor_origin.value}.{observed.revision_authorship.value}",
        observed.snapshot.manifest_bytes, observed.snapshot.language_state_bytes,
        MappingProxyType(dict(observed.snapshot.code_files)),
    )
    if deps.semantic_authority is not None:
        policy, risk, evidence = deps.semantic_authority.inputs_for(request)
    elif deps.semantic_policy is not None and deps.semantic_risk is not None:
        # This branch is reachable only through the sealed unit-test seam.
        policy, risk, evidence = deps.semantic_policy, deps.semantic_risk, deps.independent_evidence
    else:
        raise RuntimeError("semantic_review_unavailable")
    review = review_candidate_semantics(
        request, independent_evidence=evidence, policy=policy, risk_facts=risk,
    )
    if review.operational_verdict is SemanticVerdict.ALIGNED:
        return CheckResult("semantic_review", "v1", CheckStatus.PASSED, None,
                           review.review_evidence_hash, "aligned")
    code = "semantic_review_uncertain" if review.operational_verdict is SemanticVerdict.UNCERTAIN else "semantic_misaligned"
    return CheckResult("semantic_review", "v1", CheckStatus.FAILED, code,
                       review.review_evidence_hash, review.operational_verdict.value)


def _approval_check(observed: ObservedCandidate, _decision: RevisionDecision, deps: _BirthDependencies) -> CheckResult:
    if deps.approval_subject is None or deps.approval_evidence is None or deps.now is None:
        return CheckResult("approval", "v1", CheckStatus.FAILED, "approval_required",
                           _shadow_evidence("approval", observed.identities.candidate_id), "missing")
    subject = deps.approval_subject
    if (subject.candidate_id, subject.semantic_core_id, subject.admission_context_id) != (
        observed.identities.candidate_id, observed.identities.semantic_core_id,
        observed.identities.admission_context_id,
    ):
        raise ValueError("approval_subject_mismatch")
    validate_approval(subject, deps.approval_evidence, now=deps.now)
    return CheckResult("approval", "v1", CheckStatus.PASSED, None,
                       approval_evidence_hash(deps.approval_evidence), "approved")


def _always(_decision: RevisionDecision, _observed: ObservedCandidate) -> bool:
    return True


def _semantic_applies(_decision: RevisionDecision, observed: ObservedCandidate) -> bool:
    return observed.revision_authorship is RevisionAuthor.MODEL or observed.executor_origin is ExecutorOrigin.IMPORTED


def _approval_applies(decision: RevisionDecision, _observed: ObservedCandidate) -> bool:
    return _observed.executor_origin is ExecutorOrigin.SYNTHESIZED or decision.revision_class in {
        RevisionClass.AUTHORITY, RevisionClass.PROMOTION, RevisionClass.REACTIVATION,
    }


_CHECK_CATALOG_V1 = (
    ("manifest_standard", "v1", True, _always, _standard_check),
    ("properties", "v1", True, _always, _property_check),
    ("semantic_review", "v1", True, _semantic_applies, _semantic_check),
    ("approval", "v1", True, _approval_applies, _approval_check),
)


def _shadow_evidence(*parts: str) -> str:
    framed = b"".join(len(part.encode("utf-8")).to_bytes(8, "big") + part.encode("utf-8")
                      for part in parts)
    return "sha256:" + hashlib.sha256(b"metnos.executor-birth.shadow-evidence/v1\0" + framed).hexdigest()


def _unavailable(spec: CheckSpec, exc: Exception) -> CheckResult:
    # Exception text may contain secrets or paths and is deliberately excluded.
    return CheckResult(spec.check_id, spec.rule_version, CheckStatus.UNAVAILABLE,
                       "check_unavailable",
                       _shadow_evidence(spec.check_id, spec.rule_version, type(exc).__name__),
                       type(exc).__name__)


def _outcome(results: Iterable[tuple[CheckSpec, CheckResult]], origin: ExecutorOrigin) -> tuple[BirthOutcome, str | None]:
    for spec, result in results:
        if not spec.mandatory:
            continue
        if result.status in {CheckStatus.FAILED, CheckStatus.UNAVAILABLE}:
            if result.error_code in {"semantic_review_uncertain", "approval_required"}:
                return BirthOutcome.NEEDS_HUMAN, result.error_code
            if result.error_code == "candidate_quarantined":
                return BirthOutcome.QUARANTINED, result.error_code
            return BirthOutcome.REJECTED, result.error_code
    if origin is ExecutorOrigin.SYNTHESIZED:
        return BirthOutcome.PREEXERCISE, None
    return BirthOutcome.ADMITTED, None


def _observe_birth(
    source_root: object,
    *,
    contract_id: ContractId,
    executor_origin: ExecutorOrigin,
    revision_authorship: RevisionAuthor,
    objective_hash: str,
    admission_context: AdmissionContextV1,
    revision_facts: RevisionFacts,
    private_parent: object | None = None,
    _dependencies: _BirthDependencies,
) -> BirthReport:
    """Run an F3 shadow decision.  There is intentionally no publisher hook."""
    decision = classify_revision(revision_facts)
    if not isinstance(_dependencies, _BirthDependencies) or _dependencies._seal is not _DEPENDENCY_SEAL:
        raise ValueError("birth_dependencies_untrusted")
    specs = tuple(CheckSpec(check_id, version, mandatory, applicable,
                            lambda observed, decision, runner=runner: runner(observed, decision, _dependencies))
                  for check_id, version, mandatory, applicable, runner in _CHECK_CATALOG_V1)
    observed: ObservedCandidate | None = None
    results: list[tuple[CheckSpec, CheckResult]] = []
    try:
        observed = _dependencies.observer(
            source_root,
            contract_id=contract_id,
            executor_origin=executor_origin,
            revision_authorship=revision_authorship,
            objective_hash=objective_hash,
            admission_context=admission_context,
            private_parent=private_parent,
        )
        for spec in specs:
            try:
                applies = spec.applicable(decision, observed)
                if type(applies) is not bool:
                    raise TypeError("applicability_not_boolean")
                if not applies:
                    result = CheckResult(spec.check_id, spec.rule_version,
                                         CheckStatus.NOT_APPLICABLE, None,
                                         _shadow_evidence(
                                             spec.check_id, spec.rule_version,
                                             observed.identities.candidate_id,
                                             observed.identities.admission_context_id,
                                             decision.revision_class.value,
                                         ),
                                         "predicate_false")
                else:
                    result = spec.run(observed, decision)
                    if not isinstance(result, CheckResult):
                        raise TypeError("check_result_untyped")
                    if (result.check_id, result.rule_version) != (spec.check_id, spec.rule_version):
                        raise ValueError("check_result_binding_invalid")
            except Exception as exc:
                result = _unavailable(spec, exc)
            results.append((spec, result))
            if spec.mandatory and result.status in {CheckStatus.FAILED, CheckStatus.UNAVAILABLE}:
                break
        outcome, error = _outcome(results, executor_origin)
        identities = observed.identities
        return BirthReport(
            1, contract_id, identities.candidate_id, identities.semantic_core_id,
            identities.admission_context_id, decision.revision_class,
            decision.changed_dimensions, tuple(result for _, result in results),
            outcome, error,
        )
    except Exception:
        return BirthReport(1, contract_id, None, None, None, decision.revision_class,
                           decision.changed_dimensions, (), BirthOutcome.REJECTED,
                           "candidate_observation_unavailable")
    finally:
        if observed is not None:
            observed.close()


def observe_birth(
    source_root: object,
    *,
    contract_id: ContractId,
    executor_origin: ExecutorOrigin,
    revision_authorship: RevisionAuthor,
    objective_hash: str,
    admission_context: AdmissionContextV1,
    revision_facts: RevisionFacts,
    private_parent: object | None = None,
) -> BirthReport:
    """Run the fixed productive V1 catalog with core-owned dependencies."""
    return _observe_birth(
        source_root, contract_id=contract_id, executor_origin=executor_origin,
        revision_authorship=revision_authorship, objective_hash=objective_hash,
        admission_context=admission_context, revision_facts=revision_facts,
        private_parent=private_parent, _dependencies=_PRODUCTION_DEPENDENCIES,
    )


def _observe_birth_for_test(
    source_root: object, *, _dependencies: _BirthDependencies, **request: object,
) -> BirthReport:
    """Internal sealed entry point for fault injection and adapter tests."""
    return _observe_birth(source_root, _dependencies=_dependencies, **request)  # type: ignore[arg-type]

"""Sealed, non-publishing F4 reattestation of an exact current generation.

Reattestation is deliberately separate from normal Birth publication.  It
copies the bytes of an authenticated current generation, consumes one fresh
ProducerReceipt, reruns every applicable F3 check, and may write only the
AdmissionReceipt for that same generation.  The contract store rechecks the
current pointer and authenticated bytes at the persistence linearization
point; no API in this module can install a generation or move ``current``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from executor_birth import ObservedCandidate
from executor_birth_cutover import CurrentGeneration
from executor_birth_identity import (
    CandidateIdentityInput, admission_context_id, compute_candidate_identities,
)
from executor_birth_operational import (
    _BirthCore, _BorrowedObserved, _receipt_checks, candidate_source_id,
)
from executor_birth_predecessor import AdmissionContextPin
from executor_birth_producer_store import (
    ProducerReceiptBinding, claim_producer_receipt, finalize_producer_receipt,
    producer_receipt_hash,
)
from executor_birth_receipts import (
    AdmissionCheck, AdmissionKind, AdmittedCheckStatus, ApprovedLifecycle,
    RevisionClass as ReceiptRevisionClass, issue_admission_receipt,
    verify_admission_receipt, verify_producer_receipt,
)
from executor_birth_shadow import (
    BirthOutcome, CheckStatus, RevisionClass as ShadowRevisionClass,
    RevisionFacts, _BirthDependencies,
    _observe_birth_for_test,
)
from executor_birth_property_runner import ObservedPropertyRunner


class BirthReattestationError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ReattestationRequest:
    request_id: str
    current: CurrentGeneration
    producer_receipt: bytes
    actor: str
    reason: str

    def __post_init__(self) -> None:
        _digest(self.request_id, "request_id")
        if not isinstance(self.current, CurrentGeneration):
            raise BirthReattestationError("birth_reattestation_request_invalid", "current")
        if not isinstance(self.producer_receipt, bytes) or not self.producer_receipt:
            raise BirthReattestationError("birth_reattestation_request_invalid", "producer_receipt")
        if any(not isinstance(value, str) or not value or "\x00" in value
               for value in (self.actor, self.reason)):
            raise BirthReattestationError("birth_reattestation_request_invalid", "text")

    @property
    def manifest_ref(self):
        return self.current.ref

    @property
    def approval_refs(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True, slots=True)
class ReattestationResult:
    request_id: str
    contract_id: str
    generation_id: str
    receipt: bytes
    repeated: bool


Capture = Callable[[CurrentGeneration], object]
Persist = Callable[[CurrentGeneration, bytes, Mapping[str, object]], bytes]
ReadReceipt = Callable[[CurrentGeneration], bytes | None]
_SEAL = object()


@dataclass(frozen=True, slots=True)
class _ReattestationCore:
    birth: _BirthCore
    capture: Capture
    persist: Persist
    read_receipt: ReadReceipt
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _SEAL or not isinstance(self.birth, _BirthCore):
            raise BirthReattestationError("birth_reattestation_core_untrusted")
        if any(not callable(value) for value in (self.capture, self.persist, self.read_receipt)):
            raise BirthReattestationError("birth_reattestation_core_invalid")


def _digest(value: object, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 71
            or not value.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in value[7:])):
        raise BirthReattestationError("birth_reattestation_request_invalid", field)
    return value


def _hash(domain: bytes, *values: bytes) -> str:
    framed = bytearray(domain)
    for value in values:
        framed.extend(len(value).to_bytes(8, "big"))
        framed.extend(value)
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _sealed_reattestation_core_for_test(
    *, birth: _BirthCore, capture: Capture, persist: Persist,
    read_receipt: ReadReceipt,
) -> _ReattestationCore:
    return _ReattestationCore(birth, capture, persist, read_receipt, _SEAL)


def _assemble_reattestation_core(birth: _BirthCore) -> _ReattestationCore:
    """Assemble the productive store authority from sealed Birth options."""
    from contract_store import (
        acquire_current_reattestation_snapshot, persist_current_reattestation_receipt,
        read_current_birth_receipt,
    )
    options = dict(birth.publisher_options)
    trusted = options.get("trusted_publics")
    if trusted is None:
        raise BirthReattestationError("birth_reattestation_trust_missing")
    trusted = tuple(trusted)
    store_root = options.get("store_root")
    lock_timeout = float(options.get("lock_timeout", 10.0))
    replace_timeout = float(options.get("replace_timeout", 10.0))

    def capture(item: CurrentGeneration):
        return acquire_current_reattestation_snapshot(
            item.ref, item.generation_id, trusted_publics=trusted,
            store_root=store_root, lock_timeout=lock_timeout,
        )

    def persist(item: CurrentGeneration, encoded: bytes, expected: Mapping[str, object]):
        return persist_current_reattestation_receipt(
            item.ref, item.generation_id, encoded,
            verifier=lambda wire: verify_admission_receipt(
                wire, verifier_keys=birth.admission_verifier_keys,
            ),
            expected_bindings=expected, trusted_publics=trusted,
            store_root=store_root, lock_timeout=lock_timeout,
            replace_timeout=replace_timeout,
        )

    def read(item: CurrentGeneration):
        return read_current_birth_receipt(
            item.ref, item.generation_id, trusted_publics=trusted,
            store_root=store_root, lock_timeout=lock_timeout,
        )

    return _ReattestationCore(birth, capture, persist, read, _SEAL)


def _expected(request: ReattestationRequest, observed: ObservedCandidate,
              producer_hash: str) -> Mapping[str, object]:
    return MappingProxyType({
        "contract_id": request.current.ref.contract_id.value,
        "generation_id": request.current.generation_id,
        "candidate_id": observed.identities.candidate_id,
        "semantic_core_id": observed.identities.semantic_core_id,
        "admission_context_id": observed.identities.admission_context_id,
        "birth_request_id": request.request_id,
        "predecessor_id": request.current.generation_id,
        "producer_receipt_hash": producer_hash,
        "revision_class": ReceiptRevisionClass.REATTESTATION,
        "kind": AdmissionKind.REATTESTATION,
    })


def _verify_existing(encoded: bytes, request: ReattestationRequest,
                     observed: ObservedCandidate, core: _ReattestationCore) -> bytes:
    try:
        receipt = verify_admission_receipt(
            encoded, verifier_keys=core.birth.admission_verifier_keys,
        )
        for field, wanted in _expected(
            request, observed, producer_receipt_hash(request.producer_receipt),
        ).items():
            actual = getattr(receipt, field)
            if getattr(actual, "value", actual) != getattr(wanted, "value", wanted):
                raise BirthReattestationError("birth_reattestation_receipt_exists")
    except BirthReattestationError:
        raise
    except Exception as exc:
        raise BirthReattestationError("birth_reattestation_receipt_invalid") from exc
    return encoded


def _execute(request: ReattestationRequest, core: _ReattestationCore) -> ReattestationResult:
    if not isinstance(core, _ReattestationCore) or core._seal is not _SEAL:
        raise BirthReattestationError("birth_reattestation_core_untrusted")
    birth = core.birth
    instant = birth.now().astimezone(timezone.utc)
    producer = verify_producer_receipt(
        request.producer_receipt, registry=birth.producer_registry, now=instant,
    )
    snapshot = core.capture(request.current)
    observed: ObservedCandidate | None = None
    binding: ProducerReceiptBinding | None = None
    claimed = False
    persistence_started = False
    try:
        context, context_pin = birth.context_resolver(request)  # type: ignore[arg-type]
        if (not isinstance(context_pin, AdmissionContextPin)
                or context_pin.admission_context_id != admission_context_id(context)
                or birth.context_epoch_resolver() != context_pin.context_epoch):
            raise BirthReattestationError("birth_context_pin_invalid")
        identities = compute_candidate_identities(CandidateIdentityInput(
            contract_id=request.current.ref.contract_id,
            manifest_bytes=snapshot.manifest_bytes,
            language_state_bytes=snapshot.language_state_bytes,
            code_files=snapshot.code_files,
            executor_origin=producer.executor_origin,
            revision_authorship=producer.revision_authorship,
            objective_hash=producer.objective_hash,
        ), context)
        observed = ObservedCandidate(
            request.current.ref.contract_id, snapshot, identities,
            producer.executor_origin, producer.revision_authorship,
            producer.objective_hash,
        )
        binding = ProducerReceiptBinding(
            producer.objective_hash, candidate_source_id(observed),
            producer.executor_origin, producer.revision_authorship,
        )
        claim = claim_producer_receipt(
            request.producer_receipt, registry=birth.producer_registry,
            binding=binding, request_id=request.request_id, now=instant,
            db_path=birth.producer_db,
        )
        claimed = True
        if claim.state == "rejected":
            raise BirthReattestationError(
                claim.rejection_code or "birth_reattestation_previously_rejected",
            )
        existing = core.read_receipt(request.current)
        if claim.state == "committed":
            if existing is None:
                raise BirthReattestationError("birth_reattestation_receipt_not_durable")
            encoded = _verify_existing(existing, request, observed, core)
            if claim.result_binding != _hash(
                b"metnos.executor-birth.reattestation-result/v1\0", encoded,
            ):
                raise BirthReattestationError("birth_reattestation_replay_mismatch")
            return ReattestationResult(
                request.request_id, request.current.ref.contract_id.value,
                request.current.generation_id, encoded, True,
            )
        if existing is not None:
            # Crash recovery: persistence is the authority's durable point.
            # The same request/ProducerReceipt may finish its still-live claim;
            # an unrelated existing admission remains an immutable conflict.
            encoded = _verify_existing(existing, request, observed, core)
            result_binding = _hash(
                b"metnos.executor-birth.reattestation-result/v1\0", encoded,
            )
            finalize_producer_receipt(
                request.producer_receipt, registry=birth.producer_registry,
                binding=binding, request_id=request.request_id, now=instant,
                db_path=birth.producer_db, result_binding=result_binding,
            )
            return ReattestationResult(
                request.request_id, request.current.ref.contract_id.value,
                request.current.generation_id, encoded, True,
            )

        shadow = birth.shadow_dependencies
        property_runner = shadow.property_runner or ObservedPropertyRunner(
            observed, windows_registry=shadow.windows_sandbox_registry,
            linux_registry=shadow.linux_sandbox_registry,
        )
        approval_subject, approval_evidence = birth.approval_resolver(
            request, observed, ShadowRevisionClass.REATTESTATION, instant,  # type: ignore[arg-type]
        )
        dependencies = replace(
            shadow,
            observer=lambda *_args, **_kwargs: _BorrowedObserved(observed),
            property_runner=property_runner,
            approval_subject=approval_subject, approval_evidence=approval_evidence,
            now=instant,
        )
        report = _observe_birth_for_test(
            request.current.ref.manifest_dir,
            contract_id=request.current.ref.contract_id,
            executor_origin=producer.executor_origin,
            revision_authorship=producer.revision_authorship,
            objective_hash=producer.objective_hash,
            admission_context=context,
            revision_facts=RevisionFacts(reattestation=True),
            _dependencies=dependencies,
        )
        if report.outcome not in {BirthOutcome.ADMITTED, BirthOutcome.PREEXERCISE}:
            raise BirthReattestationError(
                report.error_code or "birth_reattestation_checks_failed",
            )
        if birth.context_epoch_resolver() != context_pin.context_epoch:
            raise BirthReattestationError("birth_context_pin_invalid")
        evidence = _hash(
            b"metnos.executor-birth.reattestation-snapshot/v1\0",
            request.current.ref.contract_id.value.encode("utf-8"),
            request.current.generation_id.encode("ascii"),
            observed.identities.candidate_id.encode("ascii"),
        )
        checks = dict(_receipt_checks(report))
        checks["reattestation_current_generation_v1"] = AdmissionCheck(
            "1", AdmittedCheckStatus.PASSED, evidence,
        )
        semantic_hash = next((
            check.evidence_hash for check in report.checks
            if check.check_id == "semantic_review" and check.status is CheckStatus.PASSED
        ), None)
        encoded = issue_admission_receipt(
            policy_version=birth.policy_version,
            contract_id=request.current.ref.contract_id,
            generation_id=request.current.generation_id,
            candidate_id=observed.identities.candidate_id,
            semantic_core_id=observed.identities.semantic_core_id,
            admission_context_id=observed.identities.admission_context_id,
            birth_request_id=request.request_id,
            authoring_journal_hash=evidence,
            predecessor_id=request.current.generation_id,
            producer_receipt_hash=producer_receipt_hash(request.producer_receipt),
            revision_class=ReceiptRevisionClass.REATTESTATION,
            check_results=checks,
            semantic_review_hash=semantic_hash,
            approval_hash=None,
            approved_lifecycle=(
                ApprovedLifecycle.PREEXERCISE
                if report.outcome is BirthOutcome.PREEXERCISE
                else ApprovedLifecycle.ACTIVE
            ),
            kind=AdmissionKind.REATTESTATION,
            issued_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
            key_id=birth.admission_key_id,
            private_key=birth.admission_private_key,
        )
        expected = _expected(
            request, observed, producer_receipt_hash(request.producer_receipt),
        )
        # From this point an error is ambiguous: the store callback may have
        # crossed its durable replace point before reporting a failure.  Keep
        # the ProducerReceipt claim recoverable so an exact retry can trust
        # the authenticated store reread and finish the terminal binding.
        persistence_started = True
        durable = core.persist(request.current, encoded, expected)
        if durable != encoded:
            raise BirthReattestationError("birth_reattestation_receipt_not_durable")
        result_binding = _hash(
            b"metnos.executor-birth.reattestation-result/v1\0", durable,
        )
        finalize_producer_receipt(
            request.producer_receipt, registry=birth.producer_registry,
            binding=binding, request_id=request.request_id, now=instant,
            db_path=birth.producer_db, result_binding=result_binding,
        )
        reread = core.read_receipt(request.current)
        if reread != durable:
            raise BirthReattestationError("birth_reattestation_receipt_not_durable")
        _verify_existing(reread, request, observed, core)
        return ReattestationResult(
            request.request_id, request.current.ref.contract_id.value,
            request.current.generation_id, durable, False,
        )
    except Exception as exc:
        code = getattr(exc, "code", "birth_reattestation_unavailable")
        if claimed and binding is not None and not persistence_started:
            try:
                finalize_producer_receipt(
                    request.producer_receipt, registry=birth.producer_registry,
                    binding=binding, request_id=request.request_id, now=instant,
                    db_path=birth.producer_db, rejection_code=str(code),
                )
            except Exception:
                pass
        if isinstance(exc, BirthReattestationError):
            raise
        raise BirthReattestationError(str(code)) from exc
    finally:
        if observed is not None:
            observed.close()
        else:
            close = getattr(snapshot, "close", None)
            if callable(close):
                close()


def reattest_current_generation(request: ReattestationRequest) -> ReattestationResult:
    """Run productive reattestation using only the installed sealed runtime."""
    from executor_birth_operational import _runtime_bundle_snapshot
    bundle = _runtime_bundle_snapshot()
    if bundle is None:
        raise BirthReattestationError("birth_runtime_bundle_unavailable")
    return _execute(request, _assemble_reattestation_core(bundle.core))


def _reattest_current_for_test(
    request: ReattestationRequest, *, _core: _ReattestationCore,
) -> ReattestationResult:
    return _execute(request, _core)


__all__ = [
    "BirthReattestationError", "ReattestationRequest", "ReattestationResult",
    "reattest_current_generation",
]

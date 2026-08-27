"""Fail-closed productive bootstrap for the RM-0008 Birth boundary.

Secrets are provisioned by the operator.  This module only reads and validates
them; it never creates, rotates, or repairs key material.
"""
from __future__ import annotations

import hashlib
import os
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from executor_birth import observe_candidate
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_producer_table_v1 import (
    executor_origin_v1, producer_author_v1,
)
from executor_birth_intent import BirthIntent, _ProducerCapability, _producer_capabilities_for_bootstrap
from executor_birth_operational import (
    BirthRequest, BirthRuntimeBundle, _assemble_birth_core,
    _assemble_birth_runtime_bundle, _install_birth_runtime_bundle,
    _runtime_bundle_snapshot, approval_scope, candidate_source_id,
)
from executor_birth_producer_store import get_or_issue_producer_receipt
from executor_birth_receipts import IssuerKey, IssuerRegistry, issue_producer_receipt
from executor_birth_shadow import _assemble_production_dependencies
from manifest_inventory import ManifestRef


class BirthBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _ProducerAuthority:
    capability: _ProducerCapability
    issuer_id: str
    key_id: str
    private_key: Ed25519PrivateKey
    author: RevisionAuthor


_BOOT_LOCK = threading.Condition()
_BOOT_STATE = "cold"
_BOOT_ERROR: BaseException | None = None


BIRTH_STATE_BASENAME_V1 = "birth"
PRODUCER_RECEIPTS_BASENAME_V1 = "producer_receipts.sqlite"
APPROVALS_BASENAME_V1 = "approvals.sqlite"


def _secure_state_dir(state_dir: Path) -> Path:
    """Create the durable Birth state directory and refuse a loose one."""
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = state_dir.stat()
        if (not state_dir.is_dir() or state_dir.is_symlink()
                or (os.name != "nt" and info.st_mode & 0o077)):
            raise BirthBootstrapError("birth_state_permissions")
        return state_dir
    except BirthBootstrapError:
        raise
    except OSError as exc:
        raise BirthBootstrapError("birth_state_unavailable") from exc


def _secure_state_db(state_dir: Path, basename: str) -> Path:
    """Create one durable database inside the state directory and check it.

    Receipts and approvals carry the same weight, so they get the same
    treatment from a single entry: two nearly identical helpers standing side
    by side is how the two drifted apart in the first place.
    """
    try:
        path = state_dir / basename
        flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (os.name != "nt" and info.st_mode & 0o077)):
                raise BirthBootstrapError("birth_state_permissions")
        finally:
            os.close(descriptor)
        return path
    except BirthBootstrapError:
        raise
    except OSError as exc:
        raise BirthBootstrapError("birth_state_unavailable") from exc


def _manifest_ref(intent: BirthIntent) -> ManifestRef:
    from manifest_inventory import inventory_authoring_manifests
    inventory = inventory_authoring_manifests()
    if inventory.problems:
        raise BirthBootstrapError("birth_authoring_inventory_invalid")
    matches = tuple(ref for ref in inventory.manifests if ref.contract_id == intent.contract_id)
    if len(matches) != 1:
        raise BirthBootstrapError("birth_authoring_target_unavailable")
    return matches[0]


def _hash(domain: bytes, *parts: str) -> str:
    framed = bytearray(domain)
    for part in parts:
        encoded = part.encode("utf-8")
        framed.extend(len(encoded).to_bytes(8, "big")); framed.extend(encoded)
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _request_factory(authority: _ProducerAuthority, registry: IssuerRegistry,
                     db_path: Path, ttl_seconds: int, now: Callable[[], datetime],
                     context_builder: object):
    def create(intent: BirthIntent) -> BirthRequest:
        if not isinstance(intent, BirthIntent):
            raise BirthBootstrapError("birth_intent_invalid")
        objective = _hash(b"metnos.executor-birth.objective/v1\0", intent.reason, *intent.approval_refs)
        context, _pin = context_builder.preview(intent)
        # The kind of the executor is not a property of who asks for it: it
        # comes from where the manifest of that contract lives, which the
        # inventory already authenticated.
        origin = executor_origin_v1(intent.contract_id.origin)
        observed = observe_candidate(
            intent.candidate_source_root, contract_id=intent.contract_id,
            executor_origin=origin, revision_authorship=authority.author,
            objective_hash=objective, admission_context=context,
        )
        try:
            source_id = candidate_source_id(observed)
        finally:
            observed.close()
        request_id = _hash(
            b"metnos.executor-birth.request/v1\0", authority.issuer_id,
            authority.capability.operation, intent.contract_id.value, objective, source_id,
        )
        instant = now().astimezone(timezone.utc).replace(microsecond=0)
        expires = instant + timedelta(seconds=ttl_seconds)
        def issue() -> bytes:
            return issue_producer_receipt(
                issuer_id=authority.issuer_id, executor_origin=origin,
                revision_authorship=authority.author, objective_hash=objective,
                candidate_source_id=source_id,
                issued_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                nonce=hashlib.sha256(request_id.encode()).hexdigest()[:32],
                key_id=authority.key_id, private_key=authority.private_key,
            )
        receipt = get_or_issue_producer_receipt(
            request_id=request_id, issuer_id=authority.issuer_id,
            capability_id=f"{authority.capability.producer_id}:{authority.capability.operation}",
            contract_id=intent.contract_id.value, objective_hash=objective,
            candidate_source_id=source_id, registry=registry, now=instant,
            db_path=db_path, issue=issue,
        )
        return BirthRequest(
            request_id, _manifest_ref(intent), receipt, authority.issuer_id,
            intent.reason, intent.approval_refs, authority.capability.operation,
            intent.candidate_source_root,
        )
    return create


class _PostconditionAdapter:
    def __init__(self, *, trusted_publics: tuple, verifier_keys: Mapping[str, Ed25519PublicKey],
                 store_root: Path | None = None) -> None:
        self.trusted_publics = trusted_publics
        self.verifier_keys = verifier_keys
        self.store_root = store_root

    def verify(self, request: BirthRequest, expected: object, admission: bytes | None):
        from executor_birth_postcondition import verify_birth_postcondition
        return verify_birth_postcondition(
            request, expected, admission, trusted_publics=self.trusted_publics,
            admission_verifier_keys=self.verifier_keys, store_root=self.store_root,
        )

    def recover_authoring(self) -> None:
        # Execute the same closed recovery matrix as the publisher, under the
        # same lock order, before exposing any productive facade.
        from manifest_inventory import inventory_authoring_manifests
        from executor_birth_authoring import (
            advance_version, authoring_paths, authoring_token, authoring_tree_id,
            cleanup_transaction, load_prepared_journal, observe_tree, rollback_prepared,
        )
        from contract_store import (
            DEFAULT_LOCK_TIMEOUT,
            _birth_receipt_path, _publication_base_locked, _writer_lock,
            catalog_admission_lock,
        )
        from executor_birth_receipts import verify_admission_receipt
        inventory = inventory_authoring_manifests()
        if inventory.problems:
            raise BirthBootstrapError("birth_authoring_inventory_invalid")
        for ref in inventory.manifests:
            control = authoring_paths(ref.manifest_dir, ref.contract_id.value)
            with catalog_admission_lock(store_root=self.store_root):
                with authoring_token(
                    control.lock, exclusive=True, timeout=DEFAULT_LOCK_TIMEOUT,
                ):
                    with _writer_lock(ref.contract_id, store_root=self.store_root):
                        pending = load_prepared_journal(control)
                        if pending is None:
                            continue
                        if pending.contract_id != ref.contract_id.value:
                            raise BirthBootstrapError("birth_authoring_recovery_ambiguous")
                        contract_dir, _generations, current, _payloads = _publication_base_locked(
                            ref, trusted_publics=self.trusted_publics,
                            store_root=self.store_root, technical_base=True,
                        )
                        try:
                            encoded = _birth_receipt_path(
                                contract_dir, pending.new_generation_id,
                            ).read_bytes()
                            receipt = verify_admission_receipt(
                                encoded, verifier_keys=self.verifier_keys,
                            )
                        except Exception as exc:
                            raise BirthBootstrapError("birth_authoring_recovery_receipt_invalid") from exc
                        bindings = {
                            "contract_id": ref.contract_id.value,
                            "generation_id": pending.new_generation_id,
                            "birth_request_id": pending.request_id,
                            "authoring_journal_hash": pending.journal_hash,
                            "predecessor_id": pending.predecessor_generation_id,
                            "candidate_id": pending.candidate_id,
                            "semantic_core_id": pending.semantic_core_id,
                            "admission_context_id": pending.admission_context_id,
                        }
                        if any(getattr(receipt, field) != wanted for field, wanted in bindings.items()):
                            raise BirthBootstrapError("birth_authoring_recovery_receipt_conflict")
                        if current == pending.new_generation_id:
                            if authoring_tree_id(observe_tree(control.canonical)) != pending.new_tree_id:
                                raise BirthBootstrapError("birth_authoring_recovery_ambiguous")
                            advance_version(control, pending.contract_id, pending.new_tree_id)
                            cleanup_transaction(control, pending)
                        elif current == pending.predecessor_generation_id:
                            rollback_prepared(control, pending)
                        else:
                            raise BirthBootstrapError("birth_authoring_recovery_pointer_conflict")


def _sealed_authorities(sealed):
    """Build the producer authorities and the issuer registry from the set.

    The issuer identity is the capability's own producer, the author comes
    from the closed table, and the keys come from the stores the provisioner
    prepared: no name, no origin and no key is chosen by a document.
    """
    from executor_birth_producer_table_v1 import producer_store_name_v1

    authorities: dict[_ProducerCapability, _ProducerAuthority] = {}
    entries: dict[str, list[IssuerKey]] = {}
    public_keys: set[bytes] = set()
    for capability in _producer_capabilities_for_bootstrap():
        name = producer_store_name_v1(capability.producer_id, capability.operation)
        loaded = sealed.producers.get(name)
        if loaded is None:
            raise BirthBootstrapError("birth_producer_registry_incomplete")
        author = producer_author_v1(capability.producer_id, capability.operation)
        private = loaded.active_private_key
        public_bytes = private.public_key().public_bytes_raw()
        if public_bytes in public_keys:
            raise BirthBootstrapError("birth_producer_capability_key_reused")
        public_keys.update(
            verifier.public_bytes_raw() for verifier in loaded.verifier_keys.values()
        )
        authorities[capability] = _ProducerAuthority(
            capability, capability.producer_id, loaded.active_key_id, private, author,
        )
        entries.setdefault(capability.producer_id, []).extend(IssuerKey(
            verifier_id, verifier, frozenset(ExecutorOrigin), frozenset({author}),
        ) for verifier_id, verifier in loaded.verifier_keys.items())
    return MappingProxyType(authorities), IssuerRegistry(
        {key: tuple(items) for key, items in entries.items()}
    )


def _build_sealed(*, now: Callable[[], datetime]) -> BirthRuntimeBundle:
    """Assemble the runtime from the prepared authority set, and from nothing else.

    Every authority is read once under the barrier of
    ``executor_birth_prepared_root``, the context is the one rebuilt there from
    the installed distribution, and the two policy facts come from the code.
    No configuration document takes part.
    """
    from executor_birth_commit_publisher import _build_prepared_bundle_v1
    from executor_birth_context import BuiltAdmissionContext
    from executor_birth_context_builder import ProductionContextBuilder
    from executor_birth_policy_v1 import (
        BIRTH_POLICY_VERSION_V1, birth_receipt_ttl_seconds_v1,
    )
    from executor_birth_prepared_root import load_sealed_authorities_v1
    from executor_birth_approval_store import resolve_request_approval
    import config as _config

    sealed = load_sealed_authorities_v1()
    state_dir = _secure_state_dir(
        Path(_config.PATH_USER_STATE) / BIRTH_STATE_BASENAME_V1
    )
    producer_db = _secure_state_db(state_dir, PRODUCER_RECEIPTS_BASENAME_V1)
    approval_db = _secure_state_db(state_dir, APPROVALS_BASENAME_V1)

    authorities, registry = _sealed_authorities(sealed)
    bundle = _build_prepared_bundle_v1(
        author=sealed.author,
        admission=sealed.admission,
        set_id=sealed.prepared.set_id,
        prepared_admission_context_id=sealed.prepared.prepared_admission_context_id,
        prepared_context_epoch=sealed.prepared.prepared_context_epoch,
        store_root=None,
    )
    context_builder = ProductionContextBuilder(
        BuiltAdmissionContext(sealed.material.context, sealed.material.pin, {})
    )
    trusted_publics = tuple(sorted(sealed.author.verifier_keys.items()))
    verifier = _PostconditionAdapter(
        trusted_publics=trusted_publics,
        verifier_keys=sealed.admission.verifier_keys,
    )
    verifier.recover_authoring()

    def approval_resolver(request, observed, revision, instant):
        return resolve_request_approval(
            approval_refs=request.approval_refs, request_id=request.request_id,
            candidate_id=observed.identities.candidate_id,
            semantic_core_id=observed.identities.semantic_core_id,
            admission_context_id=observed.identities.admission_context_id,
            scope=approval_scope(observed, revision), now=instant,
            db_path=approval_db, authority=sealed.approval,
        )

    core = _assemble_birth_core(
        producer_registry=registry, producer_db=producer_db,
        context_resolver=context_builder.resolve,
        context_epoch_resolver=context_builder.current_epoch,
        approval_resolver=approval_resolver,
        shadow_dependencies=_assemble_production_dependencies(
            semantic_authority=sealed.semantic, windows_sandbox_registry=None,
        ),
        admission_private_key=sealed.admission.active_private_key,
        admission_verifier_keys=sealed.admission.verifier_keys,
        admission_key_id=sealed.admission.active_key_id,
        policy_version=BIRTH_POLICY_VERSION_V1, now=now,
        commit_publisher=bundle.publisher,
        postcondition_verifier=verifier.verify,
    )
    ttl = birth_receipt_ttl_seconds_v1()
    factories = {
        cap: _request_factory(auth, registry, producer_db, ttl, now, context_builder)
        for cap, auth in authorities.items()
    }
    return _assemble_birth_runtime_bundle(core, factories)


def bootstrap_birth_runtime(
    *, now: Callable[[], datetime] | None = None
) -> BirthRuntimeBundle:
    """Initialize exactly once; concurrent callers see one result or one failure."""
    global _BOOT_STATE, _BOOT_ERROR
    with _BOOT_LOCK:
        while _BOOT_STATE == "building":
            _BOOT_LOCK.wait()
        installed = _runtime_bundle_snapshot()
        if installed is not None:
            return installed
        if _BOOT_STATE == "failed":
            raise BirthBootstrapError("birth_bootstrap_failed") from _BOOT_ERROR
        _BOOT_STATE = "building"
    try:
        bundle = _build_sealed(now=now or (lambda: datetime.now(timezone.utc)))
        _install_birth_runtime_bundle(bundle)
    except BaseException as exc:
        with _BOOT_LOCK:
            _BOOT_ERROR = exc; _BOOT_STATE = "failed"; _BOOT_LOCK.notify_all()
        raise
    with _BOOT_LOCK:
        _BOOT_STATE = "ready"; _BOOT_LOCK.notify_all()
    return bundle


def birth_authority_is_prepared_v1() -> bool:
    """Say whether this installation has a prepared authority set at all.

    The question is asked directly, never inferred from a refusal: an absent
    Birth root and a failed read produce the same input/output code, and
    treating that code as "nothing prepared" would hide a real fault behind
    the declared inactive state.
    """
    import config as C
    from executor_birth_prepared_set import MARKER_BASENAME_V1

    marker = (Path(C.PATH_USER_CONFIG) / BIRTH_STATE_BASENAME_V1
              / MARKER_BASENAME_V1)
    try:
        return marker.is_file()
    except OSError:
        return False


def require_birth_runtime_before_workers() -> None:
    """Install the Birth authority before any mutating worker starts.

    An installation on which the operator has not run the provisioner yet has
    no prepared set, so it has no sealed runtime either.  That is the declared
    ``prepared_not_active`` state, not a failure: refusing to boot there makes
    the service unstartable, which is exactly what happened once on the
    reference installation, where the running process was the last surviving
    instance and no restart could succeed.

    Once a set is prepared, every failure to activate it stays fatal.
    """
    if not birth_authority_is_prepared_v1():
        import logging

        logging.getLogger(__name__).warning(
            "Birth authority not provisioned yet: continuing without the "
            "sealed runtime, as the prepared_not_active state allows",
        )
        return
    bootstrap_birth_runtime()

"""Fail-closed productive bootstrap for the RM-0008 Birth boundary.

Secrets are provisioned by the operator.  This module only reads and validates
them; it never creates, rotates, or repairs key material.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
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
from executor_birth_producer_store import (
    ProducerReceiptBinding, get_or_issue_and_claim_producer_receipt,
    get_or_issue_producer_receipt,
)
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


_REATTESTATION_FACTORY_TOKEN = object()
_REATTESTATION_REASON_V1 = "reattest the authenticated current generation for ownership cutover"
_REATTESTATION_CAPABILITY_V1 = "installer_phase3:ownership_reattest_current"


class _CutoverReattestationFactoryV1:
    """Sealed factory: an exact current in, one bound claimed request out."""

    __slots__ = (
        "_port", "_authority", "_registry", "_db_path", "_ttl_seconds",
        "_now", "_seal",
    )

    def __init__(
        self, token: object, *, port: object, authority: _ProducerAuthority,
        registry: IssuerRegistry, db_path: Path, ttl_seconds: int,
        now: Callable[[], datetime],
    ) -> None:
        from executor_birth_commit_publisher import _is_birth_reattestation_port

        if (token is not _REATTESTATION_FACTORY_TOKEN
                or not _is_birth_reattestation_port(port)
                or not isinstance(authority, _ProducerAuthority)
                or not isinstance(registry, IssuerRegistry)
                or not isinstance(db_path, Path)
                or not isinstance(ttl_seconds, int) or ttl_seconds < 1
                or not callable(now)):
            raise BirthBootstrapError("birth_reattestation_factory_invalid")
        self._port = port
        self._authority = authority
        self._registry = registry
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._now = now
        self._seal = _REATTESTATION_FACTORY_TOKEN

    def __call__(self, current: object):
        from executor_birth_cutover import CurrentGeneration
        from executor_birth_operational import _candidate_source_id_from_snapshot
        from executor_birth_reattestation import _sealed_reattestation_request

        if (self._seal is not _REATTESTATION_FACTORY_TOKEN
                or not isinstance(current, CurrentGeneration)):
            raise BirthBootstrapError("birth_reattestation_request_invalid")
        snapshot = self._port.capture(current)
        try:
            source_id = _candidate_source_id_from_snapshot(snapshot)
        finally:
            close = getattr(snapshot, "close", None)
            if callable(close):
                close()
        authority = self._authority
        origin = executor_origin_v1(current.ref.contract_id.origin)
        objective = _hash(
            b"metnos.executor-birth.reattestation-objective/v1\0",
            current.ref.contract_id.value, current.generation_id,
            _REATTESTATION_REASON_V1,
        )
        request_id = _hash(
            b"metnos.executor-birth.reattestation-request/v1\0",
            authority.issuer_id, _REATTESTATION_CAPABILITY_V1,
            current.ref.contract_id.value, current.generation_id,
            objective, source_id,
        )
        binding = ProducerReceiptBinding(
            objective, source_id, origin, authority.author,
        )
        instant = self._now().astimezone(timezone.utc).replace(microsecond=0)
        expires = instant + timedelta(seconds=self._ttl_seconds)

        def issue() -> bytes:
            return issue_producer_receipt(
                issuer_id=authority.issuer_id, executor_origin=origin,
                revision_authorship=authority.author, objective_hash=objective,
                candidate_source_id=source_id,
                issued_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                nonce=hashlib.sha256(request_id.encode("ascii")).hexdigest()[:32],
                key_id=authority.key_id, private_key=authority.private_key,
            )

        receipt = get_or_issue_and_claim_producer_receipt(
            request_id=request_id, issuer_id=authority.issuer_id,
            capability_id=_REATTESTATION_CAPABILITY_V1,
            contract_id=current.ref.contract_id.value, binding=binding,
            registry=self._registry, now=instant, db_path=self._db_path,
            issue=issue,
        )
        return _sealed_reattestation_request(
            request_id, current, receipt, authority.issuer_id,
            _REATTESTATION_REASON_V1, binding,
        )


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


def _build_sealed(
    *, now: Callable[[], datetime], store_root: Path | None = None,
) -> BirthRuntimeBundle:
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

    def canonical_now() -> datetime:
        instant = now()
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() is None
        ):
            raise BirthBootstrapError("birth_clock_invalid")
        return instant.astimezone(timezone.utc).replace(microsecond=0)

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
        store_root=store_root,
    )
    context_builder = ProductionContextBuilder(
        BuiltAdmissionContext(sealed.material.context, sealed.material.pin, {})
    )
    trusted_publics = tuple(sorted(sealed.author.verifier_keys.items()))
    verifier = _PostconditionAdapter(
        trusted_publics=trusted_publics,
        verifier_keys=sealed.admission.verifier_keys,
        store_root=store_root,
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
            linux_sandbox_registry=sealed.sandbox,
        ),
        admission_private_key=sealed.admission.active_private_key,
        admission_verifier_keys=sealed.admission.verifier_keys,
        admission_key_id=sealed.admission.active_key_id,
        policy_version=BIRTH_POLICY_VERSION_V1, now=canonical_now,
        commit_publisher=bundle.publisher,
        postcondition_verifier=verifier.verify,
    )
    ttl = birth_receipt_ttl_seconds_v1()
    factories = {
        cap: _request_factory(
            auth, registry, producer_db, ttl, canonical_now, context_builder,
        )
        for cap, auth in authorities.items()
    }
    from executor_birth_intent import _INSTALLER

    reattestation_factory = _CutoverReattestationFactoryV1(
        _REATTESTATION_FACTORY_TOKEN,
        port=bundle.publisher.reattestation_port(),
        authority=authorities[_INSTALLER], registry=registry,
        db_path=producer_db, ttl_seconds=ttl, now=canonical_now,
    )
    return _assemble_birth_runtime_bundle(
        core, factories, reattestation_factory,
    )


_INITIAL_INSTALL_REASON_V1 = "build the initial installed executor contract catalog"


def _require_initial_install_quiescence_v1(prove_quiescent: object) -> None:
    if not callable(prove_quiescent):
        raise BirthBootstrapError("birth_initial_install_quiescence_required")
    try:
        stopped = prove_quiescent()
    except Exception as exc:
        raise BirthBootstrapError("birth_initial_install_quiescence_required") from exc
    if stopped is not True:
        raise BirthBootstrapError("birth_initial_install_quiescence_required")


def _regular_source_bytes_v1(root: Path, relative: str) -> bytes:
    """Read one candidate member twice without accepting a linked locator."""
    from code_file_paths import validate_portable_code_path

    validated = validate_portable_code_path(relative)
    try:
        if root.is_symlink():
            raise OSError("linked candidate root")
        root_resolved = root.resolve(strict=True)
        path = root_resolved.joinpath(*PurePosixPath(validated).parts)
        resolved = path.resolve(strict=True)
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise OSError("candidate member escapes its contract")
        cursor = path
        while cursor != root_resolved:
            status = cursor.lstat()
            if stat.S_ISLNK(status.st_mode):
                raise OSError("linked candidate member")
            cursor = cursor.parent
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OSError("candidate member is not a regular single-link file")
        payload = path.read_bytes()
        after = path.lstat()
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
            item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )
        if identity(before) != identity(after) or len(payload) != before.st_size:
            raise OSError("candidate member changed during read")
        return payload
    except OSError as exc:
        raise BirthBootstrapError("birth_initial_candidate_unavailable") from exc


def _initial_candidate_payloads_v1(ref: ManifestRef) -> Mapping[str, bytes]:
    """Capture one installed source and change only its derived code digest."""
    from code_file_paths import validate_portable_code_files
    from manifest_code_digest import prepare_manifest_digest_v1

    root = ref.manifest_dir
    manifest = _regular_source_bytes_v1(root, "manifest.toml")
    language_state = _regular_source_bytes_v1(root, "manifest.lang_state.json")
    try:
        parsed = tomllib.loads(manifest.decode("utf-8"))
        code = parsed.get("code")
        files = validate_portable_code_files(
            code.get("files") if isinstance(code, dict) else None,
        )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise BirthBootstrapError("birth_initial_candidate_invalid") from exc
    code_payloads = {
        relative: _regular_source_bytes_v1(root, relative)
        for relative in files
    }
    prepared = prepare_manifest_digest_v1(manifest, code_payloads)
    # A non-cooperating source writer cannot mix the first manifest with a
    # later code set without this final exact reread being noticed.
    if _regular_source_bytes_v1(root, "manifest.toml") != manifest:
        raise BirthBootstrapError("birth_initial_candidate_changed")
    return MappingProxyType({
        "manifest.toml": prepared,
        "manifest.lang_state.json": language_state,
        **code_payloads,
    })


def _materialize_initial_candidate_v1(
    parent: Path, ref: ManifestRef, payloads: Mapping[str, bytes],
) -> Path:
    directory = parent / hashlib.sha256(
        ref.contract_id.value.encode("utf-8")
    ).hexdigest()
    directory.mkdir(mode=0o700)
    for relative, payload in payloads.items():
        path = directory.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(payload)
        if path.read_bytes() != payload:
            raise BirthBootstrapError("birth_initial_candidate_reread_mismatch")
    return directory


def _initial_shadow_root_v1(
    prepared_set_id: str,
    candidates: tuple[tuple[ManifestRef, Mapping[str, bytes]], ...],
) -> Path:
    import config as _config
    from contract_store import SHADOW_RELATIVE

    digest = hashlib.sha256(b"metnos.executor-birth.initial-shadow/v1\0")
    for value in (prepared_set_id, *(ref.contract_id.value for ref, _ in candidates)):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big")); digest.update(encoded)
    for _ref, payloads in candidates:
        for name in sorted(payloads, key=str.encode):
            encoded_name = name.encode("utf-8")
            payload = payloads[name]
            digest.update(len(encoded_name).to_bytes(8, "big")); digest.update(encoded_name)
            digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
    return Path(_config.PATH_USER_STATE) / SHADOW_RELATIVE / digest.hexdigest() / "v1"


def _initial_request_id_v1(
    ref: ManifestRef, payloads: Mapping[str, bytes],
) -> str:
    """Rebuild the signed request binding without minting a producer receipt."""
    from executor_birth_intent import _INSTALLER

    framed = bytearray(b"metnos.executor-birth.candidate-source/v1\0")
    for name, payload in sorted(payloads.items(), key=lambda item: item[0].encode()):
        encoded = name.encode()
        framed.extend(len(encoded).to_bytes(8, "big")); framed.extend(encoded)
        framed.extend(len(payload).to_bytes(8, "big")); framed.extend(payload)
    source_id = "sha256:" + hashlib.sha256(framed).hexdigest()
    objective = _hash(
        b"metnos.executor-birth.objective/v1\0", _INITIAL_INSTALL_REASON_V1,
    )
    return _hash(
        b"metnos.executor-birth.request/v1\0",
        _INSTALLER.producer_id,
        _INSTALLER.operation,
        ref.contract_id.value,
        objective,
        source_id,
    )


def _private_bundle_request_v1(
    bundle: BirthRuntimeBundle, intent: BirthIntent, capability: _ProducerCapability,
):
    from executor_birth_operational import _execute

    factory = bundle.producer_factories.get(capability)
    if factory is None:
        raise BirthBootstrapError("birth_initial_producer_unavailable")
    request = factory(intent)
    return request, _execute(request, bundle.core)


def _verified_initial_receipt_v1(
    ref: ManifestRef, generation_id: str, *, store_root: Path | None,
    trusted_publics: tuple, admission_verifiers: Mapping[str, Ed25519PublicKey],
    request_id: str | None = None,
) -> bytes:
    from contract_store import current_manifest, read_current_birth_receipt
    from executor_birth_receipts import verify_admission_receipt

    current = current_manifest(
        ref, trusted_publics=trusted_publics, store_root=store_root,
    )
    if current.generation_id != generation_id:
        raise BirthBootstrapError("birth_initial_generation_reread_mismatch")
    encoded = read_current_birth_receipt(
        ref, generation_id, trusted_publics=trusted_publics,
        store_root=store_root,
    )
    if not isinstance(encoded, bytes):
        raise BirthBootstrapError("birth_initial_receipt_missing")
    try:
        receipt = verify_admission_receipt(
            encoded, verifier_keys=admission_verifiers,
        )
    except Exception as exc:
        raise BirthBootstrapError("birth_initial_receipt_invalid") from exc
    if (
        receipt.contract_id != ref.contract_id.value
        or receipt.generation_id != generation_id
        or (request_id is not None and receipt.birth_request_id != request_id)
    ):
        raise BirthBootstrapError("birth_initial_receipt_binding_invalid")
    return encoded


def prepare_initial_installer_catalog_v1(*, prove_quiescent: object) -> dict:
    """Build the initial shadow through a private, non-installed Birth bundle."""
    from contract_bootstrap import ProductionStoreMode
    from contract_store import production_store_mode
    from executor_birth_intent import _INSTALLER
    from executor_birth_legacy_gate import closed_build_enforcement
    from executor_birth_prepared_root import load_sealed_authorities_v1
    from manifest_inventory import inventory_authoring_manifests

    _require_initial_install_quiescence_v1(prove_quiescent)
    if production_store_mode() is not ProductionStoreMode.LEGACY:
        raise BirthBootstrapError("birth_initial_install_state_invalid")
    if closed_build_enforcement():
        raise BirthBootstrapError("birth_initial_install_closed")
    sealed = load_sealed_authorities_v1()
    inventory = inventory_authoring_manifests()
    refs = inventory.installed()
    if inventory.problems or not refs:
        raise BirthBootstrapError("birth_initial_inventory_invalid")
    candidates = tuple(
        (ref, _initial_candidate_payloads_v1(ref)) for ref in refs
    )
    shadow_root = _initial_shadow_root_v1(sealed.prepared.set_id, candidates)
    trusted = tuple(sorted(sealed.author.verifier_keys.items()))
    catalog: dict[str, str] = {}
    receipts: dict[str, str] = {}
    repeated = 0
    with tempfile.TemporaryDirectory(prefix="metnos-birth-initial-") as temporary:
        parent = Path(temporary)
        staged = {
            ref.contract_id: _materialize_initial_candidate_v1(parent, ref, payloads)
            for ref, payloads in candidates
        }
        bundle = _build_sealed(
            now=lambda: datetime.now(timezone.utc), store_root=shadow_root,
        )
        for ref in refs:
            request, birth = _private_bundle_request_v1(
                bundle,
                BirthIntent(
                    staged[ref.contract_id], ref.contract_id,
                    _INITIAL_INSTALL_REASON_V1,
                ),
                _INSTALLER,
            )
            if birth.error_code or birth.publication is None:
                raise BirthBootstrapError(
                    birth.error_code or "birth_initial_publication_missing"
                )
            generation_id = birth.publication.current_generation_id
            encoded = _verified_initial_receipt_v1(
                ref, generation_id, store_root=shadow_root,
                trusted_publics=trusted,
                admission_verifiers=sealed.admission.verifier_keys,
                request_id=request.request_id,
            )
            catalog[ref.contract_id.value] = generation_id
            receipts[ref.contract_id.value] = "sha256:" + hashlib.sha256(encoded).hexdigest()
            repeated += int(birth.publication.repeated)
    _require_initial_install_quiescence_v1(prove_quiescent)
    return {
        "schema": "metnos.contract-store-cutover/1",
        "shadow_root": str(shadow_root),
        "contracts": len(catalog),
        "repeated": repeated,
        "catalog": dict(sorted(catalog.items())),
        "birth_receipts": dict(sorted(receipts.items())),
        "prepared_set_id": sealed.prepared.set_id,
    }


def _verify_initial_catalog_v1(
    *, report: Mapping[str, object] | None, prove_quiescent: object,
) -> dict[str, int]:
    from contract_bootstrap import ProductionStoreMode
    from contract_store import current_manifest, production_store_mode
    from executor_birth_prepared_root import load_sealed_authorities_v1
    from manifest_inventory import (
        inventory_authoring_manifests, inventory_store_manifests,
    )

    _require_initial_install_quiescence_v1(prove_quiescent)
    mode = production_store_mode()
    sealed = load_sealed_authorities_v1()
    trusted = tuple(sorted(sealed.author.verifier_keys.items()))
    if mode is ProductionStoreMode.LEGACY:
        if report is None:
            raise BirthBootstrapError("birth_initial_report_required")
        store_root = Path(str(report.get("shadow_root", "")))
        inventory = inventory_authoring_manifests()
    elif mode in {ProductionStoreMode.STORE_ONLY, ProductionStoreMode.ACTIVE}:
        store_root = None
        inventory = inventory_store_manifests()
    else:
        raise BirthBootstrapError("birth_initial_install_state_invalid")
    if inventory.problems or not inventory.manifests:
        raise BirthBootstrapError("birth_initial_inventory_invalid")
    refs = {ref.contract_id.value: ref for ref in inventory.manifests}
    expected_requests = (
        {
            key: _initial_request_id_v1(
                ref, _initial_candidate_payloads_v1(ref),
            )
            for key, ref in refs.items()
        }
        if mode is ProductionStoreMode.LEGACY else {}
    )
    if report is None:
        catalog = {
            key: current_manifest(
                ref, trusted_publics=trusted, store_root=store_root,
            ).generation_id
            for key, ref in refs.items()
        }
        receipt_hashes = None
    else:
        catalog = report.get("catalog")
        receipt_hashes = report.get("birth_receipts")
        if (
            report.get("prepared_set_id") != sealed.prepared.set_id
            or not isinstance(catalog, dict)
            or set(catalog) != set(refs)
            or not isinstance(receipt_hashes, dict)
            or set(receipt_hashes) != set(refs)
        ):
            raise BirthBootstrapError("birth_initial_report_invalid")
    for key in sorted(refs):
        generation_id = catalog[key]
        if not isinstance(generation_id, str):
            raise BirthBootstrapError("birth_initial_report_invalid")
        encoded = _verified_initial_receipt_v1(
            refs[key], generation_id, store_root=store_root,
            trusted_publics=trusted,
            admission_verifiers=sealed.admission.verifier_keys,
            request_id=expected_requests.get(key),
        )
        if receipt_hashes is not None and receipt_hashes[key] != (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        ):
            raise BirthBootstrapError("birth_initial_report_invalid")
    _require_initial_install_quiescence_v1(prove_quiescent)
    return {"contracts": len(refs), "receipts": len(refs)}


def verify_initial_installer_report_v1(
    report: Mapping[str, object], *, prove_quiescent: object,
) -> dict[str, int]:
    """Authenticate a durable initial report before activation or replay."""
    return _verify_initial_catalog_v1(
        report=report, prove_quiescent=prove_quiescent,
    )


def verify_initial_installer_store_v1(*, prove_quiescent: object) -> dict[str, int]:
    """Authenticate every Birth receipt in a root-only recovery store."""
    return _verify_initial_catalog_v1(
        report=None, prove_quiescent=prove_quiescent,
    )


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

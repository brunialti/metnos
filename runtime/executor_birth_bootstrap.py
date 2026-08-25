"""Fail-closed productive bootstrap for the RM-0008 Birth boundary.

Secrets are provisioned by the operator.  This module only reads and validates
them; it never creates, rotates, or repairs key material.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
from executor_birth_intent import BirthIntent, _ProducerCapability, _producer_capabilities_for_bootstrap
from executor_birth_operational import (
    BirthRequest, BirthRuntimeBundle, _assemble_birth_core,
    _assemble_birth_runtime_bundle, _install_birth_runtime_bundle,
    _runtime_bundle_snapshot, approval_scope, candidate_source_id,
)
from executor_birth_producer_store import get_or_issue_producer_receipt
from executor_birth_receipts import IssuerKey, IssuerRegistry, issue_producer_receipt
from executor_birth_runner import WindowsSandboxRegistry
from executor_birth_runner_windows_v1 import helper_binary_hash
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
    origin: ExecutorOrigin
    author: RevisionAuthor


@dataclass(frozen=True, slots=True)
class BirthBootstrapPaths:
    config: Path
    state_dir: Path


_BOOT_LOCK = threading.Condition()
_BOOT_STATE = "cold"
_BOOT_ERROR: BaseException | None = None


def default_birth_bootstrap_paths() -> BirthBootstrapPaths:
    import config as C
    return BirthBootstrapPaths(
        C.PATH_USER_CONFIG / "birth" / "bootstrap.json",
        C.PATH_USER_STATE / "birth",
    )


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BirthBootstrapError(f"birth_bootstrap_config_duplicate:{key}")
        result[key] = value
    return result


def _read_config(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BirthBootstrapError("birth_bootstrap_config_unavailable") from exc
    if not isinstance(value, dict):
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    return value


def _secure_state_db(state_dir: Path) -> Path:
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = state_dir.stat()
        if not state_dir.is_dir() or state_dir.is_symlink() or (os.name != "nt" and info.st_mode & 0o077):
            raise BirthBootstrapError("birth_state_permissions")
        path = state_dir / "producer-receipts.sqlite"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            db_info = os.fstat(descriptor)
            if not stat.S_ISREG(db_info.st_mode) or db_info.st_nlink != 1 or (os.name != "nt" and db_info.st_mode & 0o077):
                raise BirthBootstrapError("birth_state_permissions")
        finally:
            os.close(descriptor)
        return path
    except BirthBootstrapError:
        raise
    except OSError as exc:
        raise BirthBootstrapError("birth_state_unavailable") from exc


def _secure_approval_db(path: Path) -> Path:
    """Validate the explicitly configured durable approval database path."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.stat()
        if (not path.parent.is_dir() or path.parent.is_symlink()
                or (os.name != "nt" and parent.st_mode & 0o077)):
            raise BirthBootstrapError("birth_approval_store_permissions")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (os.name != "nt" and info.st_mode & 0o077)):
                raise BirthBootstrapError("birth_approval_store_permissions")
        finally:
            os.close(descriptor)
        return path
    except BirthBootstrapError:
        raise
    except OSError as exc:
        raise BirthBootstrapError("birth_approval_store_unavailable") from exc


def _resolve(config_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise BirthBootstrapError("birth_key_path_invalid")
    path = Path(value)
    return path if path.is_absolute() else config_dir / path


def _load_authorities(value: Mapping[str, object], config_dir: Path, *,
                      forbidden_public_keys: tuple[Ed25519PublicKey, ...]):
    from executor_birth_keystore import load_birth_keystore
    expected = {f"{cap.producer_id}:{cap.operation}": cap for cap in _producer_capabilities_for_bootstrap()}
    producers = value.get("producers")
    if not isinstance(producers, dict) or set(producers) != set(expected):
        raise BirthBootstrapError("birth_producer_registry_incomplete")
    authorities: dict[_ProducerCapability, _ProducerAuthority] = {}
    entries: dict[str, list[IssuerKey]] = {}
    public_keys: set[bytes] = set()
    for name, capability in expected.items():
        item = producers[name]
        if not isinstance(item, dict) or set(item) != {"issuer_id", "keystore", "origin", "author"}:
            raise BirthBootstrapError("birth_producer_registry_invalid")
        try:
            issuer_id = item["issuer_id"]
            if not isinstance(issuer_id, str) or not issuer_id:
                raise ValueError
            origin, author = ExecutorOrigin(item["origin"]), RevisionAuthor(item["author"])
        except (KeyError, ValueError, TypeError) as exc:
            raise BirthBootstrapError("birth_producer_registry_invalid") from exc
        loaded = load_birth_keystore(
            _resolve(config_dir, item["keystore"]),
            forbidden_public_keys=(*forbidden_public_keys, *public_keys),
        )
        private = loaded.active_private_key
        key_id = loaded.active_key_id
        public_bytes = private.public_key().public_bytes_raw()
        if public_bytes in public_keys:
            raise BirthBootstrapError("birth_producer_capability_key_reused")
        public_keys.update(verifier.public_bytes_raw() for verifier in loaded.verifier_keys.values())
        authority = _ProducerAuthority(capability, issuer_id, key_id, private, origin, author)
        authorities[capability] = authority
        entries.setdefault(issuer_id, []).extend(IssuerKey(
            verifier_id, verifier, frozenset({origin}), frozenset({author}),
        ) for verifier_id, verifier in loaded.verifier_keys.items())
    registry = IssuerRegistry({key: tuple(items) for key, items in entries.items()})
    return MappingProxyType(authorities), registry


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
        observed = observe_candidate(
            intent.candidate_source_root, contract_id=intent.contract_id,
            executor_origin=authority.origin, revision_authorship=authority.author,
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
                issuer_id=authority.issuer_id, executor_origin=authority.origin,
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


def _context_builder(value: object, config_dir: Path):
    # Imported only at bootstrap/use time so the independently reviewed F4
    # builder remains the sole owner of admission-context observations.
    try:
        from executor_birth_context import AdmissionContextMaterial, ComponentMaterial, MaterialFile
        from executor_birth_context_builder import production_context_builder
        names = set(AdmissionContextMaterial.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != names:
            raise BirthBootstrapError("birth_context_material_incomplete")
        components = {}
        for name in names:
            item = value[name]
            if not isinstance(item, dict) or set(item) != {"version", "files", "configuration"}:
                raise BirthBootstrapError("birth_context_material_invalid")
            if not isinstance(item["files"], list):
                raise BirthBootstrapError("birth_context_material_invalid")
            files = []
            for source in item["files"]:
                if not isinstance(source, dict) or set(source) != {"label", "path"}:
                    raise BirthBootstrapError("birth_context_material_invalid")
                files.append(MaterialFile(source["label"], _resolve(config_dir, source["path"]).resolve()))
            components[name] = ComponentMaterial(
                item["version"], tuple(files), item["configuration"],
            )
        return production_context_builder(AdmissionContextMaterial(**components))
    except (ImportError, AttributeError, OSError, TypeError, ValueError) as exc:
        raise BirthBootstrapError("birth_context_builder_unavailable") from exc


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


def _build(paths: BirthBootstrapPaths, *, now: Callable[[], datetime]) -> BirthRuntimeBundle:
    value = _read_config(paths.config)
    required = {"schema_version", "policy_version", "receipt_ttl_seconds", "admission", "approval", "producers", "context", "semantic_review"}
    expected = required | ({"windows_sandbox"} if os.name == "nt" else set())
    if set(value) != expected or value["schema_version"] != 1:
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    if not isinstance(value["policy_version"], str) or not value["policy_version"]:
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    ttl = value["receipt_ttl_seconds"]
    if type(ttl) is not int or not 60 <= ttl <= 86400:
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    admission = value["admission"]
    if not isinstance(admission, dict) or set(admission) != {"keystore"}:
        raise BirthBootstrapError("birth_admission_keyring_invalid")
    approval = value["approval"]
    if not isinstance(approval, dict) or set(approval) != {"db_path", "authority_registry"}:
        raise BirthBootstrapError("birth_approval_store_invalid")
    config_dir = paths.config.parent
    windows_registry = None
    if os.name == "nt":
        sandbox = value["windows_sandbox"]
        fields = {"helper_path", "helper_binary_hash", "config_path", "config_hash", "runtime_binary_hash"}
        if not isinstance(sandbox, dict) or set(sandbox) != fields:
            raise BirthBootstrapError("windows_sandbox_registry_invalid")
        helper_path = _resolve(config_dir, sandbox["helper_path"]).resolve()
        helper_config = _resolve(config_dir, sandbox["config_path"]).resolve()
        digests = tuple(sandbox[name] for name in (
            "helper_binary_hash", "config_hash", "runtime_binary_hash",
        ))
        if any(not isinstance(item, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", item) for item in digests):
            raise BirthBootstrapError("windows_sandbox_registry_invalid")
        try:
            if (helper_binary_hash(helper_path) != sandbox["helper_binary_hash"]
                    or helper_binary_hash(helper_config) != sandbox["config_hash"]):
                raise BirthBootstrapError("windows_sandbox_registry_invalid")
        except OSError as exc:
            raise BirthBootstrapError("windows_sandbox_registry_invalid") from exc
        windows_registry = WindowsSandboxRegistry(
            helper_path, sandbox["helper_binary_hash"], helper_config,
            sandbox["config_hash"], sandbox["runtime_binary_hash"],
        )
    from executor_birth_keystore import load_birth_keystore
    from sign import list_trusted_publics
    trusted_publics = tuple(list_trusted_publics())
    author_keys = tuple(public for _name, public in trusted_publics)
    admission_store = load_birth_keystore(
        _resolve(config_dir, admission["keystore"]),
        forbidden_public_keys=author_keys,
    )
    admission_private = admission_store.active_private_key
    verifiers = admission_store.verifier_keys
    key_id = admission_store.active_key_id
    authorities, registry = _load_authorities(
        value, config_dir,
        forbidden_public_keys=(*author_keys, *tuple(verifiers.values())),
    )
    producer_db = _secure_state_db(paths.state_dir)
    approval_db = _secure_approval_db(_resolve(config_dir, approval["db_path"]))
    from executor_birth_approval_authority import load_approval_authority
    try:
        approval_authority = load_approval_authority(
            _resolve(config_dir, approval["authority_registry"])
        )
    except Exception as exc:
        raise BirthBootstrapError("birth_approval_authority_invalid") from exc
    verifier = _PostconditionAdapter(trusted_publics=trusted_publics, verifier_keys=verifiers)
    verifier.recover_authoring()
    context_builder = _context_builder(value["context"], config_dir)
    try:
        from executor_birth_semantic_authority import load_semantic_authority
        semantic_authority = load_semantic_authority(value["semantic_review"], config_dir)
    except Exception as exc:
        raise BirthBootstrapError("semantic_review_unavailable") from exc
    from executor_birth_approval_store import resolve_request_approval
    def approval_resolver(request, observed, revision, instant):
        return resolve_request_approval(
            approval_refs=request.approval_refs, request_id=request.request_id,
            candidate_id=observed.identities.candidate_id,
            semantic_core_id=observed.identities.semantic_core_id,
            admission_context_id=observed.identities.admission_context_id,
            scope=approval_scope(observed, revision), now=instant, db_path=approval_db,
            authority=approval_authority,
        )
    core = _assemble_birth_core(
        producer_registry=registry, producer_db=producer_db,
        context_resolver=context_builder.resolve,
        context_epoch_resolver=context_builder.current_epoch,
        approval_resolver=approval_resolver,
        shadow_dependencies=_assemble_production_dependencies(
            semantic_authority=semantic_authority,
            windows_sandbox_registry=windows_registry,
        ),
        admission_private_key=admission_private, admission_verifier_keys=verifiers,
        admission_key_id=key_id, policy_version=value["policy_version"], now=now,
        publisher_options={"trusted_publics": trusted_publics},
        postcondition_verifier=verifier.verify,
    )
    factories = {cap: _request_factory(auth, registry, producer_db, ttl, now, context_builder)
                 for cap, auth in authorities.items()}
    return _assemble_birth_runtime_bundle(core, factories)


def bootstrap_birth_runtime(paths: BirthBootstrapPaths | None = None, *,
                            now: Callable[[], datetime] | None = None) -> BirthRuntimeBundle:
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
        bundle = _build(paths or default_birth_bootstrap_paths(), now=now or (lambda: datetime.now(timezone.utc)))
        _install_birth_runtime_bundle(bundle)
    except BaseException as exc:
        with _BOOT_LOCK:
            _BOOT_ERROR = exc; _BOOT_STATE = "failed"; _BOOT_LOCK.notify_all()
        raise
    with _BOOT_LOCK:
        _BOOT_STATE = "ready"; _BOOT_LOCK.notify_all()
    return bundle


def require_birth_runtime_before_workers() -> None:
    bootstrap_birth_runtime()

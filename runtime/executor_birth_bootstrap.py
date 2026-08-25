"""Fail-closed productive bootstrap for the RM-0008 Birth boundary.

Secrets are provisioned by the operator.  This module only reads and validates
them; it never creates, rotates, or repairs key material.
"""
from __future__ import annotations

import hashlib
import json
import os
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
    _runtime_bundle_snapshot, candidate_source_id,
)
from executor_birth_producer_store import register_producer_receipt
from executor_birth_receipts import IssuerKey, IssuerRegistry, issue_producer_receipt
from executor_birth_shadow import _assemble_production_dependencies
from manifest_inventory import ManifestOrigin, ManifestRef, ManifestStatus


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
    from config import C
    return BirthBootstrapPaths(
        C.PATH_USER_CONFIG / "birth" / "keystore.json",
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


def _private_key(path: Path) -> Ed25519PrivateKey:
    try:
        info = path.stat()
        if os.name != "nt" and info.st_mode & 0o077:
            raise BirthBootstrapError("birth_private_key_permissions")
        payload = path.read_bytes()
        return Ed25519PrivateKey.from_private_bytes(payload)
    except BirthBootstrapError:
        raise
    except (OSError, ValueError) as exc:
        raise BirthBootstrapError("birth_private_key_invalid") from exc


def _public_key(path: Path) -> Ed25519PublicKey:
    try:
        return Ed25519PublicKey.from_public_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise BirthBootstrapError("birth_public_key_invalid") from exc


def _resolve(config_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise BirthBootstrapError("birth_key_path_invalid")
    path = Path(value)
    return path if path.is_absolute() else config_dir / path


def _load_authorities(value: Mapping[str, object], config_dir: Path):
    expected = {f"{cap.producer_id}:{cap.operation}": cap for cap in _producer_capabilities_for_bootstrap()}
    producers = value.get("producers")
    if not isinstance(producers, dict) or set(producers) != set(expected):
        raise BirthBootstrapError("birth_producer_registry_incomplete")
    authorities: dict[_ProducerCapability, _ProducerAuthority] = {}
    entries: dict[str, list[IssuerKey]] = {}
    public_keys: set[bytes] = set()
    for name, capability in expected.items():
        item = producers[name]
        if not isinstance(item, dict) or set(item) != {"issuer_id", "key_id", "private_key", "origin", "author"}:
            raise BirthBootstrapError("birth_producer_registry_invalid")
        try:
            issuer_id, key_id = item["issuer_id"], item["key_id"]
            if not isinstance(issuer_id, str) or not issuer_id or not isinstance(key_id, str) or not key_id:
                raise ValueError
            origin, author = ExecutorOrigin(item["origin"]), RevisionAuthor(item["author"])
        except (KeyError, ValueError, TypeError) as exc:
            raise BirthBootstrapError("birth_producer_registry_invalid") from exc
        private = _private_key(_resolve(config_dir, item["private_key"]))
        public_bytes = private.public_key().public_bytes_raw()
        if public_bytes in public_keys:
            raise BirthBootstrapError("birth_producer_capability_key_reused")
        public_keys.add(public_bytes)
        authority = _ProducerAuthority(capability, issuer_id, key_id, private, origin, author)
        authorities[capability] = authority
        entries.setdefault(issuer_id, []).append(IssuerKey(
            key_id, private.public_key(), frozenset({origin}), frozenset({author}),
        ))
    registry = IssuerRegistry({key: tuple(items) for key, items in entries.items()})
    return MappingProxyType(authorities), registry


def _manifest_ref(intent: BirthIntent) -> ManifestRef:
    manifest = intent.candidate_source_root / "manifest.toml"
    return ManifestRef(
        intent.contract_id, intent.contract_id.origin, ManifestStatus.ADMITTED,
        intent.candidate_source_root, manifest, intent.contract_id.relative_manifest,
        (intent.candidate_source_root,),
    )


def _hash(domain: bytes, *parts: str) -> str:
    framed = bytearray(domain)
    for part in parts:
        encoded = part.encode("utf-8")
        framed.extend(len(encoded).to_bytes(8, "big")); framed.extend(encoded)
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _request_factory(authority: _ProducerAuthority, registry: IssuerRegistry,
                     db_path: Path, ttl_seconds: int, now: Callable[[], datetime],
                     context_builder: object):
    lock = threading.Lock()
    cache: dict[str, BirthRequest] = {}

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
        with lock:
            prior = cache.get(request_id)
            if prior is not None:
                return prior
            instant = now().astimezone(timezone.utc).replace(microsecond=0)
            expires = instant + timedelta(seconds=ttl_seconds)
            receipt = issue_producer_receipt(
                issuer_id=authority.issuer_id, executor_origin=authority.origin,
                revision_authorship=authority.author, objective_hash=objective,
                candidate_source_id=source_id,
                issued_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                nonce=hashlib.sha256(request_id.encode()).hexdigest()[:32],
                key_id=authority.key_id, private_key=authority.private_key,
            )
            register_producer_receipt(receipt, registry=registry, now=instant, db_path=db_path)
            result = BirthRequest(
                request_id, _manifest_ref(intent), receipt, authority.issuer_id,
                intent.reason, intent.approval_refs, authority.capability.operation,
                intent.candidate_source_root,
            )
            cache[request_id] = result
            return result
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
        # Recovery that needs a request is completed by the publisher while
        # holding both catalog and authoring locks.  At boot, reject malformed
        # control state early; never guess a transaction's committed branch.
        from manifest_inventory import inventory_authoring_manifests
        from executor_birth_authoring import authoring_paths, load_prepared_journal
        inventory = inventory_authoring_manifests()
        if inventory.problems:
            raise BirthBootstrapError("birth_authoring_inventory_invalid")
        for ref in inventory.manifests:
            pending = load_prepared_journal(authoring_paths(ref.manifest_dir, ref.contract_id.value))
            if pending is not None and pending.contract_id != ref.contract_id.value:
                raise BirthBootstrapError("birth_authoring_recovery_ambiguous")


def _build(paths: BirthBootstrapPaths, *, now: Callable[[], datetime]) -> BirthRuntimeBundle:
    value = _read_config(paths.config)
    if set(value) != {"schema_version", "policy_version", "receipt_ttl_seconds", "admission", "producers", "context"} or value["schema_version"] != 1:
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    if not isinstance(value["policy_version"], str) or not value["policy_version"]:
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    ttl = value["receipt_ttl_seconds"]
    if type(ttl) is not int or not 60 <= ttl <= 86400:
        raise BirthBootstrapError("birth_bootstrap_config_invalid")
    admission = value["admission"]
    if not isinstance(admission, dict) or set(admission) != {"active_key_id", "private_key", "verifier_keys"}:
        raise BirthBootstrapError("birth_admission_keyring_invalid")
    key_id = admission["active_key_id"]
    if not isinstance(key_id, str) or not key_id or not isinstance(admission["verifier_keys"], dict):
        raise BirthBootstrapError("birth_admission_keyring_invalid")
    config_dir = paths.config.parent
    admission_private = _private_key(_resolve(config_dir, admission["private_key"]))
    verifiers = {name: _public_key(_resolve(config_dir, filename))
                 for name, filename in admission["verifier_keys"].items()}
    authorities, registry = _load_authorities(value, config_dir)
    admission_public = admission_private.public_key().public_bytes_raw()
    if any(auth.private_key.public_key().public_bytes_raw() == admission_public
           for auth in authorities.values()):
        raise BirthBootstrapError("birth_admission_producer_key_reused")
    paths.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    from sign import list_trusted_publics
    trusted_publics = tuple(list_trusted_publics())
    verifier = _PostconditionAdapter(trusted_publics=trusted_publics, verifier_keys=verifiers)
    verifier.recover_authoring()
    context_builder = _context_builder(value["context"], config_dir)
    core = _assemble_birth_core(
        producer_registry=registry, producer_db=paths.state_dir / "producer-receipts.sqlite",
        context_resolver=context_builder.resolve,
        context_epoch_resolver=context_builder.current_epoch,
        shadow_dependencies=_assemble_production_dependencies(),
        admission_private_key=admission_private, admission_verifier_keys=verifiers,
        admission_key_id=key_id, policy_version=value["policy_version"], now=now,
        publisher_options={"trusted_publics": trusted_publics},
        postcondition_verifier=verifier.verify,
    )
    factories = {cap: _request_factory(auth, registry, paths.state_dir / "producer-receipts.sqlite", ttl, now, context_builder)
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

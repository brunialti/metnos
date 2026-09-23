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
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from executor_birth import observe_candidate
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_intent import BirthIntent, _ProducerCapability, _producer_capabilities_for_bootstrap
from executor_birth_operational import (
    BirthRequest, BirthRuntimeVerificationView,
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
_AUTHOR_KEYSTORE_BASENAME = "author-keystore"


@dataclass(frozen=True, slots=True)
class _CreatedPrivatePath:
    path: Path
    device: int
    inode: int
    file_type: int
    change_time_ns: int


def _created_private_path(path: Path) -> _CreatedPrivatePath:
    info = path.lstat()
    return _CreatedPrivatePath(
        path,
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_ctime_ns,
    )


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


def _linked_path(path: Path, info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or (hasattr(path, "is_junction") and path.is_junction())
    )


def _require_private_directory(path: Path, *, error: str) -> None:
    try:
        for component in reversed((path, *path.parents)):
            component_info = component.lstat()
            if _linked_path(component, component_info):
                raise BirthBootstrapError(error)
        info = path.lstat()
        if _linked_path(path, info) or not stat.S_ISDIR(info.st_mode):
            raise BirthBootstrapError(error)
        if os.name == "posix" and (
            stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid()
        ):
            raise BirthBootstrapError(error)
        if os.name == "nt":
            from executor_birth_keystore import _check_windows_acl
            _check_windows_acl(path, confidential=True)
    except BirthBootstrapError:
        raise
    except Exception as exc:
        raise BirthBootstrapError(error) from exc


def _secure_private_directory_tree(
    *, anchor: Path, target: Path, error: str,
    created_paths: list[_CreatedPrivatePath] | None = None,
) -> Path:
    anchor = Path(os.path.abspath(anchor))
    target = Path(os.path.abspath(target))
    try:
        relative = target.relative_to(anchor)
    except ValueError as exc:
        raise BirthBootstrapError(error) from exc
    _require_private_directory(anchor, error=error)
    current = anchor
    for part in relative.parts:
        current = current / part
        created = False
        try:
            os.mkdir(current, 0o700)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise BirthBootstrapError(error) from exc
        if created and os.name == "nt":
            try:
                from executor_birth_keystore import _harden_windows_private_acl
                _harden_windows_private_acl(current)
            except Exception as exc:
                raise BirthBootstrapError(error) from exc
        _require_private_directory(current, error=error)
        if created and created_paths is not None:
            created_paths.append(_created_private_path(current))
    return target


def _secure_private_database(
    path: Path,
    *,
    anchor: Path,
    permissions_error: str,
    unavailable_error: str,
    created_paths: list[_CreatedPrivatePath] | None = None,
) -> Path:
    path = Path(os.path.abspath(path))
    _secure_private_directory_tree(
        anchor=anchor,
        target=path.parent,
        error=permissions_error,
        created_paths=created_paths,
    )
    flags = (
        os.O_RDWR | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    created = False
    try:
        try:
            descriptor = os.open(
                path, flags | os.O_CREAT | os.O_EXCL, 0o600,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(path, flags)
        try:
            entry = path.lstat()
            info = os.fstat(descriptor)
            if (
                _linked_path(path, entry)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise BirthBootstrapError(permissions_error)
            if created and os.name == "nt":
                from executor_birth_keystore import _harden_windows_private_acl
                try:
                    _harden_windows_private_acl(path)
                except Exception as exc:
                    if created_paths is not None:
                        created_paths.append(_created_private_path(path))
                    raise BirthBootstrapError(permissions_error) from exc
            entry = path.lstat()
            if (
                _linked_path(path, entry)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise BirthBootstrapError(permissions_error)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
                info = os.fstat(descriptor)
                if (
                    stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.geteuid()
                ):
                    raise BirthBootstrapError(permissions_error)
            else:
                from executor_birth_keystore import _check_windows_acl
                _check_windows_acl(path, confidential=True)
            if created and created_paths is not None:
                created_paths.append(_created_private_path(path))
        finally:
            os.close(descriptor)
        for suffix in ("-journal", "-shm", "-wal"):
            companion = Path(str(path) + suffix)
            if not companion.exists() and not companion.is_symlink():
                continue
            companion_info = companion.lstat()
            if (
                _linked_path(companion, companion_info)
                or not stat.S_ISREG(companion_info.st_mode)
                or companion_info.st_nlink != 1
                or (os.name == "posix" and (
                    stat.S_IMODE(companion_info.st_mode) != 0o600
                    or companion_info.st_uid != os.geteuid()
                ))
            ):
                raise BirthBootstrapError(permissions_error)
            if os.name == "nt":
                from executor_birth_keystore import _check_windows_acl
                _check_windows_acl(companion, confidential=True)
        return path
    except BirthBootstrapError:
        raise
    except OSError as exc:
        raise BirthBootstrapError(unavailable_error) from exc


def _secure_state_db(
    state_dir: Path, *, created_paths: list[_CreatedPrivatePath] | None = None,
) -> Path:
    state_dir = Path(os.path.abspath(state_dir))
    return _secure_private_database(
        state_dir / "producer-receipts.sqlite",
        anchor=state_dir.parent,
        permissions_error="birth_state_permissions",
        unavailable_error="birth_state_unavailable",
        created_paths=created_paths,
    )


def _secure_approval_db(
    path: Path,
    *,
    config_dir: Path | None = None,
    created_paths: list[_CreatedPrivatePath] | None = None,
) -> Path:
    """Validate the fixed, configuration-confined approval database path."""
    path = Path(os.path.abspath(path))
    anchor = Path(os.path.abspath(config_dir or path.parent))
    return _secure_private_database(
        path,
        anchor=anchor,
        permissions_error="birth_approval_store_permissions",
        unavailable_error="birth_approval_store_unavailable",
        created_paths=created_paths,
    )


def _rollback_created_private_paths(paths: list[_CreatedPrivatePath]) -> None:
    """Remove only empty bootstrap objects created by the failed attempt."""
    for created in reversed(paths):
        path = created.path
        try:
            info = path.lstat()
            if (
                _linked_path(path, info)
                or (info.st_dev, info.st_ino) != (
                    created.device, created.inode,
                )
                or stat.S_IFMT(info.st_mode) != created.file_type
                or (
                    stat.S_ISREG(info.st_mode)
                    and info.st_ctime_ns != created.change_time_ns
                )
            ):
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == 0:
                path.unlink()
            elif stat.S_ISDIR(info.st_mode):
                path.rmdir()
        except OSError:
            # Never replace the primary bootstrap failure with cleanup noise.
            continue


def _resolve(config_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise BirthBootstrapError("birth_key_path_invalid")
    path = Path(value)
    return path if path.is_absolute() else config_dir / path


def _resolve_private_db(config_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise BirthBootstrapError("birth_approval_store_invalid")
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != value
        or ".." in parsed.parts
        or len(parsed.parts) not in {1, 2}
        or not parsed.name.endswith(".sqlite")
    ):
        raise BirthBootstrapError("birth_approval_store_invalid")
    return config_dir.joinpath(*parsed.parts)


def _load_author_authority(
    root: Path,
) -> tuple[Ed25519PrivateKey, tuple[tuple[str, Ed25519PublicKey], ...]]:
    """Load the fixed, closed author keystore without exposing path selection."""
    from executor_birth_keystore import BirthKeyStoreError, load_birth_keystore

    try:
        loaded = load_birth_keystore(root)
    except BirthKeyStoreError as exc:
        if exc.code == "birth_keystore_unavailable":
            code = "birth_author_keystore_unavailable"
        elif exc.code == "birth_keystore_unsafe":
            code = "birth_author_keystore_unsafe"
        else:
            code = "birth_author_keystore_invalid"
        raise BirthBootstrapError(code) from exc
    trusted_publics = tuple(
        (
            "author" if key_id == loaded.active_key_id
            else f"author-verifier:{key_id}",
            loaded.verifier_keys[key_id],
        )
        for key_id in sorted(loaded.verifier_keys)
    )
    return loaded.active_private_key, trusted_publics


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

    def _classify_authoring_recovery(self, ref, control, pending) -> str:
        """Validate one journal without changing its authoring control tree."""
        from contract_store import (
            ContractStoreError,
            inspect_birth_authoring_recovery,
        )
        from executor_birth_authoring import authoring_tree_id, observe_tree

        if pending.contract_id != ref.contract_id.value:
            raise BirthBootstrapError("birth_authoring_recovery_ambiguous")
        try:
            current = inspect_birth_authoring_recovery(
                ref,
                new_generation_id=pending.new_generation_id,
                request_id=pending.request_id,
                journal_hash=pending.journal_hash,
                predecessor_generation_id=pending.predecessor_generation_id,
                candidate_id=pending.candidate_id,
                semantic_core_id=pending.semantic_core_id,
                admission_context_id=pending.admission_context_id,
                trusted_publics=self.trusted_publics,
                admission_verifier_keys=self.verifier_keys,
                store_root=self.store_root,
            )
        except ContractStoreError as exc:
            code = (
                "birth_authoring_recovery_receipt_conflict"
                if exc.code == "birth_receipt_binding_invalid"
                else "birth_authoring_recovery_receipt_invalid"
            )
            raise BirthBootstrapError(
                code,
            ) from exc
        if current == pending.new_generation_id:
            if (
                authoring_tree_id(observe_tree(control.canonical))
                != pending.new_tree_id
            ):
                raise BirthBootstrapError("birth_authoring_recovery_ambiguous")
            return "finalize"
        if current == pending.predecessor_generation_id:
            return "rollback"
        raise BirthBootstrapError("birth_authoring_recovery_pointer_conflict")

    def plan_authoring_recovery(self) -> tuple[tuple[object, object, str], ...]:
        """Validate every pending journal in a strictly read-only pass."""
        from manifest_inventory import inventory_authoring_manifests
        from executor_birth_authoring import (
            AuthoringInstallError, authoring_paths, load_prepared_journal,
        )

        inventory = inventory_authoring_manifests()
        if inventory.problems:
            raise BirthBootstrapError("birth_authoring_inventory_invalid")
        result: list[tuple[object, object, str]] = []
        try:
            for ref in inventory.manifests:
                control = authoring_paths(
                    ref.manifest_dir, ref.contract_id.value,
                )
                pending = load_prepared_journal(control)
                if pending is None:
                    continue
                action = self._classify_authoring_recovery(
                    ref, control, pending,
                )
                result.append((ref.contract_id, pending, action))
        except AuthoringInstallError as exc:
            raise BirthBootstrapError(
                "birth_authoring_recovery_ambiguous",
            ) from exc
        return tuple(result)

    def recover_authoring(
        self, plan: tuple[tuple[object, object, str], ...],
    ) -> None:
        # Revalidate and execute the same closed matrix under the publisher's
        # lock order. No durable bootstrap database is opened before ``plan``.
        from manifest_inventory import inventory_authoring_manifests
        from executor_birth_authoring import (
            advance_version, authoring_paths, authoring_token,
            cleanup_transaction, load_prepared_journal, rollback_prepared,
        )
        from contract_store import (
            DEFAULT_LOCK_TIMEOUT,
            _writer_lock, catalog_admission_lock,
        )
        inventory = inventory_authoring_manifests()
        if inventory.problems:
            raise BirthBootstrapError("birth_authoring_inventory_invalid")
        expected = {contract_id: (pending, action) for contract_id, pending, action in plan}
        if len(expected) != len(plan):
            raise BirthBootstrapError("birth_authoring_recovery_ambiguous")
        for ref in inventory.manifests:
            control = authoring_paths(ref.manifest_dir, ref.contract_id.value)
            with catalog_admission_lock(store_root=self.store_root):
                with authoring_token(
                    control.lock, exclusive=True, timeout=DEFAULT_LOCK_TIMEOUT,
                ):
                    with _writer_lock(ref.contract_id, store_root=self.store_root):
                        pending = load_prepared_journal(control)
                        if pending is None:
                            if ref.contract_id in expected:
                                raise BirthBootstrapError(
                                    "birth_authoring_recovery_ambiguous",
                                )
                            continue
                        planned = expected.pop(ref.contract_id, None)
                        if planned is None or planned[0] != pending:
                            raise BirthBootstrapError("birth_authoring_recovery_ambiguous")
                        action = self._classify_authoring_recovery(
                            ref, control, pending,
                        )
                        if action != planned[1]:
                            raise BirthBootstrapError(
                                "birth_authoring_recovery_ambiguous",
                            )
                        if action == "finalize":
                            advance_version(control, pending.contract_id, pending.new_tree_id)
                            cleanup_transaction(control, pending)
                        elif action == "rollback":
                            rollback_prepared(control, pending)
                        else:
                            raise BirthBootstrapError(
                                "birth_authoring_recovery_ambiguous",
                            )
        if expected:
            raise BirthBootstrapError("birth_authoring_recovery_ambiguous")


def _build(
    paths: BirthBootstrapPaths, *, now: Callable[[], datetime],
) -> "_BirthRuntimeState":
    from executor_birth_operational import (
        _assemble_birth_core, _assemble_birth_runtime_bundle,
    )
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
    author_private_key, trusted_publics = _load_author_authority(
        config_dir / _AUTHOR_KEYSTORE_BASENAME,
    )
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
    from executor_birth_approval_authority import load_approval_authority
    try:
        approval_authority = load_approval_authority(
            _resolve(config_dir, approval["authority_registry"])
        )
    except Exception as exc:
        raise BirthBootstrapError("birth_approval_authority_invalid") from exc
    context_builder = _context_builder(value["context"], config_dir)
    try:
        from executor_birth_semantic_authority import load_semantic_authority
        semantic_authority = load_semantic_authority(value["semantic_review"], config_dir)
    except Exception as exc:
        raise BirthBootstrapError("semantic_review_unavailable") from exc
    verifier = _PostconditionAdapter(
        trusted_publics=trusted_publics,
        verifier_keys=verifiers,
    )
    producer_db = paths.state_dir / "producer-receipts.sqlite"
    approval_db = _resolve_private_db(config_dir, approval["db_path"])
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
        author_private_key=author_private_key,
        publisher_options={"trusted_publics": trusted_publics},
        postcondition_verifier=verifier.verify,
    )
    factories = {cap: _request_factory(auth, registry, producer_db, ttl, now, context_builder)
                 for cap, auth in authorities.items()}
    bundle = _assemble_birth_runtime_bundle(core, factories)

    # No filesystem mutation precedes validation and assembly of every
    # configured authority and the complete read-only recovery plan. Durable
    # databases are created together and compensated if the second target or
    # the locked recovery revalidation fails.
    recovery_plan = verifier.plan_authoring_recovery()
    created_paths: list[_CreatedPrivatePath] = []
    try:
        if _secure_state_db(
            paths.state_dir, created_paths=created_paths,
        ) != producer_db:
            raise BirthBootstrapError("birth_state_unavailable")
        if _secure_approval_db(
            approval_db,
            config_dir=config_dir,
            created_paths=created_paths,
        ) != approval_db:
            raise BirthBootstrapError("birth_approval_store_unavailable")
        if recovery_plan:
            verifier.recover_authoring(recovery_plan)
    except BaseException:
        _rollback_created_private_paths(created_paths)
        raise
    return bundle


def bootstrap_birth_runtime() -> BirthRuntimeVerificationView:
    """Install fixed roots once and return public verification data only."""
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
        state = _build(
            default_birth_bootstrap_paths(),
            now=lambda: datetime.now(timezone.utc),
        )
        from executor_birth_operational import _install_birth_runtime_bundle
        _install_birth_runtime_bundle(state)
    except BaseException as exc:
        with _BOOT_LOCK:
            _BOOT_ERROR = exc; _BOOT_STATE = "failed"; _BOOT_LOCK.notify_all()
        raise
    with _BOOT_LOCK:
        _BOOT_STATE = "ready"; _BOOT_LOCK.notify_all()
    return state.verification


def require_birth_runtime_before_workers() -> None:
    """Install the Birth authority before any mutating worker starts.

    RM-0008 group 2 has not provisioned the authority set yet, so on an
    installation that still lacks ``birth/bootstrap.json`` the sealed runtime
    simply does not exist.  That is the declared ``prepared_not_active`` state,
    not a failure: the analysis document forbids group 2 from making the closed
    path binding before the cutover.  Refusing to boot there would make the
    service unstartable, which is what happened between commit ea9cd0ab and
    this change.  Every other bootstrap error stays fatal.
    """
    try:
        bootstrap_birth_runtime()
    except BirthBootstrapError as exc:
        if str(exc) != "birth_bootstrap_config_unavailable":
            raise
        import logging

        logging.getLogger(__name__).warning(
            "Birth runtime not provisioned yet (%s): continuing without the "
            "sealed authority, as required before the RM-0008 cutover",
            exc,
        )

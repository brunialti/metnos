"""Sealed RM-0008 F5 lifecycle integration.

The module stays unreachable from productive routing until an operator-issued,
authenticated F5 certification record is loaded. Lifecycle publications are
accepted only after the caller's independent RM-0007 reread returns the exact
AdmissionReceipt and predecessor relation expected by this coordinator.
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_canonical import decode_canonical_ascii_v1, encode_canonical_ascii_v1
from executor_birth_authority_files import (
    DEFAULT_OWNERSHIP_ROOT_V1, _directory_metadata, _read_regular, _root_owned_chain,
)
from executor_birth_certification_authority import CertificationPublicKeyV1

from executor_birth_epoch_store import (
    BirthLifecycle, EpochCacheKey, EpochReplacement, replace_current_epoch,
)
from executor_birth_receipts import AdmissionReceipt, ApprovedLifecycle
from manifest_inventory import ContractId


CERTIFICATION_DOMAIN = b"metnos.executor-birth.f5-activation/v1\0"
INSTALLATION_DOMAIN = b"metnos.executor-birth.f5-installation/v1\0"
ACTIVATION_DIRECTORY = DEFAULT_OWNERSHIP_ROOT_V1 / "certification-v1"
ACTIVATION_MAX_BYTES = 8192
ACTIVATION_POLICY = "rm0008-f5/1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ACTIVATION_SEAL = object()


class LifecycleError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    return encode_canonical_ascii_v1(value)


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise LifecycleError("lifecycle_binding_invalid", field)
    return value


@dataclass(frozen=True, slots=True)
class F5Certification:
    certificate_id: str
    installation_id: str
    qualification_id: str
    head_id: str
    closed_build_id: str
    migration_id: str
    policy_id: str
    key_id: str
    signature: str


class F5Activation:
    """Internal result of authenticating a durable certificate.

    The private codec is a fixture seam, not an authority boundary against
    arbitrary Python code. Productive consumers use the fixed-root reader.
    """
    __slots__ = ("certificate",)

    def __init__(self, certificate: F5Certification, *, _seal: object) -> None:
        if _seal is not _ACTIVATION_SEAL:
            raise LifecycleError("f5_activation_forbidden")
        self.certificate = certificate


def _decode_f5_activation_v1(
    encoded: bytes, *, authority: CertificationPublicKeyV1,
    installation_id: str, head_id: str, closed_build_id: str,
) -> F5Activation:
    """Decode an administrator's attestation, never derive qualification here.

    This parameterized codec is private. Only the fixed-root entry supplies
    productive trust and installation facts; fixtures do not qualify Births.
    """
    try:
        value = decode_canonical_ascii_v1(encoded, maximum=ACTIVATION_MAX_BYTES)
        expected = {"schema_version", "purpose", "installation_id", "qualification_id",
                    "head_id", "closed_build_id", "migration_id", "policy_id", "key_id", "signature"}
        if (type(value) is not dict or set(value) != expected
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["purpose"] != "f5_activation_v1"
                or value["policy_id"] != ACTIVATION_POLICY):
            raise ValueError("schema or policy")
        for field in ("installation_id", "qualification_id", "head_id", "closed_build_id", "migration_id"):
            _digest(value[field], field)
        if (value["installation_id"] != _digest(installation_id, "installation_id")
                or value["head_id"] != _digest(head_id, "head_id")
                or value["closed_build_id"] != _digest(closed_build_id, "closed_build_id")):
            raise ValueError("installation or required release")
        if (type(authority) is not CertificationPublicKeyV1 or authority.status != "active"
                or not isinstance(authority.public_key, Ed25519PublicKey)
                or value["key_id"] != authority.key_id):
            raise ValueError("authority")
        signature = value["signature"]
        if type(signature) is not str:
            raise ValueError("signature")
        raw_signature = base64.b64decode(signature, validate=True)
        if (len(raw_signature) != 64
                or base64.b64encode(raw_signature).decode("ascii") != signature):
            raise ValueError("noncanonical signature")
        payload = CERTIFICATION_DOMAIN + _canonical({
            key: item for key, item in value.items() if key != "signature"
        })
        authority.public_key.verify(raw_signature, payload)
    except (TypeError, ValueError, InvalidSignature, LifecycleError, RecursionError) as exc:
        raise LifecycleError("f5_activation_invalid", "document, authority or binding") from exc
    cert = F5Certification(
        "sha256:" + hashlib.sha256(payload).hexdigest(),
        value["installation_id"], value["qualification_id"], value["head_id"],
        value["closed_build_id"], value["migration_id"], value["policy_id"],
        value["key_id"], value["signature"],
    )
    return F5Activation(cert, _seal=_ACTIVATION_SEAL)


def _installation_id_v1(registries) -> str:
    identities = {}
    for purpose in ("distribution", "cutover", "head"):
        entries = tuple(getattr(registries, purpose).keys.values())
        if len(entries) != 1:
            raise LifecycleError("f5_activation_invalid", "ownership registry")
        identities[purpose] = entries[0].key_id
    return "sha256:" + hashlib.sha256(INSTALLATION_DOMAIN + _canonical(identities)).hexdigest()


def load_f5_activation() -> F5Activation:
    """Authenticate only the fixed installation's current F5 attestation.

    Missing or revoked material refuses this entry, without provisioning,
    opening private keys or falling back to a caller's certificate. Ordinary
    F4 startup does not call this optional reader.
    """
    from executor_birth_certification_authority import load_certification_public_key_v1
    from executor_birth_ownership_authorities import load_ownership_public_registries_v1
    from executor_birth_ownership_chain import (
        inspect_required_ownership_v1, VerifiedOwnershipWindowV1, OwnershipChainStore,
    )

    public = load_certification_public_key_v1()
    registries = load_ownership_public_registries_v1()
    _root_owned_chain(ACTIVATION_DIRECTORY)
    _directory_metadata(ACTIVATION_DIRECTORY, root_owned=True)
    path = ACTIVATION_DIRECTORY / "active.json"
    encoded = _read_regular(path, maximum=ACTIVATION_MAX_BYTES, mode=0o644, root_owned=True)
    window = inspect_required_ownership_v1()
    if type(window) is not VerifiedOwnershipWindowV1:
        raise LifecycleError("f5_activation_invalid", "required ownership window")
    activation = _decode_f5_activation_v1(
        encoded, authority=public, installation_id=_installation_id_v1(registries),
        head_id=window.required_head.head_id,
        closed_build_id=window.required_distribution.identity.closed_build_id,
    )
    if (_read_regular(path, maximum=ACTIVATION_MAX_BYTES, mode=0o644, root_owned=True) != encoded
            or load_certification_public_key_v1().key_id != public.key_id
            or _installation_id_v1(load_ownership_public_registries_v1()) != activation.certificate.installation_id
            or OwnershipChainStore().read_required_head().head_id != activation.certificate.head_id):
        raise LifecycleError("f5_activation_invalid", "changed activation frontier")
    return activation


@dataclass(frozen=True, slots=True)
class LifecyclePublication:
    """Result of publication followed by independent authenticated reread."""
    receipt: AdmissionReceipt
    encoded_admission_receipt: bytes
    reread_generation_id: str
    reread_predecessor_id: str | None
    reread_lifecycle: ApprovedLifecycle


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    publication: LifecyclePublication
    epochs: EpochReplacement


PublishRevision = Callable[[ContractId, str, BirthLifecycle, str | None], LifecyclePublication]
VerifyAdmission = Callable[[bytes], AdmissionReceipt]


class LifecycleCoordinator:
    """Lifecycle ordering primitive; productive owner composition is required."""
    __slots__ = ("_db_path", "_publish", "_verify_admission")

    def __init__(self, activation: F5Activation, *, db_path: Path,
                 publish_and_reread: PublishRevision,
                 verify_admission: VerifyAdmission) -> None:
        if not isinstance(activation, F5Activation):
            raise LifecycleError("f5_activation_required")
        if (not isinstance(db_path, Path) or not callable(publish_and_reread)
                or not callable(verify_admission)):
            raise LifecycleError("lifecycle_binding_invalid", "dependencies")
        self._db_path, self._publish = db_path, publish_and_reread
        self._verify_admission = verify_admission

    def revise(
        self, key: EpochCacheKey, *, expected_version: int,
        target: BirthLifecycle, name: str, source: str, occurred_at: str,
        historic_epoch_ref: str | None = None,
    ) -> LifecycleResult:
        if target not in {BirthLifecycle.PREEXERCISE, BirthLifecycle.ACTIVE,
                          BirthLifecycle.QUARANTINED}:
            raise LifecycleError("lifecycle_transition_invalid", "target")
        allowed = {
            BirthLifecycle.SYNTHESIZED: {BirthLifecycle.PREEXERCISE,
                                         BirthLifecycle.QUARANTINED},
            BirthLifecycle.PREEXERCISE: {BirthLifecycle.ACTIVE,
                                         BirthLifecycle.QUARANTINED},
            BirthLifecycle.ACTIVE: {BirthLifecycle.QUARANTINED},
        }
        # An active-to-active revision is reserved exclusively for rollback:
        # its new generation points at a separately authenticated historic
        # epoch and receives fresh counters.
        rollback = (key.lifecycle is BirthLifecycle.ACTIVE
                    and target is BirthLifecycle.ACTIVE
                    and historic_epoch_ref is not None)
        if not rollback and target not in allowed.get(key.lifecycle, set()):
            raise LifecycleError("lifecycle_transition_invalid", "edge")
        publication = self._publish(
            key.contract_id, key.generation_id, target, historic_epoch_ref)
        self._verify_publication(publication, key, target)
        replacement = replace_current_epoch(
            contract_id=key.contract_id,
            expected_generation_id=key.generation_id,
            expected_state_version=expected_version,
            generation_id=publication.reread_generation_id,
            name=name, source=source, lifecycle=target, observed_at=occurred_at,
            db_path=self._db_path,
            event_kind=f"lifecycle_{target.value}",
            historic_epoch_ref=historic_epoch_ref,
        )
        return LifecycleResult(publication, replacement)

    def quarantine_before_nonselection(self, key: EpochCacheKey, *, expected_version: int,
                                       name: str, source: str,
                                       occurred_at: str) -> LifecycleResult:
        """Publish/reread quarantine before the local selection CAS commits."""
        return self.revise(key, expected_version=expected_version,
                           target=BirthLifecycle.QUARANTINED, name=name,
                           source=source, occurred_at=occurred_at)

    def _verify_publication(self, publication: object, predecessor: EpochCacheKey,
                            target: BirthLifecycle) -> None:
        if not isinstance(publication, LifecyclePublication):
            raise LifecycleError("lifecycle_reread_invalid", "type")
        if not isinstance(publication.encoded_admission_receipt, bytes):
            raise LifecycleError("lifecycle_reread_invalid", "admission receipt bytes")
        try:
            receipt = self._verify_admission(publication.encoded_admission_receipt)
        except Exception as exc:
            raise LifecycleError("lifecycle_reread_invalid", "admission authentication") from exc
        if (not isinstance(receipt, AdmissionReceipt)
                or not isinstance(publication.receipt, AdmissionReceipt)
                or receipt.receipt_id != publication.receipt.receipt_id):
            raise LifecycleError("lifecycle_reread_invalid", "admission receipt")
        lifecycle = ApprovedLifecycle(target.value)
        if (receipt.contract_id != predecessor.contract_id.value
                or receipt.generation_id != publication.reread_generation_id
                or receipt.predecessor_id != predecessor.generation_id
                or receipt.approved_lifecycle is not lifecycle
                or publication.reread_predecessor_id != predecessor.generation_id
                or publication.reread_lifecycle is not lifecycle):
            raise LifecycleError("lifecycle_reread_invalid", "binding")

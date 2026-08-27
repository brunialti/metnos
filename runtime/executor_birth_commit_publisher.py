"""The private commit link of increment 2E.

Section 5.3 fixes the shape.  Moving the private key out of the caller is not
enough: leaving the issuer, the verifier, the epoch resolver or the store
primitive selectable would allow the same swap of authority under another
name.  So the core may hand over facts and nothing else, and every authority
is sealed inside the publisher built by the private module.

The publisher of this increment is built for the isolated proof only.  It is
not installed in the global bundle and it activates no Producer: that belongs
to group 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

_PUBLISHER_TOKEN = object()

PREPARED_BUNDLE_STATE_V1 = "prepared_not_active"


class BirthCommitLinkError(RuntimeError):
    """The commit link was asked for something it must never accept."""

    @property
    def code(self) -> str:
        """The stable code, for the caller that turns it into an outcome."""
        return str(self.args[0]) if self.args else "birth_commit_link_invalid"


@dataclass(frozen=True, slots=True)
class BirthCommitFactsV1:
    """Everything the core knows, and nothing it could use to choose authority.

    Every field is a value: no callable, no module, no key, no path and no
    already-built authorization may travel here (section 5.3, point 4).
    """

    manifest_ref: object
    snapshot: object
    request_id: str
    policy_version: str
    contract_id: str
    candidate_id: str
    semantic_core_id: str
    admission_context_id: str
    expected_generation_id: str | None
    predecessor_id: str | None
    predecessor_snapshot_id: str | None
    revision_facts_id: str | None
    observed_context_epoch: str
    producer_receipt_hash: str
    revision_class: object
    approved_lifecycle: object
    check_results: Mapping[str, object]
    semantic_review_hash: str | None
    approval_hash: str | None
    issued_at: str

    def __post_init__(self) -> None:
        for name in self.__slots__:
            value = getattr(self, name)
            if callable(value) and not isinstance(value, type):
                raise BirthCommitLinkError("birth_commit_facts_invalid")
        # ``contract_id`` is a typed identity of the inventory, not a string,
        # so it is checked for shape by the store and only for being a value
        # here.
        # ``birth_request_id`` is not a field: the store passes it to the
        # issuer at the exact commit point, so carrying a second copy here
        # would be a value nobody reads.
        for name in (
            "request_id", "policy_version",
            "candidate_id", "semantic_core_id", "admission_context_id",
            "observed_context_epoch", "producer_receipt_hash", "issued_at",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise BirthCommitLinkError("birth_commit_facts_invalid")
        object.__setattr__(
            self, "check_results", MappingProxyType(dict(self.check_results))
        )


@dataclass(frozen=True, slots=True)
class PreparedBundleViewV1:
    """The only thing the outside may hold about a prepared bundle.

    It carries identifiers and public keys.  It never leads to the core, to a
    signing closure, to a private key, to an issuer or to a factory
    (section 5.4).
    """

    version: int
    author_active_key_id: str
    author_public_keys: Mapping[str, Ed25519PublicKey]
    admission_active_key_id: str
    admission_public_keys: Mapping[str, Ed25519PublicKey]
    set_id: str
    prepared_admission_context_id: str
    prepared_context_epoch: str
    state: str = PREPARED_BUNDLE_STATE_V1

    def __post_init__(self) -> None:
        if self.state != PREPARED_BUNDLE_STATE_V1 or self.version != 1:
            raise BirthCommitLinkError("prepared_bundle_view_invalid")
        for field in ("author_public_keys", "admission_public_keys"):
            keys = dict(getattr(self, field))
            if not keys or any(
                not isinstance(value, Ed25519PublicKey) for value in keys.values()
            ):
                raise BirthCommitLinkError("prepared_bundle_view_invalid")
            object.__setattr__(self, field, MappingProxyType(keys))


class _BirthCommitPublisher:
    """Sealed authority: facts in, publication out.

    The issuer, the verifier, the epoch resolver and the store primitive are
    fixed at construction.  Nothing about them can be chosen, replaced or read
    back through this object.
    """

    __slots__ = (
        "_author_private", "_author_ring", "_admission_private",
        "_admission_key_id", "_admission_verifiers", "_epoch",
        "_primitive", "_store_root",
    )

    def __init__(
        self,
        token: object,
        *,
        author_private: Ed25519PrivateKey,
        author_ring: tuple,
        admission_private: Ed25519PrivateKey,
        admission_key_id: str,
        admission_verifiers: Mapping[str, Ed25519PublicKey],
        prepared_context_epoch: str,
        primitive,
        store_root,
    ) -> None:
        if token is not _PUBLISHER_TOKEN:
            raise BirthCommitLinkError("birth_commit_publisher_private")
        if not isinstance(author_private, Ed25519PrivateKey) or not isinstance(
            admission_private, Ed25519PrivateKey
        ):
            raise BirthCommitLinkError("birth_commit_publisher_invalid")
        if not admission_verifiers or not isinstance(prepared_context_epoch, str):
            raise BirthCommitLinkError("birth_commit_publisher_invalid")
        self._author_private = author_private
        self._author_ring = tuple(author_ring)
        self._admission_private = admission_private
        self._admission_key_id = admission_key_id
        self._admission_verifiers = MappingProxyType(dict(admission_verifiers))
        self._epoch = prepared_context_epoch
        self._primitive = primitive
        self._store_root = store_root

    def commit(self, facts: BirthCommitFactsV1) -> "BirthCommitOutcomeV1":
        """Publish one admitted snapshot through the single owned primitive.

        The issued receipt travels back with the publication instead of being
        collected in a list the publisher would have to keep: a sealed object
        shared by concurrent births must not carry mutable state.
        """
        from contract_store import BirthCommitAuthorization
        from executor_birth_receipts import (
            AdmissionCheck, AdmissionKind, AdmittedCheckStatus,
            issue_admission_receipt, verify_admission_receipt,
        )

        if not isinstance(facts, BirthCommitFactsV1):
            raise BirthCommitLinkError("birth_commit_facts_required")
        # The publisher owns the prepared epoch and receives the observed one:
        # a disagreement means the context moved between the observation and
        # this commit, and it is refused here rather than deeper down.
        if facts.observed_context_epoch != self._epoch:
            raise BirthCommitLinkError("birth_context_changed")

        issued: list[bytes] = []

        def issuer(generation_id, _payload_hashes, birth_request_id, journal_hash):
            checks = dict(facts.check_results)
            checks["authoring_install_journal_v1"] = AdmissionCheck(
                "1", AdmittedCheckStatus.PASSED, journal_hash,
            )
            encoded = issue_admission_receipt(
                policy_version=facts.policy_version,
                contract_id=facts.contract_id,
                generation_id=generation_id,
                candidate_id=facts.candidate_id,
                semantic_core_id=facts.semantic_core_id,
                admission_context_id=facts.admission_context_id,
                birth_request_id=birth_request_id,
                authoring_journal_hash=journal_hash,
                predecessor_id=facts.predecessor_id,
                producer_receipt_hash=facts.producer_receipt_hash,
                revision_class=facts.revision_class,
                check_results=checks,
                semantic_review_hash=facts.semantic_review_hash,
                approval_hash=facts.approval_hash,
                approved_lifecycle=facts.approved_lifecycle,
                kind=AdmissionKind.ADMISSION,
                issued_at=facts.issued_at,
                key_id=self._admission_key_id,
                private_key=self._admission_private,
            )
            issued.append(encoded)
            return encoded

        def verifier(encoded):
            return verify_admission_receipt(
                encoded, verifier_keys=self._admission_verifiers,
            )

        authorization = BirthCommitAuthorization(
            facts.candidate_id, facts.semantic_core_id,
            facts.admission_context_id, facts.predecessor_id, issuer, verifier,
            predecessor_snapshot_id=facts.predecessor_snapshot_id,
            revision_facts_id=facts.revision_facts_id,
            context_epoch=facts.observed_context_epoch,
            context_epoch_resolver=self._resolve_epoch,
        )
        publication = self._primitive(
            facts.manifest_ref,
            expected_generation_id=facts.expected_generation_id,
            snapshot=facts.snapshot,
            request_id=facts.request_id,
            private_key=self._author_private,
            trusted_publics=self._author_ring,
            birth_authorization=authorization,
            store_root=self._store_root,
        )
        return BirthCommitOutcomeV1(publication, issued[-1] if issued else None)

    @property
    def store_root(self):
        """Where the store lives, for the callers that must lock around it."""
        return self._store_root

    def resolve_predecessor(self, request):
        """Authenticate the predecessor with the ring this publisher owns.

        The core used to receive the trusted ring as an option and pass it
        around; owning both ends here means there is one place that knows
        which keys authenticate a generation, and no caller can substitute it.
        """
        from contract_store import authenticate_birth_predecessor

        return authenticate_birth_predecessor(
            request.manifest_ref,
            trusted_publics=self._author_ring,
            store_root=self._store_root,
        )

    def _resolve_epoch(self) -> str:
        return self._epoch


@dataclass(frozen=True, slots=True)
class BirthCommitOutcomeV1:
    """What one commit produced: the publication and the receipt it issued."""

    publication: object
    admission_receipt: bytes | None


@dataclass(frozen=True, slots=True)
class _PreparedBirthBundleV1:
    """Private module state: the publisher and the view it may show."""

    publisher: _BirthCommitPublisher
    view: PreparedBundleViewV1

    def __post_init__(self) -> None:
        if not isinstance(self.publisher, _BirthCommitPublisher):
            raise BirthCommitLinkError("prepared_bundle_invalid")
        if not isinstance(self.view, PreparedBundleViewV1):
            raise BirthCommitLinkError("prepared_bundle_invalid")


def _build_prepared_bundle_v1(
    *,
    author,
    admission,
    set_id: str,
    prepared_admission_context_id: str,
    prepared_context_epoch: str,
    store_root,
) -> _PreparedBirthBundleV1:
    """Bind the authenticated key material to one sealed publisher.

    The two stores arrive already loaded and authenticated by the productive
    loader, so this function never opens a path and never chooses an identity.
    """
    from contract_store import commit_birth_snapshot

    # ``TrustedPublic`` is an alias for the pair, so the ring is built as the
    # pairs themselves and stays a value.
    ring = tuple(sorted(author.verifier_keys.items()))
    publisher = _BirthCommitPublisher(
        _PUBLISHER_TOKEN,
        author_private=author.active_private_key,
        author_ring=ring,
        admission_private=admission.active_private_key,
        admission_key_id=admission.active_key_id,
        admission_verifiers=admission.verifier_keys,
        prepared_context_epoch=prepared_context_epoch,
        primitive=commit_birth_snapshot,
        store_root=store_root,
    )
    view = PreparedBundleViewV1(
        version=1,
        author_active_key_id=author.active_key_id,
        author_public_keys=dict(author.verifier_keys),
        admission_active_key_id=admission.active_key_id,
        admission_public_keys=dict(admission.verifier_keys),
        set_id=set_id,
        prepared_admission_context_id=prepared_admission_context_id,
        prepared_context_epoch=prepared_context_epoch,
    )
    return _PreparedBirthBundleV1(publisher, view)


__all__ = [
    "BirthCommitFactsV1", "BirthCommitLinkError", "BirthCommitOutcomeV1",
    "PREPARED_BUNDLE_STATE_V1", "PreparedBundleViewV1",
]

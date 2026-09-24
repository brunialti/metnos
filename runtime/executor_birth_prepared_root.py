"""The single read-only door the runtime has onto the prepared Birth root.

Group 2 gave the mutating capability exactly one door, on the installer side.
The runtime must read the prepared set at every start, so it needs a door of
its own — and it must be a different kind of door: this module builds an
authenticated descriptor, adopts it and hands back a session it never uses to
create, rename or remove anything.

Nothing here chooses a path: the location is the fixed Birth root of the
installation, resolved once from the configuration of the installation itself.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

BIRTH_ROOT_BASENAME_V1 = "birth"


class PreparedRootError(RuntimeError):
    """The prepared Birth root cannot be opened for reading."""

    def __init__(self, code: str, cause: BaseException | None = None) -> None:
        self.code = code
        self._internal_cause = cause
        super().__init__(code)
        self.__suppress_context__ = True

    @property
    def __cause__(self) -> None:
        return None

    @__cause__.setter
    def __cause__(self, value: BaseException | None) -> None:
        if value is not None and self._internal_cause is None:
            self._internal_cause = value


def _productive_role_catalog_v1():
    """The whole closed grammar of the layout, with no exact binding."""
    from executor_birth_secure_fs import _BirthRoleCatalogV1, _BirthRolePatternV1

    return _BirthRoleCatalogV1(
        schema_version=1,
        patterns=tuple(_BirthRolePatternV1),
        exact_bindings=(),
        generation=0,
    )


def _posix_prepared_owner_uid_v1(handles: list[int]) -> int:
    """Bind a privileged reader to the configured service-owned Birth root."""
    from executor_birth_secure_fs import BirthSecureFSError

    if len(handles) < 2:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    try:
        root = os.fstat(handles[-1])
        parent = os.fstat(handles[-2])
        caller = os.geteuid()
    except OSError as exc:
        raise BirthSecureFSError("birth_provisioning_io_unavailable", exc) from None
    if caller != 0:
        expected = caller
    elif root.st_uid != 0 and parent.st_uid == root.st_uid:
        expected = root.st_uid
    else:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    if root.st_uid != expected:
        raise BirthSecureFSError("birth_provisioning_acl_unsafe")
    return expected


def open_prepared_root_session_v1():
    """Open the fixed Birth root for reading and return the session.

    The caller receives a session, not a path, and this module performs no
    mutation of its own: the productive graph of the acceptance base is what
    keeps that promise honest, not this docstring.
    """
    import config as runtime_config
    from executor_birth_secure_fs import (
        BirthSecureFSError, _AuthenticatedRootDescriptor, _PlatformIdentity,
        _adopt_authenticated_root, _open_posix_root, _open_win_root, _win_close,
        _windows_service_sid_for_current_process,
    )

    root = Path(runtime_config.PATH_USER_CONFIG) / BIRTH_ROOT_BASENAME_V1
    try:
        if os.name == "nt":
            handles, absolute = _open_win_root(root)
            identity = _PlatformIdentity(
                posix_uid=None,
                windows_service_sid=_windows_service_sid_for_current_process(),
            )
        else:
            handles, absolute = _open_posix_root(
                root, exact_private=False, expected_uid=None,
            )
            try:
                expected_uid = _posix_prepared_owner_uid_v1(handles)
            except BaseException:
                for handle in reversed(handles):
                    os.close(handle)
                raise
            identity = _PlatformIdentity(
                posix_uid=expected_uid, windows_service_sid=None,
            )
    except BirthSecureFSError as exc:
        raise PreparedRootError(exc.code, exc) from None
    try:
        descriptor = _AuthenticatedRootDescriptor(
            handles=tuple(handles),
            root_path=absolute,
            identity=identity,
            role_catalog=_productive_role_catalog_v1(),
        )
    except BaseException:
        closer = _win_close if os.name == "nt" else os.close
        for handle in reversed(tuple(handles)):
            closer(handle)
        raise
    try:
        return _adopt_authenticated_root(descriptor)
    except BirthSecureFSError as exc:
        raise PreparedRootError(exc.code, exc) from None


def open_distribution_sources_v1():
    """Open the installed distribution read-only, to rebuild the material."""
    import config as runtime_config
    from executor_birth_secure_fs import BirthSecureFSError, _open_legacy_root_session

    try:
        return _open_legacy_root_session(
            Path(runtime_config.PATH_RUNTIME), exact_private=False,
        )
    except BirthSecureFSError as exc:
        raise PreparedRootError(exc.code, exc) from None


def _open_distribution_sources_for_verified_v1(distribution):
    """Open only the installation root carried by a verified distribution."""
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_secure_fs import BirthSecureFSError, _open_legacy_root_session

    if not is_verified_distribution(distribution):
        raise PreparedRootError("birth_context_selection_invalid")
    try:
        return _open_legacy_root_session(
            Path(distribution.installation_root) / "runtime",
            exact_private=False,
        )
    except BirthSecureFSError as exc:
        raise PreparedRootError(exc.code, exc) from None


def read_prepared_set_v1():
    """Open, revalidate and close: the runtime holds no session afterwards.

    Section 9.4 forbids trusting the recorded description: the material is
    rebuilt from the installed distribution and every digest is compared here,
    under the same lock that read the set.
    """
    from executor_birth_context_v1 import (
        ContextMaterialError, prepare_context_material_v1,
    )
    from executor_birth_prepared_set import (
        AUTHORITY_SETS_BASENAME_V1, PreparedSetError, authority_registry_v1,
        load_prepared_set_v1,
    )

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            prepared = load_prepared_set_v1(session)
            registry = authority_registry_v1(
                session, (AUTHORITY_SETS_BASENAME_V1, prepared.set_id),
            )
            sources = open_distribution_sources_v1()
            try:
                rebuilt = prepare_context_material_v1(sources, registry)
            except ContextMaterialError as exc:
                raise PreparedRootError(exc.code, exc) from None
            finally:
                sources.close()
            if (
                rebuilt.material_sha256 != prepared.context_material_sha256
                or rebuilt.prepared_admission_context_id
                != prepared.prepared_admission_context_id
                or rebuilt.prepared_context_epoch
                != prepared.prepared_context_epoch
            ):
                # The installed distribution no longer produces the material
                # the set describes.  That is a mismatch to report, never a
                # reason to adopt what is on disk.
                raise PreparedSetError("birth_prepared_set_mismatch")
    return prepared


def _load_historical_transition_anchor_v1():
    """Read the immutable V1 anchor without selecting it for runtime use.

    The first F4 transition exists because the new verified distribution no
    longer produces the V1 context.  Rebuilding the anchor with that new
    distribution would therefore make the transition impossible.  This door
    validates only the persisted marker, set, key inventories and material
    digests under the Birth barrier.  It does not return runtime authorities
    and it does not make mismatched V1 material executable.
    """
    from executor_birth_prepared_set import load_prepared_set_v1

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            return load_prepared_set_v1(session)


@dataclass(frozen=True, slots=True)
class HistoricalTransitionVerifiersV1:
    """Persisted V1 identity and public verification keys, never a runtime."""

    prepared: object
    author_verifier_keys: Mapping[str, object]
    admission_verifier_keys: Mapping[str, object]

    def __post_init__(self) -> None:
        for field in ("author_verifier_keys", "admission_verifier_keys"):
            object.__setattr__(
                self, field, MappingProxyType(dict(getattr(self, field))),
            )


def _load_historical_transition_verifiers_v1() -> HistoricalTransitionVerifiersV1:
    """Authenticate existing V1 artifacts without selecting runtime authority.

    The first transition must read the historical catalog before preparing its
    new context.  Its marker, set, material and key bindings remain validated
    under one barrier; no private keys or executable authorities escape this
    reader.  Runtime construction still rebuilds the context independently.
    """
    from executor_birth_keystore import (
        BirthKeyStoreError, _load_birth_keystore_in_session,
    )
    from executor_birth_prepared_set import (
        AUTHORITY_SETS_BASENAME_V1, AUTHOR_STORE_BASENAME_V1, PreparedSetError,
        load_prepared_set_v1,
    )

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            prepared = load_prepared_set_v1(session)
            try:
                author = _load_birth_keystore_in_session(
                    (AUTHOR_STORE_BASENAME_V1,), session,
                )
                admission = _load_birth_keystore_in_session(
                    (AUTHORITY_SETS_BASENAME_V1, prepared.set_id, "admission"),
                    session,
                )
            except BirthKeyStoreError as exc:
                raise PreparedSetError(
                    "birth_prepared_set_unavailable", exc,
                ) from None
            return HistoricalTransitionVerifiersV1(
                prepared=prepared,
                author_verifier_keys=author.verifier_keys,
                admission_verifier_keys=admission.verifier_keys,
            )


def _load_sealed_authorities_from_set_v1(
    session, prepared, open_sources, *, previous_distribution: bool = False,
):
    """Load one already selected set while its root barrier is held."""
    from executor_birth_context import _context_epoch
    from executor_birth_context_v1 import (
        ContextMaterialError, prepare_context_material_v1,
        rebuild_previous_context_material_v1,
    )
    from executor_birth_keystore import (
        BirthKeyStoreError, _load_birth_keystore_in_session,
    )
    from executor_birth_prepared_set import (
        AUTHORITY_SETS_BASENAME_V1, AUTHOR_STORE_BASENAME_V1, PreparedSetError,
        authority_registry_v1,
    )
    from executor_birth_sandbox_registry_v1 import (
        SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1,
        decode_sandbox_registry_v1,
    )
    from executor_birth_semantic_authority import (
        _load_semantic_authority_in_session,
    )

    location = (AUTHORITY_SETS_BASENAME_V1, prepared.set_id)
    registry = authority_registry_v1(session, location)
    sources = open_sources()
    try:
        rebuild = (rebuild_previous_context_material_v1 if previous_distribution
                   else prepare_context_material_v1)
        rebuilt = rebuild(sources, registry)
    except ContextMaterialError as exc:
        raise PreparedRootError(exc.code, exc) from None
    finally:
        sources.close()
    if (
        rebuilt.material_sha256 != prepared.context_material_sha256
        or rebuilt.prepared_admission_context_id
        != prepared.prepared_admission_context_id
        or rebuilt.prepared_context_epoch != prepared.prepared_context_epoch
    ):
        raise PreparedSetError("birth_prepared_set_mismatch")
    try:
        author = _load_birth_keystore_in_session(
            (AUTHOR_STORE_BASENAME_V1,), session,
        )
        admission = _load_birth_keystore_in_session(
            location + ("admission",), session,
        )
        producers = {
            name: _load_birth_keystore_in_session(
                location + ("producers", name), session,
            )
            for name in sorted(registry["producers"])
        }
    except BirthKeyStoreError as exc:
        raise PreparedSetError(
            "birth_prepared_set_unavailable", exc,
        ) from None
    semantic = _load_semantic_authority_in_session(
        location + ("semantic", "authority.json"),
        location + ("semantic", "public"),
        location + ("semantic", "evidence"),
        session,
    )._seal_for_detached_use_v1()
    approval_document = _read_prepared_document_v1(
        session, location + ("approval", "authority.json"),
    )
    sandbox_document = _read_prepared_document_v1(
        session,
        location + (
            SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1,
        ),
    )
    from executor_birth_approval_authority import _decode_approval_authority

    return SealedAuthoritiesV1(
        sandbox=decode_sandbox_registry_v1(sandbox_document),
        prepared=prepared,
        author=author,
        admission=admission,
        producers=producers,
        approval=_decode_approval_authority(approval_document),
        semantic=semantic,
        context_epoch=_context_epoch(prepared.prepared_admission_context_id),
        material=rebuilt,
    )


def load_sealed_authorities_v1():
    """Load the historical marker-selected set through the fixed roots."""
    from executor_birth_prepared_set import load_prepared_set_v1

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            prepared = load_prepared_set_v1(session)
            return _load_sealed_authorities_from_set_v1(
                session, prepared, open_distribution_sources_v1,
            )


def _read_prepared_document_v1(session, components: tuple[str, ...]) -> bytes:
    from executor_birth_prepared_set import read_document_v1

    return read_document_v1(session, components)


@dataclass(frozen=True, slots=True)
class SealedAuthoritiesV1:
    """Key material and values read under one barrier; no session inside."""

    prepared: object
    author: object
    admission: object
    producers: Mapping[str, object]
    approval: object
    semantic: object
    # The measured backend, or ``None`` on a machine measured without one.
    sandbox: object
    context_epoch: str
    material: object

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "producers", MappingProxyType(dict(self.producers))
        )
        if self.context_epoch != self.prepared.prepared_context_epoch:
            raise PreparedRootError("birth_prepared_set_mismatch")


@dataclass(frozen=True, slots=True)
class RequiredContextRuntimeV1:
    """One required-chain selection and the authorities read for that set."""

    selection: object
    authorities: SealedAuthoritiesV1
    required_head_id: str

    def __post_init__(self) -> None:
        from executor_birth_context_selection import is_context_selection_v1

        if (
            not is_context_selection_v1(self.selection)
            or not isinstance(self.authorities, SealedAuthoritiesV1)
            or self.selection.set_id != self.authorities.prepared.set_id
            or self.selection.admission_context_id
            != self.authorities.prepared.prepared_admission_context_id
            or self.selection.context_epoch
            != self.authorities.prepared.prepared_context_epoch
            or not isinstance(self.required_head_id, str)
            or len(self.required_head_id) != 71
            or not self.required_head_id.startswith("sha256:")
            or any(
                character not in "0123456789abcdef"
                for character in self.required_head_id[7:]
            )
        ):
            raise PreparedRootError("birth_context_selection_invalid")


@dataclass(frozen=True, slots=True)
class PreviousContextRuntimeV1:
    """Transition-only N context; ``required_head_id`` identifies N, not live N+1."""

    selection: object
    authorities: SealedAuthoritiesV1
    required_head_id: str

    def __post_init__(self) -> None:
        RequiredContextRuntimeV1(self.selection, self.authorities, self.required_head_id)


@dataclass(frozen=True, slots=True)
class StagedReattestationContextV1:
    """Authorities for one verified transition, scoped to reattestation."""

    selection: object
    authorities: SealedAuthoritiesV1

    def __post_init__(self) -> None:
        from executor_birth_context_selection import is_context_selection_v1

        if (
            not is_context_selection_v1(self.selection, allow_staged=True)
            or not self.selection.staged_reattestation_only
            or not isinstance(self.authorities, SealedAuthoritiesV1)
            or self.selection.set_id != self.authorities.prepared.set_id
            or self.selection.admission_context_id
            != self.authorities.prepared.prepared_admission_context_id
            or self.selection.context_epoch
            != self.authorities.prepared.prepared_context_epoch
        ):
            raise PreparedRootError("birth_context_selection_invalid")


def _load_staged_reattestation_context_v1(
    transition, distribution, expected_inventory,
) -> StagedReattestationContextV1:
    """Read one unpublished context without making it runtime-selected."""
    from executor_birth_context_selection import (
        _context_selection_for_staged_reattestation_v1,
    )
    from executor_birth_context_transition import (
        ContextTransitionError, ContextTransitionV1,
        verify_context_transition_v1,
    )
    from executor_birth_cutover import CurrentInventoryV1
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_prepared_set import load_authority_set_v1

    if (
        not isinstance(transition, ContextTransitionV1)
        or not is_verified_distribution(distribution)
        or not isinstance(expected_inventory, CurrentInventoryV1)
    ):
        raise PreparedRootError("birth_context_selection_invalid")
    try:
        verified_transition = verify_context_transition_v1(
            transition.encoded,
            expected_transition_id=transition.transition_id,
            expected_inventory=expected_inventory,
        )
    except ContextTransitionError as exc:
        raise PreparedRootError(exc.code, exc) from None
    if (
        verified_transition != transition
        or transition.closed_build_id
        != distribution.identity.closed_build_id
    ):
        raise PreparedRootError("birth_context_selection_invalid")

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            prepared = load_authority_set_v1(
                session,
                transition.set_id,
                expected_set_json_sha256=transition.set_json_sha256,
                expected_context_material_sha256=(
                    transition.context_material_sha256
                ),
            )
            authorities = _load_sealed_authorities_from_set_v1(
                session,
                prepared,
                lambda: _open_distribution_sources_for_verified_v1(
                    distribution,
                ),
            )
            selection = _context_selection_for_staged_reattestation_v1(
                transition, prepared, distribution,
            )
    return StagedReattestationContextV1(selection, authorities)


def _load_context_runtime_from_chain_v1(
    chain, *, previous_distribution: bool = False,
) -> RequiredContextRuntimeV1:
    """Load the exact set and distribution already selected by one chain."""
    from executor_birth_context_selection import (
        _context_selection_from_required_chain_v1,
    )
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_ownership_chain import VerifiedOwnershipChain, VerifiedOwnershipWindowV1
    from executor_birth_prepared_set import load_authority_set_v1

    if (
        not isinstance(chain, (VerifiedOwnershipChain, VerifiedOwnershipWindowV1))
        or not is_verified_distribution(chain.required_distribution)
        or not chain.context_transitions
    ):
        raise PreparedRootError("birth_context_transition_required")
    transition = chain.context_transitions[-1]
    distribution = chain.required_distribution
    if (
        transition.closed_build_id != chain.required_head.closed_build_id
        or transition.closed_build_id != distribution.identity.closed_build_id
    ):
        raise PreparedRootError("birth_context_selection_invalid")

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            prepared = load_authority_set_v1(
                session,
                transition.set_id,
                expected_set_json_sha256=transition.set_json_sha256,
                expected_context_material_sha256=(
                    transition.context_material_sha256
                ),
            )
            authorities = _load_sealed_authorities_from_set_v1(
                session,
                prepared,
                lambda: _open_distribution_sources_for_verified_v1(
                    distribution,
                ),
                previous_distribution=previous_distribution,
            )
            selection = _context_selection_from_required_chain_v1(
                transition, prepared, distribution,
            )
    return RequiredContextRuntimeV1(
        selection, authorities, chain.required_head.head_id,
    )


def load_required_context_runtime_v1() -> RequiredContextRuntimeV1:
    """Read the required selector twice around one exact context acquisition."""
    from executor_birth_ownership_chain import (
        VerifiedOwnershipWindowV1, inspect_required_ownership_v1,
    )

    before = inspect_required_ownership_v1()
    if not isinstance(before, VerifiedOwnershipWindowV1):
        raise PreparedRootError("birth_context_transition_required")
    loaded = _load_context_runtime_from_chain_v1(before)
    after = inspect_required_ownership_v1()
    if (
        not isinstance(after, VerifiedOwnershipWindowV1)
        or after.required_head.head_id != before.required_head.head_id
        or after.required_distribution is None
        or before.required_distribution is None
        or after.required_distribution.encoded
        != before.required_distribution.encoded
        or after.required_distribution.signature
        != before.required_distribution.signature
        or after.context_transitions != before.context_transitions
    ):
        raise PreparedRootError("birth_context_selection_changed")
    if loaded.required_head_id != after.required_head.head_id:
        raise PreparedRootError("birth_context_selection_changed")
    return loaded


def _previous_chain_for_transition_v1(chain, current_record):
    """Select an already authenticated prefix as evidence, never as a live head."""
    from executor_birth_distribution_manifest import verify_previous_distribution_record_v1
    from executor_birth_ownership_chain import VerifiedOwnershipChain, VerifiedOwnershipWindowV1

    previous_sequence = current_record.release_sequence - 1
    count = previous_sequence
    window = type(chain) is VerifiedOwnershipWindowV1
    if window:
        if (
            not 1 <= len(chain.heads) <= 2
            or chain.required_head.release_sequence not in {previous_sequence, current_record.release_sequence}
        ):
            raise PreparedRootError("birth_context_selection_invalid")
        count = len(chain.heads) - (chain.required_head.release_sequence == current_record.release_sequence)
    if (
        type(chain) not in {VerifiedOwnershipChain, VerifiedOwnershipWindowV1} or count < 1
        or len(chain.heads) not in {count, count + 1}
        or len(chain.authenticated_records) != len(chain.heads)
        or len(chain.context_transitions) != len(chain.heads)
    ):
        raise PreparedRootError("birth_context_selection_invalid")
    previous = chain.authenticated_records[count - 1]
    if (
        previous.closed_build_id != current_record.previous_closed_build_id
        or previous.release_sequence != previous_sequence
        or chain.heads[count - 1].closed_build_id != previous.closed_build_id
        or len(chain.heads) == count + 1 and (
            chain.authenticated_records[-1] != current_record
            or chain.heads[-1].previous_head_id != chain.heads[count - 1].head_id
        )
    ):
        raise PreparedRootError("birth_context_selection_invalid")
    distribution = verify_previous_distribution_record_v1(current_record, previous)
    if window:
        return VerifiedOwnershipWindowV1(
            chain.heads[:count], chain.authenticated_records[:count], distribution,
            chain.context_transitions[:count],
        )
    return VerifiedOwnershipChain(
        chain.anchor_cutover_id, chain.heads[:count],
        chain.authenticated_records[:count], distribution,
        chain.context_transitions[:count],
    )


def load_previous_context_runtime_v1(current_record) -> PreviousContextRuntimeV1:
    """Read N's exact context twice around acquisition during an explicit N+1 update."""
    from executor_birth_ownership_chain import inspect_transition_ownership_window_v1

    before = inspect_transition_ownership_window_v1(current_record)
    previous = _previous_chain_for_transition_v1(before, current_record)
    loaded = _load_context_runtime_from_chain_v1(previous, previous_distribution=True)
    after = inspect_transition_ownership_window_v1(current_record)
    repeated = _previous_chain_for_transition_v1(after, current_record)
    if after != before or repeated != previous:
        raise PreparedRootError("birth_context_selection_changed")
    if loaded.required_head_id != repeated.required_head.head_id:
        raise PreparedRootError("birth_context_selection_changed")
    return PreviousContextRuntimeV1(
        loaded.selection, loaded.authorities, loaded.required_head_id,
    )


def _public_inventory_from_stores_v1(stores) -> frozenset[bytes]:
    result: set[bytes] = set()
    try:
        for store in stores:
            result.update(
                public_key.public_bytes_raw()
                for public_key in store.verifier_keys.values()
            )
    except (AttributeError, TypeError) as exc:
        raise PreparedRootError("birth_prepared_set_untrusted") from exc
    if not result or any(len(item) != 32 for item in result):
        raise PreparedRootError("birth_prepared_set_untrusted")
    return frozenset(result)


def _birth_public_inventory_v1() -> frozenset[bytes]:
    """Reload the runtime-selected Birth set and its authenticated public keys."""
    sealed = load_sealed_authorities_v1()
    return _public_inventory_from_stores_v1((
        sealed.author, sealed.admission, *sealed.producers.values(),
    ))


def _historical_birth_public_inventory_v1() -> frozenset[bytes]:
    """Read fixed predecessor keys without rebinding them to candidate source.

    Ownership-authority key exclusion precedes construction of the successor
    distribution.  At that boundary the prepared marker and key stores are the
    authenticated predecessor, while candidate source is not yet an installed
    runtime and therefore cannot be used to rebuild its context material.
    """
    from executor_birth_keystore import raw_public_key
    from executor_birth_prepared_set import (
        AUTHOR_STORE_BASENAME_V1, PreparedSetError,
        _read_historical_public_keys_v1, load_historical_marker_public_set_v1,
    )
    from executor_birth_secure_fs import BirthSecureFSError

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            public = load_historical_marker_public_set_v1(session)
            try:
                names = session.inventory((AUTHOR_STORE_BASENAME_V1, "public"))
            except BirthSecureFSError as exc:
                raise PreparedSetError("birth_prepared_set_unavailable", exc) from None
            if not names or any(not name.endswith(".pub") for name in names):
                raise PreparedSetError("birth_prepared_set_invalid")
            # Exclusion is deliberately wider than historical verification:
            # later author keys can only add forbidden identities, not trust.
            authors = _read_historical_public_keys_v1(
                session, (AUTHOR_STORE_BASENAME_V1,), tuple(name[:-4] for name in names),
            )
            rings = (authors, public.admission_verifier_keys, *(
                producer.verifier_keys for producer in public.producers.values()
            ))
            return frozenset(
                raw_public_key(key) for ring in rings for key in ring.values()
            )


@dataclass(frozen=True, slots=True)
class HistoricalContextVerifiersV1:
    """Inert evidence bound to an observed chain frontier, never a runtime."""

    required_head_id: str
    transition_id: str
    public_set: object
    binding_kind: Literal["transition_target", "initial_predecessor"] = "transition_target"
    previous_admission_context_id: str | None = None
    initial_transition: bool = False


def _select_historical_context_v1(
    admission_context_id: str, *, include_initial_predecessor: bool = False,
):
    """Acquire one unambiguous context; policy readers remain target-only."""
    import re
    from executor_birth_ownership_chain import (
        VerifiedOwnershipChain, inspect_ownership_chain_state_v1,
    )

    if (type(admission_context_id) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", admission_context_id) is None):
        raise PreparedRootError("birth_context_selection_invalid")
    before = inspect_ownership_chain_state_v1()
    if type(before) is not VerifiedOwnershipChain or not before.context_transitions:
        raise PreparedRootError("birth_context_transition_required")
    matches = {
        ("target", transition.encoded): transition for transition in before.context_transitions
        if transition.prepared_admission_context_id == admission_context_id
    }
    first = before.context_transitions[0]
    if include_initial_predecessor and first.previous_admission_context_id == admission_context_id:
        matches[("initial", first.encoded)] = first
    if len(matches) != 1:
        raise PreparedRootError("birth_context_selection_invalid")
    return before, next(iter(matches.values()))


def _read_historical_context_set_v1(transition, *, initial_predecessor: bool = False):
    from executor_birth_prepared_set import (
        load_historical_marker_public_set_v1, load_historical_public_set_v1,
    )

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            if initial_predecessor:
                public = load_historical_marker_public_set_v1(session)
                if (public.set_id != transition.previous_set_id
                        or public.material.pin.admission_context_id != transition.previous_admission_context_id
                        or public.material.pin.context_epoch != transition.previous_context_epoch):
                    raise PreparedRootError("birth_context_selection_invalid")
                return public
            public = load_historical_public_set_v1(
                session, transition.set_id,
                expected_set_json_sha256=transition.set_json_sha256,
                expected_context_material_sha256=transition.context_material_sha256,
            )
            if (public.material.pin.admission_context_id != transition.prepared_admission_context_id
                    or public.material.pin.context_epoch != transition.prepared_context_epoch):
                raise PreparedRootError("birth_context_selection_invalid")
            return public


def _require_historical_frontier_unchanged_v1(before):
    from executor_birth_ownership_chain import (
        VerifiedOwnershipChain, inspect_ownership_chain_state_v1,
    )

    after = inspect_ownership_chain_state_v1()
    if (type(after) is not VerifiedOwnershipChain
            or after.required_head != before.required_head
            or after.context_transitions != before.context_transitions
            or after.authenticated_records != before.authenticated_records):
        raise PreparedRootError("birth_context_selection_changed")


def load_historical_context_verifiers_v1(
    admission_context_id: str,
) -> HistoricalContextVerifiersV1:
    """Resolve an untrusted context selector only inside the fixed live chain.

    The first predecessor is bound through the fixed marker and first edge,
    not a successor distribution's policy. It never activates an old context.
    """
    before, transition = _select_historical_context_v1(
        admission_context_id, include_initial_predecessor=True,
    )
    initial = admission_context_id != transition.prepared_admission_context_id
    public = _read_historical_context_set_v1(transition, initial_predecessor=initial)
    _require_historical_frontier_unchanged_v1(before)
    return HistoricalContextVerifiersV1(
        before.required_head.head_id, transition.transition_id, public,
        "initial_predecessor" if initial else "transition_target",
        None if initial else transition.previous_admission_context_id,
        not initial and transition == before.context_transitions[0],
    )


@dataclass(frozen=True, slots=True)
class HistoricalReattestationScopeV2:
    """Public source binding for the known non-publishing V2 protocol."""

    namespace: str
    capability_id: str
    source_bindings: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class HistoricalProducerDeclarationsV1:
    """An authenticated declaration, not an issuer registry or policy engine."""

    context: HistoricalContextVerifiersV1
    closed_build_id: str
    source_path: str
    source_hash: str
    authors: Mapping[str, str]
    executor_origins: Mapping[str, str]
    reattestation_scope: HistoricalReattestationScopeV2 | None = None
    reattestation_scope_error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "authors", MappingProxyType(dict(self.authors)))
        object.__setattr__(self, "executor_origins", MappingProxyType(dict(self.executor_origins)))


def _historical_literal_table_v1(source: bytes, name: str):
    """Select one bounded literal declaration without executing its module."""
    import ast
    from contract_boundary_guard import _bounded_ast_metrics
    from executor_birth_distribution_manifest import MAX_BOUNDARY_SOURCE_BYTES_V1

    try:
        if type(source) is not bytes or len(source) > MAX_BOUNDARY_SOURCE_BYTES_V1:
            raise ValueError("source size")
        tree = ast.parse(source.decode("utf-8"))
        _bounded_ast_metrics(tree)
        stores = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ]
        declarations = [
            node for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ]
        if len(stores) != 1 or len(declarations) != 1:
            raise ValueError("declaration")
        value = declarations[0].value
        if (not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name)
                or value.func.id != "MappingProxyType" or len(value.args) != 1
                or value.keywords or not isinstance(value.args[0], ast.Dict)):
            raise ValueError("literal table")
        return value.args[0]
    except (UnicodeError, SyntaxError, RecursionError, ValueError, TypeError,
            OverflowError, MemoryError) as exc:
        raise PreparedRootError("birth_context_producer_policy_invalid", exc) from None


def _historical_producer_author_declaration_v1(source: bytes) -> Mapping[str, str]:
    """Read only the table's literal authors, never loaded runtime policy."""
    import ast
    from executor_birth_identity import RevisionAuthor

    table = _historical_literal_table_v1(source, "PRODUCER_AUTHOR_V1")
    try:
        result = {}
        for key, author in zip(table.keys, table.values):
            if (not isinstance(key, ast.Tuple) or len(key.elts) != 2
                    or any(not isinstance(part, ast.Constant) or type(part.value) is not str
                           or not part.value or part.value.strip() != part.value
                           or ":" in part.value or "\x00" in part.value for part in key.elts)
                    or not isinstance(author, ast.Attribute)
                    or not isinstance(author.value, ast.Name)
                    or author.value.id != "RevisionAuthor"):
                raise ValueError("entry")
            namespace = ":".join(part.value for part in key.elts)
            if namespace in result:
                raise ValueError("duplicate namespace")
            result[namespace] = RevisionAuthor[author.attr].value
        if not result:
            raise ValueError("empty table")
        return MappingProxyType(result)
    except (UnicodeError, SyntaxError, RecursionError, ValueError, TypeError,
            KeyError, OverflowError, MemoryError) as exc:
        raise PreparedRootError("birth_context_producer_policy_invalid", exc) from None


def _historical_executor_origin_declaration_v1(source: bytes) -> Mapping[str, str]:
    """Project historical origin declarations without inventing missing ones."""
    import ast
    from executor_birth_identity import ExecutorOrigin
    from manifest_inventory import ManifestOrigin

    table = _historical_literal_table_v1(source, "_MANIFEST_ORIGIN_TO_EXECUTOR_V1")
    try:
        result = {}
        for key, origin in zip(table.keys, table.values):
            if (not isinstance(key, ast.Attribute) or not isinstance(key.value, ast.Name)
                    or key.value.id != "ManifestOrigin"
                    or not isinstance(origin, ast.Attribute)
                    or not isinstance(origin.value, ast.Name)
                    or origin.value.id != "ExecutorOrigin"):
                raise ValueError("origin entry")
            manifest_origin = ManifestOrigin[key.attr].value
            if manifest_origin in result:
                raise ValueError("duplicate origin")
            result[manifest_origin] = ExecutorOrigin[origin.attr].value
        return MappingProxyType(result)
    except (ValueError, TypeError, KeyError, MemoryError) as exc:
        raise PreparedRootError("birth_context_producer_policy_invalid", exc) from None


def load_historical_producer_declarations_v1(
    admission_context_id: str,
) -> HistoricalProducerDeclarationsV1:
    """Bind historical declarations to public bytes and a stable chain.

    A matching current public copy can supply historical bytes. A differing
    copy is unsupported, never permission to reinterpret history as current.
    """
    before, transition = _select_historical_context_v1(admission_context_id)
    result = _historical_producer_declarations_v1(before, transition, {})
    _require_historical_frontier_unchanged_v1(before)
    return result


def load_historical_producer_declarations_for_contexts_v1(
    admission_context_ids: tuple[str, ...],
    *, include_reattestation: bool = False,
    public_sources: tuple[tuple[str, bytes], ...] = (),
) -> tuple[HistoricalProducerDeclarationsV1, ...]:
    """Acquire all requested target contexts under one observed chain.

    Cache only within this call and only after exact signed-file comparison.
    The final chain reread is shared, not omitted. Initial predecessor policy
    is not inferred from a successor. This is not a cross-store frontier.
    Optional public source bytes are untrusted archive candidates: their exact
    path, role, size and hash must match the historical signed distribution.
    No archive path, Git command or filesystem root enters the product reader.
    """
    import re
    from executor_birth_ownership_chain import (
        VerifiedOwnershipChain, inspect_ownership_chain_state_v1,
    )

    if (type(include_reattestation) is not bool
            or type(admission_context_ids) is not tuple or not admission_context_ids
            or len(admission_context_ids) > 4096
            or any(type(value) is not str
                   or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                   for value in admission_context_ids)
            or len(set(admission_context_ids)) != len(admission_context_ids)):
        raise PreparedRootError("birth_context_selection_invalid")
    cache = _historical_source_candidates_v1(public_sources)
    before = inspect_ownership_chain_state_v1()
    if type(before) is not VerifiedOwnershipChain or not before.context_transitions:
        raise PreparedRootError("birth_context_transition_required")
    by_context = {}
    for transition in before.context_transitions:
        matches = by_context.setdefault(transition.prepared_admission_context_id, {})
        matches[transition.encoded] = transition
    results = []
    for identifier in admission_context_ids:
        matches = by_context.get(identifier, {})
        if len(matches) != 1:
            raise PreparedRootError("birth_context_selection_invalid")
        results.append(_historical_producer_declarations_v1(
            before, next(iter(matches.values())), cache,
            include_reattestation=include_reattestation,
        ))
    _require_historical_frontier_unchanged_v1(before)
    return tuple(results)


def _historical_producer_declarations_v1(
    before, transition, cache, *, include_reattestation=False,
):
    """Project one authenticated transition; the caller owns final reread."""
    from executor_birth_distribution_manifest import AuthenticatedDistributionRecordV1

    public = _read_historical_context_set_v1(transition)
    if (before.required_distribution is None
            or len(before.authenticated_records) != len(before.heads)
            or len(before.context_transitions) != len(before.heads)):
        raise PreparedRootError("birth_context_producer_policy_invalid")
    matches = [
        (head, record, item)
        for head, record, item in zip(
            before.heads, before.authenticated_records, before.context_transitions,
        ) if record.closed_build_id == transition.closed_build_id
    ]
    if len(matches) != 1:
        raise PreparedRootError("birth_context_producer_policy_invalid")
    head, record, associated_transition = matches[0]
    if (type(record) is not AuthenticatedDistributionRecordV1
            or head.closed_build_id != record.closed_build_id
            or head.release_sequence != record.release_sequence
            or associated_transition.encoded != transition.encoded
            or before.required_distribution.identity.closed_build_id != before.required_head.closed_build_id):
        raise PreparedRootError("birth_context_producer_policy_invalid")
    path = "runtime/executor_birth_producer_table_v1.py"
    source, source_hash = _historical_public_source_v1(before, record, path, cache)
    cached = cache.get(("declarations", source_hash))
    if cached is None:
        cached = (
            _historical_producer_author_declaration_v1(source),
            _historical_executor_origin_declaration_v1(source),
        )
        cache[("declarations", source_hash)] = cached
    declared, executor_origins = cached
    if not set(public.producers) <= set(declared):
        raise PreparedRootError("birth_context_producer_policy_invalid")
    authors = {namespace: declared[namespace] for namespace in public.producers}
    scope, scope_error = None, None
    if include_reattestation:
        try:
            scope = _historical_reattestation_scope_v2(before, record, cache)
        except PreparedRootError as exc:
            # Preserve this limitation next to otherwise verified declarations.
            # It cannot turn an unsupported protocol into a valid exclusion.
            scope_error = exc.code
    return HistoricalProducerDeclarationsV1(
        HistoricalContextVerifiersV1(
            before.required_head.head_id, transition.transition_id, public,
            previous_admission_context_id=transition.previous_admission_context_id,
            initial_transition=(record.release_sequence == 1),
        ),
        record.closed_build_id, path, source_hash, authors, executor_origins,
        scope, scope_error,
    )


def _historical_public_source_v1(before, record, path, cache):
    from executor_birth_distribution_manifest import (
        MAX_BOUNDARY_SOURCE_BYTES_V1, file_content_hash,
        read_verified_distribution_file_v1,
    )

    historical = [item for item in record.files if item.path == path]
    current = [item for item in before.required_distribution.files if item.path == path]
    if (len(historical) != 1 or historical[0].role != "runtime_code"
            or historical[0].size > MAX_BOUNDARY_SOURCE_BYTES_V1):
        raise PreparedRootError("birth_context_producer_policy_invalid")
    candidate = cache.get(("supplied", path, historical[0].content_hash))
    if candidate is not None:
        if len(candidate) != historical[0].size:
            raise PreparedRootError("birth_context_producer_policy_invalid")
        return candidate, historical[0].content_hash
    if len(current) != 1 or historical != current:
        raise PreparedRootError("birth_context_producer_policy_invalid")
    cache_key = ("source", path, historical[0].content_hash)
    source = cache.get(cache_key)
    if source is None:
        source = read_verified_distribution_file_v1(
            before.required_distribution, expected_path=path, expected_role="runtime_code",
        )
        if (len(source) != historical[0].size
                or file_content_hash(path, source) != historical[0].content_hash):
            raise PreparedRootError("birth_context_producer_policy_invalid")
        cache[cache_key] = source
    return source, historical[0].content_hash


def _historical_source_candidates_v1(public_sources):
    """Index bounded inert bytes; signed historical metadata selects them."""
    from pathlib import PurePosixPath
    from executor_birth_distribution_manifest import MAX_BOUNDARY_SOURCE_BYTES_V1, file_content_hash

    if type(public_sources) is not tuple or len(public_sources) > 256:
        raise PreparedRootError("birth_context_public_sources_invalid")
    cache, size = {}, 0
    for pair in public_sources:
        if (type(pair) is not tuple or len(pair) != 2
                or type(pair[0]) is not str or not pair[0] or len(pair[0]) > 1024
                or "\0" in pair[0] or "\\" in pair[0]
                or PurePosixPath(pair[0]).is_absolute()
                or ".." in PurePosixPath(pair[0]).parts
                or PurePosixPath(pair[0]).as_posix() != pair[0]
                or type(pair[1]) is not bytes or len(pair[1]) > MAX_BOUNDARY_SOURCE_BYTES_V1):
            raise PreparedRootError("birth_context_public_sources_invalid")
        path, encoded = pair
        size += len(encoded)
        if size > 32 * 1024 * 1024:
            raise PreparedRootError("birth_context_public_sources_invalid")
        try:
            content_hash = file_content_hash(path, encoded)
        except (UnicodeError, ValueError) as exc:
            raise PreparedRootError("birth_context_public_sources_invalid", exc) from None
        cache[("supplied", path, content_hash)] = encoded
    return cache


def _historical_reattestation_scope_v2(before, record, cache):
    paths = (
        "runtime/executor_birth_intent.py", "runtime/executor_birth_bootstrap.py",
    )
    artifacts = tuple(_historical_public_source_v1(before, record, path, cache) for path in paths)
    bindings = tuple((path, artifact[1]) for path, artifact in zip(paths, artifacts))
    key = ("reattestation", bindings)
    if key not in cache:
        namespace, capability = _project_reattestation_scope_v2(*(item[0] for item in artifacts))
        cache[key] = HistoricalReattestationScopeV2(namespace, capability, bindings)
    return cache[key]


def _project_reattestation_scope_v2(intent: bytes, bootstrap: bytes) -> tuple[str, str]:
    """Recognize the closed protocol's source declarations, never run them.

    These inputs must already be distribution-authenticated. This is not a
    Python interpreter or a safety checker for arbitrary unsigned source.
    Unsupported structural changes require a new reviewed protocol reader.
    """
    import ast
    from contract_boundary_guard import _bounded_ast_metrics
    from executor_birth_distribution_manifest import MAX_BOUNDARY_SOURCE_BYTES_V1

    def one(values):
        values = list(values)
        if len(values) != 1:
            raise ValueError("ambiguous protocol declaration")
        return values[0]

    def assignment(tree, name):
        node = one(node for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                   and node.id == name)
        return one(item.value for item in ast.walk(tree)
                   if isinstance(item, ast.Assign) and item.targets == [node])

    def expression(node, expected):
        return ast.dump(node) == ast.dump(ast.parse(expected, mode="eval").body)

    try:
        trees = []
        for encoded in (intent, bootstrap):
            if type(encoded) is not bytes or len(encoded) > MAX_BOUNDARY_SOURCE_BYTES_V1:
                raise ValueError("source size")
            tree = ast.parse(encoded.decode("utf-8"))
            _bounded_ast_metrics(tree)
            trees.append(tree)
        installer = assignment(trees[0], "_INSTALLER")
        if (not isinstance(installer, ast.Call)
                or not expression(installer.func, "_ProducerCapability")
                or len(installer.args) != 3 or installer.keywords
                or not expression(installer.args[2], "_CAPABILITY_SEAL")):
            raise ValueError("installer declaration")
        namespace_parts = [ast.literal_eval(value) for value in installer.args[:2]]
        alias = ast.literal_eval(assignment(trees[1], "_REATTESTATION_CAPABILITY_V2"))
        if any(type(value) is not str or not value or "\0" in value
               or value.strip() != value or ":" in value for value in namespace_parts):
            raise ValueError("installer namespace")
        if type(alias) is not str or alias.count(":") != 1 or "\0" in alias or not all(alias.split(":")):
            raise ValueError("alias")
        _require_reattestation_flow_v2(trees)
        return ":".join(namespace_parts), alias
    except (UnicodeError, SyntaxError, RecursionError, ValueError, TypeError,
            AttributeError, OverflowError, MemoryError) as exc:
        raise PreparedRootError("birth_context_reattestation_policy_unsupported", exc) from None


def _require_reattestation_flow_v2(trees):
    """Recognize one reviewed protocol, not an allowlist of release builds.

    The complete transfer includes constructors, inheritance, capture, issue
    and prepared-carrier validation. Only docstrings and source positions are
    omitted. An unknown semantic flow is evidence unavailable, not trusted by
    finding a few familiar nodes among otherwise different statements.
    """
    import ast
    import hashlib

    profile = (
        ("_ProducerCapability",),
        ("_CutoverReattestationFactoryV1", "_CutoverReattestationFactoryV2",
         "_PreparedReattestationV2", "_reattestation_factory_for_assembly_v1"),
    )
    framed = bytearray(b"metnos.executor-birth.reattestation-flow/v2\0")
    for tree, symbols in zip(trees, profile):
        for symbol in symbols:
            definitions = [node for node in ast.walk(tree)
                           if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                           and node.name == symbol]
            if len(definitions) != 1 or definitions[0] not in tree.body:
                raise ValueError("protocol definition")
            for node in ast.walk(tree):
                if (isinstance(node, ast.Name) and node.id == symbol
                        and isinstance(node.ctx, (ast.Store, ast.Del))):
                    raise ValueError("protocol rebinding")
                if isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == symbol:
                    raise ValueError("protocol import rebinding")
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    target = node.value
                    while isinstance(target, ast.Attribute):
                        target = target.value
                    if isinstance(target, ast.Name) and target.id == symbol:
                        raise ValueError("protocol attribute rebinding")
            selected = definitions[0]
            for node in ast.walk(selected):
                if (isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body.pop(0)
            encoded = ast.dump(selected, include_attributes=False).encode("utf-8")
            framed.extend(len(encoded).to_bytes(8, "big"))
            framed.extend(encoded)
    # This versioned semantic protocol fingerprint is independent of file
    # locations, release identifiers, comments and literal namespace values.
    expected = "31c6e81661445814acf7db120a6457c07a698be24c4b55d32d2406b2720ddb40"
    if hashlib.sha256(framed).hexdigest() != expected:
        raise ValueError("unrecognized protocol flow")

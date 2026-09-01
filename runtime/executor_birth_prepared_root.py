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
from typing import Mapping

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
            identity = _PlatformIdentity(
                posix_uid=os.geteuid(), windows_service_sid=None,
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


def _load_sealed_authorities_from_set_v1(session, prepared, open_sources):
    """Load one already selected set while its root barrier is held."""
    from executor_birth_context import _context_epoch
    from executor_birth_context_v1 import (
        ContextMaterialError, prepare_context_material_v1,
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
        rebuilt = prepare_context_material_v1(sources, registry)
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
    )
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


def _load_context_runtime_from_chain_v1(chain) -> RequiredContextRuntimeV1:
    """Load the exact set and distribution already selected by one chain."""
    from executor_birth_context_selection import (
        _context_selection_from_required_chain_v1,
    )
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_ownership_chain import VerifiedOwnershipChain
    from executor_birth_prepared_set import load_authority_set_v1

    if (
        not isinstance(chain, VerifiedOwnershipChain)
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
        VerifiedOwnershipChain, inspect_ownership_chain_state_v1,
    )

    before = inspect_ownership_chain_state_v1()
    if not isinstance(before, VerifiedOwnershipChain):
        raise PreparedRootError("birth_context_transition_required")
    loaded = _load_context_runtime_from_chain_v1(before)
    after = inspect_ownership_chain_state_v1()
    if (
        not isinstance(after, VerifiedOwnershipChain)
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


def _birth_public_inventory_v1() -> frozenset[bytes]:
    """Reload the fixed Birth set and return its authenticated public keys."""
    sealed = load_sealed_authorities_v1()
    stores = (sealed.author, sealed.admission, *sealed.producers.values())
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

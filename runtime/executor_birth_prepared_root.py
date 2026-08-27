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


def load_sealed_authorities_v1():
    """Everything the core needs, read once under the barrier and returned.

    The session is opened, used and closed here: what travels out is key
    material and immutable values, never a live capability onto the Birth root.
    The context is the one rebuilt from the installed distribution, so the
    caller never has to rebuild it a second time.
    """
    from executor_birth_context import _context_epoch
    from executor_birth_context_v1 import (
        ContextMaterialError, prepare_context_material_v1,
    )
    from executor_birth_keystore import (
        BirthKeyStoreError, _load_birth_keystore_in_session,
    )
    from executor_birth_prepared_set import (
        AUTHORITY_SETS_BASENAME_V1, AUTHOR_STORE_BASENAME_V1, PreparedSetError,
        authority_registry_v1, load_prepared_set_v1,
    )
    from executor_birth_sandbox_registry_v1 import (
        SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1,
        decode_sandbox_registry_v1,
    )
    from executor_birth_semantic_authority import (
        _load_semantic_authority_in_session,
    )

    session = open_prepared_root_session_v1()
    with session:
        with session.global_lock(exclusive=False, create=False):
            prepared = load_prepared_set_v1(session)
            location = (AUTHORITY_SETS_BASENAME_V1, prepared.set_id)
            registry = authority_registry_v1(session, location)
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
                    "birth_prepared_set_unavailable", exc
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
                location + (SANDBOX_CONTAINER_BASENAME_V1,
                            SANDBOX_REGISTRY_BASENAME_V1),
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

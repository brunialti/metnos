"""Single supported entry that produces the RM-0008 2A mutating capability.

Section 16.13.1 fixes this module as the only place where an authenticated
root descriptor may be built and adopted.  The entry takes no arguments: it
resolves the fixed Birth root, the fixed operator input location and the
service identity from the installer configuration, and returns an immutable
layout whose descriptors stay private.  No caller can choose a path, a UID, a
SID, a handle, a profile or a resolution function.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(_RUNTIME))

import executor_birth_secure_fs as _secure_fs  # noqa: E402
from executor_birth_secure_fs import (  # noqa: E402
    BirthSecureFSError,
    _AuthenticatedRootDescriptor,
    _BirthObjectRole,
    _BirthRoleCatalogV1,
    _BirthRolePatternV1,
    _PlatformIdentity,
    _SecureDirectoryHandle,
    _SecureRootSession,
    _open_win_root,
)

BIRTH_ROOT_NAME = "birth"
OPERATOR_INPUT_NAME = "operator-input-v1"


@dataclass(frozen=True, slots=True)
class ProvisioningLayoutV1:
    birth_session: _SecureRootSession
    operator_input: _SecureDirectoryHandle
    service_identity: _PlatformIdentity


def _resolve_path_user_config_v1() -> Path:
    """Resolve the installer configuration root exactly once."""
    import config as runtime_config

    return Path(runtime_config.PATH_USER_CONFIG)


def _resolve_birth_service_identity_v1() -> _PlatformIdentity:
    """Derive the service identity from the platform, never from an argument."""
    if os.name == "nt":
        from executor_birth_secure_fs import (
            _windows_service_sid_for_current_process,
        )

        return _PlatformIdentity(
            posix_uid=None,
            windows_service_sid=_windows_service_sid_for_current_process(),
        )
    return _PlatformIdentity(posix_uid=os.geteuid(), windows_service_sid=None)


def _resolve_birth_root_v1(
    root: Path, identity: _PlatformIdentity,
) -> tuple[tuple[int, ...], str]:
    """Open the fixed Birth root and return its authenticated handle chain."""
    if not isinstance(root, Path) or not isinstance(identity, _PlatformIdentity):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    if os.name == "nt":
        handles, absolute = _open_win_root(root)
        return tuple(handles), absolute
    from executor_birth_secure_fs import _open_posix_root

    # The Birth root carries the integrity-only role, so it is readable by the
    # service without granting it a private profile.
    handles, absolute = _open_posix_root(
        root, exact_private=False, expected_uid=identity.posix_uid,
    )
    return tuple(handles), absolute


def _resolve_operator_input_v1(
    session: _SecureRootSession,
    components: tuple[str, ...],
    identity: _PlatformIdentity,
) -> _SecureDirectoryHandle:
    """Expose the operator input location as a read capability only.

    The authority here is the capability the caller hands over, not the name
    of its class: this resolver only asks for a directory it may read.  The
    identity stays typed, because it decides who the location belongs to.
    """
    if not isinstance(identity, _PlatformIdentity):
        raise BirthSecureFSError("birth_provisioning_io_unavailable")
    return session.open_directory(
        components, role=_BirthObjectRole.birth_integrity_only,
    )


def open_birth_provisioning_layout_v1() -> ProvisioningLayoutV1:
    """Build the one supported provisioning capability of increment 2A."""
    # The configuration location is resolved before the identity: the layout
    # is decided by where the installation lives, and only then by who runs it.
    root = _resolve_path_user_config_v1() / BIRTH_ROOT_NAME
    identity = _resolve_birth_service_identity_v1()
    handles, absolute = _resolve_birth_root_v1(root, identity)
    # The catalogue is built once, named, and handed over unchanged: the
    # descriptor must not be able to receive a narrowed or reordered variant.
    catalog = _secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=tuple(_secure_fs._BirthRolePatternV1),
        exact_bindings=(),
        generation=0,
    )
    descriptor = _AuthenticatedRootDescriptor(
        handles=handles,
        root_path=absolute,
        identity=identity,
        role_catalog=catalog,
    )
    # Adoption is looked up on the module that defines it, so the single
    # adoption point of section 16.13.1 cannot be captured at import time.
    session = _secure_fs._adopt_authenticated_root(descriptor)
    operator_input = _resolve_operator_input_v1(
        session, (OPERATOR_INPUT_NAME,), identity,
    )
    return ProvisioningLayoutV1(
        birth_session=session,
        operator_input=operator_input,
        service_identity=identity,
    )

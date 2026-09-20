from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ._support import (
    assert_birth_error,
    chown_other_uid,
    lock_role_binding,
    make_root,
    mkdir_private,
    open_session,
    private_role,
    role_binding,
    restore_owner,
    secure_fs,
    write_private,
)


CASES = ("reject-root-uid", "reject-intermediate-uid", "reject-file-uid")


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_uid_binding(tmp_path: Path, case: str) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    intermediate = root / "private"
    payload = intermediate / "payload.bin"
    mkdir_private(intermediate)
    write_private(payload, b"authenticated")
    target = {
        "reject-root-uid": root,
        "reject-intermediate-uid": intermediate,
        "reject-file-uid": payload,
    }[case]
    bindings = (
        lock_role_binding(module),
        role_binding(
            module,
            ("private",),
            directory=True,
            role=private_role(module),
        ),
        role_binding(
            module,
            ("private", "payload.bin"),
            directory=False,
            role=private_role(module),
        ),
    )
    prepared_session = None
    if case != "reject-root-uid":
        prepared_session = open_session(
            root,
            authenticated_uid=os.geteuid(),
            role_bindings=bindings,
        )
        with prepared_session.global_lock(exclusive=True, create=True):
            pass
    chown_other_uid(iter((target,)))

    def access_target() -> None:
        with open_session(
            root,
            authenticated_uid=os.geteuid(),
            role_bindings=bindings,
        ) as session:
            if case == "reject-root-uid":
                session.inventory(())
                return
            with session.global_lock(exclusive=False, create=False):
                if case == "reject-intermediate-uid":
                    session.open_directory(
                        ("private",),
                        role=module._BirthObjectRole("birth_confidential"),
                    )
                else:
                    assert session.read_file(
                        ("private", "payload.bin"),
                        maximum=32,
                        role=module._BirthObjectRole("birth_confidential"),
                    ) == b"authenticated"

    try:
        with pytest.raises(module.BirthSecureFSError) as caught:
            session_context = (
                open_session(
                    root,
                    authenticated_uid=os.geteuid(),
                    role_bindings=bindings,
                )
                if prepared_session is None
                else prepared_session
            )
            with session_context as session:
                if case == "reject-root-uid":
                    session.inventory(())
                else:
                    with session.global_lock(exclusive=False, create=False):
                        if case == "reject-intermediate-uid":
                            session.open_directory(
                                ("private",),
                                role=module._BirthObjectRole("birth_confidential"),
                            )
                        else:
                            session.read_file(
                                ("private", "payload.bin"),
                                maximum=32,
                                role=module._BirthObjectRole("birth_confidential"),
                            )
        assert_birth_error(caught.value, code="birth_provisioning_acl_unsafe")
    finally:
        restore_owner(iter((target,)))

    access_target()
    correct_mode = {
        "reject-root-uid": 0o755,
        "reject-intermediate-uid": 0o700,
        "reject-file-uid": 0o600,
    }[case]
    unsafe_mode = {
        "reject-root-uid": 0o775,
        "reject-intermediate-uid": 0o750,
        "reject-file-uid": 0o640,
    }[case]
    target.chmod(unsafe_mode)
    try:
        with pytest.raises(module.BirthSecureFSError) as caught:
            access_target()
        assert_birth_error(caught.value, code="birth_provisioning_acl_unsafe")
    finally:
        target.chmod(correct_mode)
    assert stat.S_IMODE(target.stat().st_mode) == correct_mode

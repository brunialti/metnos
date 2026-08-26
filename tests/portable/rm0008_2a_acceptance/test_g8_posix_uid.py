from __future__ import annotations

import os
from pathlib import Path

import pytest

from ._support import (
    assert_birth_error,
    chown_other_uid,
    make_root,
    mkdir_private,
    open_session,
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
    prepared_session = None
    if case != "reject-root-uid":
        prepared_session = open_session(root, authenticated_uid=os.geteuid())
        with prepared_session.global_lock(exclusive=True, create=True):
            pass
    chown_other_uid(iter((target,)))
    try:
        with pytest.raises(module.BirthSecureFSError) as caught:
            session_context = (
                open_session(root, authenticated_uid=os.geteuid())
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

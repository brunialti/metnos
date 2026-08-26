from __future__ import annotations

import errno
from pathlib import Path

import pytest

from ._support import (
    assert_birth_error,
    close_primitive,
    inject_unlock_failure,
    invalid_descriptor,
    make_root,
    open_session,
    secure_fs,
)


CASES = (
    "close-error-primary-preserved",
    "unlock-error-primary-preserved",
    "adoption-error-normalized",
    "handle-close-exactly-once",
    "public-error-redacted",
)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_cleanup_error_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth-secret-sid-S-1-5-99")
    if case == "adoption-error-normalized":
        descriptor = invalid_descriptor(module, root)
        with pytest.raises(module.BirthSecureFSError) as caught:
            session = module._adopt_authenticated_root(descriptor)
            session.inventory(())
        assert_birth_error(caught.value)
        return

    session = open_session(root)
    if case == "handle-close-exactly-once":
        handles = tuple(session._handles)
        counts = {handle: 0 for handle in handles}
        owner, closer_name = close_primitive(module)
        real_close = getattr(owner, closer_name)

        def counted_close(fd: int) -> None:
            if fd in counts:
                counts[fd] += 1
            return real_close(fd)

        monkeypatch.setattr(owner, closer_name, counted_close)
        session.close()
        session.close()
        assert counts == {handle: 1 for handle in handles}
        return

    if case == "public-error-redacted":
        try:
            with pytest.raises(module.BirthSecureFSError) as caught:
                session.read_file(
                    ("missing-secret.bin",),
                    maximum=1,
                    role=module._BirthObjectRole("birth_confidential"),
                )
            assert_birth_error(caught.value)
            public = str(caught.value)
            assert "birth-secret" not in public
            assert "S-1-5-99" not in public
            assert "missing-secret" not in public
            assert "[Errno" not in public and "DACL" not in public
        finally:
            session.close()
        return

    primary = module.BirthSecureFSError("birth_provisioning_transaction_conflict")
    if case == "close-error-primary-preserved":
        target = session._handles[0]
        owner, closer_name = close_primitive(module)
        real_close = getattr(owner, closer_name)

        def failing_close(fd: int) -> None:
            if fd == target:
                raise OSError(errno.EIO, "private close diagnostic")
            return real_close(fd)

        monkeypatch.setattr(owner, closer_name, failing_close)
        with pytest.raises(module.BirthSecureFSError) as caught:
            with session:
                raise primary
        assert caught.value is primary
        return

    inject_unlock_failure(module, monkeypatch)
    with pytest.raises(module.BirthSecureFSError) as caught:
        with session:
            with session.global_lock(exclusive=True, create=True):
                raise primary
    assert caught.value is primary

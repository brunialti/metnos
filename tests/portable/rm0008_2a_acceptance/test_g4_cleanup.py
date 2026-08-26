from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from ._support import (
    assert_birth_error,
    close_primitive,
    inject_unlock_failure,
    invalid_descriptor,
    lock_role_binding,
    make_root,
    open_session,
    private_role,
    role_binding,
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
    bindings = (
        lock_role_binding(module),
        role_binding(
            module,
            ("retained-handle",),
            directory=True,
            role=private_role(module),
        ),
    )
    if case == "adoption-error-normalized":
        descriptor = invalid_descriptor(module, root)
        with pytest.raises(module.BirthSecureFSError) as caught:
            session = module._adopt_authenticated_root(descriptor)
            session.inventory(())
        assert_birth_error(caught.value)
        return

    adopted_batches: list[tuple[int, ...]] = []
    real_adopt = module._adopt_authenticated_root

    def observed_adoption(descriptor):
        adopted_batches.append(
            tuple(
                int(getattr(handle, "value", handle) or 0)
                for handle in descriptor.handles
            )
        )
        return real_adopt(descriptor)

    monkeypatch.setattr(module, "_adopt_authenticated_root", observed_adoption)

    # Track native handle generations rather than trusting session._handles.
    # Descriptor-number reuse therefore cannot hide a leak or create a spurious
    # double-close on either platform.
    active_generations: dict[int, int] = {}
    close_counts: dict[int, int] = {}
    generation = 0

    def register_native_handle(handle) -> None:
        nonlocal generation
        value = int(getattr(handle, "value", handle) or 0)
        generation += 1
        if not value or value in active_generations:
            raise AssertionError("native open returned an invalid or active handle")
        active_generations[value] = generation
        close_counts[generation] = 0

    if case == "handle-close-exactly-once" and os.name != "nt":
        real_os_open, real_os_close = os.open, os.close

        def observed_open(*args, **kwargs):
            handle = real_os_open(*args, **kwargs)
            register_native_handle(handle)
            return handle

        def observed_close(handle):
            value = int(getattr(handle, "value", handle) or 0)
            current = active_generations.get(value)
            result = real_os_close(handle)
            if current is not None:
                close_counts[current] += 1
                del active_generations[value]
            return result

        monkeypatch.setattr(os, "open", observed_open)
        monkeypatch.setattr(os, "close", observed_close)
    elif case == "handle-close-exactly-once":
        import ctypes

        native_create = module._KERNEL32.CreateFileW
        native_ntcreate = module._NTDLL.NtCreateFile
        native_close = module._KERNEL32.CloseHandle

        def observed_create(*args):
            handle = native_create(*args)
            value = int(getattr(handle, "value", handle) or 0)
            if value != ctypes.c_void_p(-1).value:
                register_native_handle(handle)
            return handle

        def observed_ntcreate(*args):
            status = native_ntcreate(*args)
            if int(status) >= 0:
                handle = ctypes.cast(
                    args[0], ctypes.POINTER(ctypes.c_void_p)
                ).contents.value
                register_native_handle(handle)
            return status

        def observed_close(handle):
            value = int(getattr(handle, "value", handle) or 0)
            current = active_generations.get(value)
            result = native_close(handle)
            if result and current is not None:
                close_counts[current] += 1
                del active_generations[value]
            return result

        monkeypatch.setattr(module._KERNEL32, "CreateFileW", observed_create)
        monkeypatch.setattr(module._NTDLL, "NtCreateFile", observed_ntcreate)
        monkeypatch.setattr(module._KERNEL32, "CloseHandle", observed_close)

    session = open_session(root, role_bindings=bindings)
    assert adopted_batches and adopted_batches[-1]

    def independently_seeded_handles(active) -> tuple[int, ...]:
        registered = tuple(
            int(getattr(handle, "value", handle) or 0)
            for handle in active._handles
        )
        return tuple(dict.fromkeys((*adopted_batches[-1], *registered)))

    if case == "handle-close-exactly-once":
        with session.global_lock(exclusive=True, create=True):
            session.create_directory_exclusive(
                ("retained-handle",), role=private_role(module)
            )
        assert set(adopted_batches[-1]) <= set(active_generations)
        owned_generations = set(active_generations.values())
        assert owned_generations
        session.close()
        session.close()
        assert not owned_generations & set(active_generations.values())
        assert {
            current: close_counts[current] for current in owned_generations
        } == {current: 1 for current in owned_generations}
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
            current: BaseException | None = caught.value
            while current is not None:
                rendered = str(current)
                assert "birth-secret" not in rendered
                assert "S-1-5-99" not in rendered
                assert "missing-secret" not in rendered
                assert "[Errno" not in rendered and "DACL" not in rendered
                current = current.__cause__ or current.__context__
        finally:
            session.close()
        return

    primary = module.BirthSecureFSError("birth_provisioning_transaction_conflict")
    if case == "close-error-primary-preserved":
        with session.global_lock(exclusive=True, create=True):
            session.create_directory_exclusive(
                ("retained-handle",), role=private_role(module)
            )
        handles = independently_seeded_handles(session)
        assert len(handles) >= 2
        target = handles[-1]
        owner, closer_name = close_primitive(module)
        real_close = getattr(owner, closer_name)
        counts = {handle: 0 for handle in handles}

        def failing_close(fd: int) -> None:
            if fd in counts:
                counts[fd] += 1
            if fd == target:
                raise OSError(errno.EIO, "private close diagnostic")
            return real_close(fd)

        with monkeypatch.context() as injected:
            injected.setattr(owner, closer_name, failing_close)
            with pytest.raises(module.BirthSecureFSError) as caught:
                with session:
                    raise primary
        assert caught.value is primary
        assert counts == {handle: 1 for handle in handles}
        try:
            real_close(target)
        except OSError:
            pass
        cleanup = open_session(root, role_bindings=bindings)
        with cleanup.global_lock(exclusive=False, create=False):
            cleanup.open_directory(
                ("retained-handle",), role=private_role(module)
            )
        cleanup_handles = tuple(cleanup._handles)
        assert len(cleanup_handles) >= 2
        cleanup_target = cleanup_handles[-1]
        cleanup_counts = {handle: 0 for handle in cleanup_handles}

        def cleanup_close(fd: int) -> None:
            if fd in cleanup_counts:
                cleanup_counts[fd] += 1
            if fd == cleanup_target:
                raise OSError(errno.EIO, "private close diagnostic")
            return real_close(fd)

        with monkeypatch.context() as injected:
            injected.setattr(owner, closer_name, cleanup_close)
            with pytest.raises(module.BirthSecureFSError) as caught:
                cleanup.close()
        assert_birth_error(caught.value)
        assert cleanup_counts == {handle: 1 for handle in cleanup_handles}
        try:
            real_close(cleanup_target)
        except OSError:
            pass
        return

    with monkeypatch.context() as injected:
        first_lifecycle = inject_unlock_failure(module, injected)
        with pytest.raises(module.BirthSecureFSError) as caught:
            with session:
                with session.global_lock(exclusive=True, create=True):
                    raise primary
    assert caught.value is primary
    assert len(first_lifecycle["unlock_handles"]) == 1
    assert first_lifecycle["closed_handles"] == first_lifecycle["unlock_handles"]
    cleanup = open_session(root, role_bindings=bindings)
    with monkeypatch.context() as injected:
        second_lifecycle = inject_unlock_failure(module, injected)
        with pytest.raises(module.BirthSecureFSError) as caught:
            with cleanup:
                with cleanup.global_lock(exclusive=True, create=False):
                    pass
    assert_birth_error(caught.value)
    assert len(second_lifecycle["unlock_handles"]) == 1
    assert second_lifecycle["closed_handles"] == second_lifecycle["unlock_handles"]

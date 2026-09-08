"""Read-only inspection of the terminal legacy-state adoption journal."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from contract_cutover_guard import _require_maintenance_session_v1
from executor_birth_host_chain_policy import host_directory_chain_expectations_v1
from executor_birth_host_path_policy import LEGACY_STATE_JOURNAL_ROOT_V1
import executor_birth_legacy_state_journal as journal
from executor_birth_legacy_state_policy import (
    LegacyStateDispositionV1,
    classify_legacy_state_v1,
)
from executor_birth_legacy_state_request import (
    build_legacy_state_request_v1,
    require_canonical_legacy_state_request_v1,
)
from install.executor_birth_legacy_state_journal_posix import (
    LegacyStateJournalStoreV1,
    require_legacy_journal_lock_bound_v1,
)
from install.executor_birth_posix_directory import (
    BoundDirectoryChainV1,
    PosixDirectoryError,
    close_descriptors_v1,
    run_cleanup_v1,
)
from install.executor_birth_legacy_state_posix import observe_legacy_state_v1


class LegacyStateInspectionError(RuntimeError):
    pass


def _fail(detail: str, cause: BaseException | None = None):
    error = LegacyStateInspectionError(detail)
    if cause is None:
        raise error
    raise error from cause


def _expected_v1(account, target):
    return {
        Path(path.as_posix()): (uid, gid, mode)
        for path, uid, gid, mode in host_directory_chain_expectations_v1(
            account, target,
        )
    }


def _open_existing_lock_v1(root_fd: int) -> int:
    flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open("journal.lock", flags, dir_fd=root_fd)
    try:
        require_legacy_journal_lock_bound_v1(root_fd, descriptor)
    except BaseException:
        try:
            close_descriptors_v1((descriptor,))
        except PosixDirectoryError as exc:
            _fail("legacy inspection close", exc)
        raise
    return descriptor


@contextmanager
def _inspection_roots_v1(request, *, include_state: bool):
    state = (
        BoundDirectoryChainV1(Path(request.state_root.as_posix()))
        if include_state else None
    )
    journal_root, lock_fd = None, None
    try:
        journal_root = BoundDirectoryChainV1(
            Path(LEGACY_STATE_JOURNAL_ROOT_V1.as_posix()),
        )
        lock_fd = _open_existing_lock_v1(journal_root.root_fd)
        yield state, journal_root, lock_fd
    finally:
        actions = []
        if lock_fd is not None:
            actions.append(lambda: close_descriptors_v1((lock_fd,)))
        if journal_root is not None:
            actions.append(journal_root.close)
        if state is not None:
            actions.append(state.close)
        try:
            run_cleanup_v1(actions, detail="legacy inspection cleanup")
        except PosixDirectoryError as exc:
            _fail("legacy inspection close", exc)


def _terminal_record_v1(raw, request, expected_record_sha256):
    records = journal.decode_legacy_state_chain_v1(raw)
    latest = records[-1]
    if (
        len(records) != 4
        or latest.state is not journal.LegacyStateV1.LEGACY_STATE_READY
        or latest.request_id != request.request_id
        or latest.record_sha256 != expected_record_sha256
    ):
        _fail("legacy journal terminal")
    return latest


def _require_current_state_v1(request, latest) -> None:
    current = observe_legacy_state_v1(request)
    if (
        current.observation_sha256 != latest.ready_sha256
        or classify_legacy_state_v1(request, current)
        is not LegacyStateDispositionV1.exact_service
    ):
        _fail("legacy state changed")


def _inspect_terminal_legacy_state_v1(
    request, account, expected_record_sha256, maintenance_session, *, live,
):
    _require_maintenance_session_v1(maintenance_session)
    require_canonical_legacy_state_request_v1(request)
    if (
        build_legacy_state_request_v1(account, request.distribution_sha256)
        != request
    ):
        _fail("legacy inspection binding")
    try:
        with _inspection_roots_v1(
            request, include_state=live,
        ) as (state, journal_root, lock_fd):
            _require_maintenance_session_v1(maintenance_session)
            if state is not None:
                state.attest_metadata(_expected_v1(account, request.state_root))
            journal_root.attest_metadata(
                _expected_v1(account, LEGACY_STATE_JOURNAL_ROOT_V1),
            )
            store = LegacyStateJournalStoreV1(journal_root.root_fd)
            latest = _terminal_record_v1(
                store.inspect_records(), request, expected_record_sha256,
            )
            if live:
                _require_current_state_v1(request, latest)
            require_legacy_journal_lock_bound_v1(journal_root.root_fd, lock_fd)
            _require_maintenance_session_v1(maintenance_session)
            if state is not None:
                state.attest_metadata(_expected_v1(account, request.state_root))
            journal_root.attest_metadata(
                _expected_v1(account, LEGACY_STATE_JOURNAL_ROOT_V1),
            )
            _require_maintenance_session_v1(maintenance_session)
            return latest
    except LegacyStateInspectionError:
        raise
    except Exception as exc:
        _fail("legacy inspection", exc)


def inspect_ready_legacy_state_live_v1(
    request, account, expected_record_sha256, maintenance_session,
):
    """Prove post-convergence READY against the live exact-service state."""
    return _inspect_terminal_legacy_state_v1(
        request, account, expected_record_sha256, maintenance_session,
        live=True,
    )


def inspect_terminal_legacy_state_history_v1(
    request, account, expected_record_sha256, maintenance_session,
):
    """Verify immutable post-convergence READY history."""
    return _inspect_terminal_legacy_state_v1(
        request, account, expected_record_sha256, maintenance_session,
        live=False,
    )


__all__ = [
    "LegacyStateInspectionError", "inspect_ready_legacy_state_live_v1",
    "inspect_terminal_legacy_state_history_v1",
]

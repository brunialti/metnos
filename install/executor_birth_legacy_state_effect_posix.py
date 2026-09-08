"""Locked POSIX effects for one-time legacy authoring ownership adoption."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from contract_cutover_guard import _require_maintenance_session_v1
from executor_birth_host_chain_policy import host_directory_chain_expectations_v1
from executor_birth_host_path_policy import LEGACY_STATE_JOURNAL_ROOT_V1
from executor_birth_legacy_state_policy import (
    MAX_LEGACY_STATE_FILE_BYTES_V1,
    LegacyNodeKindV1,
    LegacyStateObservationV1,
    legacy_state_adoption_target_sha256_v1,
    legacy_state_file_sha256_v1,
)
from executor_birth_legacy_state_journal import (
    legacy_state_adoption_resume_valid_v1,
)
from executor_birth_legacy_state_request import (
    LegacyStateRequestV1,
    build_legacy_state_request_v1,
    require_canonical_legacy_state_request_v1,
)
from install.executor_birth_legacy_state_journal_posix import (
    LegacyStateJournalStoreV1,
    open_legacy_journal_lock_v1,
    require_legacy_journal_lock_bound_v1,
)
from install.executor_birth_posix_directory import (
    BoundDirectoryChainV1,
    PosixDirectoryError,
    close_descriptors_v1,
    require_no_acl_v1,
    require_posix_directory_platform_v1,
    run_cleanup_v1,
)
from install.executor_birth_legacy_state_posix import observe_legacy_state_v1


class LegacyStateEffectPosixError(RuntimeError):
    pass


def _fail(detail: str, cause: BaseException | None = None):
    error = LegacyStateEffectPosixError(detail)
    if cause is None:
        raise error
    raise error from cause


def _require_effect_platform_v1() -> None:
    try:
        require_posix_directory_platform_v1()
    except PosixDirectoryError as exc:
        _fail("legacy platform unsupported", exc)
    functions = (
        getattr(os, "fchown", None), getattr(os, "fsync", None),
        getattr(fcntl, "flock", None),
    )
    constants = (getattr(fcntl, "LOCK_EX", None), getattr(fcntl, "LOCK_UN", None))
    if any(not callable(item) for item in functions) or any(
        type(item) is not int for item in constants
    ):
        _fail("legacy platform unsupported")


def _expectations_v1(account, target) -> dict[Path, tuple[int, int, int | None]]:
    try:
        raw = host_directory_chain_expectations_v1(account, target)
        return {
            Path(path.as_posix()): (uid, gid, mode)
            for path, uid, gid, mode in raw
        }
    except (TypeError, ValueError) as exc:
        _fail("host path policy", exc)


def _read_regular_v1(descriptor: int, expected_size: int) -> bytes:
    if expected_size > MAX_LEGACY_STATE_FILE_BYTES_V1:
        _fail("authoring file size")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks, remaining = [], expected_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                _fail("authoring file shortened")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("authoring file extended")
        return b"".join(chunks)
    except OSError as exc:
        _fail("authoring file read", exc)


def _require_entry_v1(descriptor: int, entry, owner) -> None:
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        _fail("authoring metadata", exc)
    directory = entry.node_kind is LegacyNodeKindV1.directory
    if (
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        is not True
        or (info.st_dev, info.st_ino) != (entry.device, entry.inode)
        or (info.st_uid, info.st_gid) != owner
        or stat.S_IMODE(info.st_mode) != entry.mode
        or info.st_nlink != entry.nlink
        or (not directory and info.st_size != entry.size)
    ):
        _fail("authoring metadata changed")
    try:
        require_no_acl_v1(descriptor)
    except PosixDirectoryError as exc:
        _fail("authoring ACL", exc)
    if not directory and legacy_state_file_sha256_v1(
        _read_regular_v1(descriptor, entry.size),
    ) != entry.content_sha256:
        _fail("authoring content changed")


def _ordered_root_authoring_v1(observation) -> tuple:
    selected = tuple(
        item for item in observation.entries
        if item.relative_path.parts[0] == "contract-authoring"
        and (item.uid, item.gid) == (0, 0)
    )
    return tuple(sorted(
        selected,
        key=lambda item: (
            -len(item.relative_path.parts),
            item.relative_path.as_posix().encode("utf-8"),
        ),
    ))


class _LegacyStateEffectsV1:
    def __init__(self, request, state_chain, journal, attest) -> None:
        self._request = request
        self._state_chain = state_chain
        self._journal = journal
        self._attest = attest

    def load_records(self):
        return self._journal.load_records()

    def append_record(self, sequence, encoded):
        self._journal.append_record(sequence, encoded)

    def observe(self):
        return observe_legacy_state_v1(self._request)

    def _change_owner_v1(self, entry) -> None:
        self._attest()
        directory = entry.node_kind is LegacyNodeKindV1.directory
        try:
            with self._state_chain.open_relative(
                entry.relative_path, directory=directory,
            ) as descriptor:
                _require_entry_v1(descriptor, entry, (0, 0))
                os.fchown(
                    descriptor,
                    self._request.service_uid,
                    self._request.service_gid,
                )
                os.fsync(descriptor)
                _require_entry_v1(
                    descriptor, entry,
                    (self._request.service_uid, self._request.service_gid),
                )
        except (OSError, PosixDirectoryError) as exc:
            _fail("authoring ownership effect", exc)
        self._attest()

    def adopt_authoring(self, before):
        target = legacy_state_adoption_target_sha256_v1(
            self._request, before,
        )
        for original in _ordered_root_authoring_v1(before):
            current = self.observe()
            if not legacy_state_adoption_resume_valid_v1(
                self._request, target, current,
            ):
                _fail("authoring adoption target changed")
            indexed = {
                item.relative_path: item for item in current.entries
            }
            candidate = indexed.get(original.relative_path)
            if candidate is None:
                _fail("authoring path disappeared")
            if (candidate.uid, candidate.gid) == (0, 0):
                self._change_owner_v1(candidate)
        return self.observe()

    def checkpoint(self, name: str) -> None:
        if type(name) is not str or not name:
            _fail("checkpoint")


class _LockedLegacyStateEffectsV1:
    __slots__ = ("_active", "_attest", "_effects", "_pid")

    def __init__(self, effects, attest) -> None:
        self._active, self._effects = True, effects
        self._attest, self._pid = attest, os.getpid()

    def _guarded_v1(self, operation):
        if not self._active or self._pid != os.getpid():
            _fail("legacy capability inactive")
        self._attest()
        try:
            return operation()
        finally:
            self._attest()

    def load_records(self):
        return self._guarded_v1(self._effects.load_records)

    def append_record(self, sequence, encoded):
        return self._guarded_v1(
            lambda: self._effects.append_record(sequence, encoded),
        )

    def observe(self):
        return self._guarded_v1(self._effects.observe)

    def adopt_authoring(self, before):
        return self._guarded_v1(lambda: self._effects.adopt_authoring(before))

    def checkpoint(self, name):
        return self._guarded_v1(lambda: self._effects.checkpoint(name))

    def deactivate(self) -> None:
        if self._active and self._pid == os.getpid():
            self._attest()
        self._active, self._effects, self._attest = False, None, None

    def __copy__(self):
        _fail("legacy capability copy")

    def __deepcopy__(self, _memo):
        _fail("legacy capability copy")

    def __reduce__(self):
        _fail("legacy capability serialization")

    def __reduce_ex__(self, _protocol):
        _fail("legacy capability serialization")


def _bound_chains_v1(request, account):
    state_path = Path(request.state_root.as_posix())
    journal_path = Path(LEGACY_STATE_JOURNAL_ROOT_V1.as_posix())
    state_chain = BoundDirectoryChainV1(state_path)
    try:
        journal_chain = BoundDirectoryChainV1(journal_path)
    except BaseException:
        state_chain.close()
        raise
    try:
        expected = (
            _expectations_v1(account, request.state_root),
            _expectations_v1(account, LEGACY_STATE_JOURNAL_ROOT_V1),
        )
    except BaseException:
        journal_chain.close()
        state_chain.close()
        raise
    return state_chain, journal_chain, expected


def _release_context_v1(
    capability, lock_fd, locked, owner, journal_chain, state_chain,
) -> None:
    actions = []
    if capability is not None:
        actions.append(capability.deactivate)
    if lock_fd is not None and locked and owner:
        actions.append(lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN))
    if lock_fd is not None:
        actions.append(lambda: close_descriptors_v1((lock_fd,)))
    if journal_chain is not None:
        actions.append(journal_chain.close)
    if state_chain is not None:
        actions.append(state_chain.close)
    try:
        run_cleanup_v1(actions, detail="legacy effect cleanup")
    except PosixDirectoryError as exc:
        _fail("legacy effect cleanup", exc)


@contextmanager
def locked_legacy_state_effects_v1(request, account, maintenance_session):
    """Bind journal J after the caller already owns D→S→C→L."""
    _require_maintenance_session_v1(maintenance_session)
    _require_effect_platform_v1()
    require_canonical_legacy_state_request_v1(request)
    if (
        build_legacy_state_request_v1(account, request.distribution_sha256)
        != request
    ):
        _fail("legacy capability binding")
    owner_pid = os.getpid()
    state_chain = journal_chain = None
    lock_fd, locked, capability = None, False, None
    try:
        state_chain, journal_chain, expected = _bound_chains_v1(
            request, account,
        )
        lock_fd = open_legacy_journal_lock_v1(journal_chain.root_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        locked = True

        def attest() -> None:
            _require_maintenance_session_v1(maintenance_session)
            state_chain.attest_metadata(expected[0])
            journal_chain.attest_metadata(expected[1])
            require_legacy_journal_lock_bound_v1(
                journal_chain.root_fd, lock_fd,
            )
            _require_maintenance_session_v1(maintenance_session)

        attest()
        raw = _LegacyStateEffectsV1(
            request, state_chain,
            LegacyStateJournalStoreV1(journal_chain.root_fd), attest,
        )
        capability = _LockedLegacyStateEffectsV1(raw, attest)
        yield capability
    finally:
        owner = os.getpid() == owner_pid
        _release_context_v1(capability, lock_fd, locked, owner, journal_chain, state_chain)


__all__ = [
    "LegacyStateEffectPosixError", "locked_legacy_state_effects_v1",
]

"""Typed host-provisioning facade over the shared POSIX journal kernel."""
from __future__ import annotations

import executor_birth_host_provisioning_journal as journal
from install import executor_birth_append_journal_posix as append_journal


_LAYOUT_V1 = append_journal.PosixAppendJournalLayoutV1(
    record_count=4,
    maximum_record_bytes=journal.MAX_HOST_PROVISIONING_RECORD_BYTES_V1,
)


class HostProvisioningPosixError(RuntimeError):
    pass


def raise_posix_v1(detail: str, cause: BaseException | None = None):
    error = HostProvisioningPosixError(detail)
    if cause is None:
        raise error
    raise error from cause


def _translated_v1(operation):
    try:
        return operation()
    except append_journal.PosixAppendJournalError as exc:
        detail = str(exc)
        if detail.startswith("append journal "):
            detail = detail.removeprefix("append ")
        raise_posix_v1(detail, exc)


class PosixJournalStoreV1:
    """Host record grammar bound to one already-opened journal directory."""

    __slots__ = ("_store",)

    def __init__(
        self, root_fd: int, owner: tuple[int, int] = (0, 0),
    ) -> None:
        self._store = _translated_v1(
            lambda: append_journal.BoundPosixAppendJournalV1(
                root_fd, _LAYOUT_V1, owner,
            ),
        )

    def _load_v1(self, *, recover: bool) -> tuple[bytes, ...]:
        raw = _translated_v1(lambda: self._store.read_prefix(recover=recover))
        if raw:
            try:
                journal.decode_host_provisioning_chain_v1(raw)
            except Exception as exc:
                raise_posix_v1("journal append chain", exc)
        return raw

    def load_records(self) -> tuple[bytes, ...]:
        return self._load_v1(recover=True)

    def inspect_records(self) -> tuple[bytes, ...]:
        """Read the exact prefix without crash recovery or any write."""
        return self._load_v1(recover=False)

    def append_record(self, sequence: int, encoded: bytes) -> None:
        prefix = self.load_records()
        try:
            record = journal.decode_host_provisioning_record_v1(encoded)
        except Exception as exc:
            raise_posix_v1("journal append chain", exc)
        if type(sequence) is not int or record.sequence != sequence:
            raise_posix_v1("journal append binding")
        if sequence < len(prefix):
            if prefix[sequence] != encoded:
                raise_posix_v1("journal record conflict")
            return
        if sequence != len(prefix):
            raise_posix_v1("journal append sequence")
        try:
            decoded = journal.decode_host_provisioning_chain_v1(
                prefix + (encoded,),
            )
        except Exception as exc:
            raise_posix_v1("journal append chain", exc)
        if decoded[-1].sequence != sequence:
            raise_posix_v1("journal append binding")
        _translated_v1(lambda: self._store.append_exact(sequence, encoded))


class HostJournalEffectsV1:
    """Narrow journal port composed by the host effect adapter."""

    def __init__(self, journal_store: PosixJournalStoreV1 | None = None) -> None:
        if journal_store is not None and not all(
            callable(getattr(journal_store, name, None))
            for name in ("load_records", "append_record")
        ):
            raise_posix_v1("journal store binding")
        self._journal_store = journal_store

    def load_records(self) -> tuple[bytes, ...]:
        if self._journal_store is None:
            raise_posix_v1("journal lock absent")
        return self._journal_store.load_records()

    def append_record(self, sequence: int, encoded: bytes) -> None:
        if self._journal_store is None:
            raise_posix_v1("journal lock absent")
        self._journal_store.append_record(sequence, encoded)


def require_journal_lock_bound_v1(
    root_fd: int, descriptor: int, owner: tuple[int, int],
) -> None:
    _translated_v1(
        lambda: append_journal.require_journal_lock_bound_v1(
            root_fd, descriptor, owner,
        ),
    )


def open_journal_lock_v1(root_fd: int, owner: tuple[int, int]) -> int:
    return _translated_v1(
        lambda: append_journal.open_journal_lock_v1(root_fd, owner),
    )


__all__ = [
    "HostJournalEffectsV1", "HostProvisioningPosixError", "PosixJournalStoreV1",
    "open_journal_lock_v1", "raise_posix_v1",
    "require_journal_lock_bound_v1",
]

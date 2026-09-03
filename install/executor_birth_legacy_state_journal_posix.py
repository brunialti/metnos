"""Typed legacy-state facade over the shared POSIX append journal."""
from __future__ import annotations

from install import executor_birth_append_journal_posix as append_journal
import executor_birth_legacy_state_journal as journal


_LAYOUT_V1 = append_journal.PosixAppendJournalLayoutV1(
    record_count=4,
    maximum_record_bytes=journal.MAX_LEGACY_STATE_RECORD_BYTES_V1,
)


class LegacyStateJournalPosixError(RuntimeError):
    pass


def _fail(detail: str, cause: BaseException | None = None):
    error = LegacyStateJournalPosixError(detail)
    if cause is None:
        raise error
    raise error from cause


def _translated_v1(operation):
    try:
        return operation()
    except append_journal.PosixAppendJournalError as exc:
        detail = str(exc).removeprefix("append journal ")
        _fail("legacy journal " + detail, exc)


class LegacyStateJournalStoreV1:
    """Legacy FSM bytes bound to one root-owned journal directory."""

    __slots__ = ("_store",)

    def __init__(self, root_fd: int) -> None:
        self._store = _translated_v1(
            lambda: append_journal.BoundPosixAppendJournalV1(
                root_fd, _LAYOUT_V1, (0, 0),
            ),
        )

    def _load_v1(self, *, recover: bool) -> tuple[bytes, ...]:
        raw = _translated_v1(lambda: self._store.read_prefix(recover=recover))
        if raw:
            try:
                journal.decode_legacy_state_chain_v1(raw)
            except Exception as exc:
                _fail("legacy journal chain", exc)
        return raw

    def load_records(self) -> tuple[bytes, ...]:
        return self._load_v1(recover=True)

    def inspect_records(self) -> tuple[bytes, ...]:
        return self._load_v1(recover=False)

    def append_record(self, sequence: int, encoded: bytes) -> None:
        prefix = self.load_records()
        try:
            record = journal.decode_legacy_state_record_v1(encoded)
        except Exception as exc:
            _fail("legacy journal chain", exc)
        if type(sequence) is not int or record.sequence != sequence:
            _fail("legacy journal append binding")
        if sequence < len(prefix):
            if prefix[sequence] != encoded:
                _fail("legacy journal record conflict")
            return
        if sequence != len(prefix):
            _fail("legacy journal append sequence")
        try:
            decoded = journal.decode_legacy_state_chain_v1(
                prefix + (encoded,),
            )
        except Exception as exc:
            _fail("legacy journal chain", exc)
        if decoded[-1].sequence != sequence:
            _fail("legacy journal append binding")
        _translated_v1(lambda: self._store.append_exact(sequence, encoded))


def open_legacy_journal_lock_v1(root_fd: int) -> int:
    return _translated_v1(
        lambda: append_journal.open_journal_lock_v1(root_fd, (0, 0)),
    )


def require_legacy_journal_lock_bound_v1(
    root_fd: int, descriptor: int,
) -> None:
    _translated_v1(
        lambda: append_journal.require_journal_lock_bound_v1(
            root_fd, descriptor, (0, 0),
        ),
    )


__all__ = [
    "LegacyStateJournalPosixError", "LegacyStateJournalStoreV1",
    "open_legacy_journal_lock_v1", "require_legacy_journal_lock_bound_v1",
]

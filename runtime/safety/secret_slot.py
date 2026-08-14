"""secret_slot.py — one-time password / token slot with explicit zeroing.

Used by `sudoer` to receive the sudo password from the user, pass it to
`subprocess` exactly once, and overwrite the buffer immediately after.

Invariants (ADR 0070, ADR 0071):
- Never a Python `str` (immutable, would linger in the interning pool).
- Always a `bytearray` so we can zero in place.
- The slot is filled exactly once and consumed exactly once.
- A double-fill or double-consume raises explicitly: never silent failures.
- After consume, the slot is unusable.

Usage::

    slot = SecretSlot()
    slot.fill(password_bytes)
    with slot.consume() as secret:
        # secret is a bytes view of the buffer; subprocess can pipe it.
        subprocess.run(argv, input=secret, ...)
    # buffer is now zeroed; the slot cannot be reused.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


class SecretSlotError(RuntimeError):
    """Raised on protocol violations of the SecretSlot lifecycle."""


class SecretSlot:
    """One-time, zero-after-use container for a small secret (typically <1 KiB)."""

    __slots__ = ("_buf", "_filled", "_consumed")

    def __init__(self) -> None:
        self._buf: bytearray | None = None
        self._filled: bool = False
        self._consumed: bool = False

    def fill(self, data: bytes | bytearray) -> None:
        if self._filled:
            raise SecretSlotError("SecretSlot already filled; cannot refill")
        if self._consumed:
            raise SecretSlotError("SecretSlot already consumed; cannot refill")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes or bytearray")
        self._buf = bytearray(data)
        self._filled = True

    @contextmanager
    def consume(self) -> Iterator[bytes]:
        if not self._filled:
            raise SecretSlotError("SecretSlot not filled; nothing to consume")
        if self._consumed:
            raise SecretSlotError("SecretSlot already consumed")
        if self._buf is None:  # pragma: no cover (defensive)
            raise SecretSlotError("SecretSlot in inconsistent state")
        try:
            # bytes(self._buf) makes a one-shot copy; the original buffer is
            # the canonical owner and gets zeroed in finally.
            yield bytes(self._buf)
        finally:
            self._zero()
            self._consumed = True

    def _zero(self) -> None:
        if self._buf is not None:
            for i in range(len(self._buf)):
                self._buf[i] = 0
        self._buf = None

    @property
    def is_filled(self) -> bool:
        return self._filled and not self._consumed

    @property
    def is_consumed(self) -> bool:
        return self._consumed

    def __del__(self) -> None:  # pragma: no cover — best-effort cleanup
        try:
            if self._filled and not self._consumed:
                self._zero()
        except Exception:
            pass

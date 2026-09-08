"""Process-bound capability for the locked Executor Birth host adapter."""
from __future__ import annotations

import os
from typing import Callable

from install.executor_birth_host_journal_posix import raise_posix_v1


_CAPABILITY_SEAL_V1 = object()


class _LockedHostEffectsV1:
    """Expose fixed host effects only while their lock remains attested."""

    __slots__ = ("__active", "__attest", "__effects", "__pid")

    def __init__(self, seal: object, effects: object, attest: Callable[[], None]):
        if seal is not _CAPABILITY_SEAL_V1 or not callable(attest):
            raise_posix_v1("host capability construction")
        self.__active = True
        self.__attest = attest
        self.__effects = effects
        self.__pid = os.getpid()

    def _require_active_v1(self) -> None:
        if not self.__active or self.__pid != os.getpid():
            raise_posix_v1("host capability inactive")

    def __guarded_v1(self, operation: Callable[[], object]):
        self._require_active_v1()
        self.__attest()
        try:
            return operation()
        finally:
            self.__attest()

    def _deactivate_v1(self) -> None:
        try:
            self._require_active_v1()
            self.__attest()
        finally:
            self.__active = False
            self.__attest = None
            self.__effects = None

    def load_records(self):
        return self.__guarded_v1(lambda: self.__effects.load_records())

    def append_record(self, sequence, encoded) -> None:
        self.__guarded_v1(
            lambda: self.__effects.append_record(sequence, encoded),
        )

    def observe_account(self):
        return self.__guarded_v1(lambda: self.__effects.observe_account())

    def observe_primary_group(self):
        return self.__guarded_v1(
            lambda: self.__effects.observe_primary_group(),
        )

    def create_primary_group(self) -> None:
        self.__guarded_v1(lambda: self.__effects.create_primary_group())

    def create_account(self) -> None:
        self.__guarded_v1(lambda: self.__effects.create_account())

    def observe_layout(self, account):
        return self.__guarded_v1(
            lambda: self.__effects.observe_layout(account),
        )

    def apply_layout_step(self, step, account) -> None:
        self.__guarded_v1(
            lambda: self.__effects.apply_layout_step(step, account),
        )

    def checkpoint(self, name) -> None:
        self.__guarded_v1(lambda: self.__effects.checkpoint(name))

    def __copy__(self):
        raise_posix_v1("host capability copy")

    def __deepcopy__(self, _memo):
        raise_posix_v1("host capability copy")

    def __reduce__(self):
        raise_posix_v1("host capability serialization")

    def __reduce_ex__(self, _protocol):
        raise_posix_v1("host capability serialization")


def bind_locked_host_effects_v1(effects: object, attest: Callable[[], None]):
    return _LockedHostEffectsV1(_CAPABILITY_SEAL_V1, effects, attest)


__all__ = ["bind_locked_host_effects_v1"]

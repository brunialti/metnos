"""Bounded admission and dedicated workers for blocking HTTP turns."""
from __future__ import annotations

import asyncio
import contextvars
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


class TurnPoolBusy(RuntimeError):
    pass


def _bounded_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def default_worker_count() -> int:
    """Piccolo per costruzione; override esplicito per host più capaci."""
    cpu_cap = max(1, min(8, int(os.cpu_count() or 1) // 2 or 1))
    llm_slots = _bounded_env("METNOS_LLM_MAX_IN_FLIGHT", 1, 1, 32)
    inferred = min(cpu_cap, max(2, llm_slots))
    return _bounded_env("METNOS_HTTP_TURN_WORKERS", inferred, 1, 16)


@dataclass
class _PrincipalGate:
    semaphore: asyncio.BoundedSemaphore
    users: int = 0


@dataclass
class _Reservation:
    principal: str
    gate: _PrincipalGate
    queued_at: float
    released: bool = False


class HttpTurnPool:
    """Pool dedicato con backpressure globale e fair-use per principal."""

    def __init__(self, *, workers: int | None = None,
                 queue_slots: int | None = None,
                 per_principal: int | None = None,
                 admission_timeout_s: float = 0.05):
        self.workers = workers or default_worker_count()
        self.queue_slots = (queue_slots if queue_slots is not None
                            else self.workers * 2)
        self.per_principal = (per_principal if per_principal is not None
                              else min(2, self.workers))
        self.admission_timeout_s = max(0.001, admission_timeout_s)
        self._executor = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="metnos-http-turn")
        self._global = asyncio.BoundedSemaphore(
            self.workers + max(0, self.queue_slots))
        # Solo principal con richieste attive/in attesa: principal arbitrari
        # non devono poter far crescere la memoria del daemon senza limite.
        self._principals: dict[str, _PrincipalGate] = {}
        self._closed = False
        self.admitted = 0
        self.rejected = 0
        self.completed = 0

    async def reserve(self, principal: str) -> _Reservation:
        if self._closed:
            raise TurnPoolBusy("turn pool is closed")
        key = str(principal or "anonymous")
        gate = self._principals.get(key)
        if gate is None:
            gate = _PrincipalGate(
                asyncio.BoundedSemaphore(max(1, self.per_principal)))
            self._principals[key] = gate
        gate.users += 1
        try:
            await asyncio.wait_for(
                self._global.acquire(), timeout=self.admission_timeout_s)
        except asyncio.TimeoutError as ex:
            self._release_principal(key, gate)
            self.rejected += 1
            raise TurnPoolBusy("global turn capacity exhausted") from ex
        except BaseException:
            self._release_principal(key, gate)
            raise
        try:
            await asyncio.wait_for(
                gate.semaphore.acquire(), timeout=self.admission_timeout_s)
        except asyncio.TimeoutError as ex:
            self._global.release()
            self._release_principal(key, gate)
            self.rejected += 1
            raise TurnPoolBusy("principal turn capacity exhausted") from ex
        except BaseException:
            self._global.release()
            self._release_principal(key, gate)
            raise
        self.admitted += 1
        return _Reservation(key, gate, time.monotonic())

    async def run_reserved(self, reservation: _Reservation,
                           function: Callable[[], T]) -> T:
        """Run *function* without releasing capacity before its thread ends.

        Cancelling an asyncio Future does not stop the underlying Python
        thread.  Submitting through ``ThreadPoolExecutor`` directly lets us
        retain the admission permits until the concurrent Future really is
        done, preventing disconnects from bypassing backpressure.
        """
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        try:
            future = self._executor.submit(context.run, function)
        except BaseException:
            self.release(reservation)
            raise
        try:
            result = await asyncio.shield(asyncio.wrap_future(future))
        except asyncio.CancelledError:
            def _release_when_done(_done) -> None:
                try:
                    loop.call_soon_threadsafe(
                        self.release, reservation, True)
                except RuntimeError:
                    # Shutdown: il loop puo' essere gia' chiuso.
                    self.release(reservation, completed=True)
            future.add_done_callback(_release_when_done)
            raise
        except BaseException:
            self.release(reservation, completed=True)
            raise
        self.release(reservation, completed=True)
        return result

    async def run(self, principal: str, function: Callable[[], T]) -> T:
        reservation = await self.reserve(principal)
        return await self.run_reserved(reservation, function)

    def release(self, reservation: _Reservation,
                completed: bool = False) -> None:
        """Release an unused/completed reservation exactly once."""
        if reservation.released:
            return
        reservation.released = True
        reservation.gate.semaphore.release()
        self._global.release()
        self._release_principal(reservation.principal, reservation.gate)
        if completed:
            self.completed += 1

    def _release_principal(self, principal: str, gate: _PrincipalGate) -> None:
        gate.users -= 1
        if gate.users == 0 and self._principals.get(principal) is gate:
            self._principals.pop(principal, None)

    def stats(self) -> dict:
        return {
            "workers": self.workers,
            "queue_slots": self.queue_slots,
            "per_principal": self.per_principal,
            "admitted": self.admitted,
            "rejected": self.rejected,
            "completed": self.completed,
        }

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

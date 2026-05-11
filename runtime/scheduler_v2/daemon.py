"""SchedulerDaemon — asyncio-native scheduler loop for Metnos v2.

Runs as a single asyncio.Task hosted by the aiohttp HTTP server (no
standalone process). The loop wakes at MIN(next_fire_at) or when kicked.
Sync callbacks run in a ThreadPoolExecutor; async callbacks are awaited
directly. Per-entry re-entrancy lock honors max_concurrent.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .callbacks import CallbackRegistry
from .models import ScheduleEntry
from .schedule_parser import next_fire_at as compute_next_fire
from .storage import SchedulerStorage

log = logging.getLogger(__name__)


_MAX_SLEEP_S = 60.0   # cap on idle sleep so DST transitions can't strand us
_MIN_SLEEP_S = 0.001  # floor to avoid busy-spin if a timer is in the past


def _utc_iso(epoch: float | None = None) -> str:
    if epoch is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _weekday_token(epoch: float, tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    dt = datetime.fromtimestamp(epoch, tz=tz)
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][dt.weekday()]


class SchedulerDaemon:
    def __init__(
        self,
        db_path: Path,
        *,
        pool_size: int | None = None,
        tz_name: str = "Europe/Rome",
    ):
        self.db_path = Path(db_path)
        self.tz_name = tz_name
        self.pool_size = pool_size or min(32, (os.cpu_count() or 4) * 4)
        self.callbacks = CallbackRegistry()
        self.storage = SchedulerStorage(self.db_path)
        self.wake_evt: asyncio.Event | None = None
        self.shutdown_evt: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # entry_id -> in-flight count (asyncio-safe lock guards mutation;
        # created in start() so it binds to the running loop).
        self._running: dict[int, int] = {}
        self._running_lock: asyncio.Lock | None = None

    # --- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.wake_evt = asyncio.Event()
        self.shutdown_evt = asyncio.Event()
        self._running_lock = asyncio.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=self.pool_size, thread_name_prefix="metnos-sched-v2"
        )
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(self._loop_main(), name="scheduler_v2_loop")

    async def stop(self, timeout: float = 5.0) -> None:
        if self.shutdown_evt is not None:
            self.shutdown_evt.set()
        if self.wake_evt is not None:
            self.wake_evt.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        self._loop = None

    def kick(self) -> None:
        """Signal the loop to re-check schedule_entries immediately.

        Thread-safe. If invoked from the loop's own thread, sets the event
        directly. If invoked from another thread (e.g. HTTP handler running
        in aiohttp executor), schedules `wake_evt.set()` on the loop via
        `call_soon_threadsafe` so asyncio internals stay consistent.
        """
        if self.wake_evt is None:
            return
        loop = self._loop
        if loop is None:
            self.wake_evt.set()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self.wake_evt.set()
        else:
            try:
                loop.call_soon_threadsafe(self.wake_evt.set)
            except RuntimeError:
                # Loop is closed; nothing to wake.
                pass

    # --- main loop ---------------------------------------------------

    async def _loop_main(self) -> None:
        try:
            self.storage.mark_crashed_runs()
            await self._recover_missed()
            assert self.shutdown_evt is not None and self.wake_evt is not None
            while not self.shutdown_evt.is_set():
                now = time.time()
                due = self.storage.fetch_due(now, limit=100)
                if due:
                    await asyncio.gather(
                        *[self._fire(e) for e in due], return_exceptions=True
                    )
                # Recompute now to avoid stale sleep
                now2 = time.time()
                sleep_s = self._compute_sleep(now2)
                self.wake_evt.clear()
                try:
                    await asyncio.wait_for(self.wake_evt.wait(), timeout=sleep_s)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("scheduler v2 loop crashed")
            raise

    def _compute_sleep(self, now: float) -> float:
        nxt = self.storage.next_fire_at_min()
        if nxt is None:
            return _MAX_SLEEP_S
        delta = nxt - now
        if delta <= 0:
            return _MIN_SLEEP_S
        return min(_MAX_SLEEP_S, max(_MIN_SLEEP_S, delta))

    # --- recover missed ---------------------------------------------

    async def _recover_missed(self) -> None:
        """On boot, fire entries whose next_fire_at is in the past iff within
        their grace_window_s. Past-grace entries are advanced forward (recurring)
        or disabled (one-shot) without firing.
        """
        now = time.time()
        all_entries = self.storage.list_all()
        for e in all_entries:
            if not e.enabled:
                continue
            age = now - e.next_fire_at
            if age <= 0:
                continue
            grace = e.grace_window_s
            if grace is None or age <= grace:
                # In grace: fire normally on next loop iteration; nothing to do here
                # (the loop will pick it up via fetch_due).
                continue
            # Past grace: skip without firing
            if e.recurring:
                try:
                    nxt = compute_next_fire(e.trigger, now, self.tz_name)
                except Exception:
                    log.exception("recover_missed: parse failed for %s", e.name)
                    continue
                self.storage.update_next_fire(e.id, nxt)  # type: ignore[arg-type]
            else:
                self.storage.disable(e.id)  # type: ignore[arg-type]

    # --- fire --------------------------------------------------------

    async def _fire(self, entry: ScheduleEntry) -> None:
        if entry.id is None:
            return
        # Weekday filter
        if entry.weekdays:
            allowed = {w.strip().lower() for w in entry.weekdays.split(",") if w.strip()}
            if _weekday_token(time.time(), self.tz_name) not in allowed:
                # Advance and skip
                if entry.recurring:
                    nxt = compute_next_fire(entry.trigger, time.time(), self.tz_name)
                    self.storage.update_next_fire(entry.id, nxt)
                else:
                    self.storage.disable(entry.id)
                return
        # Expiry filter
        if entry.expires_at:
            try:
                exp = datetime.fromisoformat(entry.expires_at.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if time.time() >= exp.timestamp():
                    self.storage.disable(entry.id)
                    return
            except Exception:
                log.warning("entry %s: malformed expires_at %r", entry.name, entry.expires_at)
        # Re-entrancy lock
        async with self._running_lock:
            cur = self._running.get(entry.id, 0)
            if cur >= max(1, entry.max_concurrent):
                # Skip this fire to avoid overlap; advance schedule so we don't
                # spin every tick.
                if entry.recurring:
                    nxt = compute_next_fire(entry.trigger, time.time(), self.tz_name)
                    self.storage.update_next_fire(entry.id, nxt)
                return
            self._running[entry.id] = cur + 1

        cb = self.callbacks.get(entry.callback_key)
        run_id = self.storage.begin_run(entry.id, entry.name)
        t0 = time.time()
        status = "success"
        error: str | None = None
        output = ""

        try:
            if cb is None:
                status = "error"
                error = f"unknown callback_key: {entry.callback_key!r}"
            else:
                coro = self._invoke(cb.fn, entry, is_async=cb.is_async)
                if entry.timeout_s and entry.timeout_s > 0:
                    result = await asyncio.wait_for(coro, timeout=entry.timeout_s)
                else:
                    result = await coro
                if result is not None:
                    output = str(result)[:4096]
        except asyncio.TimeoutError:
            status = "timeout"
            error = f"timed out after {entry.timeout_s}s"
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            log.exception("entry %s: callback failed", entry.name)
        finally:
            duration_ms = int((time.time() - t0) * 1000)
            self.storage.end_run(
                run_id, status=status, duration_ms=duration_ms, output=output
            )
            # Decide next_fire_at and enabled
            disable = False
            decrement_remaining = False
            new_next: float | None = None
            if entry.recurring:
                try:
                    new_next = compute_next_fire(
                        entry.trigger, time.time(), self.tz_name
                    )
                except Exception:
                    log.exception(
                        "entry %s: cannot recompute next_fire_at", entry.name
                    )
                # remaining_runs > 0 means countdown is active
                if entry.remaining_runs and entry.remaining_runs > 0:
                    decrement_remaining = True
                    if entry.remaining_runs - 1 <= 0:
                        disable = True
            else:
                disable = True  # one-shot fired (success or error): disable
            self.storage.record_outcome(
                entry.id,
                status=status,
                duration_ms=duration_ms,
                error=error,
                next_fire_at=new_next,
                decrement_remaining=decrement_remaining,
                disable=disable,
            )
            async with self._running_lock:
                cur = self._running.get(entry.id, 0)
                if cur <= 1:
                    self._running.pop(entry.id, None)
                else:
                    self._running[entry.id] = cur - 1

    async def _invoke(self, fn, entry: ScheduleEntry, *, is_async: bool):
        if is_async:
            return await fn(entry.payload)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, entry.payload)

    # --- convenience -------------------------------------------------

    def schedule(
        self,
        name: str,
        trigger: str,
        callback_key: str,
        *,
        recurring: bool = True,
        payload: dict | None = None,
        timeout_s: int | None = None,
        is_async: bool = False,
        max_concurrent: int = 1,
        grace_window_s: int | None = None,
        weekdays: str = "",
        expires_at: str = "",
        remaining_runs: int = 0,
        origin: str = "system",
        label: str = "",
        source_command: str = "",
        description: str = "",
    ) -> int:
        """Compute next_fire_at and upsert. Returns entry id. Kicks the loop."""
        nxt = compute_next_fire(trigger, time.time(), self.tz_name)
        entry = ScheduleEntry(
            name=name,
            trigger=trigger,
            next_fire_at=nxt,
            recurring=recurring,
            callback_key=callback_key,
            payload=payload or {},
            weekdays=weekdays,
            expires_at=expires_at,
            remaining_runs=remaining_runs,
            timeout_s=timeout_s,
            is_async=is_async,
            max_concurrent=max_concurrent,
            grace_window_s=grace_window_s,
            origin=origin,
            label=label,
            source_command=source_command,
            description=description,
        )
        eid = self.storage.upsert(entry)
        self.kick()
        return eid

    def remove(self, name: str) -> bool:
        ok = self.storage.delete(name)
        self.kick()
        return ok

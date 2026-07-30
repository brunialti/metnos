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
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .callbacks import CallbackRegistry
from .models import CallbackOutcome, ScheduleEntry
from .schedule_parser import next_fire_at as compute_next_fire
from .storage import SchedulerStorage

log = logging.getLogger(__name__)


_MAX_SLEEP_S = 60.0   # cap on idle sleep so DST transitions can't strand us
_MIN_SLEEP_S = 0.001  # floor to avoid busy-spin if a timer is in the past

# Circuit-breaker: dopo N fallimenti CONSECUTIVI (error/timeout) un task
# ricorrente viene auto-disabilitato, per non ri-sparare all'infinito una
# pipeline rotta (PAT scaduto, repo rimosso, ...). L'owner viene notificato
# via hook `on_circuit_break` (continua/sospendi/cancella). Soglia override
# via env per ops. Allineato a 3 come l'escalation Strato 3 (≥3 ✗).
_CIRCUIT_BREAK_AFTER = int(os.environ.get("METNOS_SCHED_CIRCUIT_BREAK_AFTER") or 3)




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
        # Runtime health is process-local and therefore authoritative for the
        # co-hosted loop.  Observers must not infer liveness from whether a job
        # happened to run recently: an idle scheduler is still healthy.
        self._started_at_epoch: float | None = None
        self._stopped_at_epoch: float | None = None
        self._heartbeat_at_epoch: float | None = None
        self._last_error_class = ""
        self._last_error_summary = ""
        # Hook opzionale invocato quando un task ricorrente viene
        # auto-disabilitato dal circuit-breaker. Firma: (entry, error: str|None).
        # Settato da chi conosce i canali (recurring_tasks): il daemon resta
        # channel-agnostico. None = solo log.
        self.on_circuit_break = None

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
        now = time.time()
        self._started_at_epoch = now
        self._stopped_at_epoch = None
        self._heartbeat_at_epoch = now
        self._last_error_class = ""
        self._last_error_summary = ""
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(self._loop_main(), name="scheduler_v2_loop")
        self._task.add_done_callback(lambda _task: self._publish_health())
        self._publish_health()

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
            except Exception:
                # A loop failure is already preserved by runtime_status() and
                # logged at its origin.  Shutdown must still complete so the
                # HTTP service can restart cleanly and recover the scheduler.
                log.warning("scheduler v2 stopped after loop failure")
        self._task = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        self._loop = None
        self._stopped_at_epoch = time.time()
        self._publish_health()

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
                self._heartbeat_at_epoch = now
                self._publish_health()
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
                self._heartbeat_at_epoch = time.time()
                self._publish_health()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._last_error_class = type(exc).__name__
            # Bounded, single-line diagnostics are safe to expose to the
            # administrator; callback outputs and payloads never enter here.
            summary = re.sub(r"\s+", " ", str(exc)).strip()
            self._last_error_summary = summary[:240]
            log.exception("scheduler v2 loop crashed")
            raise

    def _publish_health(self) -> None:
        """Best-effort IPC snapshot for kill-isolated Tutor probes."""

        try:
            from .health import publish
            publish(self.runtime_status())
        except Exception:
            log.warning("scheduler v2 health publication failed", exc_info=True)

    @staticmethod
    def _iso(epoch: float | None) -> str:
        if epoch is None:
            return ""
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace(
            "+00:00", "Z")

    def runtime_status(self) -> dict:
        """Return a bounded, source-attested snapshot of the co-hosted loop.

        This is intentionally about the loop itself, not about the success of
        individual jobs.  A failed callback does not make the scheduler down,
        and an idle period is not a missing heartbeat.
        """

        now = time.time()
        task = self._task
        if task is None:
            state = "stopped"
            healthy = False
            reason_code = (
                "graceful_stop" if self._stopped_at_epoch is not None
                else "not_started"
            )
        elif task.cancelled():
            state = "stopped"
            healthy = False
            reason_code = "loop_cancelled"
        elif task.done():
            state = "failed" if self._last_error_class else "stopped"
            healthy = False
            reason_code = (
                "loop_failed" if self._last_error_class else "loop_finished"
            )
        else:
            state = "running"
            healthy = True
            reason_code = "loop_active"

        try:
            entries = self.storage.list_all()
            recent = self.storage.list_runs(limit=1)
        except Exception as exc:  # health must remain observable on DB faults
            entries = []
            recent = []
            if healthy:
                state = "degraded"
                healthy = False
                reason_code = "storage_unavailable"
            if not self._last_error_class:
                self._last_error_class = type(exc).__name__
                self._last_error_summary = re.sub(
                    r"\s+", " ", str(exc)).strip()[:240]

        last_run = recent[0] if recent else None
        heartbeat_age = (
            max(0.0, now - self._heartbeat_at_epoch)
            if self._heartbeat_at_epoch is not None else None
        )
        return {
            "component": "scheduler_v2",
            "cohost": "http",
            "state": state,
            "healthy": healthy,
            "reason_code": reason_code,
            "started_at": self._iso(self._started_at_epoch),
            "stopped_at": self._iso(self._stopped_at_epoch),
            "heartbeat_at": self._iso(self._heartbeat_at_epoch),
            "heartbeat_age_s": (
                round(heartbeat_age, 3) if heartbeat_age is not None else None
            ),
            "jobs_total": len(entries),
            "jobs_enabled": sum(1 for entry in entries if entry.enabled),
            "jobs_running": sum(self._running.values()),
            "last_run_at": (
                (last_run.finished_at or last_run.started_at)
                if last_run is not None else ""
            ),
            "last_run_status": last_run.status if last_run is not None else "",
            "error_class": self._last_error_class,
            "error_summary": self._last_error_summary,
        }

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
        # Compatibilità JIT: le entry user finite create prima che il client
        # propagasse `times` in remaining_runs non devono rifirare per sempre.
        if entry.recurring and not entry.remaining_runs:
            try:
                declared_times = int((entry.payload or {}).get("times") or 0)
            except (TypeError, ValueError):
                declared_times = 0
            if declared_times > 0:
                inferred = max(1, declared_times - int(entry.total_runs or 0))
                if self.storage.initialize_remaining_runs(entry.id, inferred):
                    entry.remaining_runs = inferred
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
                if isinstance(result, CallbackOutcome):
                    allowed = {"success", "partial", "error", "timeout"}
                    status = result.status if result.status in allowed else "error"
                    output = str(result.output or "")[:4096]
                    error = str(result.error or "")[:1000] or None
                    if result.status not in allowed:
                        error = f"invalid callback status: {result.status!r}"
                elif result is not None:
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
            circuit_broken = False
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
                # Circuit-breaker: N fallimenti CONSECUTIVI → auto-disable.
                # entry.consecutive_failures e' il valore PRE-run; +1 = quello
                # che record_outcome scrivera' (coerente, stesso incremento).
                if status in {"error", "timeout"} and _CIRCUIT_BREAK_AFTER > 0:
                    if (entry.consecutive_failures or 0) + 1 >= _CIRCUIT_BREAK_AFTER:
                        disable = True
                        circuit_broken = True
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
            if circuit_broken:
                log.warning(
                    "entry %s: circuit-break dopo %d fallimenti consecutivi "
                    "(last_error=%s) → disabilitato",
                    entry.name, _CIRCUIT_BREAK_AFTER, error,
                )
                hook = self.on_circuit_break
                if hook is not None:
                    def _safe_hook(_e=entry, _err=error, _h=hook):
                        try:
                            _h(_e, _err)
                        except Exception:
                            # Notifica mai-bloccante: auto-disable gia' persistito.
                            log.exception(
                                "entry %s: on_circuit_break hook fallito", _e.name)
                    # Il hook fa I/O bloccante (send Telegram, timeout 30s): NON
                    # eseguirlo nell'event loop co-hostato con l'HTTP server →
                    # thread pool fire-and-forget (come i callback sync).
                    if self._pool is not None:
                        self._pool.submit(_safe_hook)
                    else:
                        _safe_hook()
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

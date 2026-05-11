"""Metnos scheduler v2 — co-hosted asyncio scheduler.

See `runtime/scheduler_v2/daemon.py::SchedulerDaemon` for the entry point.
This package is import-safe: no side effects at import time, no DB created
unless `SchedulerDaemon(db_path)` is instantiated.
"""
from .callbacks import CallbackInfo, CallbackRegistry
from .daemon import SchedulerDaemon
from .models import Run, ScheduleEntry
from .schedule_parser import next_fire_at, parse_trigger
from .storage import DEFAULT_DB_PATH, SchedulerStorage

__all__ = [
    "SchedulerDaemon",
    "CallbackRegistry",
    "CallbackInfo",
    "ScheduleEntry",
    "Run",
    "SchedulerStorage",
    "DEFAULT_DB_PATH",
    "next_fire_at",
    "parse_trigger",
]

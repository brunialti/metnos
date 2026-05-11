"""Callback registry for scheduler v2.

Explicit registration: a stored entry references a callback by string key,
the daemon resolves the key to a Python callable at fire-time. Functions can
be sync (run in thread pool) or async (awaited directly); the daemon detects
via inspect.iscoroutinefunction.

Duplicate-key default = error. Use replace=True to overwrite.
"""
from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class CallbackInfo:
    key: str
    fn: Callable[..., Any]
    description: str
    is_async: bool


class CallbackRegistry:
    def __init__(self) -> None:
        self._items: dict[str, CallbackInfo] = {}
        self._lock = threading.RLock()

    def register(
        self,
        key: str,
        fn: Callable[..., Any],
        description: str = "",
        *,
        replace: bool = False,
    ) -> CallbackInfo:
        if not key or not isinstance(key, str):
            raise ValueError("callback key must be a non-empty string")
        if not callable(fn):
            raise TypeError(f"callback {key!r}: fn must be callable")
        info = CallbackInfo(
            key=key,
            fn=fn,
            description=description,
            is_async=inspect.iscoroutinefunction(fn),
        )
        with self._lock:
            if not replace and key in self._items:
                raise KeyError(f"callback already registered: {key!r}")
            self._items[key] = info
        return info

    def unregister(self, key: str) -> bool:
        with self._lock:
            return self._items.pop(key, None) is not None

    def get(self, key: str) -> CallbackInfo | None:
        with self._lock:
            return self._items.get(key)

    def list(self) -> list[CallbackInfo]:
        with self._lock:
            return list(self._items.values())

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._items

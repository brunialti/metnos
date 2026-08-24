"""host_throttle — budget per-host condiviso nel processo runtime (ADR 0103).

Estratto da find_urls / read_urls_html / read_urls_pdf (regola del 3, §7.2)
dopo Round 2 di parallelizzazione executor (ADR 0103). Combina:

  - Budget FIFO con N slot per fetch concorrenti verso lo stesso host,
    condiviso fra executor e invocazioni nello stesso runtime.
  - Lock + last_ts opzionale per garantire `rate_limit_ms` minimo fra
    request consecutive sullo stesso host (0 = disabilitato).

Convenzione import dagli executor (runtime/ già su sys.path via PYTHONPATH
o tramite il bootstrap universale env METNOS_RUNTIME):

    from host_throttle import HostThrottle

API:
    HostThrottle(per_host_limit, rate_limit_ms=0)
    .acquire(host) -> None      # bloccante; pareggia con release(host)
    .release(host) -> None
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _HostState:
    active_limits: list[int] = field(default_factory=list)
    waiters: deque[tuple[object, int]] = field(default_factory=deque)
    last_start: float = 0.0


_COORDINATOR = threading.Condition()
_HOSTS: dict[str, _HostState] = {}


def _normalized_host(value: str) -> str:
    return str(value or "").strip().casefold()


def _admission_ceiling(state: _HostState, requested: int) -> int:
    """Most restrictive limit among work already active and the candidate."""

    return min([requested, *state.active_limits])


class HostThrottle:
    """Runtime-wide per-host throttle with FIFO admission.

    `per_host_limit`: numero massimo di fetch concurrent verso lo stesso host.
    `rate_limit_ms`: ms minimi fra request consecutive sullo stesso host.
                     0 (default) = solo limite di concorrenza, niente delay.
    """

    def __init__(self, per_host_limit: int, rate_limit_ms: int = 0):
        self._per_host_limit = max(1, int(per_host_limit))
        self._rate_limit_ms = max(0, int(rate_limit_ms))

    def acquire(self, host: str) -> None:
        key = _normalized_host(host)
        token = object()
        with _COORDINATOR:
            state = _HOSTS.setdefault(key, _HostState())
            state.waiters.append((token, self._per_host_limit))
            while True:
                is_head = bool(state.waiters and state.waiters[0][0] is token)
                room = (
                    len(state.active_limits)
                    < _admission_ceiling(state, self._per_host_limit)
                )
                if is_head and room:
                    state.waiters.popleft()
                    state.active_limits.append(self._per_host_limit)
                    now = time.monotonic()
                    not_before = max(
                        now,
                        state.last_start + self._rate_limit_ms / 1000.0,
                    )
                    state.last_start = not_before
                    _COORDINATOR.notify_all()
                    break
                _COORDINATOR.wait()
        delay = not_before - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def release(self, host: str) -> None:
        key = _normalized_host(host)
        with _COORDINATOR:
            state = _HOSTS.get(key)
            if state is None:
                return
            try:
                state.active_limits.remove(self._per_host_limit)
            except ValueError:
                return
            if not state.active_limits and not state.waiters:
                _HOSTS.pop(key, None)
            _COORDINATOR.notify_all()


def _reset_for_tests() -> None:
    """Clear coordinator state; callers must ensure no acquisition is live."""

    with _COORDINATOR:
        _HOSTS.clear()
        _COORDINATOR.notify_all()

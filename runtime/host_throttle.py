"""host_throttle — throttle thread-safe per-host condiviso (ADR 0103).

Estratto da find_urls / read_urls_html / read_urls_pdf (regola del 3, §7.2)
dopo Round 2 di parallelizzazione executor (ADR 0103). Combina:

  - Semaphore con N slot per concurrent fetch verso lo stesso host.
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


class HostThrottle:
    """Throttle thread-safe per-host (semaforo + rate-limit opzionale).

    `per_host_limit`: numero massimo di fetch concurrent verso lo stesso host.
    `rate_limit_ms`: ms minimi fra request consecutive sullo stesso host.
                     0 (default) = solo limite di concorrenza, niente delay.
    """

    def __init__(self, per_host_limit: int, rate_limit_ms: int = 0):
        self._per_host_limit = per_host_limit
        self._rate_limit_ms = rate_limit_ms
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._last_ts: dict[str, float] = {}
        self._lock = threading.Lock()

    def _semaphore_for(self, host: str) -> threading.Semaphore:
        with self._lock:
            sem = self._semaphores.get(host)
            if sem is None:
                sem = threading.Semaphore(self._per_host_limit)
                self._semaphores[host] = sem
            return sem

    def acquire(self, host: str) -> None:
        sem = self._semaphore_for(host)
        sem.acquire()
        if self._rate_limit_ms <= 0:
            return
        with self._lock:
            last = self._last_ts.get(host, 0.0)
            elapsed_ms = (time.time() - last) * 1000.0
            wait_ms = max(0.0, self._rate_limit_ms - elapsed_ms)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
        with self._lock:
            self._last_ts[host] = time.time()

    def release(self, host: str) -> None:
        sem = self._semaphores.get(host)
        if sem is not None:
            sem.release()

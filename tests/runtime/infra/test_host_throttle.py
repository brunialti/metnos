from __future__ import annotations

import threading
import time


def setup_function():
    from host_throttle import _reset_for_tests

    _reset_for_tests()


def teardown_function():
    from host_throttle import _reset_for_tests

    _reset_for_tests()


def test_limit_is_shared_between_independent_throttle_instances():
    from host_throttle import HostThrottle

    throttles = (HostThrottle(2), HostThrottle(2))
    gate = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def worker(throttle):
        nonlocal active, maximum
        gate.wait()
        throttle.acquire("Example.COM")
        try:
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.025)
            with lock:
                active -= 1
        finally:
            throttle.release("example.com")

    threads = [
        threading.Thread(target=worker, args=(throttles[index % 2],))
        for index in range(8)
    ]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum == 2


def test_distinct_hosts_have_independent_budgets():
    from host_throttle import HostThrottle

    throttle = HostThrottle(1)
    entered = []
    both_entered = threading.Event()

    def worker(host):
        throttle.acquire(host)
        try:
            entered.append(host)
            if len(entered) == 2:
                both_entered.set()
            both_entered.wait(timeout=1)
        finally:
            throttle.release(host)

    threads = [threading.Thread(target=worker, args=(host,))
               for host in ("a.example", "b.example")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert both_entered.is_set()
    assert all(not thread.is_alive() for thread in threads)


def test_more_restrictive_waiter_is_not_starved_by_later_work():
    from host_throttle import HostThrottle

    loose = HostThrottle(2)
    strict = HostThrottle(1)
    loose.acquire("same.example")
    loose.acquire("same.example")
    order = []
    strict_done = threading.Event()

    def strict_worker():
        strict.acquire("same.example")
        try:
            order.append("strict")
        finally:
            strict.release("same.example")
            strict_done.set()

    def late_loose_worker():
        loose.acquire("same.example")
        try:
            order.append("loose")
        finally:
            loose.release("same.example")

    first = threading.Thread(target=strict_worker)
    second = threading.Thread(target=late_loose_worker)
    first.start()
    time.sleep(0.02)
    second.start()
    loose.release("same.example")
    loose.release("same.example")
    first.join(timeout=2)
    second.join(timeout=2)

    assert strict_done.is_set()
    assert order == ["strict", "loose"]

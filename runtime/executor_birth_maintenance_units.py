"""Single portable inventory of services covered by the F4 maintenance proof."""
from __future__ import annotations


CONTRACT_CUTOVER_UNITS = tuple(sorted({
    "metnos.target",
    "metnos-http.service",
    "metnos-durable-worker.service",
    "metnos-telegram-daemon.service",
    "metnos-i18n-translator.service",
    "metnos-i18n-translator.timer",
    "metnos-stack-ready.service",
    "metnos-stack-quarantine.service",
    "metnos-stack-watchdog.service",
    "metnos-stack-watchdog.timer",
}))

MAINTENANCE_TARGETS_V1 = tuple(sorted({
    *(('user', unit) for unit in CONTRACT_CUTOVER_UNITS),
    ("system", "metnos-http.service"),
}))


__all__ = ["CONTRACT_CUTOVER_UNITS", "MAINTENANCE_TARGETS_V1"]

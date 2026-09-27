"""Small independent clock for closed, signed periodic service commands.

Preferences contain only bounded intervals. Commands and authority remain in
code; each invocation releases its memory when it exits. No HTTP dependency.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time


@dataclass(frozen=True)
class PeriodicService:
    module: str
    arguments: tuple[str, ...]
    label_key: str
    default_minutes: int = 30
    minimum_minutes: int = 5
    maximum_minutes: int = 1440
    timeout_seconds: int = 180


SERVICES = {
    "stack-watchdog": PeriodicService(
        "stack_reconcile", ("watchdog", "--require-sidecar", "auto"),
        "UI_PERIODIC_STACK_WATCHDOG",
    ),
}
POLL_SECONDS = 30
_LOG = logging.getLogger(__name__)


def configuration_path(name: str) -> Path:
    SERVICES[name]  # reject arbitrary file names before resolving a path
    root = os.environ.get("METNOS_USER_CONFIG")
    if not root:
        import config
        root = config.PATH_USER_CONFIG
    return Path(root) / "periodic-services" / f"{name}.json"


def _validate_interval(name: str, value: object) -> int:
    service = SERVICES[name]
    if (type(value) is not int or not
            service.minimum_minutes <= value <= service.maximum_minutes):
        raise ValueError("periodic service interval out of range")
    return value


def configuration(name: str) -> dict:
    service = SERVICES[name]
    value, valid = service.default_minutes, True
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(os.open(configuration_path(name), flags), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("periodic configuration is not a regular file")
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise ValueError("periodic configuration too large")
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"interval_minutes"}:
            raise ValueError("periodic configuration contains unknown fields")
        value = _validate_interval(name, document["interval_minutes"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError):
        valid = False
    return {"name": name, "label_key": service.label_key,
            "interval_minutes": value, "valid": valid,
            "minimum_minutes": service.minimum_minutes,
            "maximum_minutes": service.maximum_minutes}


def configure(name: str, minutes: int) -> None:
    value = _validate_interval(name, minutes)
    import config
    config.write_private_text(
        configuration_path(name), json.dumps({"interval_minutes": value}) + "\n",
    )


def _execute(service: PeriodicService) -> None:
    try:
        with subprocess.Popen(
            [sys.executable, "-E", "-s", "-B", "-m", service.module, *service.arguments],
            start_new_session=True,
        ) as process:
            try:
                code = process.wait(timeout=service.timeout_seconds)
            except subprocess.TimeoutExpired:
                # Preserve the former oneshot's bound on child processes too.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
        if code:
            _LOG.warning("periodic_service_failed code=%s", code)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOG.warning("periodic_service_failed type=%s", type(exc).__name__)


def timer_active(name: str, scope: str) -> bool:
    SERVICES[name]
    if scope not in {"user", "system"}:
        raise ValueError("invalid systemd scope")
    result = subprocess.run(
        ["systemctl", f"--{scope}", "is-active", "--quiet", f"metnos-{name}.timer"],
        timeout=5, check=False,
    )
    if result.returncode not in {0, 3}:
        raise RuntimeError("periodic service timer unavailable")
    return result.returncode == 0


def run(name: str, *, clock=time.monotonic, wait=time.sleep, execute=_execute,
        active=lambda: True) -> None:
    service = SERVICES[name]
    last_start = None
    while active():
        interval = configuration(name)["interval_minutes"] * 60
        now = clock()
        if last_start is None or now - last_start >= interval:
            last_start = now
            execute(service)
        # Poll only the small preferences file, not the stack or its catalog.
        # Read changes between calls; calls are serial and never overlap.
        wait(POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=tuple(SERVICES))
    parser.add_argument("--scope", choices=("user", "system"), default="system")
    args = parser.parse_args()
    run(args.service, active=lambda: timer_active(args.service, args.scope))


if __name__ == "__main__":
    main()

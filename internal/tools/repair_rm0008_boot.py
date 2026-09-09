#!/usr/bin/python3.12
"""Repair only the missing boot preparation of the verified production release.

Root, zero arguments. No release/authority changes and no machine reboot.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import urllib.request


ROOT = Path("/var/lib/metnos/executor-birth")
RUNTIME = Path("/run/metnos-executor-birth-v1")
RULE = Path("/etc/tmpfiles.d/metnos-executor-birth-v1.conf")
RULE_BYTES = (
    b"d! /run/metnos-executor-birth-v1 0700 0 0 -\n"
    b"f! /run/metnos-executor-birth-v1/startup-v1.lock 0600 0 0 -\n"
)
PREFLIGHT = Path("/usr/libexec/metnos/executor-birth-v1/preflight.py")
PINNED = {
    PREFLIGHT: "0fd620bf0d519024368dfec61e5a39f39f22fa7c7e8a8642457be978c0cd15ca",
    ROOT / "releases-v1/00000000000000000001/deployment/executor-birth-deployment-v1.json":
        "241c8d93912f64cc6356be056fa72260737195af74f8e6392a86436d1abdd503",
}
UNITS = (
    "metnos-http.service", "metnos-telegram-daemon.service",
    "metnos-durable-worker.service", "metnos-playwright.service",
    "metnos-side-display.service", "metnos-stack-ready.service",
    "metnos-stack-watchdog.service", "metnos-stack-watchdog.timer",
    "metnos-i18n-translator.service", "metnos-i18n-translator.timer",
)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise RuntimeError(detail)


def emit(message: str) -> None:
    print(message, flush=True)
    subprocess.run(["/usr/bin/logger", "--tag", "metnos-boot-repair", "--", message],
                   check=False, timeout=5)


def metadata(path: Path, mode: int, *, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(kind(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
            and stat.S_IMODE(info.st_mode) == mode, f"unsafe metadata: {path}")
    require(directory or info.st_nlink == 1, f"linked file: {path}")


def command(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True,
                            timeout=timeout, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                                                  "LANG": "C", "LC_ALL": "C"})
    return result.stdout.strip()


def pins() -> None:
    for path, digest in PINNED.items():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
                and info.st_nlink == 1 and not info.st_mode & 0o022,
                f"unsafe pinned file: {path}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                f"release changed: {path}")


def require_stopped() -> None:
    active = command("/usr/bin/systemctl", "list-units", "--all", "--no-legend",
                     "--plain", "--no-pager", "--state=active,activating,reloading,deactivating",
                     *UNITS, "metnos-rm0008-*.service")
    require(not active, "Metnos work is active; nothing was stopped")


def validate_runtime() -> None:
    if not os.path.lexists(RUNTIME):
        return
    metadata(RUNTIME, 0o700, directory=True)
    require({p.name for p in RUNTIME.iterdir()} <= {"startup-v1.lock"},
            "unexpected runtime content")
    gate = RUNTIME / "startup-v1.lock"
    if os.path.lexists(gate):
        metadata(gate, 0o600)
        require(gate.stat().st_size == 0, "startup lock is not empty")


def install_rule() -> None:
    metadata(RULE.parent, 0o755, directory=True)
    if os.path.lexists(RULE):
        metadata(RULE, 0o644)
        require(RULE.read_bytes() == RULE_BYTES, "existing boot rule differs")
        return
    fd, temporary = tempfile.mkstemp(prefix=".metnos-boot-", dir=RULE.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(RULE_BYTES)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, RULE, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    fd = os.open(RULE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    metadata(RULE, 0o644)
    require(RULE.read_bytes() == RULE_BYTES, "boot rule verification failed")


def repair() -> None:
    pins()
    require_stopped()
    metadata(Path("/run"), 0o755, directory=True)
    metadata(ROOT, 0o755, directory=True)
    lock = ROOT / "ownership-deployment-v1.lock"
    metadata(lock, 0o600)
    fd = os.open(lock, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_stopped()
        validate_runtime()
        install_rule()
        command("/usr/bin/systemd-tmpfiles", "--create", "--boot",
                "--prefix=/run/metnos-executor-birth-v1", str(RULE))
        validate_runtime()
        metadata(RUNTIME / "startup-v1.lock", 0o600)
    finally:
        os.close(fd)
    emit("BOOT_PREPARATION_INSTALLED")
    command("/usr/bin/python3.12", "-I", "-S", str(PREFLIGHT),
            "check", "--entry-id", "service-http", timeout=300)
    emit("FULL_PREFLIGHT_OK")
    with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
        require(response.status == 200, "model is not healthy")
    command("/usr/bin/systemctl", "start", "metnos.target", timeout=240)
    for unit in (*UNITS[:6], "metnos.target", "metnos-i18n-translator.timer",
                 "metnos-stack-watchdog.timer"):
        require(command("/usr/bin/systemctl", "is-active", unit) == "active",
                f"service not active: {unit}")
    with urllib.request.urlopen("http://127.0.0.1:8770/agent/health", timeout=5) as response:
        require(response.status == 200, "HTTP is not healthy")
    emit("METNOS_STACK_READY reboot certification still required")


def main() -> int:
    require(os.geteuid() == 0 and len(sys.argv) == 1, "root and zero arguments required")
    try:
        repair()
    except Exception as error:
        emit(f"BOOT_REPAIR_REFUSED {type(error).__name__}: {error}")
        if isinstance(error, subprocess.CalledProcessError):
            emit(error.stderr[-4000:])
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

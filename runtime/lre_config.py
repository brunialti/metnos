"""Persistent, fail-closed deployment gate for LRE.

The feature switch has one durable source shared by the worker, HTTP and
channels.  A process environment value remains an explicit deployment
override, primarily for isolated tests and emergency operation; otherwise the
private ``lre.env`` file is authoritative.  Reading this module performs no
I/O and creates no files.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time
from typing import Mapping


FEATURE_ENV = "METNOS_DURABLE_WORKLOADS_ENABLED"
FEATURE_CONFIG_FILENAME = "lre.env"
_MAX_CONFIG_BYTES = 4096
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class LREFeatureConfiguration:
    """Closed observation of the LRE deployment gate."""

    enabled: bool
    valid: bool
    source: str


def default_feature_config_path() -> Path:
    """Return the configured private gate path without creating it."""

    import config

    return Path(config.PATH_USER_CONFIG) / FEATURE_CONFIG_FILENAME


def _parse_switch(value: object) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _read_bounded_regular_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("LRE feature configuration is not a regular file")
        if metadata.st_size > _MAX_CONFIG_BYTES:
            raise ValueError("LRE feature configuration exceeds its boundary")
        data = bytearray()
        while len(data) <= _MAX_CONFIG_BYTES:
            chunk = os.read(descriptor, _MAX_CONFIG_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_CONFIG_BYTES:
            raise ValueError("LRE feature configuration exceeds its boundary")
        return bytes(data).decode("utf-8")
    finally:
        os.close(descriptor)


def _parse_file(text: str) -> bool:
    selected: bool | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("LRE feature configuration contains an invalid line")
        key, raw_value = (part.strip() for part in line.split("=", 1))
        if key != FEATURE_ENV or selected is not None:
            raise ValueError("LRE feature configuration is not canonical")
        selected = _parse_switch(raw_value)
        if selected is None:
            raise ValueError("LRE feature configuration contains an invalid value")
    if selected is None:
        raise ValueError("LRE feature configuration has no feature switch")
    return selected


def read_feature_configuration(
    *,
    path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LREFeatureConfiguration:
    """Read the deployment gate; malformed or unsafe input disables LRE."""

    environment = os.environ if environ is None else environ
    if FEATURE_ENV in environment:
        enabled = _parse_switch(environment.get(FEATURE_ENV))
        return LREFeatureConfiguration(
            enabled=bool(enabled),
            valid=enabled is not None,
            source="environment",
        )

    selected_path = Path(path) if path is not None else default_feature_config_path()
    try:
        enabled = _parse_file(_read_bounded_regular_file(selected_path))
    except FileNotFoundError:
        return LREFeatureConfiguration(False, True, "default")
    except (OSError, UnicodeDecodeError, ValueError):
        return LREFeatureConfiguration(False, False, "file")
    return LREFeatureConfiguration(enabled, True, "file")


def feature_enabled() -> bool:
    """Return the effective fail-closed LRE feature state."""

    return read_feature_configuration().enabled


@contextmanager
def feature_configuration_lock(
    *,
    path: str | Path | None = None,
    timeout_s: float = 5.0,
):
    """Serialize a gate change with the final admission transaction.

    Discovery and hashing remain outside this short lock.  A submitter takes
    it only to re-read the switch and make the admitted revision reachable;
    disabling LRE therefore cannot cross that boundary unnoticed.
    """

    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not 0 < float(timeout_s) <= 30
    ):
        raise ValueError("timeout_s must be in (0, 30]")
    import config
    import fcntl

    selected = Path(path) if path is not None else default_feature_config_path()
    lock_path = selected.with_name(selected.name + ".lock")
    config.ensure_private_dir(lock_path.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    acquired = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("LRE feature lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + float(timeout_s)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("LRE feature lock is busy") from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _canonical_text(enabled: bool) -> str:
    value = "1" if enabled else "0"
    return (
        "# Managed by Metnos. LRE remains disabled unless explicitly enabled.\n"
        f"{FEATURE_ENV}={value}\n"
    )


def write_feature_configuration(
    enabled: bool,
    *,
    path: str | Path | None = None,
) -> Path:
    """Atomically write the canonical private feature configuration."""

    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    import config

    selected_path = Path(path) if path is not None else default_feature_config_path()
    config.write_private_text(selected_path, _canonical_text(enabled))
    return selected_path


def ensure_default_feature_configuration(
    *,
    path: str | Path | None = None,
) -> bool:
    """Create a disabled fresh-install gate while preserving upgrade intent.

    The return value is true only when a new file was created.  An existing
    file, including an invalid one that needs operator repair, is never
    overwritten silently during an update.
    """

    selected_path = Path(path) if path is not None else default_feature_config_path()
    import config

    return config.create_private_text(selected_path, _canonical_text(False))


__all__ = [
    "FEATURE_CONFIG_FILENAME",
    "FEATURE_ENV",
    "LREFeatureConfiguration",
    "default_feature_config_path",
    "ensure_default_feature_configuration",
    "feature_configuration_lock",
    "feature_enabled",
    "read_feature_configuration",
    "write_feature_configuration",
]

"""Causal target for the signed, disposable G6-C activation cell."""
from __future__ import annotations

import json
import os
import re
import sys
import time


_MARKER_RE_V1 = re.compile(
    r"/run/metnos-g6c-[0-9a-f]{16}/marker\.json"
)
_STATUS_FIELDS_V1 = frozenset({
    "Uid", "Gid", "Groups", "NoNewPrivs",
    "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
})


def _status_fields_v1() -> dict[str, str]:
    result: dict[str, str] = {}
    with open("/proc/self/status", "r", encoding="ascii") as stream:
        for line in stream:
            name, separator, value = line.partition(":")
            if separator and name in _STATUS_FIELDS_V1:
                result[name] = value.strip()
    return result


def _open_descriptors_v1() -> list[int]:
    result: list[int] = []
    for raw in os.listdir("/proc/self/fd"):
        descriptor = int(raw)
        try:
            os.fstat(descriptor)
        except OSError:
            continue
        result.append(descriptor)
    return sorted(result)


def main() -> int:
    if (
        len(sys.argv) != 2
        or _MARKER_RE_V1.fullmatch(sys.argv[1]) is None
    ):
        return 2
    marker = sys.argv[1]
    payload = {
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "environment": dict(sorted(os.environ.items())),
        "fds": _open_descriptors_v1(),
        "gid": os.getgid(),
        "groups": os.getgroups(),
        "mount_namespace": os.readlink("/proc/self/ns/mnt"),
        "pid": os.getpid(),
        "status": _status_fields_v1(),
        "uid": os.getuid(),
    }
    temporary = marker + ".tmp"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666,
    )
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short marker write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, marker)
    directory = os.open(
        os.path.dirname(marker), os.O_RDONLY | os.O_DIRECTORY,
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    time.sleep(2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

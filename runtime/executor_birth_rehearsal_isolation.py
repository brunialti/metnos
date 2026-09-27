"""Native observation for the approved, temporary F5 rehearsal.

These facts are not a permit. The administrative owner binds its own native
observation to the installation and frozen profile; runtime rereads it.
Nothing in this module signs, installs, starts or certifies an installation.
Linux observations come from the kernel, not a guest's assertion or name.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from executor_birth_canonical import encode_canonical_ascii_v1


_NAMESPACES = ("user", "mnt", "pid", "net", "ipc", "uts", "cgroup")
_VIRTUAL_FILESYSTEMS = frozenset({"proc", "sysfs", "tmpfs", "devpts", "cgroup2", "mqueue"})
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
_DOMAIN = b"metnos.executor-birth.rehearsal-isolation/v1\0"


class RehearsalIsolationError(RuntimeError):
    def __init__(self, detail: str):
        self.code, self.detail = "f5_rehearsal_isolation_unavailable", detail
        super().__init__(f"{self.code}: {detail}")


@dataclass(frozen=True)
class _Mount:
    device: str
    root: str
    target: str
    options: tuple[str, ...]
    filesystem: str


@dataclass(frozen=True)
class RehearsalIsolationV1:
    """Observed native identity; never accepted as lifecycle authorization."""
    boot_id: str
    namespaces: tuple[tuple[str, int, int], ...]
    uid_map: tuple[int, int, int]
    gid_map: tuple[int, int, int]
    root_identity: tuple[int, int, int, int]
    mounts_hash: str

    @property
    def observation_id(self) -> str:
        return "sha256:" + hashlib.sha256(
            _DOMAIN + encode_canonical_ascii_v1(asdict(self)),
        ).hexdigest()


def _read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(maximum + 1)
    if not value or len(value) > maximum:
        raise RehearsalIsolationError("kernel observation size")
    return value


def _mapping(raw: bytes) -> tuple[int, int, int]:
    try:
        rows = raw.decode("ascii").splitlines()
        if len(rows) != 1:
            raise ValueError("one mapping required")
        inside, outside, length = map(int, rows[0].split())
        # No host system identity is mapped into the guest. A single range
        # also excludes sparse maps that quietly add another host account.
        if inside != 0 or outside < 65536 or length < 65536 or outside + length > 2**32 - 1:
            raise ValueError("unprivileged identity range")
        return inside, outside, length
    except (UnicodeError, ValueError) as exc:
        raise RehearsalIsolationError("user or group mapping") from exc


def _mounts(raw: bytes) -> tuple[_Mount, ...]:
    try:
        entries = []
        for line in raw.decode("utf-8", errors="strict").splitlines():
            fields = line.split()
            separator = fields.index("-")
            if separator < 6 or len(fields) != separator + 4:
                raise ValueError("mount fields")
            paths = tuple(_OCTAL_ESCAPE.sub(lambda match: chr(int(match[1], 8)), value)
                          for value in (fields[3], fields[4]))
            if any(not path.startswith("/") or ".." in PurePosixPath(path).parts for path in paths):
                raise ValueError("mount path")
            entries.append(_Mount(fields[2], *paths, tuple(fields[5].split(",")), fields[separator + 1]))
        if not entries:
            raise ValueError("empty mounts")
        return tuple(entries)
    except (UnicodeError, ValueError, IndexError) as exc:
        raise RehearsalIsolationError("mount observation") from exc


def _validate_mounts(guest: tuple[_Mount, ...], host: tuple[_Mount, ...],
                     *, inaccessible: frozenset[str] = frozenset(),
                     cgroup: str | None = None) -> None:
    roots = [mount for mount in guest if mount.target == "/"]
    if len(roots) != 1:
        raise RehearsalIsolationError("guest root")
    root = roots[0]
    if any(m.target == "/" and (m.device, m.root) == (root.device, root.root) for m in host):
        raise RehearsalIsolationError("host root shared")
    for mount in guest:
        if any(PurePosixPath(mount.target).is_relative_to(PurePosixPath(path))
               for path in inaccessible):
            # mountinfo also lists covered mounts. The observer independently
            # checks that their visible covering node is inaccessible.
            continue
        if mount.filesystem in _VIRTUAL_FILESYSTEMS:
            shared = any(m.device == mount.device for m in host)
            if shared and mount.filesystem != "cgroup2":
                raise RehearsalIsolationError("host virtual filesystem shared")
            if (mount.filesystem == "cgroup2"
                    and (not cgroup or cgroup == "/"
                         or not PurePosixPath(mount.root).is_relative_to(PurePosixPath(cgroup)))):
                raise RehearsalIsolationError("host control groups shared")
            continue
        if (mount.device == root.device and mount.filesystem == root.filesystem
                and PurePosixPath(mount.root).is_relative_to(PurePosixPath(root.root))):
            continue
        # Standard container supervisors expose this public host metadata.
        # No other host disk mount, including a read-only secret, is accepted.
        if (mount.target == "/run/host/os-release"
                and mount.root in {"/usr/lib/os-release", "/etc/os-release"}
                and "ro" in mount.options and "rw" not in mount.options):
            continue
        raise RehearsalIsolationError("host disk mount shared")


def _validate_proc(status: bytes, mounts: tuple[_Mount, ...], device: str, pid: int,
                   additional: tuple[tuple[str, bytes], ...] = ()) -> None:
    # A private PID namespace alone does not prove that /proc was remounted:
    # a host procfs bind would still expose host processes. NSpid is expressed
    # relative to procfs's owning namespace, so exactly the local PID must show.
    verified = {device}
    for observed_device, observed_status in ((device, status), *additional):
        identities = [line.split()[1:] for line in observed_status.splitlines()
                      if line.startswith(b"NSpid:")]
        if identities != [[str(pid).encode("ascii")]]:
            raise RehearsalIsolationError("host process filesystem shared")
        verified.add(observed_device)
    if any(m.filesystem == "proc" and m.device not in verified for m in mounts):
        raise RehearsalIsolationError("additional process filesystem shared")


def _additional_proc_status(mount: _Mount) -> bytes:
    """Read a kernel status from this exact procfs, never a covering file."""
    if mount.root != "/":
        raise RehearsalIsolationError("additional process filesystem root")
    expected = tuple(map(int, mount.device.split(":")))
    root = os.open(mount.target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(root)
        if (os.major(info.st_dev), os.minor(info.st_dev)) != expected:
            raise RehearsalIsolationError("process filesystem covered")
        descriptor = os.open("self/status", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=root)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode)
                    or (os.major(info.st_dev), os.minor(info.st_dev)) != expected):
                raise RehearsalIsolationError("process status covered")
            raw = os.read(descriptor, 16385)
            if not raw or len(raw) > 16384:
                raise RehearsalIsolationError("process status size")
            return raw
        finally:
            os.close(descriptor)
    finally:
        os.close(root)


def _observe_local_rehearsal_v1() -> RehearsalIsolationV1:
    """Observe the caller's containment using namespace ownership in the kernel.

    All six namespaces must belong to this unprivileged user namespace. This
    is independently verifiable inside the guest and excludes the host, even
    if someone copies a permit, sets an environment variable or renames it.
    This adapter supports private directory roots on Linux; unsupported native
    layouts refuse rehearsal rather than being declared isolated by an option.
    """
    if not sys.platform.startswith("linux"):
        raise RehearsalIsolationError("native observer unsupported")
    import fcntl

    proc = Path("/proc/self")
    uid_map = _mapping(_read(proc / "uid_map", 4096))
    gid_map = _mapping(_read(proc / "gid_map", 4096))
    namespaces = []
    user = (proc / "ns/user").stat()
    for name in _NAMESPACES:
        descriptor = os.open(proc / "ns" / name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            current = os.fstat(descriptor)
            if name != "user":
                owner = fcntl.ioctl(descriptor, 0xb701)  # Linux NS_GET_USERNS.
                try:
                    owned_by = os.fstat(owner)
                    if (owned_by.st_dev, owned_by.st_ino) != (user.st_dev, user.st_ino):
                        raise RehearsalIsolationError(f"host {name} namespace owned")
                finally:
                    os.close(owner)
            namespaces.append((name, current.st_dev, current.st_ino))
        finally:
            os.close(descriptor)
    root = Path("/").stat()
    if (not stat.S_ISDIR(root.st_mode) or root.st_mode & 0o022
            or (root.st_uid, root.st_gid) != (0, 0)):
        raise RehearsalIsolationError("root custody")
    raw_mounts = _read(proc / "mountinfo", 512 * 1024)
    mounts = _mounts(raw_mounts)
    proc_device = proc.stat().st_dev
    device = f"{os.major(proc_device)}:{os.minor(proc_device)}"
    additional = {}
    for mount in mounts:
        if mount.filesystem == "proc" and mount.device != device and mount.device not in additional:
            additional[mount.device] = _additional_proc_status(mount)
    _validate_proc(_read(proc / "status", 16384), mounts,
                   device, os.getpid(), tuple(additional.items()))
    if any(m.target == "/" and m.root == "/" for m in mounts):
        raise RehearsalIsolationError("private directory root required")
    overflow_uid = int(_read(Path("/proc/sys/kernel/overflowuid"), 32))
    overflow_gid = int(_read(Path("/proc/sys/kernel/overflowgid"), 32))
    private_memory = set()
    inaccessible = set()
    for mount in mounts:
        if mount.filesystem != "tmpfs":
            continue
        visible = Path(mount.target).stat()
        if (mount.root == "/" and (visible.st_uid, visible.st_gid) == (0, 0)
                and (os.major(visible.st_dev), os.minor(visible.st_dev))
                == tuple(map(int, mount.device.split(":")))):
            private_memory.add(mount.device)
        if ("ro" in mount.options and "rw" not in mount.options
                and stat.S_ISDIR(visible.st_mode) and stat.S_IMODE(visible.st_mode) == 0
                and (visible.st_uid, visible.st_gid) == (overflow_uid, overflow_gid)):
            inaccessible.add(mount.target)
    shared = tuple(m for m in mounts if m.filesystem == "tmpfs" and m.device not in private_memory)
    cgroups = [m for m in mounts if m.filesystem == "cgroup2"]
    if (len(cgroups) != 1 or cgroups[0].root != "/"
            or cgroups[0].target != "/sys/fs/cgroup"):
        raise RehearsalIsolationError("private control group root required")
    current_cgroup = _read(proc / "cgroup", 4096).decode("utf-8").strip()
    if (not current_cgroup.startswith("0::/") or "\n" in current_cgroup
            or ".." in PurePosixPath(current_cgroup[3:]).parts):
        raise RehearsalIsolationError("process outside guest control groups")
    # The private cgroup namespace was independently checked above. Its
    # virtual '/' is expected here; the outside observer uses the host path.
    _validate_mounts(tuple(m for m in mounts if m.filesystem != "cgroup2"), shared,
                     inaccessible=frozenset(inaccessible))
    boot = _read(Path("/proc/sys/kernel/random/boot_id"), 128).decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot) is None:
        raise RehearsalIsolationError("boot identity")
    return RehearsalIsolationV1(
        boot, tuple(namespaces), uid_map, gid_map,
        (root.st_dev, root.st_ino, root.st_uid, root.st_gid),
        "sha256:" + hashlib.sha256(raw_mounts).hexdigest(),
    )


def observe_local_rehearsal_v1() -> RehearsalIsolationV1:
    """Return verified kernel facts, or one explicit unavailable outcome."""
    try:
        return _observe_local_rehearsal_v1()
    except (OSError, UnicodeError, ValueError) as exc:
        raise RehearsalIsolationError("kernel observation unavailable") from exc

"""Bounded private-file custody shared by native F6 owners, without writes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import time

from durable_workloads.artifacts import (
    ArtifactIntegrityError, ArtifactSecurityError, ArtifactStore, _DEFAULT_MAX_BLOB_BYTES,
)
from executor_birth_retention import RetentionError
from install.birth_retention_maintenance import _digest


class _PrivateFiles:
    def __init__(self, *, root: Path, require_exclusion, owner, private_directory=True,
                 file_modes=frozenset({0o600}), copied_timestamps=False):
        if (os.name != "posix" or not root.is_absolute()
                or ".." in root.parts or root == Path(root.anchor)):
            raise RetentionError("retention_owner_invalid", "native private-file directory")
        self.root, self.require_exclusion, self.owner = root, require_exclusion, owner
        self.private_directory = private_directory
        self.file_modes, self.copied_timestamps = file_modes, copied_timestamps

    @staticmethod
    def _custody(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)

    @contextmanager
    def _directory(self, *parts):
        """No mkdir/chmod on a read, and no links in any ancestor."""
        self.require_exclusion()
        if any(not part or Path(part).name != part or part in {".", ".."} for part in parts):
            raise RetentionError("retention_owner_path_invalid", "private-file relative path")
        path = self.root.joinpath(*parts)
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        custody = []
        observed = False
        try:
            for index, part in enumerate(path.parts[1:], start=1):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                private = index >= len(self.root.parts) - 1
                if (not stat.S_ISDIR(info.st_mode)
                        or (private and self.private_directory and stat.S_IMODE(info.st_mode) != 0o700)
                        or (self.owner is not None and (
                            (private and (info.st_uid, info.st_gid) != self.owner)
                            or info.st_uid not in {0, self.owner[0]}
                            or info.st_gid not in {0, self.owner[1]} or info.st_mode & 0o022))):
                    raise RetentionError("retention_owner_path_invalid", "private-file directory custody")
                custody.append(self._custody(info))
            observed = True
            yield fd, tuple(custody)
            self.require_exclusion()
            # Effects may change directory timestamps, but never its identity.
            if self._custody(os.stat(path, follow_symlinks=False)) != custody[-1]:
                raise RetentionError("retention_owner_changed", "private-file directory replaced")
        except FileNotFoundError as exc:
            if observed:
                raise RetentionError("retention_owner_changed", "private-file directory disappeared") from exc
            raise
        except OSError as exc:
            raise RetentionError("retention_owner_path_invalid", "private-file directory") from exc
        finally:
            os.close(fd)

    def _read_file(self, directory, custody, name, identity, *, expected_digest=None,
                   expected_size=None, include_payload=False, max_bytes=_DEFAULT_MAX_BLOB_BYTES):
        try:
            fd = ArtifactStore._open_regular(directory, name, allowed_modes=self.file_modes)
        except FileNotFoundError:
            return None
        except (ArtifactSecurityError, OSError) as exc:
            raise RetentionError("retention_owner_path_invalid", "private-file file") from exc
        try:
            info = os.fstat(fd)
            if info.st_nlink != 1 or (self.owner is not None and (info.st_uid, info.st_gid) != self.owner):
                raise RetentionError("retention_owner_path_invalid", "private-file file custody")
            digest, size, file_version = ArtifactStore._verify_descriptor_state(
                fd, max_bytes=max_bytes)
            if ((expected_digest is not None and digest != expected_digest)
                    or (expected_size is not None and size != expected_size)):
                raise RetentionError("retention_owner_invalid", "private-file content digest")
            payload = None
            if include_payload:
                os.lseek(fd, 0, os.SEEK_SET)
                parts, total, deadline = [], 0, time.monotonic() + 15
                while chunk := os.read(fd, 1024 * 1024):
                    parts.append(chunk)
                    total += len(chunk)
                    if total > max_bytes or time.monotonic() > deadline:
                        raise RetentionError("retention_inventory_incomplete", "private-file read budget")
                payload = b"".join(parts)
                if ("sha256:" + hashlib.sha256(payload).hexdigest() != digest
                        or ArtifactStore._file_identity(os.fstat(fd)) != file_version):
                    raise RetentionError("retention_owner_changed", "private-file content changed")
            if ArtifactStore._file_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != file_version:
                raise RetentionError("retention_owner_changed", "private-file file replaced")
            version = _digest({"directories": custody, "file": file_version,
                               "uid": info.st_uid, "gid": info.st_gid,
                               "links": info.st_nlink, "digest": digest})
            # copy2 preserves source mtime. A fresh native backup cannot inherit
            # an old source's expiration; ctime is also bound into file_version.
            timestamp = max(info.st_mtime, info.st_ctime) if self.copied_timestamps else info.st_mtime
            observed = identity, version, size, datetime.fromtimestamp(timestamp, timezone.utc)
            return (*observed, payload) if include_payload else observed
        except (ArtifactSecurityError, ArtifactIntegrityError, OSError) as exc:
            raise RetentionError("retention_owner_invalid", "private-file read") from exc
        finally:
            os.close(fd)

    @staticmethod
    def _names(directory):
        with os.scandir(directory) as entries:
            names, deadline = [], time.monotonic() + 15
            for entry in entries:
                if len(names) >= 100_000 or time.monotonic() > deadline:
                    raise RetentionError("retention_inventory_incomplete", "private-file inventory limit")
                names.append(entry.name)
        return tuple(sorted(names))


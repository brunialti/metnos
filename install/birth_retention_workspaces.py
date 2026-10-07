"""LRE package scratch: native fences, CAS leaves and later empty directories.

Paths come from admitted revisions and optional native namespace discovery.
Discovered workspaces without a revision remain open and cannot be collected.
Runtime TemporaryStorage cleanup/report integration is required: a clean report
may follow physical disappearance or accepted durable custody; job collection
still requires actual disappearance.
No recursive purge or quarantine protocol is introduced. Parent directories and
native stale-writer fences remain; empty workspace directories close on a later
inventory after their children have been collected.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import time

from durable_workloads.temporary_storage import TemporaryWorkspace
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_artifact_auxiliary import _ArtifactAuxiliaryOwner
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject, _digest
from install.birth_retention_sqlite import _bounded_rows, _iso
from install.birth_retention_workspace_paths import workspace_paths


class _WorkspaceOwner:
    name = "durable_workspaces"

    def __init__(self, *, workloads, resolve_workspaces, excluded_workspaces=(), discover_workspaces=None):
        self.workloads = workloads
        self.resolve_workspaces = resolve_workspaces
        self.discover_workspaces = discover_workspaces
        self.excluded_workspaces = frozenset(excluded_workspaces)

    def _files(self, workspace):
        # Native image-build parents/files can be 0755/0644; ownership and
        # non-writable ancestors are still enforced by the shared primitive.
        return _PrivateFiles(root=workspace.parent, require_exclusion=self.workloads.require_exclusion,
            owner=self.workloads.owner, private_directory=False, file_modes=frozenset({0o600, 0o644}))

    def _bindings(self, rows, discovered=()):
        bindings, deadline = {}, time.monotonic() + 15
        for row in rows:
            if time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "workspace binding budget")
            for workspace in workspace_paths(row, self.resolve_workspaces):
                if workspace in self.excluded_workspaces:
                    continue
                bindings.setdefault(workspace, {})[row.identity] = row
                if len(bindings) > 100_000:
                    raise RetentionError("retention_inventory_incomplete", "workspace count")
        for workspace in discovered:
            if not isinstance(workspace, TemporaryWorkspace):
                raise RetentionError("retention_owner_invalid", "discovered workspace capability")
            if workspace not in self.excluded_workspaces:
                bindings.setdefault(workspace, {})
            if len(bindings) > 100_000 or time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "workspace discovery budget")
        # One physical subtree must never receive two signed identities.
        paths = sorted((workspace.parent / name for workspace in bindings
                        for name in (workspace.name, workspace.removing)), key=lambda path: path.parts)
        for previous, current in zip(paths, paths[1:]):
            if current.is_relative_to(previous):
                raise RetentionError("retention_inventory_incomplete", "overlapping workspace capabilities")
        for workspace in bindings:
            for excluded in self.excluded_workspaces:
                if time.monotonic() > deadline:
                    raise RetentionError("retention_inventory_incomplete", "workspace exclusion budget")
                for name in (workspace.name, workspace.removing):
                    path = workspace.parent / name
                    for excluded_name in (excluded.name, excluded.removing):
                        other = excluded.parent / excluded_name
                        if path.is_relative_to(other) or other.is_relative_to(path):
                            raise RetentionError("retention_inventory_incomplete", "workspace overlaps excluded owner")
        return {workspace: tuple(jobs.values()) for workspace, jobs in bindings.items()}

    def identity(self, workspace, relative, kind):
        parts = Path(relative).parts
        if (not isinstance(workspace, TemporaryWorkspace) or kind not in {"file", "directory", "fence"}
                or not parts or Path(relative).is_absolute() or any(part in {".", ".."} for part in parts)
                or str(Path(relative)) != relative or "\0" in relative
                or (kind == "fence" and relative != ".lre-lock-" + workspace.name)
                or (kind != "fence" and parts[0] not in {workspace.name, workspace.removing})):
            raise RetentionError("retention_owner_invalid", "workspace identity")
        return ObjectIdentity(self.name, self.workloads.path.as_uri(), "blob",
            json.dumps([str(workspace.parent), workspace.name, relative, kind], separators=(",", ":")))

    def _parts(self, identity):
        try:
            values = json.loads(identity.local_id)
            if type(values) is not list or len(values) != 4 or any(type(v) is not str for v in values):
                raise ValueError("identity shape")
            workspace = TemporaryWorkspace(Path(values[0]), values[1])
            if self.identity(workspace, *values[2:]) != identity:
                raise ValueError("foreign identity")
            return workspace, values[2], values[3]
        except (TypeError, ValueError) as exc:
            raise RetentionError("retention_owner_invalid", "workspace identity") from exc

    @staticmethod
    def _directory_observation(files, fd, custody, name, identity):
        try:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RetentionError("retention_owner_path_invalid", "workspace directory") from exc
        try:
            info = os.fstat(child)
            if (files.owner is not None and ((info.st_uid, info.st_gid) != files.owner or info.st_mode & 0o022)):
                raise RetentionError("retention_owner_path_invalid", "workspace directory custody")
            names = files._names(child)
            version = _digest({"ancestors": custody, "directory": files._custody(info),
                "ctime_ns": info.st_ctime_ns, "mtime_ns": info.st_mtime_ns, "names": names})
            after = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (any((files._custody(item), item.st_ctime_ns, item.st_mtime_ns) !=
                    (files._custody(info), info.st_ctime_ns, info.st_mtime_ns)
                    for item in (os.fstat(child), after)) or files._names(child) != names):
                raise RetentionError("retention_owner_changed", "workspace directory drift")
            return identity, version, 0, datetime.fromtimestamp(info.st_mtime, timezone.utc), names
        finally:
            os.close(child)

    def _observe(self, identity):
        workspace, relative, kind = self._parts(identity)
        files, parts = self._files(workspace), Path(relative).parts
        try:
            with files._directory(*parts[:-1]) as (fd, custody):
                if kind == "directory":
                    result = self._directory_observation(files, fd, custody, parts[-1], identity)
                else:
                    result = files._read_file(fd, custody, parts[-1], identity, max_bytes=128 * 1024 * 1024)
                if result is None:
                    os.fsync(fd)
                return result
        except FileNotFoundError:
            # A later signed run may already have removed empty ancestors.
            # Fsync the closest surviving parent; never recreate a workspace.
            for depth in range(len(parts) - 2, -1, -1):
                try:
                    with files._directory(*parts[:depth]) as (fd, _):
                        os.fsync(fd)
                    return None
                except FileNotFoundError:
                    continue
            raise RetentionError("retention_owner_changed", "workspace parent missing")

    def inventory(self):
        rows = self.workloads.scan()
        discovery = self.discover_workspaces() if self.discover_workspaces else None
        bindings = self._bindings(rows, discovery.workspaces if discovery is not None else ())
        objects, job_edges = [], {row.identity: set() for row in rows}
        deadline, count, used = time.monotonic() + 15, 0, 0

        def visit(workspace, relative, depth, device):
            nonlocal count, used
            count += 1
            if count > 100_000 or depth > 64 or time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "workspace traversal budget")
            files, parts = self._files(workspace), Path(relative).parts
            with files._directory(*parts[:-1]) as (fd, custody):
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if info.st_dev != device:
                    raise RetentionError("retention_inventory_incomplete", "workspace device boundary")
                kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
                if relative == ".lre-lock-" + workspace.name:
                    kind = "fence"
                    if stat.S_IMODE(info.st_mode) != 0o600:
                        raise RetentionError("retention_owner_path_invalid", "workspace fence mode")
                identity = self.identity(workspace, relative, kind)
                observed = (self._directory_observation(files, fd, custody, parts[-1], identity)
                            if kind == "directory" else files._read_file(fd, custody, parts[-1], identity,
                                                                        max_bytes=128 * 1024 * 1024))
                if observed is None:
                    raise RetentionError("retention_owner_changed", "workspace disappeared")
            used += observed[2]
            if used > 128 * 1024 * 1024:
                raise RetentionError("retention_inventory_incomplete", "workspace bytes budget")
            jobs = bindings[workspace]
            busy = any(_ArtifactAuxiliaryOwner._busy(row) for row in jobs)
            roots = ((RootKind.IN_PROGRESS_JOB,) if busy else
                     (RootKind.OPEN_AUDIT,) if not jobs or kind == "fence" or (kind == "directory" and observed[4]) else ())
            if kind == "fence":
                with files._directory() as (fd, _):
                    lock = os.open(relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        if os.read(lock, 8) not in {b"", b"closed\n"}:
                            raise RetentionError("retention_owner_invalid", "workspace fence contents")
                    finally:
                        os.close(lock)
            if busy:
                for row in jobs:
                    if _ArtifactAuxiliaryOwner._busy(row):
                        job_edges[row.identity].add(identity)
            objects.append(OwnerObject(identity, observed[1], NodeState.OPEN if roots else NodeState.CLOSED,
                _iso(observed[3]), None if roots else _iso(observed[3] + timedelta(days=90)),
                () if kind == "fence" else tuple(row.identity for row in jobs), roots))
            if kind == "directory":
                for name in observed[4]:
                    visit(workspace, relative + "/" + name, depth + 1, device)
            if self._observe(identity) != observed:
                raise RetentionError("retention_owner_changed", "workspace inventory drift")

        for workspace in sorted(bindings, key=lambda item: (str(item.parent), item.name)):
            files = self._files(workspace)
            try:
                with files._directory() as (fd, _):
                    device = os.fstat(fd).st_dev
                    names = tuple(name for name in (workspace.name, workspace.removing, ".lre-lock-" + workspace.name)
                                  if name in files._names(fd))
                for name in names:
                    visit(workspace, name, 0, device)
                with files._directory() as (fd, _):
                    after = tuple(name for name in (workspace.name, workspace.removing, ".lre-lock-" + workspace.name)
                                  if name in files._names(fd))
                    if after != names:
                        raise RetentionError("retention_owner_changed", "workspace containers drift")
            except FileNotFoundError:
                continue
        if tuple((row.identity, row.version) for row in rows) != tuple(
                (row.identity, row.version) for row in self.workloads.scan()):
            raise RetentionError("retention_owner_changed", "workspace metadata drift")
        if self.discover_workspaces and self.discover_workspaces() != discovery:
            raise RetentionError("retention_owner_changed", "workspace discovery drift")
        for row in rows:
            state = self.workloads.state_of(row)
            objects.append(OwnerObject(row.identity, row.version, state.state, state.created_at,
                state.eligible_after, tuple(sorted(job_edges[row.identity], key=lambda item: item.local_id)), state.roots))
        return tuple(objects)

    def version(self, identity):
        observed = self._observe(identity)
        return None if observed is None else observed[1]

    @contextmanager
    def _fence(self, workspace):
        files, name = self._files(workspace), ".lre-lock-" + workspace.name
        with files._directory() as (fd, _):
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            created = False
            try:
                lock = os.open(name, flags, dir_fd=fd)
            except FileNotFoundError:
                lock = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
                created = True
            try:
                if created and files.owner is not None:
                    os.fchown(lock, *files.owner)
                info = os.fstat(lock)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                        or (files.owner is not None and (info.st_uid, info.st_gid) != files.owner)):
                    raise RetentionError("retention_owner_path_invalid", "workspace fence custody")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RetentionError("retention_owner_state_invalid", "workspace writer active") from exc
                if os.read(lock, 8) not in {b"", b"closed\n"}:
                    raise RetentionError("retention_owner_invalid", "workspace fence contents")
                if files._custody(os.stat(name, dir_fd=fd, follow_symlinks=False)) != files._custody(info):
                    raise RetentionError("retention_owner_changed", "workspace fence replaced")
                TemporaryWorkspace.retire(lock)
                os.fsync(fd)
                yield
            finally:
                os.close(lock)

    def delete(self, identity, expected_version):
        workspace, relative, kind = self._parts(identity)
        if kind == "fence":
            raise RetentionError("retention_owner_state_invalid", "workspace fence is permanent")
        with self.workloads._open(write=True) as (connection, metadata):
            rows = tuple(self.workloads._native(row, metadata, self.workloads._related(connection, row))
                         for row in _bounded_rows(connection, "SELECT * FROM workloads", ()))
            jobs = self._bindings(rows).get(workspace, ())
            observed = self._observe(identity)
            if observed is None:
                return
            if not jobs or any(_ArtifactAuxiliaryOwner._busy(row) for row in jobs):
                raise RetentionError("retention_owner_state_invalid", "workspace unbound or active")
            if observed[1] != expected_version:
                raise RetentionError("retention_owner_changed", "workspace version")
            if ((kind == "directory" and observed[4])
                    or observed[3] + timedelta(days=90) >= datetime.now(timezone.utc)):
                raise RetentionError("retention_owner_state_invalid", "workspace retention window or children")
            with self._fence(workspace):
                files, parts = self._files(workspace), Path(relative).parts
                with files._directory(*parts[:-1]) as (fd, _):
                    if self._observe(identity) != observed:
                        raise RetentionError("retention_owner_changed", "workspace changed before effect")
                    if kind == "directory":
                        os.rmdir(parts[-1], dir_fd=fd)
                    else:
                        os.unlink(parts[-1], dir_fd=fd)
                    os.fsync(fd)

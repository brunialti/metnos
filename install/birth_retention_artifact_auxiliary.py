"""Native LRE publication copies and artifact staging, under F6 exclusion.

Publication metadata keeps its copies until a later inventory after metadata
collection. Artifact scratch has the existing native terminal-job lifetime;
its files can close without discarding a job's delivery or audit records.
Workspace fence files and directories remain in place. Package-owned scratch
outside ArtifactStore is a different owner, not implicitly covered here.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import stat
import time

from durable_workloads.artifacts import (
    ArtifactStore, _BLOB_TEMP_RE, _HEX_RE, _owner_key, _publication_temp_name,
    _require_owner, _target_key,
)
from durable_workloads.temporary_storage import TemporaryWorkspace, _NAME, _TERMINAL
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _bounded_rows, _iso


class _ArtifactAuxiliaryOwner:
    def __init__(self, *, blobs, kind):
        if kind not in {"publications", "temporary"}:
            raise RetentionError("retention_owner_invalid", "artifact auxiliary kind")
        self.blobs, self.workloads, self.kind = blobs, blobs.workloads, kind
        self.name = "durable_artifact_" + kind

    @staticmethod
    def _workspace(container):
        name = container.removeprefix(".lre-cleanup-")
        if not _NAME.fullmatch(name):
            raise RetentionError("retention_owner_invalid", "artifact workspace name")
        return name

    def identity(self, owner_key, container, name):
        valid = bool(_HEX_RE.fullmatch(owner_key))
        if self.kind == "publications":
            valid &= bool(_HEX_RE.fullmatch(container)) and (
                name == "artifact" or (name.startswith(".prepared-")
                                       and bool(_HEX_RE.fullmatch(name[10:]))))
        elif container:
            self._workspace(container)
            valid &= bool(_BLOB_TEMP_RE.fullmatch(name))
        else:
            valid &= name.startswith(".lre-lock-") and bool(_NAME.fullmatch(name[10:]))
        if not valid:
            raise RetentionError("retention_owner_invalid", "artifact auxiliary name")
        return ObjectIdentity(self.name, self.blobs.root.as_uri(), "blob",
                              json.dumps([owner_key, container, name], separators=(",", ":")))

    def _parts(self, identity):
        try:
            parts = json.loads(identity.local_id)
            if (type(parts) is not list or len(parts) != 3
                    or any(type(value) is not str for value in parts)
                    or self.identity(*parts) != identity):
                raise ValueError("foreign identity")
            return tuple(parts)
        except (TypeError, ValueError) as exc:
            raise RetentionError("retention_owner_invalid", "artifact auxiliary identity") from exc

    def _directory(self, owner, container):
        parts = ("owners", owner, self.kind) + ((container,) if container else ())
        return self.blobs._directory(*parts)

    @staticmethod
    def _busy(row):
        return (row.values["state"] not in _TERMINAL
                or any(item["ended_at"] is None for item in dict(row.related)["attempts"]))

    def _references(self, rows):
        targets, active, jobs = {}, set(), {}
        for row in rows:
            owner = _owner_key(_require_owner(row.values["owner_user_id"]))
            jobs[(owner, row.values["id"])] = row
            if self.workloads.state_of(row).state is NodeState.OPEN:
                active.add(owner)
            if self.kind == "publications":
                artifacts = {item["id"]: item for item in dict(row.related)["artifacts"]}
                for publication in dict(row.related)["publications"]:
                    target = _target_key(row.values["owner_user_id"], publication["target_key"])
                    targets.setdefault((owner, target), []).append(
                        (row.identity, publication, artifacts[publication["artifact_id"]]))
        return targets, active, jobs

    def inventory(self):
        """Return files and jobs with their edges; assembler merges job edges."""
        rows = self.workloads.scan()
        targets, active, jobs = self._references(rows)
        objects, found, edges = [], set(), {row.identity: set() for row in rows}
        deadline = time.monotonic() + 15
        entries = 0

        def budget():
            nonlocal entries
            entries += 1
            if entries > 100_000 or time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "artifact auxiliary budget")

        def observe(owner, container, name, directory, custody):
            budget()
            identity = self.identity(owner, container, name)
            observed = self.blobs._read_file(directory, custody, name, identity)
            if observed is None:
                raise RetentionError("retention_owner_changed", "artifact auxiliary disappeared")
            _, version, _size, modified = observed
            roots = ()
            if self.kind == "publications":
                references = targets.get((owner, container), ())
                for job, publication, artifact in references:
                    # A published final copy must still match the native binding.
                    if name == "artifact" and publication["state"] == "published":
                        checked = self.blobs._read_file(directory, custody, name, identity,
                            expected_digest=publication["expected_digest"],
                            expected_size=artifact["size_bytes"])
                        if checked != observed:
                            raise RetentionError("retention_owner_changed", "published artifact changed")
                    if name not in {"artifact", _publication_temp_name(publication["id"])}:
                        continue
                    edges[job].add(identity)
                if references:
                    roots = (RootKind.OPEN_AUDIT,)
                elif owner in active:
                    roots = (RootKind.IN_PROGRESS_JOB,)
            elif not container:
                # The native lock inode is a permanent stale-writer fence.
                roots = (RootKind.IN_PROGRESS_JOB,)
                fd = ArtifactStore._open_regular(directory, name)
                try:
                    if os.read(fd, 8) not in {b"", b"closed\n"}:
                        raise RetentionError("retention_owner_invalid", "workspace fence contents")
                finally:
                    os.close(fd)
                if self.blobs._read_file(directory, custody, name, identity) != observed:
                    raise RetentionError("retention_owner_changed", "workspace fence changed")
            else:
                job = jobs.get((owner, self._workspace(container)))
                if job is not None and self._busy(job):
                    roots = (RootKind.IN_PROGRESS_JOB,)
                    edges[job.identity].add(identity)
                elif job is None and owner in active:
                    roots = (RootKind.IN_PROGRESS_JOB,)
            objects.append(OwnerObject(identity, version,
                NodeState.OPEN if roots else NodeState.CLOSED, _iso(modified),
                None if roots else _iso(modified + timedelta(days=90)), roots=roots))
            found.add((owner, container, name))

        try:
            with self.blobs._directory() as (root_fd, _):
                if self.blobs._names(root_fd) != ("owners",):
                    raise RetentionError("retention_inventory_incomplete", "unknown artifact root entries")
        except FileNotFoundError:
            if targets:
                raise RetentionError("retention_inventory_incomplete", "publication store absent")
        else:
            if self.workloads._files(allow_absent=True) is None:
                raise RetentionError("retention_inventory_incomplete", "artifact metadata absent")
            with self.blobs._directory("owners") as (owners_fd, _):
                namespaces = self.blobs._names(owners_fd)
                for owner in namespaces:
                    budget()
                    if not _HEX_RE.fullmatch(owner):
                        raise RetentionError("retention_inventory_incomplete", "unknown artifact owner")
                    with self.blobs._directory("owners", owner) as (owner_fd, _):
                        children = self.blobs._names(owner_fd)
                        if set(children) - {"blobs", "publications", "temporary"}:
                            raise RetentionError("retention_inventory_incomplete", "unknown artifact subtree")
                        if self.kind not in children:
                            continue
                    with self._directory(owner, "") as (parent, parent_custody):
                        containers = self.blobs._names(parent)
                        for container in containers:
                            budget()
                            if self.kind == "temporary" and container.startswith(".lre-lock-"):
                                observe(owner, "", container, parent, parent_custody)
                                continue
                            if self.kind == "publications" and not _HEX_RE.fullmatch(container):
                                raise RetentionError("retention_inventory_incomplete", "unknown publication target")
                            if self.kind == "temporary":
                                self._workspace(container)
                            with self._directory(owner, container) as (directory, file_custody):
                                names = self.blobs._names(directory)
                                for name in names:
                                    observe(owner, container, name, directory, file_custody)
                                if self.blobs._names(directory) != names:
                                    raise RetentionError("retention_owner_changed", "artifact auxiliary files changed")
                        if self.blobs._names(parent) != containers:
                            raise RetentionError("retention_owner_changed", "artifact auxiliary containers changed")
                if self.blobs._names(owners_fd) != namespaces:
                    raise RetentionError("retention_owner_changed", "artifact namespaces changed")
        for (owner, target), references in targets.items():
            if any(pub["state"] == "published" for _, pub, _ in references):
                if (owner, target, "artifact") not in found:
                    raise RetentionError("retention_inventory_incomplete", "published artifact absent")
        if time.monotonic() > deadline or tuple((row.identity, row.version) for row in rows) != tuple(
                (row.identity, row.version) for row in self.workloads.scan()):
            raise RetentionError("retention_owner_changed", "artifact auxiliary metadata changed")
        for row in rows:
            state = self.workloads.state_of(row)
            objects.append(OwnerObject(row.identity, row.version, state.state, state.created_at,
                state.eligible_after, tuple(sorted(edges[row.identity], key=lambda item: item.key.node_id)), state.roots))
        return tuple(objects)

    def version(self, identity):
        owner, container, name = self._parts(identity)
        with self._directory(owner, container) as (directory, custody):
            observed = self.blobs._read_file(directory, custody, name, identity)
            if observed is None:
                os.fsync(directory)
            return None if observed is None else observed[1]

    @contextmanager
    def _fence(self, owner, container):
        if self.kind != "temporary":
            yield
            return
        import fcntl

        # Native lock and retirement prevent a stale worker from reopening the
        # workspace. Creation/retirement occurs only inside a signed effect.
        name = ".lre-lock-" + self._workspace(container)
        with self._directory(owner, "") as (directory, _):
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                lock_fd = os.open(name, flags, dir_fd=directory)
            except FileNotFoundError:
                # The administrator may differ from the service account.
                # Only a newly created inode receives the declared custody.
                lock_fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                try:
                    if self.blobs.owner is not None:
                        os.fchown(lock_fd, *self.blobs.owner)
                except BaseException:
                    os.close(lock_fd)
                    raise
            try:
                info = os.fstat(lock_fd)
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_nlink != 1 or (self.blobs.owner is not None
                            and (info.st_uid, info.st_gid) != self.blobs.owner)):
                    raise RetentionError("retention_owner_path_invalid", "workspace fence custody")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RetentionError("retention_owner_state_invalid", "workspace writer still active") from exc
                if os.read(lock_fd, 8) not in {b"", b"closed\n"}:
                    raise RetentionError("retention_owner_invalid", "workspace fence contents")
                if self.blobs._custody(os.stat(name, dir_fd=directory, follow_symlinks=False)) != self.blobs._custody(info):
                    raise RetentionError("retention_owner_changed", "workspace fence replaced")
                TemporaryWorkspace.retire(lock_fd)
                os.fsync(directory)
                yield
            finally:
                os.close(lock_fd)

    def delete(self, identity, expected_version):
        owner, container, name = self._parts(identity)
        if not container:
            raise RetentionError("retention_owner_state_invalid", "workspace fence is retained")
        with self.workloads._open(write=True) as (connection, files):
            rows = tuple(self.workloads._native(row, files, self.workloads._related(connection, row))
                         for row in _bounded_rows(connection, "SELECT * FROM workloads", ()))
            targets, active, jobs = self._references(rows)
            if self.kind == "publications":
                blocked = (owner, container) in targets or owner in active
            else:
                job = jobs.get((owner, self._workspace(container)))
                blocked = self._busy(job) if job is not None else owner in active
            if blocked:
                raise RetentionError("retention_owner_state_invalid", "artifact auxiliary still referenced")
            with self._directory(owner, container) as (directory, custody):
                observed = self.blobs._read_file(directory, custody, name, identity)
                if observed is None:
                    os.fsync(directory)
                    return
                if observed[1] != expected_version:
                    raise RetentionError("retention_owner_changed", "artifact auxiliary version")
                if observed[3] + timedelta(days=90) >= datetime.now(timezone.utc):
                    raise RetentionError("retention_owner_state_invalid", "artifact auxiliary window")
                with self._fence(owner, container):
                    self.blobs.require_exclusion()
                    if self.blobs._read_file(directory, custody, name, identity) != observed:
                        raise RetentionError("retention_owner_changed", "artifact auxiliary changed before unlink")
                    os.unlink(name, dir_fd=directory)
                    os.fsync(directory)

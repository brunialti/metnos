"""Job-owned scratch lifecycle, driven by the existing LRE maintenance cycle.

Packages declare private workspaces from admitted plans. The common policy
retains checkpoints for suspended jobs and removes them after terminal states.
HTTP reads the persisted report; it never walks the filesystem. Published
artifacts and source directories must never be declared as workspaces.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat

from .migrations import utc_now


_TERMINAL = frozenset({"completed", "completed_with_errors", "failed", "cancelled"})
_SETTLED = (*sorted(_TERMINAL), "paused", "needs_attention", "cancel_requested")
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemporaryWorkspace:
    """A dedicated child of a package-owned private scratch directory."""

    parent: Path
    name: str

    def __post_init__(self):
        if (not isinstance(self.parent, Path) or not self.parent.is_absolute()
                or ".." in self.parent.parts or not _NAME.fullmatch(self.name)):
            raise ValueError("invalid temporary workspace")

    @property
    def removing(self):
        return ".lre-cleanup-" + self.name

    @contextmanager
    def directory(self, *, create=False):
        # Open every ancestor without following links. Holding the final fd
        # also prevents a concurrent path replacement from redirecting unlink.
        fd = os.open(self.parent.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.parent.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def use(self, *, exclusive=False):
        """Fence cleanup against actual writers, including expired attempts.

        The small lock/retirement record stays outside scratch. Keeping its
        inode prevents stale processes from recreating a removed workspace.
        """
        import fcntl

        with self.directory(create=True) as parent:
            fd = os.open(".lre-lock-" + self.name,
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("unsafe temporary workspace lock")
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                if not exclusive and os.read(fd, 1):
                    raise OSError("temporary workspace has been retired")
                yield fd
            finally:
                os.close(fd)

    @staticmethod
    def retire(lock_fd):
        os.pwrite(lock_fd, b"closed\n", 0)
        os.fsync(lock_fd)

    def measure(self):
        size = files = 0

        def walk(fd, name, device, depth=0):
            nonlocal size, files
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                raise OSError("temporary workspace changed during inspection") from None
            if info.st_dev != device or depth > 64:
                raise OSError("temporary workspace inspection is incomplete")
            size += info.st_blocks * 512 if hasattr(info, "st_blocks") else info.st_size
            if not stat.S_ISDIR(info.st_mode):
                files += 1
                return
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                raise OSError("temporary workspace changed during inspection") from None
            try:
                with os.scandir(child) as items:
                    for item in items:
                        walk(child, item.name, device, depth + 1)
            finally:
                os.close(child)

        try:
            with self.directory() as fd:
                device = os.fstat(fd).st_dev
                for name in (self.name, self.removing):
                    try:
                        os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    walk(fd, name, device)
        except FileNotFoundError:
            pass
        return size, files

    def detach(self):
        """Short rename under the admission transaction, never recursive I/O."""
        try:
            with self.directory() as fd:
                try:
                    os.stat(self.removing, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    try:
                        os.rename(self.name, self.removing, src_dir_fd=fd, dst_dir_fd=fd)
                        os.fsync(fd)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass

    def detached(self):
        """Verify the closed fence and return whether detached data exists.

        The cleanup caller holds the exclusive fence while this proof runs.
        Reading by directory descriptor keeps the proof on the same no-follow
        path boundary used by detach and purge.
        """
        with self.directory() as parent:
            lock_fd = os.open(
                ".lre-lock-" + self.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            try:
                info = os.fstat(lock_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or os.pread(lock_fd, 8, 0) != b"closed\n"):
                    raise OSError("temporary workspace fence is not closed")
            finally:
                os.close(lock_fd)
            try:
                os.stat(self.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("temporary workspace is not detached")
            try:
                os.stat(self.removing, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return True

    def purge(self):
        if not shutil.rmtree.avoids_symlink_attacks:
            raise OSError("safe temporary workspace removal is unavailable")
        try:
            with self.directory() as fd:
                try:
                    info = os.stat(self.removing, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    return
                if stat.S_ISDIR(info.st_mode):
                    shutil.rmtree(self.removing, dir_fd=fd)
                else:
                    os.unlink(self.removing, dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:
            pass


def reports(store, owner, workload_ids):
    """One owner-scoped bounded query, with no paths or filesystem access."""
    if not workload_ids:
        return {}
    if len(workload_ids) > 200:
        raise ValueError("temporary storage report page is too large")
    marks = ",".join("?" for _ in workload_ids)
    rows = store._connection.execute(f"""
        SELECT w.id, w.state, w.version, w.active_revision_id, t.*
        FROM workloads w LEFT JOIN workload_temporary_storage t
          ON t.owner_user_id=w.owner_user_id AND t.workload_id=w.id
        WHERE w.owner_user_id=? AND w.id IN ({marks})
    """, (owner, *workload_ids))
    result = {}
    for row in rows:
        current = row["workload_version"] == row["version"]
        status = (row["status"] if current else
                  "clean" if row["active_revision_id"] is None else "pending")
        result[row["id"]] = {
            "status": status,
            "size_bytes": row["size_bytes"] if current else None,
            "file_count": row["file_count"] if current else None,
            "checked_at": row["checked_at"] if current else None,
            "can_dismiss": status == "clean" and row["state"] in _TERMINAL,
        }
    return result


class TemporaryStorage:
    """Inspect scratch and close terminal ownership.

    A configured handoff must durably and idempotently accept the exact
    owner/workload/version and detached capabilities before returning ``None``.
    It may be called again after a crash or a reported failure.
    """

    def __init__(self, store, resolve_workspaces, *, artifact_workspace=None,
                 handoff_detached=None):
        if handoff_detached is not None and not callable(handoff_detached):
            raise TypeError("detached workspace handoff must be callable")
        self.store = store
        self.resolve = resolve_workspaces
        self.artifact_workspace = artifact_workspace
        self.handoff_detached = handoff_detached

    def paths(self, owner, workload_id):
        paths = set()
        if self.artifact_workspace is not None:
            paths.add(self.artifact_workspace(owner, workload_id))
        # Include old revisions: accepting a replacement does not surrender
        # ownership of scratch left by the preceding revision.
        for row in self.store._connection.execute("""
            SELECT plan_json FROM revisions
            WHERE owner_user_id=? AND workload_id=? AND admitted_at IS NOT NULL
        """, (owner, workload_id)):
            paths.update(self.resolve(json.loads(row["plan_json"])))
        if any(not isinstance(path, TemporaryWorkspace) for path in paths):
            raise ValueError("unregistered temporary workspace")
        return paths

    def busy(self, owner, workload_id):
        return self.store._connection.execute("""
            SELECT 1 FROM revisions r JOIN units u
              ON u.owner_user_id=r.owner_user_id AND u.revision_id=r.id
            JOIN attempts a ON a.owner_user_id=u.owner_user_id AND a.unit_id=u.id
            WHERE r.owner_user_id=? AND r.workload_id=?
              AND a.state IN ('leased', 'running') LIMIT 1
        """, (owner, workload_id)).fetchone() is not None

    def shared(self, owner, workload_id, paths):
        if not paths:
            return False
        # A recovery job can refer to the same checkpoint generation. Check
        # all owners, because deployment storage itself can be shared.
        for row in self.store._connection.execute("""
            SELECT owner_user_id, id, state FROM workloads
            WHERE NOT (owner_user_id=? AND id=?)
        """, (owner, workload_id)):
            if row["state"] in _TERMINAL and not self.busy(row["owner_user_id"], row["id"]):
                continue
            if paths.intersection(self.paths(row["owner_user_id"], row["id"])):
                return True
        return False

    def observe(self, row):
        owner, workload_id, version = row["owner_user_id"], row["id"], row["version"]
        size = files = None
        status = "pending"
        try:
            paths = self.paths(owner, workload_id)
            with ExitStack() as locks:
                if self.busy(owner, workload_id):
                    raise BlockingIOError("attempts still active")
                held = {path: locks.enter_context(path.use(exclusive=True))
                        for path in sorted(paths, key=lambda p: (str(p.parent), p.name))}
                measurements = [path.measure() for path in paths]
                size = sum(item[0] for item in measurements)
                files = sum(item[1] for item in measurements)
                status = "retained" if size or files else "clean"
                if row["state"] in _TERMINAL:
                    # Revalidate under the same writer reservation as admission.
                    # Recursive removal happens after this short transaction.
                    with self.store._transaction():
                        latest = self.store.get_workload(owner, workload_id)
                        if latest.version != version or self.busy(owner, workload_id):
                            return
                        status = "shared" if self.shared(owner, workload_id, paths) else "pending"
                        if status != "shared":
                            for path in paths:
                                path.retire(held[path])
                                path.detach()
                    if status != "shared":
                        ordered = tuple(sorted(
                            paths, key=lambda path: (str(path.parent), path.name),
                        ))
                        if self.handoff_detached is not None and (size or files):
                            try:
                                accepted = self.handoff_detached(
                                    owner, workload_id, version, ordered,
                                )
                            except Exception as exc:
                                raise OSError("detached workspace handoff failed") from exc
                            if accepted is not None:
                                raise ValueError("detached workspace handoff returned a value")
                            # The physical owner now accounts for the detached
                            # trees. LRE no longer reports them as job scratch.
                            size = files = 0
                            status = "clean"
                        else:
                            for path in paths:
                                path.purge()
                            remaining = [path.measure() for path in paths]
                            size = sum(item[0] for item in remaining)
                            files = sum(item[1] for item in remaining)
                            status = "pending" if size or files else "clean"
        except BlockingIOError:
            status = "pending"
        except (OSError, ValueError, TypeError, LookupError):
            status = "error"
            # A failed or partial inspection must never masquerade as zero.
            size = files = None
            _LOG.warning("temporary storage cleanup/inspection failed for %s", workload_id)
        with self.store._transaction() as connection:
            if self.store.get_workload(owner, workload_id).version != version:
                return
            previous = connection.execute(
                "SELECT status FROM workload_temporary_storage WHERE owner_user_id=? AND workload_id=?",
                (owner, workload_id),
            ).fetchone()
            connection.execute("""
                INSERT INTO workload_temporary_storage
                  (owner_user_id, workload_id, workload_version, status, size_bytes, file_count, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, workload_id) DO UPDATE SET
                  workload_version=excluded.workload_version, status=excluded.status,
                  size_bytes=excluded.size_bytes, file_count=excluded.file_count,
                  checked_at=excluded.checked_at
            """, (owner, workload_id, version, status, size, files, utc_now()))
            if previous is None or previous["status"] != status:
                _LOG.info("LRE temporary storage %s: %s (%s bytes, %s files)",
                          workload_id, status, size, files)

    def maintain(self, *, limit=4):
        """Bounded, restart-safe work; unchanged suspended jobs are not rescanned."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid temporary maintenance limit")
        marks = ",".join("?" for _ in _SETTLED)
        rows = self.store._connection.execute(f"""
            SELECT w.* FROM workloads w LEFT JOIN workload_temporary_storage t
              ON t.owner_user_id=w.owner_user_id AND t.workload_id=w.id
            WHERE w.state IN ({marks}) AND (
              t.workload_version IS NULL OR t.workload_version<>w.version
              OR t.status IN ('pending', 'shared', 'error'))
            ORDER BY coalesce(t.checked_at, ''), w.updated_at LIMIT ?
        """, (*_SETTLED, limit)).fetchall()
        for row in rows:
            self.observe(row)


class DetachedWorkspaceCustody:
    """Accept detached scratch by proving its existing durable F6 identity.

    The workload, admitted revisions and deterministic artifact workspace are
    already the authoritative inventory.  Acceptance therefore records no
    parallel state: it re-derives the exact capabilities in the same database
    snapshot and verifies their physical retired form while cleanup holds the
    exclusive writer fences.  Repeating the call after a crash is harmless.
    """

    def __init__(self, store, resolve_workspaces, *, artifact_workspace=None):
        self._storage = TemporaryStorage(
            store,
            resolve_workspaces,
            artifact_workspace=artifact_workspace,
        )

    def __call__(self, owner, workload_id, version, paths):
        storage = self._storage
        with storage.store._transaction():
            current = storage.store.get_workload(owner, workload_id)
            if current.version != version or current.state.value not in _TERMINAL:
                raise OSError("detached workspace authority changed")
            if storage.busy(owner, workload_id):
                raise OSError("detached workspace still has active attempts")
            expected = tuple(sorted(
                storage.paths(owner, workload_id),
                key=lambda path: (str(path.parent), path.name),
            ))
            if tuple(paths) != expected:
                raise ValueError("detached workspace capabilities disagree")
        detached = tuple(path.detached() for path in expected)
        if not any(detached):
            raise OSError("detached workspace custody has no physical data")

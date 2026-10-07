"""Stopped LRE blob inventory, using native artifact identities and verification.

Metadata remains authoritative. A referenced blob is retained even when its
last workload is a collection candidate. After that workload is removed, a
later inventory can prove the blob unreferenced. This deliberately avoids a
cross-store deletion transaction or a second artifact metadata store.

This owner covers ``blobs/sha256`` only. Publication and temporary workspaces
have distinct lifetimes and must also be covered by the complete assembler.
There is no executable entrypoint or autonomous collection here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

from durable_workloads.artifacts import (
    _BLOB_REF_PREFIX, _BLOB_TEMP_RE, _HEX_RE,
    _blob_ref, _owner_key, _require_digest, _require_owner,
)
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _bounded_rows, _iso
from install.birth_retention_files import _PrivateFiles


def _binding(owner, reference):
    if type(reference) is not str or not reference.startswith(_BLOB_REF_PREFIX):
        raise RetentionError("retention_inventory_incomplete", "unknown LRE blob reference")
    try:
        digest = _require_digest(reference[len(_BLOB_REF_PREFIX):])
        return _owner_key(_require_owner(owner)), digest[7:]
    except ValueError as exc:
        raise RetentionError("retention_inventory_incomplete", "invalid LRE blob reference") from exc


def _artifact_binding(row):
    key = _binding(row["owner_user_id"], row["blob_ref"])
    if row["blob_ref"] != _blob_ref(row["digest"]):
        raise RetentionError("retention_inventory_incomplete", "artifact digest binding")
    return key


class _ArtifactBlobOwner(_PrivateFiles):
    name = "durable_artifact_blobs"

    def __init__(self, *, root: Path, workloads):
        super().__init__(root=root, require_exclusion=workloads.require_exclusion, owner=workloads.owner)
        self.workloads = workloads

    def identity(self, owner_key, name):
        if not _HEX_RE.fullmatch(owner_key) or not (
            _HEX_RE.fullmatch(name) or _BLOB_TEMP_RE.fullmatch(name)
        ):
            raise RetentionError("retention_owner_invalid", "artifact namespace")
        return ObjectIdentity(self.name, self.root.as_uri(), "blob",
                              json.dumps([owner_key, name], separators=(",", ":")))

    def _parts(self, identity):
        try:
            parts = json.loads(identity.local_id)
            if (type(parts) is not list or len(parts) != 2
                    or any(type(value) is not str for value in parts)
                    or self.identity(*parts) != identity):
                raise ValueError("foreign identity")
            return tuple(parts)
        except (ValueError, TypeError) as exc:
            raise RetentionError("retention_owner_invalid", "artifact identity") from exc

    def _read(self, directory, custody, owner_key, name):
        return self._read_file(directory, custody, name, self.identity(owner_key, name),
                               expected_digest="sha256:" + name if _HEX_RE.fullmatch(name) else None)

    def inventory(self):
        """Return native jobs and their blob edges; not a complete F6 inventory."""
        rows = self.workloads.scan()
        jobs, references, sizes, active = [], {}, {}, set()
        for row in rows:
            owner = row.values["owner_user_id"]
            state = self.workloads.state_of(row)
            # A terminal label can still have an unfinished native attempt,
            # delivery, publication or temporary-workspace recovery.
            if state.state is NodeState.OPEN:
                active.add(_owner_key(_require_owner(owner)))
            related, linked = dict(row.related), set()
            for artifact in related["artifacts"]:
                key = _artifact_binding(artifact)
                if key in sizes and sizes[key] != artifact["size_bytes"]:
                    raise RetentionError("retention_inventory_incomplete", "artifact size conflict")
                sizes[key] = artifact["size_bytes"]
                linked.add(key)
            for result in related["results"]:
                if result["blob_ref"] is not None:
                    linked.add(_binding(result["owner_user_id"], result["blob_ref"]))
            for key in linked:
                references.setdefault(key, set()).add(row.identity)
            jobs.append(OwnerObject(row.identity, row.version, state.state, state.created_at,
                                    state.eligible_after,
                                    tuple(self.identity(*key) for key in sorted(linked)), state.roots))
        deadline = time.monotonic() + 15
        blobs, found = [], set()
        try:
            with self._directory() as (root_fd, _):
                if self._names(root_fd) != ("owners",):
                    raise RetentionError("retention_inventory_incomplete", "unknown artifact root entries")
        except FileNotFoundError:
            if references:
                raise RetentionError("retention_inventory_incomplete", "referenced artifact root absent")
            return tuple(jobs)
        # An existing artifact store without its metadata is a historical gap.
        if self.workloads._files(allow_absent=True) is None:
            raise RetentionError("retention_inventory_incomplete", "artifact metadata absent")
        with self._directory("owners") as (owners_fd, _):
            namespaces = self._names(owners_fd)
            for owner_key in namespaces:
                if not _HEX_RE.fullmatch(owner_key):
                    raise RetentionError("retention_inventory_incomplete", "unknown artifact owner namespace")
                with self._directory("owners", owner_key) as (owner_fd, _):
                    children = self._names(owner_fd)
                    if set(children) - {"blobs", "publications", "temporary"}:
                        raise RetentionError("retention_inventory_incomplete", "unknown artifact subtree")
                    if "blobs" not in children:
                        continue
                with self._directory("owners", owner_key, "blobs") as (blobs_fd, _):
                    if self._names(blobs_fd) != ("sha256",):
                        raise RetentionError("retention_inventory_incomplete", "unknown artifact algorithm")
                with self._directory("owners", owner_key, "blobs", "sha256") as (directory, custody):
                    names = self._names(directory)
                    for name in names:
                        self.identity(owner_key, name)  # validate before opening
                        if len(blobs) >= 100_000 or time.monotonic() > deadline:
                            raise RetentionError("retention_inventory_incomplete", "artifact inventory budget")
                        observed = self._read(directory, custody, owner_key, name)
                        if observed is None:
                            raise RetentionError("retention_owner_changed", "artifact disappeared")
                        identity, version, size, modified = observed
                        key = owner_key, name
                        if key in sizes and size != sizes[key]:
                            raise RetentionError("retention_owner_invalid", "artifact content size")
                        roots = ((RootKind.IN_PROGRESS_JOB,) if owner_key in active else
                                 (RootKind.OPEN_AUDIT,) if key in references else ())
                        blobs.append(OwnerObject(identity, version,
                            NodeState.OPEN if roots else NodeState.CLOSED, _iso(modified),
                            None if roots else _iso(modified + timedelta(days=90)), roots=roots))
                        found.add(key)
                    if self._names(directory) != names:
                        raise RetentionError("retention_owner_changed", "artifact inventory changed")
            if self._names(owners_fd) != namespaces:
                raise RetentionError("retention_owner_changed", "artifact owners changed")
        if time.monotonic() > deadline or set(references) - found:
            raise RetentionError("retention_inventory_incomplete", "missing artifact or exceeded budget")
        if tuple((row.identity, row.version) for row in self.workloads.scan()) != tuple(
            (row.identity, row.version) for row in rows
        ):
            raise RetentionError("retention_owner_changed", "artifact metadata changed")
        return tuple(jobs + blobs)

    def version(self, identity):
        owner_key, name = self._parts(identity)
        with self._directory("owners", owner_key, "blobs", "sha256") as (directory, custody):
            observed = self._read(directory, custody, owner_key, name)
            if observed is None:
                # A previous process may have died between unlink and fsync.
                # Confirm durable absence before the journal records success.
                os.fsync(directory)
            return None if observed is None else observed[1]

    def delete(self, identity, expected_version):
        owner_key, name = self._parts(identity)
        # Keep the actual database write exclusion while rechecking references
        # and unlinking. No native table, trigger or metadata row is rewritten.
        with self.workloads._open(write=True) as (connection, files):
            for row in _bounded_rows(connection, "SELECT * FROM workloads", ()):
                if _owner_key(_require_owner(row["owner_user_id"])) != owner_key:
                    continue
                native = self.workloads._native(row, files, self.workloads._related(connection, row))
                if self.workloads.state_of(native).state is NodeState.OPEN:
                    raise RetentionError("retention_owner_state_invalid", "artifact owner has active work")
            for row in _bounded_rows(connection, "SELECT owner_user_id,blob_ref FROM artifacts "
                                     "UNION ALL SELECT owner_user_id,blob_ref FROM results "
                                     "WHERE blob_ref IS NOT NULL", ()):
                if _binding(row["owner_user_id"], row["blob_ref"]) == (owner_key, name):
                    raise RetentionError("retention_owner_state_invalid", "artifact still referenced")
            with self._directory("owners", owner_key, "blobs", "sha256") as (directory, custody):
                observed = self._read(directory, custody, owner_key, name)
                if observed is None:
                    os.fsync(directory)
                    return
                if observed[1] != expected_version:
                    raise RetentionError("retention_owner_changed", "artifact version")
                if observed[3] + timedelta(days=90) >= datetime.now(timezone.utc):
                    raise RetentionError("retention_owner_state_invalid", "artifact retention window")
                self.require_exclusion()
                os.unlink(name, dir_fd=directory)
                os.fsync(directory)

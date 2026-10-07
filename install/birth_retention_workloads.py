"""One stopped inventory for LRE metadata and ArtifactStore files.

Each file owner observes the same native jobs. Join only their references;
identity, physical version, lifetime and roots must agree exactly. This is a
bounded part of the installation inventory, not a declaration that all F6
owners or roots have been observed. Package workspaces remain a distinct owner.
"""
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from executor_birth_retention import RetentionError
from install.birth_retention_artifacts import _ArtifactBlobOwner
from install.birth_retention_artifact_auxiliary import _ArtifactAuxiliaryOwner
from install.birth_retention_maintenance import OwnerObject


class _WorkloadInventory:
    def __init__(self, *, workloads, artifact_root):
        self.workloads = workloads
        blobs = _ArtifactBlobOwner(root=artifact_root, workloads=workloads)
        self.file_owners = (
            blobs,
            _ArtifactAuxiliaryOwner(blobs=blobs, kind="publications"),
            _ArtifactAuxiliaryOwner(blobs=blobs, kind="temporary"),
        )
        self.owners = MappingProxyType({owner.name: owner for owner in (workloads, *self.file_owners)})

    def _jobs(self):
        jobs = {}
        for row in self.workloads.scan():
            state = self.workloads.state_of(row)
            jobs[row.identity] = OwnerObject(row.identity, row.version, state.state,
                state.created_at, state.eligible_after, roots=state.roots)
        return jobs

    def inventory(self):
        """Read a fixed owner set; never accept an incomplete projection."""
        self.workloads.require_exclusion()
        jobs = self._jobs()
        objects = dict(jobs)
        for owner in self.file_owners:
            seen, observed_jobs = set(), set()
            for obj in owner.inventory():
                identity = obj.identity
                if identity in seen:
                    raise RetentionError("retention_inventory_incomplete", "duplicate workload object")
                seen.add(identity)
                if identity.owner == self.workloads.name:
                    if jobs.get(identity) != replace(obj, references=()):
                        raise RetentionError("retention_owner_changed", "workload projections disagree")
                    observed_jobs.add(identity)
                    previous = objects[identity]
                    objects[identity] = replace(previous, references=tuple(sorted(
                        set(previous.references) | set(obj.references), key=lambda item: item.key.node_id)))
                elif identity.owner == owner.name and identity not in objects:
                    objects[identity] = obj
                else:
                    raise RetentionError("retention_inventory_incomplete", "foreign workload object")
            if observed_jobs != set(jobs):
                raise RetentionError("retention_inventory_incomplete", "missing workload projection")
        if any(ref not in objects for obj in objects.values() for ref in obj.references):
            raise RetentionError("retention_inventory_incomplete", "unresolved workload reference")
        if self._jobs() != jobs:
            raise RetentionError("retention_owner_changed", "workload changed during inventory")
        self.workloads.require_exclusion()
        return tuple(sorted(objects.values(), key=lambda obj: obj.identity.key.node_id))

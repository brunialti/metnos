"""Compose native LRE metadata, artifact files and package scratch.

The caller supplies an authenticated installed workload owner and image root
under the existing maintenance exclusion. This is an LRE subgraph, not the
complete installation census or a grant to collect it.
"""
from types import MappingProxyType

from durable_workloads.artifacts import ArtifactStore, ArtifactWorkspacePaths

from executor_birth_retention import RetentionError
from install.birth_retention_image_workspaces import _ImageWorkspaceCensus
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_workloads import _WorkloadInventory
from install.birth_retention_workspaces import _WorkspaceOwner


class _WorkspaceInventory:
    def __init__(self, workspace):
        self.workspace = workspace
        self.owners = MappingProxyType({
            workspace.workloads.name: workspace.workloads,
            workspace.name: workspace,
        })

    def inventory(self):
        return self.workspace.inventory()


class _LreInventory(_InventoryComposition):
    def __init__(self, *, workloads, artifact_root, image_root):
        # Both projections must use the exact resolver already selected for
        # workload lifetime checks. A second registry can select other paths.
        if not callable(workloads.resolve_workspaces):
            raise RetentionError('retention_inventory_incomplete', 'LRE workspace resolver missing')
        self.workloads = workloads
        self._resolver = workloads.resolve_workspaces
        self._artifact_resolver = workloads.artifact_workspace
        self._artifact_root = ArtifactStore.root_path(artifact_root)
        self._check_artifact_binding()
        workloads.require_exclusion()
        artifacts = _WorkloadInventory(workloads=workloads, artifact_root=artifact_root)
        discovery = _ImageWorkspaceCensus(
            image_root=image_root, require_exclusion=workloads.require_exclusion,
            owner=workloads.owner)
        workspace = _WorkspaceOwner(
            workloads=workloads, resolve_workspaces=workloads.resolve_workspaces,
            discover_workspaces=discovery)
        super().__init__(
            components=(artifacts, _WorkspaceInventory(workspace)),
            required_owners=frozenset({
                'durable_workloads', 'durable_artifact_blobs',
                'durable_artifact_publications', 'durable_artifact_temporary',
                'durable_workspaces',
            }),
            require_exclusion=workloads.require_exclusion)

    def _check_artifact_binding(self):
        resolver = self.workloads.artifact_workspace
        store = getattr(resolver, '__self__', None)
        if type(store) is ArtifactStore:
            native = ArtifactStore.temporary_workspace
            root = store._root
        elif type(store) is ArtifactWorkspacePaths:
            native = ArtifactWorkspacePaths.temporary_workspace
            root = store.root
        else:
            raise RetentionError('retention_owner_changed', 'LRE artifact workspace root binding')
        if (getattr(resolver, '__func__', None) is not native
                or root != self._artifact_root):
            raise RetentionError('retention_owner_changed', 'LRE artifact workspace root binding')

    def _check_owners(self):
        super()._check_owners()
        if (self.workloads.resolve_workspaces is not self._resolver
                or self.workloads.artifact_workspace is not self._artifact_resolver):
            raise RetentionError('retention_owner_changed', 'LRE workspace resolver changed')
        self._check_artifact_binding()

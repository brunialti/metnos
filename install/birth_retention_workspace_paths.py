"""Native admitted-workspace census shared by job and physical F6 owners."""
from __future__ import annotations

import json
import os
import time

from durable_workloads.temporary_storage import TemporaryWorkspace
from executor_birth_retention import RetentionError
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity


def workspace_paths(row, resolve_workspaces, artifact_workspace=None):
    """Use native package capabilities, including superseded admitted revisions."""
    if not callable(resolve_workspaces):
        raise RetentionError("retention_inventory_incomplete", "native workspace resolver missing")
    paths, deadline = set(), time.monotonic() + 15
    if artifact_workspace is not None:
        paths.add(artifact_workspace(row.values["owner_user_id"], row.values["id"]))
    for revision in dict(row.related)["revisions"]:
        if revision["admitted_at"] is None:
            continue
        for workspace in resolve_workspaces(json.loads(revision["plan_json"])):
            paths.add(workspace)
            if len(paths) > 100_000 or time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "workspace path budget")
    if any(not isinstance(path, TemporaryWorkspace) for path in paths):
        raise RetentionError("retention_owner_invalid", "native workspace capability")
    return frozenset(paths)


def workspaces_absent(paths, *, require_exclusion, owner):
    """Read-only proof: private parent, exact permanent fence, no live/residual tree.

    A missing parent is not a custody proof. Native observe creates and retires
    the fence even for an unused declared workspace before reporting it clean.
    """
    deadline = time.monotonic() + 15
    for workspace in sorted(paths, key=lambda value: (str(value.parent), value.name)):
        if time.monotonic() > deadline:
            raise RetentionError("retention_inventory_incomplete", "workspace absence budget")
        files = _PrivateFiles(root=workspace.parent, require_exclusion=require_exclusion,
                              owner=owner, private_directory=False)
        name = ".lre-lock-" + workspace.name
        identity = ObjectIdentity("durable_workspace_fence_check", workspace.parent.as_uri(), "blob", name)
        try:
            with files._directory() as (fd, custody):
                before = files._read_file(fd, custody, name, identity, include_payload=True, max_bytes=8)
                if before is None or before[-1] != b"closed\n":
                    return False
                for container in (workspace.name, workspace.removing):
                    try:
                        os.stat(container, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    return False
                if files._read_file(fd, custody, name, identity, include_payload=True, max_bytes=8) != before:
                    raise RetentionError("retention_owner_changed", "workspace absence fence drift")
        except FileNotFoundError:
            return False
    return True

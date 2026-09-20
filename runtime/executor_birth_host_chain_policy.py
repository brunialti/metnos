"""Pure projection of canonical host paths onto descriptor chains."""
from __future__ import annotations

from pathlib import PurePosixPath

from executor_birth_account_identity import PosixAccountSnapshotV1
from executor_birth_host_layout import (
    HOST_TRUST_ANCHORS_V1,
    build_host_layout_spec_v1,
)


def host_directory_chain_expectations_v1(
    account: PosixAccountSnapshotV1, target: PurePosixPath,
) -> tuple[tuple[PurePosixPath, int, int, int | None], ...]:
    """Project one canonical path into complete descriptor-chain metadata."""
    if type(target) is not PurePosixPath or not target.is_absolute():
        raise ValueError("host chain target")
    spec = build_host_layout_spec_v1(account)
    index = {item.path: item for item in spec.objects}
    if target not in index:
        raise ValueError("host chain target")
    paths = [PurePosixPath("/")]
    for part in target.parts[1:]:
        paths.append(paths[-1] / part)
    result = []
    for path in paths:
        if path in HOST_TRUST_ANCHORS_V1:
            result.append((path, 0, 0, None))
            continue
        item = index.get(path)
        if item is None:
            raise ValueError("host chain policy gap")
        result.append((
            path, item.ownership.uid, item.ownership.gid, item.mode,
        ))
    return tuple(result)


__all__ = ["host_directory_chain_expectations_v1"]

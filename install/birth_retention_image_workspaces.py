"""Read-only discovery of native image scratch, including pre-context crashes."""
from dataclasses import dataclass
import os
import re
import time

from durable_workloads.temporary_storage import TemporaryWorkspace
from executor_birth_retention import RetentionError
from image_index_build import _GENERATION
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import _digest


@dataclass(frozen=True)
class WorkspaceDiscovery:
    workspaces: frozenset[TemporaryWorkspace]
    token: str


class _ImageWorkspaceCensus:
    """The caller supplies the selected image root, never ambient configuration."""

    def __init__(self, *, image_root, require_exclusion, owner):
        self.files = _PrivateFiles(root=image_root, require_exclusion=require_exclusion,
                                   owner=owner, private_directory=False)

    def __call__(self):
        facts, paths = [], set()
        deadline = time.monotonic() + 15

        def visit(parts, level):
            if len(facts) + len(paths) >= 100_000 or time.monotonic() > deadline:
                raise RetentionError('retention_inventory_incomplete', 'image workspace census budget')
            try:
                with self.files._directory(*parts) as (fd, custody):
                    info = os.fstat(fd)
                    if info.st_mode & 0o022:
                        raise RetentionError('retention_owner_path_invalid', 'image workspace directory writable')
                    names = self.files._names(fd)
                    facts.append((parts, custody, info.st_mtime_ns, info.st_ctime_ns, names))
                    if level == 0:
                        for name in names:
                            if re.fullmatch('[0-9a-f]{16}', name) is None:
                                raise RetentionError('retention_inventory_incomplete', 'unknown image corpus namespace')
                            visit((*parts, name), 1)
                    elif level == 1 and 'unified' in names:
                        visit((*parts, 'unified'), 2)
                    elif level == 2 and '.builds' in names:
                        visit((*parts, '.builds'), 3)
                    elif level == 3:
                        for name in names:
                            generation = name
                            for prefix in ('.lre-cleanup-', '.lre-lock-'):
                                if name.startswith(prefix):
                                    generation = name[len(prefix):]
                                    break
                            if _GENERATION.fullmatch(generation) is None:
                                raise RetentionError('retention_inventory_incomplete', 'unknown image workspace namespace')
                            paths.add(TemporaryWorkspace(self.files.root.joinpath(*parts), generation))
                            if len(paths) >= 100_000 or time.monotonic() > deadline:
                                raise RetentionError('retention_inventory_incomplete', 'image workspace census budget')
                    after = os.fstat(fd)
                    if (self.files._names(fd) != names or
                            (after.st_mtime_ns, after.st_ctime_ns) != (info.st_mtime_ns, info.st_ctime_ns)):
                        raise RetentionError('retention_owner_changed', 'image workspace namespace drift')
            except FileNotFoundError:
                if parts:
                    raise RetentionError('retention_owner_changed', 'image workspace namespace disappeared') from None
                facts.append(('absent',))

        visit((), 0)
        return WorkspaceDiscovery(frozenset(paths), _digest(facts))

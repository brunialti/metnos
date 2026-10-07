"""Physical native journal segments; shared custody and recoverable compaction."""
from dataclasses import replace
from pathlib import Path
import os
import re
import time
from types import MappingProxyType

from executor_birth_retention import RetentionError
from install.birth_retention_files import _PrivateFiles


class _RotatedJournalOwner:
    journal_type = None

    @property
    def name(self):
        return self.journal_type.name

    def __init__(self, *, path: Path, require_exclusion, owner):
        self.current = self.journal_type(path=path, require_exclusion=require_exclusion, owner=owner)
        self.files = _PrivateFiles(root=path.parent, require_exclusion=require_exclusion,
                                   owner=owner, private_directory=False)
        self.require_exclusion = require_exclusion

    def _journal(self, path):
        name = self.current.path.name
        if path.parent != self.current.path.parent or not (
                path.name == name or re.fullmatch(re.escape(name) +
                    r"\.(?:[1-9][0-9]*|[0-9]+_[0-9a-f]{32})", path.name)):
            raise RetentionError("retention_inventory_incomplete", "unknown audit segment")
        return self.journal_type(path=path, require_exclusion=self.require_exclusion,
                               owner=self.current.owner)

    def _matches_name(self, name):
        return name == self.current.path.name or name.startswith(self.current.path.name + ".")

    def scan(self, *, recovery=False):
        try:
            with self.files._directory() as (parent, _):
                names = self.files._names(parent)
                entries = []
                total_bytes = 0
                segments = 0
                deadline = time.monotonic() + 15
                for name in names:
                    if self._matches_name(name):
                        segments += 1
                        total_bytes += os.stat(name, dir_fd=parent, follow_symlinks=False).st_size
                        if segments > 1000 or total_bytes > 128 * 1024 * 1024 or time.monotonic() > deadline:
                            raise RetentionError("retention_inventory_incomplete", "audit inventory budget")
                        journal = self._journal(self.current.path.with_name(name))
                        if recovery:
                            # Existing signed receipts must read the native source
                            # while the shared compactor's partial copy remains.
                            with journal._parent() as (directory, custody), journal._read(directory) as data:
                                info, lines, records = data
                                if info is not None:
                                    entries.extend(journal._entries(custody, info, lines, records))
                        else:
                            entries.extend(journal.scan())
                        if len(entries) > 100_000 or time.monotonic() > deadline:
                            raise RetentionError("retention_inventory_incomplete", "audit inventory budget")
                    elif not recovery and name.startswith(".metnos-f6-"):
                        raise RetentionError("retention_inventory_incomplete", "audit recovery pending")
                if names != self.files._names(parent):
                    raise RetentionError("retention_owner_changed", "audit segments changed")
                return tuple(entries)
        except FileNotFoundError:
            if self.current.path.parent.exists():
                raise RetentionError("retention_owner_changed", "audit segment disappeared") from None
            return ()

    @staticmethod
    def link_copies(objects):
        objects = tuple(objects)
        copies = {}
        for obj in objects:
            copies.setdefault(obj.identity.local_id, set()).add(obj.identity)
        if sum(len(group) * (len(group) - 1) for group in copies.values()) > 1_000_000:
            raise RetentionError("retention_inventory_incomplete", "audit copy-reference budget")
        return tuple(replace(obj, references=tuple(sorted(
            set(obj.references) | (copies[obj.identity.local_id] - {obj.identity}),
            key=lambda identity: identity.key.node_id))) for obj in objects)

    def inventory(self):
        return self.link_copies(entry.object for entry in self.scan())

    def _dispatch(self, identity):
        # Only canonical native sibling identities survive _identity below.
        prefix = self.current.path.parent.as_uri() + "/"
        if not identity.store.startswith(prefix):
            raise RetentionError("retention_owner_invalid", "foreign audit store")
        from urllib.parse import unquote
        journal = self._journal(self.current.path.parent / unquote(identity.store[len(prefix):]))
        journal._identity(identity)
        return journal

    def version(self, identity):
        return self._dispatch(identity).version(identity)

    def delete(self, identity, expected_version):
        self._dispatch(identity).delete(identity, expected_version)

    @property
    def owners(self):
        return MappingProxyType({self.name: self})

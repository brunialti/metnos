"""Native daily TurnLog gzip archives, retained and removed as whole segments.

Compression never proves closure. Every original record passes the live turn
owner's parser and closure rules. Cross-store references and duplicate turns
must still be joined by the full inventory; no standalone cleanup is exposed.
"""
from datetime import datetime, timedelta, timezone
import gzip
import io
import os
import time
import zlib

from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import JournalEntry, _MAX_BYTES
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso, _utc
from install.birth_retention_turns import _TurnJournal, _TurnLogOwner


class _TurnArchiveOwner(_PrivateFiles):
    name = "turn_archives"

    @staticmethod
    def _relative(value):
        if type(value) is not str:
            raise RetentionError("retention_owner_invalid", "turn archive identity")
        parts = value.split("/")
        if len(parts) != 3 or not parts[-1].endswith(".gz"):
            raise RetentionError("retention_inventory_incomplete", "unknown turn archive entry")
        daily = _TurnLogOwner._filename(parts[-1][:-3])
        if parts[0] != daily[:4] or parts[1] != daily[5:7]:
            raise RetentionError("retention_inventory_incomplete", "turn archive date layout")
        return tuple(parts)

    def identity(self, relative):
        self._relative(relative)
        return ObjectIdentity(self.name, self.root.as_uri(), "audit_segment", relative)

    def _identity(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError("retention_owner_invalid", "foreign turn archive identity")
        return self._relative(identity.local_id)

    def _read(self, directory, custody, relative):
        parts = self._relative(relative)
        observed = self._read_file(directory, custody, parts[-1], self.identity(relative),
                                   include_payload=True, max_bytes=_MAX_BYTES)
        if observed is None:
            return None
        identity, version, _, modified, compressed = observed
        if not compressed.startswith(b"\x1f\x8b"):
            raise RetentionError("retention_inventory_incomplete", "missing turn archive compression header")
        deadline, payload, total = time.monotonic() + 15, [], 0
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
                while block := stream.read(1024 * 1024):
                    total += len(block)
                    if total > _MAX_BYTES or time.monotonic() > deadline:
                        raise RetentionError("retention_inventory_incomplete", "expanded turn archive budget")
                    payload.append(block)
        except (OSError, EOFError, zlib.error) as exc:
            raise RetentionError("retention_inventory_incomplete", "invalid turn archive compression") from exc
        journal = _TurnJournal(path=self.root.joinpath(*parts),
                               require_exclusion=self.require_exclusion, owner=self.owner)
        lines, records = journal._decode(b"".join(payload))
        entries = journal._entries(custody, os.stat(parts[-1], dir_fd=directory, follow_symlinks=False),
                                   lines, records)
        roots = tuple(sorted({root for entry in entries for root in entry.object.roots},
                             key=lambda root: root.value))
        created = min([_iso(modified)] + [entry.object.created_at for entry in entries])
        eligible = None if roots else max([_iso(modified + timedelta(days=90))]
                                         + [entry.object.eligible_after for entry in entries])
        obj = OwnerObject(identity, version, NodeState.OPEN if roots else NodeState.CLOSED,
                          created, eligible, roots=roots)
        return JournalEntry(obj, records)

    def scan(self):
        deadline, result, count = time.monotonic() + 15, [], 0
        try:
            with self._directory() as (root, _):
                years = self._names(root)
                for year in years:
                    if len(year) != 4 or not year.isascii() or not year.isdigit() or year == "0000":
                        raise RetentionError("retention_inventory_incomplete", "unknown turn archive year")
                    with self._directory(year) as (year_dir, _):
                        months = self._names(year_dir)
                        for month in months:
                            if month not in {f"{number:02d}" for number in range(1, 13)}:
                                raise RetentionError("retention_inventory_incomplete", "unknown turn archive month")
                            with self._directory(year, month) as (directory, custody):
                                names = self._names(directory)
                                for name in names:
                                    entry = self._read(directory, custody, "/".join((year, month, name)))
                                    if entry is None:
                                        raise RetentionError("retention_owner_changed", "turn archive disappeared")
                                    count += max(1, len(entry.records))
                                    if count > 100_000 or time.monotonic() > deadline:
                                        raise RetentionError("retention_inventory_incomplete", "turn archive inventory budget")
                                    result.append(entry)
                                if names != self._names(directory):
                                    raise RetentionError("retention_owner_changed", "turn archive set changed")
                        if months != self._names(year_dir):
                            raise RetentionError("retention_owner_changed", "turn archive months changed")
                if years != self._names(root):
                    raise RetentionError("retention_owner_changed", "turn archive years changed")
            return tuple(result)
        except FileNotFoundError:
            return ()

    def version(self, identity):
        parts = self._identity(identity)
        with self._directory(*parts[:-1]) as (directory, custody):
            entry = self._read(directory, custody, identity.local_id)
            if entry is None:
                os.fsync(directory)
                return None
            return entry.object.version

    def delete(self, identity, expected_version):
        parts = self._identity(identity)
        with self._directory(*parts[:-1]) as (directory, custody):
            entry = self._read(directory, custody, identity.local_id)
            if entry is None:
                os.fsync(directory)
                return
            obj = entry.object
            if obj.version != expected_version:
                raise RetentionError("retention_owner_changed", "turn archive version")
            if obj.state is not NodeState.CLOSED or obj.roots or _utc(obj.eligible_after) >= datetime.now(timezone.utc):
                raise RetentionError("retention_owner_state_invalid", "turn archive still needed")
            self.require_exclusion()
            # Exclusion guards native writers; repeat file custody/version
            # after parsing so a replacement cannot be unlinked by pathname.
            observed = self._read_file(directory, custody, parts[-1], identity, max_bytes=_MAX_BYTES)
            if observed is None or observed[1] != expected_version:
                raise RetentionError("retention_owner_changed", "turn archive replaced")
            os.unlink(parts[-1], dir_fd=directory)
            os.fsync(directory)

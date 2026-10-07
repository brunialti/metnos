"""Promoter's native <proposal-id>/<source>.json archive and source union.

Moving a synthesis record does not close promotion or erase intent witnesses.
The union projects all physical source copies and intents once, so callers must
use it in place of the standalone source owner when composing this subgraph.
"""
from pathlib import Path
import time
from types import MappingProxyType

from executor_birth_retention import RetentionError
from install.birth_retention_maintenance import ObjectIdentity
from install.birth_retention_synth import _SynthProposalOwner


class _SynthArchiveOwner(_SynthProposalOwner):
    name = 'synth_archive_files'

    @staticmethod
    def _component(value):
        return bool(value) and Path(value).name == value and value not in {'.', '..'}

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        if (len(parts) != 2 or not all(self._component(p) for p in parts)
                or parts[1] != parts[0] + '.json'):
            raise RetentionError('retention_owner_invalid', 'promoter synthesis archive identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'proposal', local_id)

    def scan(self):
        result, total, count, deadline = {}, 0, 0, time.monotonic() + 15
        def budget():
            if total > 128 << 20 or count > 100_000 or time.monotonic() > deadline:
                raise RetentionError('retention_inventory_incomplete', 'synthesis archive budget')
        try:
            with self._directory() as (root, _):
                names = self._names(root)
                for name in names:
                    count += 1; budget()
                    if not self._component(name):
                        raise RetentionError('retention_owner_invalid', 'synthesis archive directory')
                    with self._directory(name) as (directory, custody):
                        children = self._names(directory)
                        for child in children:
                            count += 1; budget()
                            identity = self.identity(name + '/' + child)
                            entry = self._read(directory, custody, identity)
                            if entry is None:
                                raise RetentionError('retention_owner_changed', 'synthesis archive disappeared')
                            result[identity] = entry
                            total += entry[2]; budget()
                        if children != self._names(directory):
                            raise RetentionError('retention_owner_changed', 'synthesis archive directory changed')
                if names != self._names(root):
                    raise RetentionError('retention_owner_changed', 'synthesis archive namespace changed')
        except FileNotFoundError:
            if self.root.exists() or result:
                raise RetentionError('retention_owner_changed', 'synthesis archive disappeared') from None
        return result


class _SynthSourcesInventory:
    """Complete source/intent projection accepted by the promoter inventory."""
    def __init__(self, *, synth, archive):
        if (synth.name != 'synth_proposal_files' or archive.name != 'synth_archive_files'
                or synth.intents is not archive.intents):
            raise RetentionError('retention_owner_invalid', 'synthesis archive source owners')
        self.synth, self.archive = synth, archive
        self.name = synth.name
        self.require_exclusion = synth.require_exclusion
        self.proposal_owner_names = frozenset({synth.name, archive.name})
        self.owners = MappingProxyType({**synth.owners, archive.name: archive})

    def inventory(self):
        self.require_exclusion()
        originals, archived = self.synth.scan(), self.archive.scan()
        rows = self.synth.intents.scan()
        objects = self.synth._project({**originals, **archived}, rows)
        if (originals != self.synth.scan() or archived != self.archive.scan()
                or rows != self.synth.intents.scan()):
            raise RetentionError('retention_owner_changed', 'synthesis archive references changed')
        self.require_exclusion()
        return objects

"""Read-only LRE source grants and the native lane snapshot namespace.

Neither grant expiry nor an abandoned snapshot proves workload closure. These
owners keep every native record and file open; only SourceAuthority's normal
lifecycle may revoke/prune grants or remove its session files.
"""
from pathlib import Path
import time
from types import MappingProxyType

from durable_workloads import source_authority as native
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _SQLiteOwner, RowState, _iso


def _schema(connection):
    connection.executescript(native._SCHEMA_SQL_V3)
    connection.execute(native._CHECK_INDEX_SQL)


def _grant_state(row):
    try:
        native.SourceAuthority._scope(row['owner_user_id'], row['workload_id'])
        native._source_facts(row)
        locator = row['locator_bytes']
        if type(locator) is not bytes or not 1 <= len(locator) <= native._MAX_LOCATOR_BYTES:
            raise ValueError('source locator')
        granted = native.parse_instant(row['granted_at'], name='granted_at')
        expires = native.parse_instant(row['expires_at'], name='expires_at')
        native.parse_instant(row['checked_at'], name='checked_at')
        if not granted < expires <= granted + native._MAX_AUTHORITY_LIFETIME:
            raise ValueError('source grant lifetime')
        generation = row['check_generation']
        if type(generation) is not int or generation < 0:
            raise ValueError('source check generation')
        if row['revoked_at'] is not None:
            native.parse_instant(row['revoked_at'], name='revoked_at')
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise RetentionError('retention_owner_state_invalid', 'native source grant') from exc
    return RowState(NodeState.OPEN, _iso(granted), None, (RootKind.OPEN_AUDIT,))


class _SourceGrantOwner(_SQLiteOwner):
    def __init__(self, *, path, require_exclusion, owner):
        super().__init__(name='lre_source_grants', path=path, schema=_schema,
            table='source_grants', primary_key=('owner_user_id', 'workload_id', 'source_id'),
            node_type='audit_segment', state=_grant_state,
            require_exclusion=require_exclusion, owner=owner)

    def _files(self, *, allow_absent=False):
        files = super()._files(allow_absent=allow_absent)
        if files is not None and files[2] != 0o600:
            raise RetentionError('retention_owner_path_invalid', 'private source authority database')
        return files

    def _validate_store(self, connection):
        rows = connection.execute(
            'SELECT singleton, version FROM source_authority_schema').fetchmany(2)
        if len(rows) != 1 or tuple(rows[0]) != (1, native.SOURCE_AUTHORITY_SCHEMA_VERSION):
            raise RetentionError('retention_owner_state_invalid', 'native source authority version')

    def delete(self, identity, expected_version):
        raise RetentionError('retention_owner_state_invalid', 'source grant lacks native closure')


class _SourceSnapshots(_PrivateFiles):
    name = 'lre_source_snapshots'

    def __init__(self, *, root, require_exclusion, owner):
        super().__init__(root=root, require_exclusion=require_exclusion, owner=owner,
            file_modes=frozenset({0o400}))
        # Native lease.lock files are empty and writable; immutable copies are
        # mode 0400. Separate readers enforce each producer's exact mode.
        self._leases = _PrivateFiles(root=root, require_exclusion=require_exclusion,
            owner=owner, file_modes=frozenset({0o600}))

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        try:
            if len(parts) != 2 or not native._SESSION_RE.fullmatch(parts[0]):
                raise ValueError('source snapshot session')
            if parts[1] != 'lease.lock':
                selected = Path(parts[1])
                native._source_id(selected.stem)
                if (not native._SUFFIX_RE.fullmatch(selected.suffix)
                        or parts[1] != parts[1].lower()):
                    raise ValueError('source snapshot suffix')
        except (ValueError, TypeError) as exc:
            raise RetentionError('retention_owner_invalid', 'native source snapshot identity') from exc
        return ObjectIdentity(self.name, self.root.as_uri(), 'blob', local_id)

    def _parts(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign source snapshot')
        return identity.local_id.split('/')

    def _read(self, directory, custody, identity):
        name = self._parts(identity)[1]
        lease = name == 'lease.lock'
        reader = self._leases if lease else self
        raw = reader._read_file(directory, custody, name, identity,
            expected_size=0 if lease else None)
        if raw is None:
            return None
        return (OwnerObject(identity, raw[1], NodeState.OPEN, _iso(raw[3]), None,
            roots=(RootKind.OPEN_REVISION,)), raw[2])

    def scan(self):
        files, count, total, deadline = {}, 0, 0, time.monotonic() + 15
        try:
            with self._directory() as (root, _root_custody):
                sessions = self._names(root)
                for session in sessions:
                    count += 1
                    if count > 100_000 or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'source snapshot budget')
                    if not native._SESSION_RE.fullmatch(session):
                        raise RetentionError('retention_owner_invalid', 'unknown source snapshot session')
                    with self._directory(session) as (directory, custody):
                        names = self._names(directory)
                        for name in names:
                            identity = self.identity(session + '/' + name)
                            entry = self._read(directory, custody, identity)
                            if entry is None:
                                raise RetentionError('retention_owner_changed', 'source snapshot disappeared')
                            files[identity] = entry
                            total += entry[1]
                            count += 1
                            if count > 100_000 or total > 128 << 20 or time.monotonic() > deadline:
                                raise RetentionError('retention_inventory_incomplete', 'source snapshot budget')
                        if self._names(directory) != names:
                            raise RetentionError('retention_owner_changed', 'source snapshot files changed')
                if self._names(root) != sessions:
                    raise RetentionError('retention_owner_changed', 'source snapshot sessions changed')
        except FileNotFoundError:
            if self.root.exists() or files:
                raise RetentionError('retention_owner_changed', 'source snapshot root disappeared') from None
        return files

    def version(self, identity):
        session, _name = self._parts(identity)
        try:
            with self._directory(session) as (directory, custody):
                entry = self._read(directory, custody, identity)
                return None if entry is None else entry[0].version
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        self._parts(identity)
        raise RetentionError('retention_owner_state_invalid', 'source snapshot lacks native closure')


class _SourceAuthorityInventory:
    def __init__(self, *, grants, snapshots):
        if (type(grants) is not _SourceGrantOwner or type(snapshots) is not _SourceSnapshots
                or grants.require_exclusion is not snapshots.require_exclusion
                or grants.owner != snapshots.owner):
            raise RetentionError('retention_owner_invalid', 'native source authority owners')
        self.grants, self.snapshots = grants, snapshots
        self.owners = MappingProxyType({owner.name: owner for owner in (grants, snapshots)})

    def inventory(self):
        self.grants.require_exclusion()
        rows, files = self.grants.scan(), self.snapshots.scan()
        objects = {identity: entry[0] for identity, entry in files.items()}
        for row in rows:
            state = self.grants.state_of(row)
            objects[row.identity] = OwnerObject(row.identity, row.version, state.state,
                state.created_at, state.eligible_after, roots=state.roots)
        if self.grants.scan() != rows or self.snapshots.scan() != files:
            raise RetentionError('retention_owner_changed', 'source authority inventory changed')
        self.grants.require_exclusion()
        return tuple(sorted(objects.values(), key=lambda obj: obj.identity.key.node_id))

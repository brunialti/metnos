"""Private, read-only reconciliation of native objects with observed files.

PATH_KEYS is an input boundary, never a claim of coverage. Every regular file
inside those roots must be accounted for by a reader that actually inventoried
the native store. A metadata walk brackets those reads and rejects links,
unknown files and namespace drift. Nothing here creates or repairs a store.

Birth snapshots are checked only if physically inside an observed root. The
native snapshot writer may use an arbitrary private_parent (normally /tmp);
this helper makes no claim about that unobserved namespace.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import time
from types import MappingProxyType

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject, _digest
from install.birth_retention_path_probe import PATH_KEYS
from install.birth_retention_sqlite import _iso

_MAX_ENTRIES = 100_000
_MAX_DEPTH = 32
_MAX_PRIVATE_BYTES = 128 << 20
_SECONDS = 15


def _invalid(detail, *, changed=False):
    raise RetentionError('retention_owner_changed' if changed else
                         'retention_inventory_incomplete', detail)


def _absolute(value):
    path = Path(value)
    if (not path.is_absolute() or path == Path(path.anchor)
            or '..' in path.parts or '\0' in str(path)
            or str(path) != str(value)):
        _invalid('physical census absolute path')
    return path


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _custody(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


@dataclass(frozen=True)
class _PhysicalSnapshot:
    entries: object
    anchors: object
    absent: tuple


def _physical_snapshot_v1(roots, *, owner, require_exclusion):
    """Observe real nodes with descriptor-relative, no-follow traversal.

    Absence is accepted only after ENOENT under an opened, checked parent.
    Ancestor custody is recorded without its unrelated directory timestamps.
    The governed directories themselves include names and full metadata.
    """
    require_exclusion()
    entries, anchors, absent = {}, {}, []
    deadline = time.monotonic() + _SECONDS

    def budget():
        if len(entries) > _MAX_ENTRIES or time.monotonic() > deadline:
            _invalid('physical census budget')

    def trusted(info, path, *, ancestor=False):
        if ((info.st_uid not in {0, owner[0]})
                or info.st_gid not in {0, owner[1]}
                or info.st_mode & 0o022):
            # /tmp may be a trusted traversal ancestor, never a governed root.
            sticky_parent = (ancestor and stat.S_ISDIR(info.st_mode)
                             and info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            if not sticky_parent:
                _invalid('physical census custody: ' + str(path))

    def walk(parent, name, path, depth):
        budget()
        if depth > _MAX_DEPTH:
            _invalid('physical census depth')
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            trusted(info, path)
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=parent)
            try:
                if _metadata(os.fstat(descriptor)) != _metadata(info):
                    _invalid('physical census directory replaced', changed=True)
                names = tuple(sorted(os.listdir(descriptor)))
                entries[path] = ('directory', _metadata(info), names)
                for child in names:
                    walk(descriptor, child, path / child, depth + 1)
                if (tuple(sorted(os.listdir(descriptor))) != names
                        or _metadata(os.fstat(descriptor)) != _metadata(info)
                        or _metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
                           != _metadata(info)):
                    _invalid('physical census directory drift', changed=True)
            finally:
                os.close(descriptor)
        elif stat.S_ISREG(info.st_mode):
            trusted(info, path)
            if info.st_nlink != 1:
                _invalid('physical census hardlink: ' + str(path))
            descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                                 dir_fd=parent)
            try:
                if (_metadata(os.fstat(descriptor)) != _metadata(info)
                        or _metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
                           != _metadata(info)):
                    _invalid('physical census file replaced', changed=True)
                entries[path] = ('file', _metadata(info), ())
            finally:
                os.close(descriptor)
        else:
            _invalid('physical census link or special node: ' + str(path))

    for root in sorted({_absolute(path) for path in roots}, key=str):
        # A containing observed directory already walks this exact namespace.
        if root in entries:
            continue
        descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = Path(root.anchor)
        try:
            for index, part in enumerate(root.parts[1:]):
                budget()
                anchors[parent] = _custody(os.fstat(descriptor))
                try:
                    if index == len(root.parts) - 2:
                        # Only this initial ENOENT proves an optional root
                        # absent. A later ENOENT is drift in an observed tree.
                        os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                        try:
                            walk(descriptor, part, root, 0)
                        except FileNotFoundError:
                            _invalid('physical census node disappeared', changed=True)
                        break
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=descriptor)
                except FileNotFoundError:
                    absent.append((root, parent, tuple(root.parts[index + 1:])))
                    break
                info = os.fstat(child)
                try:
                    trusted(info, parent / part, ancestor=True)
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor, parent = child, parent / part
        except RetentionError:
            raise
        except OSError as exc:
            _invalid('physical census unreadable node: ' + str(root))
        finally:
            os.close(descriptor)
    require_exclusion()
    return _PhysicalSnapshot(MappingProxyType(entries), MappingProxyType(anchors),
                             tuple(sorted(absent, key=lambda item: str(item[0]))))


class _AlwaysOpen:
    def delete(self, identity, expected_version):
        raise RetentionError('retention_owner_state_invalid', 'protected native namespace')


from install.birth_retention_introvertiva import _IntrovertivaOwner
from install.birth_retention_synth import _SynthProposalOwner


class _OpenSynthHistoryOwner(_AlwaysOpen, _SynthProposalOwner):
    name = 'synth_history_files'

    def inventory(self):
        # The native projection also contains the shared intent rows. They
        # already belong to the main composition and must not be duplicated.
        return tuple(replace(obj, state=NodeState.OPEN, eligible_after=None,
                             roots=(RootKind.OPEN_REVISION,))
                     for obj in super().inventory() if obj.identity.owner == self.name)


class _OpenAuditRootOwner(_AlwaysOpen, _IntrovertivaOwner):
    name = 'native_audit_root'

    def inventory(self):
        return tuple(replace(obj, state=NodeState.OPEN, eligible_after=None,
                             roots=(RootKind.OPEN_AUDIT,))
                     for obj in super().inventory())


class _ProtectedUndoKeyOwner(_AlwaysOpen):
    name = 'protected_undo_key'

    def __init__(self, native):
        self.native, self.path = native, native.key_path

    def identity(self):
        return ObjectIdentity(self.name, self.path.as_uri(), 'evidence', 'admin.key')

    def _read(self):
        try:
            with self.native.key_files._directory() as (directory, custody):
                observed = self.native.key_files._read_file(
                    directory, custody, self.path.name, None, max_bytes=4096)
        except FileNotFoundError:
            return None
        if observed is None:
            return None
        value = self.native._key()
        if value[0] != observed[1]:
            _invalid('protected undo key changed', changed=True)
        return observed

    def inventory(self):
        # The native key reader verifies the actual format and custody and
        # returns its physical version, without generating a missing key.
        value = self._read()
        if value is None:
            return ()
        return (OwnerObject(self.identity(), value[1], NodeState.OPEN,
                            _iso(value[3]), None,
                            roots=(RootKind.OPEN_AUDIT,)),)

    def version(self, identity):
        if identity != self.identity():
            _invalid('foreign protected undo key')
        value = self._read()
        return None if value is None else value[1]


class _BirthPrivateOwner(_AlwaysOpen):
    name = 'birth_private_namespace'

    def __init__(self, *, session, require_exclusion):
        from executor_birth_secure_fs import _SecureRootSession
        if type(session) is not _SecureRootSession:
            _invalid('physical census native Birth session')
        self.session, self.root = session, Path(session._root_path)
        self.require_exclusion = require_exclusion

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        if not parts or any(part in {'', '.', '..'} for part in parts):
            _invalid('physical census Birth identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'evidence', local_id)

    def _read(self, parts):
        from executor_birth_secure_fs import _ObjectKind
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        binding = self.session._resolve_effective_role_binding_v1(parts).binding
        if binding.kind is not _ObjectKind.regular_file:
            _invalid('physical census Birth kind')
        path = self.root.joinpath(*parts)
        before = path.lstat()
        payload = self.session.read_file(parts, maximum=8 << 20, role=binding.role)
        if _metadata(before) != _metadata(path.lstat()):
            _invalid('physical census Birth file drift', changed=True)
        return (_digest({'file': _metadata(before), 'role': binding.role.value,
                         'content': hashlib.sha256(payload).hexdigest()}), before, len(payload))

    def inventory(self):
        from executor_birth_secure_fs import _ObjectKind
        result, total = [], 0

        def visit(parts):
            nonlocal total
            names = self.session.inventory(parts)
            for name in names:
                child = (*parts, name)
                binding = self.session._resolve_effective_role_binding_v1(child).binding
                if binding.kind is _ObjectKind.directory:
                    # The effective writer catalogue decides the profile; a
                    # caller-selected role or a filename enum is insufficient.
                    self.session.open_directory(child, role=binding.role)
                    visit(child)
                elif binding.kind is _ObjectKind.regular_file:
                    version, info, size = self._read(child)
                    total += size
                    if total > _MAX_PRIVATE_BYTES or len(result) >= _MAX_ENTRIES:
                        _invalid('physical census Birth budget')
                    result.append(OwnerObject(self.identity('/'.join(child)), version,
                        NodeState.OPEN, _iso(datetime.fromtimestamp(info.st_mtime, timezone.utc)), None,
                        roots=(RootKind.OPEN_AUDIT,)))
                else:
                    _invalid('physical census Birth node kind')
            if names != self.session.inventory(parts):
                _invalid('physical census Birth namespace drift', changed=True)

        visit(())
        return tuple(result)

    def version(self, identity):
        if identity != self.identity(identity.local_id):
            _invalid('foreign private Birth object')
        try:
            return self._read(tuple(identity.local_id.split('/')))[0]
        except FileNotFoundError:
            return None


class _BirthSnapshotOwner(_AlwaysOpen):
    name = 'birth_private_snapshots'

    def __init__(self, *, roots, require_exclusion):
        self.roots, self.require_exclusion = tuple(roots), require_exclusion
        self._observed = ()

    def identity(self, root):
        root = _absolute(root)
        if (not root.name.startswith('metnos-birth-')
                or not any(root.is_relative_to(parent) for parent in self.roots)):
            _invalid('foreign Birth snapshot root')
        return ObjectIdentity(self.name, root.as_uri(), 'evidence', 'snapshot')

    def _read(self, root):
        from executor_birth_snapshot import (
            _check_closed_tree, _declared_code_files, _expected_entries,
            _read_regular, _tree_state,
        )
        self.require_exclusion()
        first = _tree_state(root)
        manifest = _read_regular(root, 'manifest.toml')
        expected = _expected_entries(_declared_code_files(manifest))
        _check_closed_tree(first, expected)
        payloads, total = {}, 0
        for relative, kind in expected.items():
            if kind != 'file':
                continue
            raw = _read_regular(root, relative)
            total += len(raw)
            if total > _MAX_PRIVATE_BYTES:
                _invalid('physical census Birth snapshot bytes')
            payloads[relative] = hashlib.sha256(raw).hexdigest()
        if first != _tree_state(root):
            _invalid('physical census Birth snapshot drift', changed=True)
        return _digest({'tree': first, 'payloads': payloads})

    def inventory(self):
        return tuple(OwnerObject(self.identity(root), self._read(root), NodeState.OPEN,
                     _iso(datetime.fromtimestamp(root.stat().st_mtime, timezone.utc)),
                     None, roots=(RootKind.OPEN_REVISION,))
                     for root in self._observed)

    def version(self, identity):
        from urllib.parse import unquote, urlsplit
        parsed = urlsplit(identity.store)
        root = Path(unquote(parsed.path))
        if identity != self.identity(root):
            _invalid('foreign Birth snapshot identity')
        try:
            return self._read(root)
        except FileNotFoundError:
            return None


def _object_files(owner, obj):
    """Translate a projected native identity using its own physical decoder."""
    from install.birth_retention_artifacts import _ArtifactBlobOwner
    from install.birth_retention_artifact_auxiliary import _ArtifactAuxiliaryOwner
    from install.birth_retention_files import _PrivateFiles
    from install.birth_retention_jsonl import _JsonlOwner
    from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
    from install.birth_retention_protected_undo import _ProtectedUndoOwner
    from install.birth_retention_sqlite import _SQLiteOwner
    from install.birth_retention_synth_candidates import _PersistentSynthOwner
    from install.birth_retention_telos import _TelosProposalOwner
    from install.birth_retention_turns import _TurnLogOwner
    from install.birth_retention_workspaces import _WorkspaceOwner
    identity = obj.identity
    if isinstance(owner, (_SQLiteOwner, _JsonlOwner)):
        return (owner.path,), ()
    if isinstance(owner, _RotatedJournalOwner):
        return (owner._dispatch(identity).path,), ()
    if isinstance(owner, _TurnLogOwner):
        return (owner._owner_for(identity).path,), ()
    if isinstance(owner, _ProtectedUndoOwner):
        return (owner.root / (owner._handle(identity) + '.age'),), ()
    if isinstance(owner, _ArtifactBlobOwner):
        namespace, name = owner._parts(identity)
        return (owner.root / 'owners' / namespace / 'blobs' / 'sha256' / name,), ()
    if isinstance(owner, _ArtifactAuxiliaryOwner):
        namespace, container, name = owner._parts(identity)
        return (owner.blobs.root / 'owners' / namespace / owner.kind / container / name,), ()
    if isinstance(owner, _WorkspaceOwner):
        workspace, relative, kind = owner._parts(identity)
        return (() if kind == 'directory' else (workspace.parent / relative,)), ()
    if isinstance(owner, _PersistentSynthOwner):
        if identity != owner.identity(identity.local_id):
            _invalid('foreign persistent Synth projection')
        label, name = identity.local_id.split('/')
        # Its native _tree/_validate read closes the entire candidate tree.
        return (), (owner.stores[label].root / name,)
    if isinstance(owner, _TelosProposalOwner):
        return (owner._journal(identity).path,), ()
    if isinstance(owner, (_BirthPrivateOwner,)):
        if identity != owner.identity(identity.local_id):
            _invalid('foreign Birth private projection')
        return (owner.root / identity.local_id,), ()
    if isinstance(owner, _ProtectedUndoKeyOwner):
        if identity != owner.identity():
            _invalid('foreign protected key projection')
        return (owner.path,), ()
    if isinstance(owner, _BirthSnapshotOwner):
        from urllib.parse import unquote, urlsplit
        root = Path(unquote(urlsplit(identity.store).path))
        if identity != owner.identity(root):
            _invalid('foreign Birth snapshot projection')
        return (), (root,)
    if isinstance(owner, _PrivateFiles):
        # These native owners project a relative file, unlike the explicitly
        # decoded artifact/workspace/candidate identities above. The actual
        # inventory already validated its identity, bytes and physical version.
        parts = identity.local_id.split('/')
        if (identity.store != owner.root.as_uri()
                or any(part in {'', '.', '..'} for part in parts)):
            _invalid('physical census native relative file')
        return (owner.root.joinpath(*parts),), ()
    _invalid('physical census unsupported native owner: ' + owner.name)


def _empty_native_files(owner, entries):
    """Include empty journals/databases that produce no logical object.

    This runs only after that exact owner's inventory has read the store. The
    native journal dispatchers supply the grammar, including dynamic segments.
    """
    from install.birth_retention_jsonl import _JsonlOwner
    from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
    from install.birth_retention_sqlite import _SQLiteOwner
    from install.birth_retention_telos import _TelosProposalOwner
    from install.birth_retention_turns import _TurnLogOwner
    if isinstance(owner, _SQLiteOwner):
        return {Path(str(owner.path) + suffix) for suffix in ('', '-wal', '-shm', '-journal')
                if Path(str(owner.path) + suffix) in entries}
    if isinstance(owner, _JsonlOwner):
        return {owner.path} if owner.path in entries else set()
    if isinstance(owner, _RotatedJournalOwner):
        return {owner._journal(path).path for path, item in entries.items()
                if item[0] == 'file' and path.parent == owner.current.path.parent
                and owner._matches_name(path.name)}
    if isinstance(owner, _TurnLogOwner):
        return {owner._journal(path.name).path for path, item in entries.items()
                if item[0] == 'file' and path.parent == owner.root}
    if isinstance(owner, _TelosProposalOwner):
        return {journal.path for journal in owner.journals if journal.path in entries}
    return set()


class _PhysicalCensusV1:
    """Inventory wrapper; it grants neither deletion nor installation authority."""

    def __init__(self, *, native_inventory, paths, account_owner, birth_root,
                 session, require_exclusion):
        from install.birth_retention_inventory import _InventoryComposition
        from install.birth_retention_protected_undo import _ProtectedUndoOwner
        from install.birth_retention_synth_candidates import _PersistentSynthOwner
        if (type(native_inventory) is not _InventoryComposition or set(paths) != PATH_KEYS
                or native_inventory.require_exclusion != require_exclusion
                or type(account_owner) is not tuple or len(account_owner) != 2
                or any(type(value) is not int or value < 0 for value in account_owner)):
            _invalid('physical census native inputs')
        self.paths = MappingProxyType({key: _absolute(value) for key, value in paths.items()})
        self.native, self.account_owner = native_inventory, account_owner
        self.require_exclusion = require_exclusion
        self._observed_objects = None
        self._observed_components = None
        self._observed_owner_locations = None
        if Path(birth_root) != Path(session._root_path):
            _invalid('physical census Birth root binding')
        self.extra = [_BirthPrivateOwner(session=session, require_exclusion=require_exclusion)]
        owners = dict(native_inventory.owners)
        intents = owners.get('change_intents')
        if self.paths['synth_history'] != self.paths['promoter_proposals']:
            if intents is None:
                _invalid('physical census history native intent owner')
            self.extra.append(_OpenSynthHistoryOwner(root=self.paths['synth_history'], intents=intents))
        if self.paths['PATH_AUDIT'] != self.paths['introvertiva']:
            self.extra.append(_OpenAuditRootOwner(root=self.paths['PATH_AUDIT'],
                require_exclusion=require_exclusion, owner=account_owner))
        for owner in tuple(owners.values()):
            if isinstance(owner, _ProtectedUndoOwner):
                self.extra.append(_ProtectedUndoKeyOwner(owner))
        roots = set(self.paths.values()) | {Path(birth_root)}
        # Native owners may include administrative reviews, archive siblings or
        # installed stores outside the common user directories.
        for owner in tuple(owners.values()):
            for attribute in ('root', 'path'):
                value = getattr(owner, attribute, None)
                if isinstance(value, Path):
                    roots.add(_absolute(value))
            if isinstance(owner, _PersistentSynthOwner):
                roots.update(store.root for store in owner.stores.values())
        self.roots = tuple(sorted(roots, key=str))
        snapshots = _BirthSnapshotOwner(roots=self.roots, require_exclusion=require_exclusion)
        self.extra.append(snapshots)
        self.snapshots = snapshots
        for owner in self.extra:
            if owner.name in owners:
                _invalid('physical census duplicate residual owner')
            owners[owner.name] = owner
        self.owners = MappingProxyType(owners)

    def inventory(self, *, context=None):
        # A failed second observation must not leave the first one usable as
        # evidence for a new plan.
        self._observed_objects = None
        self._observed_components = None
        self._observed_owner_locations = None
        self.require_exclusion()
        self.native._check_owners()
        first = _physical_snapshot_v1(self.roots, owner=self.account_owner,
                                      require_exclusion=self.require_exclusion)
        self.snapshots._observed = tuple(path for path, item in first.entries.items()
            if item[0] == 'directory' and path.name.startswith('metnos-birth-')
            and not any(parent.name.startswith('metnos-birth-') for parent in path.parents))
        if context is None:
            result = self.native.inventory()
        else:
            result = self.native._inventory_in_exclusion_v1(context)
        result = tuple(result) + tuple(obj for owner in self.extra for obj in owner.inventory())
        files, trees = set(), set()
        for obj in result:
            if not isinstance(obj, OwnerObject) or obj.identity.owner not in self.owners:
                _invalid('physical census foreign object')
            local_files, local_trees = _object_files(self.owners[obj.identity.owner], obj)
            files.update(local_files)
            trees.update(local_trees)
        for owner in self.owners.values():
            files.update(_empty_native_files(owner, first.entries))
        if (any(path not in first.entries or first.entries[path][0] != 'file' for path in files)
                or any(root not in first.entries or first.entries[root][0] != 'directory'
                       for root in trees)):
            _invalid('unobserved native physical object')
        for path, item in first.entries.items():
            if (item[0] == 'file' and path not in files
                    and not any(path.is_relative_to(root) for root in trees)):
                _invalid('unaccounted physical file: ' + str(path))
        second = _physical_snapshot_v1(self.roots, owner=self.account_owner,
                                       require_exclusion=self.require_exclusion)
        if first != second:
            _invalid('physical census changed during native inventory', changed=True)
        self.native._check_owners()
        self.require_exclusion()
        identities = {obj.identity for obj in result}
        if len(identities) != len(result) or any(
                reference not in identities for obj in result for reference in obj.references):
            _invalid('physical census residual projection')
        result = tuple(sorted(result, key=lambda obj: obj.identity.key.node_id))
        self._observed_components = tuple(self.native.components)
        self._observed_owner_locations = self._owner_locations()
        self._observed_objects = result
        return result

    def _owner_locations(self):
        return tuple((name, owner, getattr(owner, 'path', None),
                      getattr(owner, 'root', None))
                     for name, owner in sorted(self.owners.items()))

    def observed_roots(self, objects, *, holds_root, holds, root_owned=True,
                       observed_at=None):
        """Prove root discovery from this completed native/physical census.

        Optional stores may be absent: inventory() already proved that absence
        under their checked parents. Classes and shared instances below name
        the readers that actually checked each native schema and relationship,
        including an empty store; neither PATH_KEYS nor an owner-name set proves
        that a root was observed. This performs no second application scan.
        """
        from contract_store import STORE_RELATIVE
        from install.birth_retention_admission_links import _ApprovalAdmissionInventory
        from install.birth_retention_birth_sqlite import _BirthBundleOwner, _review_state
        from install.birth_retention_change_rollback import _ChangeRollbackInventory, _ChangeRollbackBlobs
        from install.birth_retention_epoch_inventory import _EpochInventory
        from install.birth_retention_epoch_sqlite import _EpochOwner, _EpochLegacyOwner
        from install.birth_retention_feedback import _FeedbackOwner
        from install.birth_retention_history import _HistoryBackupOwner
        from install.birth_retention_jobs_sqlite import _WorkloadOwner, _schedule_state, _run_state
        from install.birth_retention_journals import _JournalAdmissionInventory, _JournalInventory
        from install.birth_retention_lre_inventory import _LreInventory
        from install.birth_retention_maintenance import read_holds
        from install.birth_retention_native_inventory import _RowsInventory, _OwnerInventory
        from install.birth_retention_producer_links import _ProducerAdmissionInventory
        from install.birth_retention_promoter import _PromoterInventory, _PromoterOwner, _PromoterBlobs
        from install.birth_retention_protected_undo import _ProtectedUndoOwner
        from install.birth_retention_review_links import _ReviewApprovalInventory
        from install.birth_retention_reviews import _ReviewOwner
        from install.birth_retention_semantic_inventory import _SemanticEvidenceInventory
        from install.birth_retention_signed_store import _SignedStoreOwner
        from install.birth_retention_source_authority import (
            _SourceAuthorityInventory, _SourceGrantOwner, _SourceSnapshots,
        )
        from install.birth_retention_sqlite import (
            _SQLiteOwner, _utc, _proposal_state, _intent_state, _approval_state,
        )
        from install.birth_retention_synth import _SynthProposalOwner
        from install.birth_retention_synth_archive import _SynthArchiveOwner, _SynthSourcesInventory
        from install.birth_retention_synth_candidates import _PersistentSynthOwner
        from install.birth_retention_turn_archives import _TurnArchiveOwner
        from install.birth_retention_turns import _TurnLogOwner
        from install.birth_retention_undo import _UndoOwner

        self.require_exclusion()
        self.native._check_owners()
        if (self._observed_objects is None or type(objects) is not tuple
                or objects != self._observed_objects
                or tuple(self.native.components) != self._observed_components
                or self._owner_locations() != self._observed_owner_locations):
            _invalid('root proof requires the completed unchanged census')
        p, owners = self.paths, self.native.owners
        roots = set()

        def component(kind):
            found = tuple(item for item in self.native.components if type(item) is kind)
            if len(found) != 1:
                _invalid('native root reader absent or ambiguous: ' + kind.__name__)
            return found[0]

        def owner(name, kind, *, path=None, root=None):
            value = owners.get(name)
            if (type(value) is not kind or value.name != name
                    or value.require_exclusion != self.require_exclusion
                    or (path is not None and value.path != path)
                    or (root is not None and value.root != root)):
                _invalid('native root owner binding: ' + name)
            return value

        def shared(value, **expected):
            if any(getattr(value, name, None) is not target for name, target in expected.items()):
                _invalid('native root relationship: ' + type(value).__name__)

        def rows(name, path, table, key, state):
            value = owner(name, _SQLiteOwner, path=path)
            if (value.table != table or value.primary_keys != (key,)
                    or value.state is not state or not value.expected_schema
                    or not any(type(item) is _RowsInventory and item.owner is value
                               for item in self.native.components)):
                _invalid('native root SQLite projection: ' + name)
            return value

        birth = p['PATH_USER_STATE'] / 'birth'
        private = next(item for item in self.extra if type(item) is _BirthPrivateOwner)
        private.session._require_exclusive_global_lock()
        signed = owner('signed_contract_history', _SignedStoreOwner,
                       root=p['PATH_USER_STATE'] / STORE_RELATIVE)
        producers = owner('birth_producer_receipts', _BirthBundleOwner,
                          path=birth / 'producer_receipts.sqlite')
        approvals = owner('birth_approvals', _BirthBundleOwner, path=birth / 'approvals.sqlite')
        if (producers.table != 'birth_producer_receipts'
                or producers.child != 'birth_producer_issuance'
                or approvals.table != 'birth_approvals'
                or approvals.child != 'birth_approval_consumptions'):
            _invalid('native Birth relationship schema')
        producer_links = component(_ProducerAdmissionInventory)
        shared(producer_links, producers=producers, signed_store=signed, session=private.session)
        approval_links = component(_ApprovalAdmissionInventory)
        links = approval_links.review_links
        if type(links) is not _ReviewApprovalInventory:
            _invalid('native approval relationship reader')
        reviews = owner('synth_review_records', _ReviewOwner, root=links.reviews.root)
        shared(links, approvals=approvals, reviews=reviews, session=private.session)
        shared(approval_links, signed_store=signed)
        semantic = component(_SemanticEvidenceInventory)
        shared(semantic, reviews=reviews, session=private.session)
        if not semantic.contexts or not semantic.bindings or not semantic.evidence:
            _invalid('native semantic authority coverage')
        # Historical signatures prove identity, never audit completion or a
        # collector's permission. Keep all signed evidence and rollback history.
        if any(obj.state is not NodeState.OPEN or obj.eligible_after is not None
               for obj in objects if obj.identity.owner in {signed.name, producers.name}):
            _invalid('signed history must remain protected')
        roots.update((RootKind.CURRENT_POINTER, RootKind.RETIREMENT_PREDECESSOR,
                      RootKind.OPEN_APPROVAL, RootKind.OPEN_AUDIT))

        epochs = component(_EpochInventory)
        epoch = owner('birth_epochs', _EpochOwner, path=birth / 'executor_epochs.sqlite')
        shared(epochs, epochs=epoch, signed_store=signed)
        if (len(epochs.legacy) != 2
                or {item.table for item in epochs.legacy} !=
                   {'executor_legacy_state', 'executor_legacy_migrations'}
                or any(type(item) is not _EpochLegacyOwner or item.path != epoch.path
                       or owners.get(item.name) is not item for item in epochs.legacy)):
            _invalid('native epoch and legacy relationship coverage')
        roots.add(RootKind.CURRENT_EPOCH)

        journals = component(_JournalAdmissionInventory)
        shared(journals, signed_store=signed)
        native_journals = journals.journals
        if type(native_journals) is not _JournalInventory:
            _invalid('native feedback and undo relationship reader')
        turns = owner('turn_logs', _TurnLogOwner, root=p['PATH_TURNS'])
        archives = owner('turn_archives', _TurnArchiveOwner, root=p['turn_archives'])
        feedback = owner('turn_feedback', _FeedbackOwner, path=p['turn_feedback'])
        failures = owner('birth_failure_reviews', _SQLiteOwner, path=birth / 'failure_reviews.sqlite')
        if failures.table != 'executor_failure_review_queue' or failures.state is not _review_state:
            _invalid('native feedback queue schema')
        undo = owner('undo_operations', _UndoOwner, path=p['undo'])
        protected = owner('protected_undo_blobs', _ProtectedUndoOwner, root=p['protected_undo_blobs'])
        history = owner('history_backup_blobs', _HistoryBackupOwner, root=p['history_backups'])
        shared(native_journals, turns=turns, archives=archives, feedback=feedback,
               reviews=failures, undo=undo, protected_undo=protected, history=history)
        shared(feedback, reviews=failures)
        shared(protected, journal=undo)
        shared(history, journal=undo)
        roots.update((RootKind.OPEN_FEEDBACK, RootKind.ADMITTED_ROLLBACKABLE))

        # Native rollback and revision readers retain finalized-but-undoable
        # intents. A terminal status by itself does not close their evidence.
        rollback = component(_ChangeRollbackInventory)
        intents = owner('change_intents', _SQLiteOwner, path=p['DB_CHANGE_INTENTS'])
        if intents.table != 'change_intents' or intents.state is not _intent_state:
            _invalid('native revision intent schema')
        rollback_blobs = owner('change_rollback_blobs', _ChangeRollbackBlobs,
                               root=p['PATH_USER_DATA'] / 'rollback_blobs')
        shared(rollback, intents=intents, blobs=rollback_blobs)
        shared(rollback_blobs, intents=intents)
        promoter = component(_PromoterInventory)
        shared(promoter, signed_store=signed, reviews=reviews)
        promoter_rows = owner('promoter', _PromoterOwner, path=p['promoter_state'])
        promoter_blobs = owner('promoter_rollback_blobs', _PromoterBlobs, root=p['promoter_blobs'])
        shared(promoter, promoter=promoter_rows, blobs=promoter_blobs)
        if type(promoter.synth) is not _SynthSourcesInventory:
            _invalid('native synthesis source union')
        synth = owner('synth_proposal_files', _SynthProposalOwner, root=p['promoter_proposals'])
        archive = owner('synth_archive_files', _SynthArchiveOwner, root=p['promoter_archive'])
        shared(promoter.synth, synth=synth, archive=archive)
        shared(synth, intents=intents)
        shared(archive, intents=intents)
        persistent = owner('persistent_synth_candidates', _PersistentSynthOwner,
                           root=p['persistent_synth'])
        if not any(type(item) is _OwnerInventory and item.owner is persistent
                   for item in self.native.components):
            _invalid('native persistent revision reader')
        rows('proposals_state', p['proposals_state'], 'proposals_state', 'sig_key', _proposal_state)
        rows('approval_registry', p['approvals'], 'pending', 'token', _approval_state)
        roots.add(RootKind.OPEN_REVISION)

        lre = component(_LreInventory)
        workloads = owner('durable_workloads', _WorkloadOwner, path=p['workloads'])
        shared(lre, workloads=workloads)
        lre._check_owners()
        lre._check_artifact_binding()
        if lre._artifact_root != p['lre_artifact_store']:
            _invalid('native workload artifact root')
        source_authority = component(_SourceAuthorityInventory)
        grants = owner('lre_source_grants', _SourceGrantOwner, path=p['lre_source_authority'])
        snapshots = owner('lre_source_snapshots', _SourceSnapshots,
                          root=p['lre_source_authority'].parent / 'source_snapshots')
        shared(source_authority, grants=grants, snapshots=snapshots)
        rows('scheduler_entries', p['scheduler'], 'schedule_entries', 'id', _schedule_state)
        rows('scheduler_runs', p['scheduler'], 'runs', 'id', _run_state)
        roots.add(RootKind.IN_PROGRESS_JOB)

        # Every eligibility window is part of the native version already read.
        # mark() compares it with the plan time; observing an unexpired window
        # does not require at least one such object to happen to exist today.
        _utc(observed_at if observed_at is not None else _iso(datetime.now(timezone.utc)))
        for obj in objects:
            if (obj.state is NodeState.CLOSED
                    and _utc(obj.eligible_after) < _utc(obj.created_at)):
                _invalid('native eligibility window precedes object')
            if obj.state is NodeState.OPEN and obj.eligible_after is not None:
                _invalid('native open object has a collection deadline')
        roots.add(RootKind.UNEXPIRED_WINDOW)
        if type(holds) is not frozenset or read_holds(holds_root, root_owned=root_owned) != holds:
            _invalid('native legal hold observation changed')
        roots.add(RootKind.LEGAL_HOLD)
        self.require_exclusion()
        return frozenset(roots)

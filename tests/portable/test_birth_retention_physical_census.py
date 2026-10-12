"""Real namespace custody, native file reconciliation and protected residuals."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.name != 'posix':
    pytest.skip('native Linux/POSIX F6 physical namespace custody', allow_module_level=True)

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_open_audit import _TelosFilteredOwner
from install.birth_retention_path_probe import PATH_KEYS
from install.birth_retention_physical_census import (
    _BirthSnapshotOwner, _OpenAuditRootOwner, _OpenSynthHistoryOwner,
    _PhysicalCensusV1, _physical_snapshot_v1,
)
from test_birth_retention_reviews import review
from test_birth_retention_semantic_inventory import linked
from test_birth_retention_synth import sample


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX physical census')


@pytest.fixture(autouse=True)
def trusted_fixture_ancestors(tmp_path, monkeypatch):
    """Model the trusted machine boundary, without changing any tested node.

    The developer sandbox remaps root-owned /tmp to nobody. Its metadata is
    deliberately unacceptable in production. Only ancestors outside this
    fixture's namespace are projected; custody, links and drift at/below
    tmp_path remain actual filesystem observations.
    """
    import importlib
    import install.birth_retention_physical_census as module

    native_os = module.os
    anchors = {(info.st_dev, info.st_ino) for info in
               (native_os.stat(path, follow_symlinks=False) for path in tmp_path.parents)}

    def projected(info):
        if (info.st_dev, info.st_ino) not in anchors:
            return info
        fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
        fields.update(st_uid=0, st_gid=0, st_mode=info.st_mode & ~0o022)
        return SimpleNamespace(**fields)

    def fstat(descriptor):
        return projected(native_os.fstat(descriptor))

    def stat(path, *args, **kwargs):
        return projected(native_os.stat(path, *args, **kwargs))

    def os_lstat(path, *args, **kwargs):
        return projected(native_os.lstat(path, *args, **kwargs))

    native_stat = Path.stat
    native_lstat = Path.lstat

    def path_stat(path, *args, **kwargs):
        return projected(native_stat(path, *args, **kwargs))

    def lstat(path, *args, **kwargs):
        return projected(native_lstat(path, *args, **kwargs))

    # All native metadata readers must see the same outside boundary. In
    # particular JSONL checks its directory with both fstat and os.stat; a
    # projection of just the descriptor would also manufacture a drift failure.
    # The real os module is never patched. Only these native reader modules use
    # the proxy, and the exact outside inode set is the only changed metadata.
    monkeypatch.setattr(Path, 'stat', path_stat)
    monkeypatch.setattr(Path, 'lstat', lstat)
    overrides = {'stat': stat, 'lstat': os_lstat, 'fstat': fstat}
    projected_os = SimpleNamespace(**{
        name: overrides.get(name, getattr(native_os, name))
        for name in dir(native_os) if not name.startswith('__')})
    metadata_readers = (
        'install.birth_retention_physical_census',
        'install.birth_retention_files',
        'install.birth_retention_jsonl',
        'install.birth_retention_workspaces',
        'install.birth_retention_workspace_paths',
        'install.birth_retention_image_workspaces',
        'install.birth_retention_rotated_jsonl',
        'install.birth_retention_turn_archives',
        'install.birth_retention_synth_candidates',
        'install.birth_retention_artifact_auxiliary',
        'durable_workloads.artifacts',
        'contract_store',
        'executor_birth_authority_files',
    )
    for name in metadata_readers:
        monkeypatch.setattr(importlib.import_module(name), 'os', projected_os)

    # The shared native fixtures create intermediate directories with mkdir's
    # default mode. A group-writable developer umask is not the installed
    # custody boundary; explicit unsafe modes in the tests remain unchanged.
    previous_umask = native_os.umask(0o022)
    try:
        yield
    finally:
        native_os.umask(previous_umask)


def snapshot(*roots):
    return _physical_snapshot_v1(roots, owner=(os.geteuid(), os.getegid()),
                                 require_exclusion=lambda: None)


def test_absence_is_observed_without_creating_optional_namespace(tmp_path):
    root = tmp_path / 'not-created' / 'optional'
    before = snapshot(root)
    assert not root.parent.exists() and not before.entries
    assert before.absent == ((root, tmp_path, ('not-created', 'optional')),)
    root.mkdir(parents=True, mode=0o700)
    assert snapshot(root) != before


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'fifo', 'writable', 'ancestor_link'])
def test_links_special_files_and_unsafe_custody_block_even_unclaimed_nodes(tmp_path, fault):
    root = tmp_path / 'observed'
    root.mkdir(mode=0o700)
    path = root / 'unclaimed'
    if fault == 'symlink':
        path.symlink_to(tmp_path / 'absent')
    elif fault == 'hardlink':
        path.write_bytes(b'private')
        os.link(path, tmp_path / 'alias')
    elif fault == 'fifo':
        os.mkfifo(path, 0o600)
    elif fault == 'writable':
        path.write_bytes(b'private')
        path.chmod(0o666)
    else:
        alias = tmp_path / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        root = alias / 'absent'
    with pytest.raises(RetentionError, match='retention_inventory_incomplete'):
        snapshot(root)


def test_snapshot_detects_same_size_rewrite_and_preserves_bytes(tmp_path):
    path = tmp_path / 'object'
    path.write_bytes(b'first')
    path.chmod(0o600)
    before = snapshot(path)
    info = path.stat()
    path.write_bytes(b'other')
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
    assert before != snapshot(path)
    assert path.read_bytes() == b'other'


@pytest.fixture
def census(linked, tmp_path):
    root = tmp_path / 'observed'
    root.mkdir(mode=0o700)
    guard = lambda: None
    journal = _TelosFilteredOwner(path=root / 'telos_filtered.jsonl',
                                  require_exclusion=guard, owner=None)
    component = SimpleNamespace(owners={journal.name: journal}, inventory=journal.inventory)
    native = _InventoryComposition(components=(component,),
        required_owners=frozenset(component.owners), require_exclusion=guard)
    paths = {key: str(root) for key in PATH_KEYS}
    wrapper = _PhysicalCensusV1(native_inventory=native, paths=paths,
        account_owner=(os.geteuid(), os.getegid()), birth_root=linked.base / 'birth',
        session=linked.session, require_exclusion=guard)
    return SimpleNamespace(**locals())


def test_native_empty_journal_is_accounted_and_birth_files_remain_protected(census):
    path = census.journal.current.path
    path.write_bytes(b'')
    path.chmod(0o600)
    objects = census.wrapper.inventory()
    private = [obj for obj in objects if obj.identity.owner == 'birth_private_namespace']
    assert private and all(obj.state is NodeState.OPEN and obj.eligible_after is None
                           and obj.roots == (RootKind.OPEN_AUDIT,) for obj in private)
    owner = census.wrapper.owners[private[0].identity.owner]
    assert owner.version(private[0].identity) == private[0].version
    with pytest.raises(RetentionError, match='protected native namespace'):
        owner.delete(private[0].identity, private[0].version)
    assert path.read_bytes() == b''


def test_unknown_file_cannot_hide_behind_known_path_keys(census):
    path = census.root / 'unrecognized.bin'
    path.write_bytes(b'keep')
    path.chmod(0o600)
    with pytest.raises(RetentionError, match='unaccounted physical file'):
        census.wrapper.inventory()
    assert path.read_bytes() == b'keep'


def test_appearance_between_physical_boundaries_blocks_inventory(census):
    original = census.component.inventory
    def inventory():
        result = original()
        path = census.root / 'appeared.bin'
        path.write_bytes(b'keep')
        path.chmod(0o600)
        return result
    census.component.inventory = inventory
    with pytest.raises(RetentionError, match='physical census changed'):
        census.wrapper.inventory()
    assert (census.root / 'appeared.bin').read_bytes() == b'keep'


def test_unknown_birth_name_is_not_attested_by_a_directory_enum(census):
    path = census.linked.base / 'birth' / 'unrecognized.bin'
    path.write_bytes(b'keep')
    path.chmod(0o600)
    with pytest.raises(Exception) as failure:
        census.wrapper.inventory()
    assert getattr(failure.value, 'code', '').startswith('birth_provisioning_')
    assert path.read_bytes() == b'keep'


def test_path_key_set_and_successful_partial_inventory_cannot_attest_roots(census, tmp_path):
    objects = census.wrapper.inventory()
    with pytest.raises(RetentionError, match='native root owner binding'):
        census.wrapper.observed_roots(objects, holds_root=tmp_path / 'absent',
                                     holds=frozenset(), root_owned=False)


def test_failed_census_invalidates_previous_root_evidence(census, tmp_path):
    objects = census.wrapper.inventory()
    path = census.root / 'unknown.bin'
    path.write_bytes(b'keep')
    path.chmod(0o600)
    with pytest.raises(RetentionError, match='unaccounted physical file'):
        census.wrapper.inventory()
    with pytest.raises(RetentionError, match='completed unchanged census'):
        census.wrapper.observed_roots(objects, holds_root=tmp_path / 'absent',
                                     holds=frozenset(), root_owned=False)


@pytest.fixture
def complete_census(tmp_path, monkeypatch):
    import executor_birth_prepared_root as prepared
    from contract_store import STORE_RELATIVE
    from executor_birth_canonical import encode_canonical_ascii_v1
    from install.birth_retention_native_inventory import _compose_native_inventory_v1
    from tests.portable.native_catalog_fixtures import authenticated_catalog
    from tests.portable.rm0008_2b.test_historical_context_session import historical
    from test_birth_retention_native_inventory import observed

    catalog = authenticated_catalog(tmp_path, monkeypatch)
    base, _, selector = historical(tmp_path, monkeypatch, 'target')
    paths = observed(tmp_path)
    # Unlike the constructor-only assembly fixture, a physical inventory must
    # observe the installed signed-store root. Empty is valid; absent is not.
    signed_root = Path(paths['PATH_USER_STATE']) / STORE_RELATIVE
    signed_root.mkdir(mode=0o700, parents=True)
    # Administrative reviews live below a root-owned machine boundary, outside
    # the service-owned namespace. Keep the optional leaf absent below the
    # fixture's projected trusted ancestors, never below its actual user root.
    reviews_root = tmp_path.parent / (tmp_path.name + '-administrative-reviews')
    assert not reviews_root.exists()
    guard = lambda: None
    holds_root = tmp_path / 'maintenance'
    holds_root.mkdir(mode=0o755)
    hold_file = holds_root / 'holds.json'
    hold_file.write_bytes(encode_canonical_ascii_v1({'schema_version': 1, 'holds': []}))
    hold_file.chmod(0o600)
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            binding, = prepared.load_historical_context_verifiers_for_contexts_in_session_v1(
                (selector,), session)
            native = _compose_native_inventory_v1(paths=paths, account_owner=(os.geteuid(), os.getegid()),
                birth_root=base / 'birth', session=session, context_bindings={selector: binding},
                catalog=catalog, reviews_root=reviews_root, require_exclusion=guard)
            wrapper = _PhysicalCensusV1(native_inventory=native, paths=paths,
                account_owner=(os.geteuid(), os.getegid()), birth_root=base / 'birth',
                session=session, require_exclusion=guard)
            objects = wrapper.inventory()
            yield SimpleNamespace(**locals())


def test_native_relations_and_proved_absences_cover_all_roots(complete_census):
    sample = complete_census
    assert sample.wrapper.observed_roots(sample.objects, holds_root=sample.holds_root,
        holds=frozenset(), root_owned=False) == frozenset(RootKind)
    assert sample.signed_root.is_dir() and not tuple(sample.signed_root.iterdir())
    assert all(not Path(path).exists() for name, path in sample.paths.items()
               if name != 'PATH_USER_STATE')
    assert not sample.reviews_root.exists()
    assert all(obj.state is NodeState.OPEN for obj in sample.objects)


@pytest.mark.parametrize('fault', ['objects', 'path', 'holds_missing', 'holds_changed'])
def test_root_proof_rejects_projection_path_or_legal_hold_drift(complete_census, fault):
    from executor_birth_authority_files import OwnershipAuthorityError

    sample = complete_census
    objects = sample.objects
    if fault == 'objects':
        objects = objects[1:]
    elif fault == 'path':
        sample.native.owners['scheduler_runs'].path = sample.tmp_path / 'other.sqlite'
    elif fault == 'holds_missing':
        sample.hold_file.unlink()
    else:
        from executor_birth_canonical import encode_canonical_ascii_v1
        sample.hold_file.write_bytes(encode_canonical_ascii_v1(
            {'schema_version': 1, 'holds': ['sha256:' + 'a' * 64]}))
    # The shared authenticated-file reader owns missing authority documents.
    # Its typed custody failure must propagate; the census does not translate
    # missing holds into a valid empty set or invent another reader contract.
    expected = OwnershipAuthorityError if fault == 'holds_missing' else RetentionError
    with pytest.raises(expected) as failure:
        sample.wrapper.observed_roots(objects, holds_root=sample.holds_root,
                                      holds=frozenset(), root_owned=False)
    assert failure.value.code == ('birth_ownership_authority_missing'
        if fault == 'holds_missing' else 'retention_inventory_incomplete')


def test_distinct_synth_history_keeps_native_intent_reference_and_never_deletes(sample):
    source = sample.write()
    sample.intent()
    owner = _OpenSynthHistoryOwner(root=sample.root, intents=sample.intents)
    obj, = owner.inventory()
    assert obj.state is NodeState.OPEN and obj.eligible_after is None
    assert obj.roots == (RootKind.OPEN_REVISION,)
    assert obj.references == (sample.intents.scan()[0].identity,)
    assert owner.version(obj.identity) == obj.version
    with pytest.raises(RetentionError, match='protected native namespace'):
        owner.delete(obj.identity, obj.version)
    assert source.exists()


def test_distinct_native_audit_snapshot_is_protected_and_unknown_names_block(tmp_path):
    root = tmp_path / 'audit'
    root.mkdir(mode=0o700)
    path = root / 'candidates_dedupe_1577836800.jsonl'
    path.write_bytes(b'{"native":true}\n')
    path.chmod(0o600)
    owner = _OpenAuditRootOwner(root=root, owner=None, require_exclusion=lambda: None)
    obj, = owner.inventory()
    assert obj.state is NodeState.OPEN and obj.roots == (RootKind.OPEN_AUDIT,)
    with pytest.raises(RetentionError, match='protected native namespace'):
        owner.delete(obj.identity, obj.version)
    (root / 'candidates_unknown_1577836800.jsonl').write_bytes(b'{}\n')
    with pytest.raises(RetentionError):
        owner.inventory()


def test_private_snapshot_uses_closed_native_tree_and_never_claims_external_parent(tmp_path):
    root = tmp_path / 'metnos-birth-native'
    root.mkdir(mode=0o700)
    for name, raw in {
        'manifest.toml': b'[code]\nfiles=["compute_sample.py"]\n',
        'manifest.lang_state.json': b'{}',
        'compute_sample.py': b'def run(args): return args\n',
    }.items():
        path = root / name
        path.write_bytes(raw)
        path.chmod(0o600)
    owner = _BirthSnapshotOwner(roots=(tmp_path,), require_exclusion=lambda: None)
    owner._observed = (root,)
    obj, = owner.inventory()
    assert obj.state is NodeState.OPEN and owner.version(obj.identity) == obj.version
    with pytest.raises(RetentionError, match='foreign Birth snapshot root'):
        owner.identity(Path('/unobserved/metnos-birth-native'))
    (root / 'unknown.bin').write_bytes(b'keep')
    with pytest.raises(Exception) as failure:
        owner.inventory()
    assert getattr(failure.value, 'code', None) == 'candidate_file_extra'

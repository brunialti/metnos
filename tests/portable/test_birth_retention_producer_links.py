"""Physical Producer joins use native signatures and retain interrupted work."""
import sqlite3
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import contract_store as store
import executor_birth_prepared_root as prepared
import executor_birth_producer_store as producer
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_birth_sqlite import _BirthBundleOwner
from install.birth_retention_producer_links import _ProducerAdmissionInventory
from install.birth_retention_signed_store import _SignedStoreOwner
from tests.portable.executor_birth_history_fixtures import _fixture, _v2


@pytest.fixture
def history(tmp_path, monkeypatch, request):
    native = _fixture()
    if getattr(request, 'param', None) == 'v2':
        native.inputs = _v2(adoption=True, base=native)
    data = native.inputs
    evidence, declarations = data['evidence'], data['declarations']
    public = declarations.context.public_set
    db = tmp_path / 'producer.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript(producer._RECEIPT_SCHEMA + producer._ISSUANCE_SCHEMA)
        for table, row in [('birth_producer_receipts', data['receipt_row']),
                           ('birth_producer_issuance', data['issuance_row'])]:
            values = asdict(row); values.pop('row_id')
            connection.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
    db.chmod(0o600); tmp_path.chmod(0o700)
    producers = _BirthBundleOwner('producer_receipts', path=db, owner=None, require_exclusion=lambda: None)
    root = tmp_path / 'signed'; root.mkdir(mode=0o700)
    directory = root / store.contract_storage_key(evidence.contract_id)
    def write(path, content):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(content); path.chmod(0o600)
        parent = path.parent
        while parent != root.parent:
            parent.chmod(0o700); parent = parent.parent
    write(directory / store.BINDING_FILE, evidence.binding_bytes)
    write(directory / 'writer.lock', b'\0')
    gen = directory / 'generations' / evidence.generation_id[7:]
    for name, content in [('manifest.toml', evidence.manifest_bytes),
                          ('manifest.toml.sig', evidence.signature_bytes),
                          ('manifest.lang_state.json', evidence.language_state_bytes)]:
        write(gen / name, content)
    receipts = (store._birth_receipt_path(directory, evidence.generation_id),
                store._birth_receipt_path_v2(directory, evidence.generation_id, evidence.admission_context_id))
    if getattr(request, 'param', None) == 'v2':
        receipts = receipts[1:]
    for path in receipts:
        write(path, evidence.receipt_bytes)
    write(directory / 'current', (evidence.generation_id + '\n').encode())
    signed = _SignedStoreOwner(root=root, owner=None, require_exclusion=lambda: None,
        admission_keys=public.admission_verifier_keys, author_keys=public.author_verifier_keys)
    session = SimpleNamespace(_require_exclusive_global_lock=lambda: None)
    monkeypatch.setattr(prepared, 'load_historical_producer_declarations_for_contexts_in_session_v1',
                        lambda contexts, actual_session, **kwargs: (declarations,))
    inventory = _ProducerAdmissionInventory(producers=producers, signed_store=signed, session=session,
                                           require_exclusion=lambda: None)
    return SimpleNamespace(**locals())


def test_all_physical_admissions_link_bidirectionally_without_collection(history, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('historical inventory attempted current policy or mutation')
    monkeypatch.setattr(store, 'commit_birth_snapshot', forbidden)
    objects = history.inventory.inventory()
    row = next(obj for obj in objects if obj.identity.owner == history.producers.name)
    admissions = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    assert len(admissions) == 2
    assert all(row.identity in obj.references and obj.identity in row.references for obj in admissions)
    assert row.state is NodeState.OPEN and RootKind.OPEN_AUDIT in row.roots
    assert row.eligible_after is None


def test_native_projection_has_no_fabricated_sql_identity(history):
    row = history.producers.scan()[0]
    receipt, issuance = producer.producer_history_rows_for_binding_v1(row.values, row.related[0][1][0])
    assert receipt.row_id is None and issuance.row_id is None
    with pytest.raises(ValueError):
        producer.producer_history_rows_for_binding_v1(dict(row.values, row_id=1), row.related[0][1][0])


@pytest.mark.parametrize('state', ['available', 'in_progress', 'rejected', 'committed'])
def test_requests_without_durable_admission_are_preserved(history, state):
    for path in history.receipts:
        path.unlink()
    # Empty signed store is legitimate; current without admission would fail custody.
    for path in history.root.rglob('*'):
        if path.is_file():
            path.unlink()
    for path in sorted(history.root.rglob('*'), key=lambda path: len(path.parts), reverse=True):
        if path.is_dir():
            path.rmdir()
    with sqlite3.connect(history.db) as connection:
        if state == 'available':
            connection.execute("UPDATE birth_producer_receipts SET state='available',request_id=NULL,claimed_at=NULL,finalized_at=NULL,result_binding=NULL,terminal_envelope=NULL,terminal_auth=NULL")
        elif state == 'in_progress':
            connection.execute("UPDATE birth_producer_receipts SET state='in_progress',lease_expires_at=expires_at,finalized_at=NULL,result_binding=NULL")
        elif state == 'rejected':
            connection.execute("UPDATE birth_producer_receipts SET state='rejected',result_binding=NULL,rejection_code='native_rejection'")
    objects = history.inventory.inventory()
    assert len(objects) == 1 and objects[0].state is NodeState.OPEN
    assert RootKind.OPEN_AUDIT in objects[0].roots


def test_crash_after_durable_admission_before_terminal_preserves_job(history):
    with sqlite3.connect(history.db) as connection:
        connection.execute("UPDATE birth_producer_receipts SET state='in_progress',lease_expires_at=expires_at,finalized_at=NULL,result_binding=NULL,terminal_envelope=NULL,terminal_auth=NULL")
    objects = history.inventory.inventory()
    row = next(obj for obj in objects if obj.identity.owner == history.producers.name)
    assert RootKind.IN_PROGRESS_JOB in row.roots and len(row.references) == 2


@pytest.mark.parametrize('fault', ['terminal', 'issuance', 'absent', 'rejected', 'declarations'])
def test_unauthenticated_join_blocks_inventory(history, monkeypatch, fault):
    if fault == 'declarations':
        monkeypatch.setattr(prepared, 'load_historical_producer_declarations_for_contexts_in_session_v1', lambda *a, **kw: ())
    else:
        with sqlite3.connect(history.db) as connection:
            if fault == 'terminal':
                connection.execute("UPDATE birth_producer_receipts SET terminal_auth=?", (b'x' * 64,))
            elif fault == 'issuance':
                connection.execute("UPDATE birth_producer_issuance SET capability_id='forged'")
            elif fault == 'absent':
                connection.execute('DELETE FROM birth_producer_issuance')
                connection.execute('DELETE FROM birth_producer_receipts')
            else:
                connection.execute("UPDATE birth_producer_receipts SET state='rejected',result_binding=NULL,rejection_code='rejected'")
    with pytest.raises(RetentionError):
        history.inventory.inventory()


def test_frontier_change_blocks_return(history, monkeypatch):
    original = history.producers.scan
    calls = 0
    def scan():
        nonlocal calls
        calls += 1
        return original() if calls == 1 else ()
    monkeypatch.setattr(history.producers, 'scan', scan)
    with pytest.raises(RetentionError, match='producer admission links changed'):
        history.inventory.inventory()


@pytest.mark.parametrize('history', ['v2'], indirect=True)
def test_native_v2_terminal_less_protocol_stays_open(history):
    objects = history.inventory.inventory()
    row = next(obj for obj in objects if obj.identity.owner == history.producers.name)
    receipt = next(obj for obj in objects if obj.identity.node_type == 'admission_receipt')
    assert receipt.identity in row.references and row.identity in receipt.references
    assert row.state is NodeState.OPEN and RootKind.OPEN_AUDIT in row.roots


def test_exclusion_is_required_before_acquisition(history, monkeypatch):
    def denied():
        raise RetentionError('retention_owner_changed', 'barrier absent')
    history.inventory.require_exclusion = denied
    monkeypatch.setattr(history.producers, 'scan', lambda: pytest.fail('scan before exclusion'))
    with pytest.raises(RetentionError, match='barrier absent'):
        history.inventory.inventory()


@pytest.mark.parametrize('extra', [False, True])
def test_archived_public_sources_are_forwarded_and_coverage_is_exact(history, monkeypatch, extra):
    sources = (('historic-policy', b'archived public bytes'),)
    history.inventory.public_sources = sources
    def load(contexts, session, *, include_reattestation, public_sources):
        assert session is history.session and include_reattestation
        assert public_sources == sources
        declaration = history.data['declarations']
        return (declaration, declaration) if extra else (declaration,)
    monkeypatch.setattr(prepared, 'load_historical_producer_declarations_for_contexts_in_session_v1', load)
    if extra:
        with pytest.raises(RetentionError, match='historical producer authentication'):
            history.inventory.inventory()
    else:
        assert history.inventory.inventory()

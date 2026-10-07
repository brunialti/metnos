"""Real installed loader/binder under held EX; host acquisition is a seam.

The fixture supplies already authenticated epoch facts and captured bytes.
It does not claim real root custody, live systemd, or signature verification.
Native loader decoding, relational binding, repeated reconstruction and Birth
lock behavior execute unchanged. Existing native authentication oracles remain
in their own registered cluster.
"""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as native
import executor_birth_prepared_root as prepared
from executor_birth_account_identity import PosixAccountRecordV1, PosixAccountSnapshotV1
from install import birth_retention_installed_environment as reader
from test_executor_birth_admin_preflight_materials import _bound_graph
from tests.portable.rm0008_2b.test_historical_context_session import (
    historical, snapshot, deny_new_authority,
)


def acquisition(monkeypatch, mutation):
    graph = _bound_graph()
    builds = []
    units = []
    selected = SimpleNamespace(build=graph['distribution'], predecessor=graph['predecessor'],
        transaction=SimpleNamespace(prefix=SimpleNamespace(records=(graph['transaction'],))))
    monkeypatch.setattr(native, '_authenticate_fixed_ownership_snapshot_v1', lambda:
        native._AuthenticatedFixedOwnershipSnapshotV1(object(), None))
    monkeypatch.setattr(native, '_select_ownership_epoch_v1', lambda _: copy.deepcopy(selected))
    monkeypatch.setattr(native, 'RELEASE_ROOT', Path(graph['distribution'].facts.installation_root).parent)
    monkeypatch.setattr(native, '_local_g6_architecture_v1', lambda: graph['distribution'].facts.architecture)
    def capture(*args, capture_paths, **kwargs):
        captured = {path: memoryview(value).tobytes() for path, value in graph['captured'].items()}
        if mutation in ('catalog', 'descriptor', 'helper'):
            path = {'catalog': native.SERVICE_CATALOG_PATH_V1,
                    'descriptor': native.DEPLOYMENT_DESCRIPTOR_PATH_V1,
                    'helper': 'deployment/admin/preflight.py'}[mutation]
            captured[path] += b'\n'
        builds.append(captured)
        return {path: captured[path] for path in capture_paths}
    monkeypatch.setattr(native, '_snapshot_exact_distribution_tree_v1', capture)
    raw = graph['prerequisite_encoded'] + (b'\n' if mutation == 'prerequisite' else b'')
    monkeypatch.setattr(native, '_capture_trusted_file_v1', lambda *a, **kw:
        SimpleNamespace(content=memoryview(raw).tobytes(), identity=(0, 0, 0o100644)))
    def check(materials, entry):
        units.append((materials, entry.entry_id))
    monkeypatch.setattr(native, '_check_installed_service_v1', check)
    descriptor = native._decode_deployment_descriptor_v1(graph['captured'][native.DEPLOYMENT_DESCRIPTOR_PATH_V1])
    account = PosixAccountSnapshotV1(PosixAccountRecordV1(
        descriptor.service_user, descriptor.service_uid, descriptor.service_gid,
        descriptor.service_home, descriptor.service_shell), descriptor.service_supplementary_gids)
    monkeypatch.setattr(reader, 'resolve_posix_account_snapshot_v1', lambda _: account)
    return builds, units


@pytest.mark.parametrize('mutation', [None, 'catalog', 'descriptor', 'helper', 'prerequisite'])
def test_real_loader_reconstructs_materials_under_existing_exclusive_session(tmp_path, monkeypatch, mutation):
    base, _, _ = historical(tmp_path, monkeypatch, 'target')
    builds, units = acquisition(monkeypatch, mutation)
    # Include the lock file, but read it outside EX: Windows byte-range locks
    # reject snapshot's independent handle while the exclusion is held.
    before = snapshot(base)
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            deny_new_authority(monkeypatch)
            if mutation:
                with pytest.raises(native.PreflightError):
                    reader._capture_installed_environment_v1(
                        require_stability=session._require_exclusive_global_lock)
            else:
                result = reader._capture_installed_environment_v1(
                    require_stability=session._require_exclusive_global_lock)
                assert len(builds) == 4
                assert builds[0] == builds[2] and builds[0] is not builds[2]
                count = len(result.target_environments)
                assert count and len(units) == 2 * count
                first, second = units[0][0], units[count][0]
                assert first == second and first is not second
                assert first.catalog == second.catalog and first.catalog is not second.catalog
            assert session._holds_global_exclusive()
    assert snapshot(base) == before

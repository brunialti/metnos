"""Native file-lock observations and service-observation refusal cases.

Service fixtures do not count as installed maintenance acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import sys
from types import SimpleNamespace

import pytest

from executor_birth_retention import RetentionError
from install.birth_retention_exclusion import (
    _birth_exclusion_v1, _require_authority_lock_v1, _require_empty_units_v1,
    administrative_retention_exclusion_v1,
)


@pytest.fixture
def units(monkeypatch):
    import install.birth_lifecycle_migration as migration
    import stack_reconcile

    observed = (
        {"scope": "system", "unit": "writer.service", "load_state": "loaded",
         "active_state": "inactive", "main_pid": 0},
        {"scope": "system", "unit": "writer.timer", "load_state": "loaded",
         "active_state": "inactive", "main_pid": 0},
        {"scope": "user", "unit": "retired.service", "load_state": "manager-absent",
         "active_state": "inactive", "main_pid": 0},
    )
    response = SimpleNamespace(returncode=0, stdout="Id=writer.service\nControlPID=0\nControlGroup=\n")
    calls = []

    def stopped(user):
        assert user == "service_account"
        return observed

    def run(self, scope, *args, **kwargs):
        calls.append((scope, args))
        assert scope == "system" and args[0:2] == ("show", "writer.service")
        return response

    monkeypatch.setattr(migration, "_prove_productive_services_stopped_v1", stopped)
    monkeypatch.setattr(stack_reconcile.Systemctl, "run", run)
    return observed, response, calls


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux service observation")
def test_stopped_main_pid_is_not_proof_of_an_empty_service(units):
    observed, response, calls = units
    assert _require_empty_units_v1("service_account") == observed
    assert len(calls) == 1  # No model or other external unit is added to the catalog.
    response.stdout = "Id=writer.service\nControlPID=0\nControlGroup=/remaining-child\n"
    with pytest.raises(RetentionError, match="retention_writer_running"):
        _require_empty_units_v1("service_account")


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux service observation")
@pytest.mark.parametrize("output", [
    "", "Id=writer.service\nControlPID=0\n",
    "Id=writer.service\nControlPID=87\nControlGroup=\n",
    "Id=wrong.service\nControlPID=0\nControlGroup=\n",
    "Id=writer.service\nControlPID=invalid\nControlGroup=\n",
])
def test_unknown_or_nonquiescent_process_observation_refuses(units, output):
    units[1].stdout = output
    with pytest.raises(RetentionError, match="retention_writer_running"):
        _require_empty_units_v1("service_account")


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux service observation")
def test_failed_manager_call_refuses_even_with_plausible_output(units):
    units[1].returncode = 1
    with pytest.raises(RetentionError, match="retention_writer_running"):
        _require_empty_units_v1("service_account")


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux administrative exclusion")
@pytest.mark.parametrize("mutation", ["replace", "symlink", "hardlink", "permissions", "close"])
def test_live_authority_descriptor_cannot_be_rebound(tmp_path, mutation):
    import fcntl
    from install.birth_ownership_authority_provisioner import (
        _LOCK_BASENAME_V1, _provisioning_exclusion_v1,
    )

    tmp_path.chmod(0o755)
    path = tmp_path / _LOCK_BASENAME_V1
    with _provisioning_exclusion_v1(tmp_path, root_owned=False) as descriptor:
        _require_authority_lock_v1(descriptor, path, (os.geteuid(), os.getegid()))
        competing = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(competing)
        if mutation in {"replace", "symlink"}:
            original = tmp_path / "original"
            path.rename(original)
            if mutation == "replace":
                path.touch(mode=0o600)
            else:
                path.symlink_to(original)
        elif mutation == "hardlink":
            os.link(path, tmp_path / "alias")
        elif mutation == "permissions":
            path.chmod(0o644)
        else:
            descriptor = os.dup(descriptor)
            os.close(descriptor)
        with pytest.raises((RetentionError, OSError)):
            _require_authority_lock_v1(descriptor, path, (os.geteuid(), os.getegid()))


def test_administrative_entry_has_no_caller_selected_root_or_owner(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1, raising=False)
    with pytest.raises(RetentionError, match="retention_administrator_required"):
        with administrative_retention_exclusion_v1():
            pytest.fail("unprivileged maintenance")
    with pytest.raises(TypeError):
        administrative_retention_exclusion_v1(root="caller-root")


@pytest.fixture
def existing_birth_reader(tmp_path, monkeypatch):
    """Real native reader/lock; identity switching is a component fixture."""
    import config
    import executor_birth_prepared_root as prepared
    import executor_birth_secure_fs as secure
    from install import birth_authority_provisioner, birth_authority_provisioning

    if os.geteuid() == 0:
        pytest.skip("unprivileged fixture; installed root custody is qualified separately")
    base = tmp_path / "config"
    root = base / "birth"
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    lock = root / "provisioning-v1.lock"
    lock.write_bytes(b"0")
    lock.chmod(0o644)
    monkeypatch.setattr(config, "PATH_USER_CONFIG", base)
    account = SimpleNamespace(
        record=SimpleNamespace(uid=os.geteuid(), gid=os.getegid()),
        supplementary_gids=tuple(sorted(set(os.getgroups()) | {os.getegid()})),
    )
    observed = SimpleNamespace(
        account=account, root=root, lock=lock, identity_active=False,
        opened=[], closed=[],
    )

    @contextmanager
    def service_identity(identity):
        assert (identity.service_uid, identity.service_gid,
                identity.service_supplementary_gids) == (
                    account.record.uid, account.record.gid, account.supplementary_gids)
        assert not observed.identity_active
        observed.identity_active = True
        try:
            yield
        finally:
            observed.identity_active = False

    def forbidden_provisioning():
        pytest.fail("maintenance must not open the provisioning capability")

    real_open, real_close = prepared.open_prepared_root_session_v1, secure._SecureRootSession.close

    def open_reader():
        assert observed.identity_active
        session = real_open()
        observed.opened.append(session)
        return session

    def close_reader(session):
        assert observed.identity_active
        observed.closed.append(session)
        real_close(session)

    monkeypatch.setattr(birth_authority_provisioner, "_service_owned_birth_identity_v2", service_identity)
    monkeypatch.setattr(birth_authority_provisioning, "open_birth_provisioning_layout_v1", forbidden_provisioning)
    monkeypatch.setattr(prepared, "open_prepared_root_session_v1", open_reader)
    monkeypatch.setattr(secure._SecureRootSession, "close", close_reader)
    return observed


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux administrative exclusion")
@pytest.mark.parametrize("body_fails", [False, True])
def test_birth_reader_holds_existing_lock_and_closes_in_service_identity(existing_birth_reader, body_fails):
    import fcntl
    from executor_birth_secure_fs import BirthSecureFSError

    observed = existing_birth_reader
    before = observed.lock.stat()
    failure = RuntimeError("maintenance fixture failed")
    try:
        with _birth_exclusion_v1(observed.account) as session:
            assert not observed.identity_active
            session._require_exclusive_global_lock()
            assert session._root_path == str(observed.root)
            assert session._expected_uid == observed.account.record.uid
            assert session.inventory(()) == ("provisioning-v1.lock",)
            # No operator-input directory or provisioning material is needed.
            with observed.lock.open("rb") as competing:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if body_fails:
                raise failure
    except RuntimeError as exc:
        assert body_fails and exc is failure
    else:
        assert not body_fails
    assert not observed.identity_active
    assert observed.opened == observed.closed == [session]
    with pytest.raises(BirthSecureFSError):
        session.inventory(())
    with observed.lock.open("rb") as competing:
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(competing, fcntl.LOCK_UN)
    after = observed.lock.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino, after.st_mtime_ns, after.st_mode)
    assert observed.lock.read_bytes() == b"0"
    assert tuple(observed.root.iterdir()) == (observed.lock,)


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux administrative exclusion")
@pytest.mark.parametrize("fault", ["missing", "unsafe-mode"])
def test_birth_reader_refuses_unavailable_lock_without_provisioning(existing_birth_reader, fault):
    from executor_birth_secure_fs import BirthSecureFSError

    observed = existing_birth_reader
    if fault == "missing":
        observed.lock.unlink()
    else:
        observed.lock.chmod(0o666)
    with pytest.raises(BirthSecureFSError) as failure:
        with _birth_exclusion_v1(observed.account):
            pytest.fail("unavailable lock must refuse maintenance")
    assert failure.value.code == (
        "birth_provisioning_lock_unavailable" if fault == "missing"
        else "birth_provisioning_acl_unsafe")
    assert not observed.identity_active
    assert len(observed.opened) == 1 and observed.closed == observed.opened
    with pytest.raises(BirthSecureFSError):
        observed.opened[0].inventory(())
    if fault == "missing":
        assert tuple(observed.root.iterdir()) == ()
    else:
        assert observed.lock.stat().st_mode & 0o777 == 0o666
        assert observed.lock.read_bytes() == b"0"

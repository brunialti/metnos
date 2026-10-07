"""Native file-lock observations and service-observation refusal cases.

Service fixtures do not count as installed maintenance acceptance.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from executor_birth_retention import RetentionError
from install.birth_retention_exclusion import (
    _require_authority_lock_v1, _require_empty_units_v1,
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

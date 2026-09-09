"""The incident repair has one exact predecessor and a recoverable replacement."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def repair(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "internal/tools/repair_rm0008_service_startup.py"
    spec = importlib.util.spec_from_file_location("startup_repair_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "protected_directory", lambda path: None)
    module.TARGET = tmp_path / "preflight.py"
    module.BACKUP = tmp_path / "backup"
    module.OLD_SHA = hashlib.sha256(b"old").hexdigest()
    module.NEW_SHA = hashlib.sha256(b"new").hexdigest()
    original_read = module.read_exact
    monkeypatch.setattr(module, "read_exact", lambda path, digest, **kwargs:
                        original_read(path, digest, root=False))
    module.TARGET.write_bytes(b"old")
    return module


def test_exact_replacement_retains_previous_bytes(repair):
    repair.replace_helper(b"new", b"old")
    assert repair.TARGET.read_bytes() == b"new"
    assert repair.TARGET.stat().st_mode & 0o777 == 0o755
    saved = repair.BACKUP / "preflight.before.py"
    assert saved.read_bytes() == b"old"
    assert saved.stat().st_mode & 0o777 == 0o400


def test_unknown_predecessor_is_not_overwritten(repair):
    repair.TARGET.write_bytes(b"unexpected")
    with pytest.raises(RuntimeError, match="changed file"):
        repair.replace_helper(b"new", b"old")
    assert repair.TARGET.read_bytes() == b"unexpected"
    assert not tuple(repair.TARGET.parent.glob(".service-startup-*"))


def test_staging_must_have_exact_new_digest(repair):
    with pytest.raises(RuntimeError, match="changed file"):
        repair.replace_helper(b"not reviewed", b"old")
    assert repair.TARGET.read_bytes() == b"old"


def test_preserved_backup_cannot_be_overwritten(repair):
    repair.BACKUP.mkdir()
    saved = repair.BACKUP / "preflight.before.py"
    saved.write_bytes(b"different")
    with pytest.raises(RuntimeError, match="changed file"):
        repair.replace_helper(b"new", b"old")
    assert saved.read_bytes() == b"different"
    assert repair.TARGET.read_bytes() == b"old"


def test_symlink_target_refused(repair):
    repair.TARGET.unlink()
    elsewhere = repair.TARGET.with_name("elsewhere")
    elsewhere.write_bytes(b"old")
    repair.TARGET.symlink_to(elsewhere)
    with pytest.raises(OSError):
        repair.read_exact(repair.TARGET, repair.OLD_SHA)
    assert elsewhere.read_bytes() == b"old"


def test_hardlinked_target_refused(repair):
    os.link(repair.TARGET, repair.TARGET.with_name("hardlink"))
    with pytest.raises(RuntimeError, match="unsafe file"):
        repair.read_exact(repair.TARGET, repair.OLD_SHA)


def test_active_work_is_not_stopped(repair, monkeypatch):
    calls = []
    def command(*args):
        calls.append(args)
        return "metnos-http.service loaded active running"
    monkeypatch.setattr(repair, "command", command)
    with pytest.raises(RuntimeError, match="no services were stopped"):
        repair.require_stopped()
    assert len(calls) == 1 and "list-units" in calls[0]


def test_current_verifier_definitions_load_without_running_main(repair):
    # Exercise the loader against current definitions, not a historical incident
    # digest: the development source must be allowed to advance after repair.
    content = repair.SOURCE.read_bytes()
    namespace = repair.load_reviewed_verifier(content)
    assert callable(namespace["_check_installed_service_v1"])
    assert callable(namespace["_authenticate_fixed_ownership_snapshot_v1"])


def test_unreviewed_source_is_refused_before_loading_or_live_io(repair, monkeypatch):
    source = repair.TARGET.with_name("candidate.py")
    source.write_bytes(b"new but not reviewed")
    monkeypatch.setattr(repair, "SOURCE", source)
    monkeypatch.setattr(repair.os, "geteuid", lambda: 0)
    monkeypatch.setattr(repair.sys, "argv", ["repair"])
    monkeypatch.setattr(repair, "load_reviewed_verifier", lambda content:
                        pytest.fail("unreviewed code must never run"))
    monkeypatch.setattr(repair, "command", lambda *a, **kw: pytest.fail("no command"))
    with pytest.raises(RuntimeError, match="changed file"):
        repair.repair()
    assert repair.TARGET.read_bytes() == b"old"

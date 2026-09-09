"""The one-shot recovery must not modify a release or stop active services."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def repair():
    path = Path(__file__).resolve().parents[2] / "internal/tools/repair_rm0008_boot.py"
    spec = importlib.util.spec_from_file_location("boot_repair", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repair_uses_same_rule_as_product_installer(repair):
    from install.executor_birth_startup_gate import STARTUP_BOOT_RULE_V1

    assert repair.RULE_BYTES == STARTUP_BOOT_RULE_V1


def test_active_service_refuses_without_stop_or_write(repair, monkeypatch):
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        return "metnos-http.service loaded active running"

    monkeypatch.setattr(repair, "command", command)
    with pytest.raises(RuntimeError, match="work is active"):
        repair.require_stopped()
    assert len(calls) == 1
    assert calls[0][1] == "list-units"
    assert "stop" not in calls[0]


def test_rule_publication_is_idempotent_and_rejects_different_bytes(
    repair, monkeypatch, tmp_path,
):
    rule = tmp_path / "metnos-executor-birth-v1.conf"
    monkeypatch.setattr(repair, "RULE", rule)
    # Ownership is root-only in production; no elevated privileges in this test.
    monkeypatch.setattr(repair, "metadata", lambda *args, **kwargs: None)
    repair.install_rule()
    inode = rule.stat().st_ino
    repair.install_rule()
    assert rule.stat().st_ino == inode
    assert rule.read_bytes() == repair.RULE_BYTES
    assert len(list(tmp_path.iterdir())) == 1
    rule.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="differs"):
        repair.install_rule()
    assert rule.read_bytes() == b"changed"


def test_failed_preflight_never_starts_target(repair, monkeypatch, tmp_path):
    root = tmp_path / "authority"
    root.mkdir()
    lock = root / "ownership-deployment-v1.lock"
    lock.touch(mode=0o600)
    monkeypatch.setattr(repair, "ROOT", root)
    for name in ("pins", "require_stopped", "validate_runtime", "install_rule"):
        monkeypatch.setattr(repair, name, lambda: None)
    monkeypatch.setattr(repair, "metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(repair, "emit", lambda message: None)
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        if "check" in args:
            raise subprocess.CalledProcessError(20, args, stderr="preflight refused")
        return ""

    monkeypatch.setattr(repair, "command", command)
    with pytest.raises(subprocess.CalledProcessError):
        repair.repair()
    assert not any("start" in call for call in calls)
    assert lock.exists()


def test_old_repair_refuses_a_changed_release_before_effects(
        repair, monkeypatch, tmp_path):
    # A successful later repair legitimately changes the host. This one-shot
    # tool must stay pinned to its historical input, not track the live helper.
    from types import SimpleNamespace
    import stat

    path = tmp_path / "preflight.py"
    path.write_bytes(b"historical verifier")
    monkeypatch.setattr(repair, "PINNED", {
        path: hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(
        st_mode=stat.S_IFREG | 0o755, st_uid=0, st_gid=0, st_nlink=1,
    ))
    repair.pins()
    path.write_bytes(b"later authorized verifier")
    monkeypatch.setattr(repair, "require_stopped", lambda: pytest.fail("no I/O"))
    with pytest.raises(RuntimeError, match="release changed"):
        repair.repair()
    assert path.read_bytes() == b"later authorized verifier"

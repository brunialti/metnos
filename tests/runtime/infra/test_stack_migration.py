"""Fresh/upgrade/pilot/rollback state machine for the integrated stack."""
from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import stack_migration as migration
import stack_reconcile as sr


class FakeSystemctl:
    def __init__(self):
        self.system_active = True
        self.system_enabled = True
        self.target_loaded = True
        self.target_active = False
        self.user_active = {
            "metnos-side-display.service",
            "metnos-playwright.service",
            "metnos-telegram-daemon.service",
            "metnos-i18n-translator.timer",
        }
        self.calls: list[tuple] = []
        self.fail_user_start = False

    def show(self, unit: str, scope: str = "user"):
        self.calls.append(("show", scope, unit))
        if scope == "system":
            return {
                "LoadState": "loaded",
                "ActiveState": "active" if self.system_active else "inactive",
            }
        if unit == sr.TARGET_UNIT:
            return {
                "LoadState": "loaded" if self.target_loaded else "not-found",
                "ActiveState": "active" if self.target_active else "inactive",
            }
        return {
            "LoadState": "loaded",
            "ActiveState": "active" if unit in self.user_active else "inactive",
        }

    def run(self, scope: str, *args: str, timeout_s: float = 120):
        self.calls.append(("run", scope, *args))
        if "show" in args:
            prop = next(item.split("=", 1)[1] for item in args if item.startswith("--property="))
            values = {
                "Environment": (
                    "HOME=/home/test PYTHONPATH=/repo:/runtime "
                    "METNOS_HTTP_HOST=0.0.0.0 METNOS_HTTP_PORT=8770 "
                    "UNRELATED_SECRET=not-copied"
                ),
                "ExecStart": (
                    "{ path=/legacy/bin/python ; argv[]=/legacy/bin/python "
                    "/repo/runtime/metnos_http_server.py --host 0.0.0.0 "
                    "--port 8770 ; ignore_errors=no ; }"
                    if scope == "system" else
                    "{ path=/opt/metnos/.venv/bin/python ; "
                    "argv[]=/opt/metnos/.venv/bin/python "
                    "-m runtime.metnos_http_server --host 127.0.0.1 --port 8770 "
                    "; ignore_errors=no ; }"
                ),
                "WorkingDirectory": "/repo",
            }
            return subprocess.CompletedProcess(args, 0, stdout=values[prop] + "\n", stderr="")
        action = next((item for item in args if item in {
            "start", "stop", "enable", "disable", "restart",
        }), "")
        if scope == "system":
            if action == "stop":
                self.system_active = False
            elif action == "start":
                self.system_active = True
            elif action == "disable":
                self.system_enabled = False
            elif action == "enable":
                self.system_enabled = True
        else:
            units = {
                item for item in args
                if item.endswith((".service", ".timer", ".target"))
            }
            if sr.TARGET_UNIT in units and action in {"start", "enable"}:
                if self.fail_user_start:
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed")
                self.target_active = True
            elif sr.TARGET_UNIT in units and action in {"stop", "disable"}:
                self.target_active = False
                self.user_active.clear()
            elif action == "start":
                self.user_active.update(units)
            elif action == "stop":
                self.user_active.difference_update(units)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class FakeReconciler:
    def __init__(self, systemctl):
        self.systemctl = systemctl
        self.endpoints = sr.Endpoints()
        self.checks = 0
        self.waits = 0

    def check(self, **_kwargs):
        self.checks += 1
        return {"ok": True}

    def wait_ready(self, **_kwargs):
        self.waits += 1
        assert self.systemctl.target_active is True
        assert self.systemctl.system_active is False
        return {"ok": True}


def _instance(tmp_path, fake):
    return migration.HttpScopeMigration(
        systemctl=fake,
        reconciler=FakeReconciler(fake),
        user_unit_dir=tmp_path / "user-units",
        evidence_path=tmp_path / "pilot.json",
        turn_probe=lambda: {"turn_id": "turn-test", "final_kind": "answer", "steps": 1},
    )


def _wire_lock_and_health(monkeypatch, tmp_path):
    lock_type = sr.ReconcileLock
    monkeypatch.setattr(
        migration, "ReconcileLock",
        lambda *_args, **_kwargs: lock_type(tmp_path / "migration.lock"),
    )


def _pilot_evidence(instance, configuration_sha256, **overrides):
    payload = {
        "schema_version": 1,
        "ok": True,
        "cycles_completed": 2,
        "rollback_verified": True,
        "baseline_restored": True,
        "configuration_sha256": configuration_sha256,
        "stack_contract_sha256": instance._stack_contract_sha256(),
        "host_fingerprint": instance._host_fingerprint(),
    }
    payload.update(overrides)
    return payload


def test_prepare_preserves_runtime_settings_but_not_legacy_python_environment(
        tmp_path):
    fake = FakeSystemctl()
    instance = _instance(tmp_path, fake)
    result = instance.prepare()
    dropin = Path(result["dropin"])
    body = dropin.read_text()
    assert result["ok"] is True
    assert stat.S_IMODE(dropin.stat().st_mode) == 0o600
    assert "ExecStart=" not in body
    assert 'Environment="METNOS_HTTP_HOST=0.0.0.0"' in body
    assert "PYTHONPATH" not in body
    assert "HOME=" not in body
    assert "WorkingDirectory=/repo" in body
    assert 'WorkingDirectory="/repo"' not in body
    assert "UNRELATED_SECRET" not in body
    assert "User=" not in body
    assert result["legacy_exec_program"] == "/legacy/bin/python"
    assert result["target_exec_program"] == (
        "/opt/metnos/.venv/bin/python"
    )
    assert ("run", "user", "daemon-reload") in fake.calls


def test_unit_path_escapes_spaces_without_quoting_the_path():
    assert migration._unit_path("/srv/metnos instance/%live") == (
        "/srv/metnos\\x20instance/%%live"
    )


def test_pilot_runs_two_e2e_cycles_and_restores_baseline(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    baseline = set(fake.user_active)
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    report = instance.pilot(cycles=2)
    assert report["ok"] is True
    assert report["cycles_completed"] == 2
    assert report["rollback_verified"] is True
    assert report["baseline_restored"] is True
    assert fake.system_active is True
    assert fake.target_active is False
    assert fake.user_active == baseline
    assert report["baseline_user_units"] == sorted(
        baseline, key=migration.BASELINE_USER_UNITS.index,
    )
    assert re.fullmatch(r"[0-9a-f]{64}", report["stack_contract_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", report["host_fingerprint"])
    assert all(row["turn"]["steps"] >= 1 for row in report["cycles"])
    assert len(json.loads((tmp_path / "pilot.json").read_text())["cycles"]) == 2


def test_pilot_refuses_a_single_cycle(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    with pytest.raises(sr.StackFailure) as caught:
        instance.pilot(cycles=1)
    assert caught.value.code == "pilot_cycles_invalid"


def test_pilot_failure_rolls_back_to_system_service(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    baseline = set(fake.user_active)
    fake.fail_user_start = True
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    with pytest.raises(sr.StackFailure) as caught:
        instance.pilot(cycles=2)
    assert caught.value.code == "target_start_failed"
    assert fake.system_active is True
    assert fake.target_active is False
    assert fake.user_active == baseline
    quarantine_stop = (
        "run", "user", "stop", "metnos-stack-quarantine.service",
    )
    assert quarantine_stop in fake.calls


def test_pilot_preflight_failure_does_not_touch_live_baseline(
        monkeypatch, tmp_path):
    fake = FakeSystemctl()
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    monkeypatch.setattr(
        instance, "_stack_contract_sha256",
        lambda: (_ for _ in ()).throw(
            sr.StackFailure("stack_inventory_failed", "preflight failed")
        ),
    )
    with pytest.raises(sr.StackFailure) as caught:
        instance.pilot(cycles=2)
    assert caught.value.code == "stack_inventory_failed"
    assert not any(call[0] == "run" and "stop" in call for call in fake.calls)


def test_cutover_requires_verified_pilot_evidence(tmp_path):
    evidence = tmp_path / "bad.json"
    evidence.write_text(json.dumps({"schema_version": 1, "ok": True, "cycles_completed": 1}))
    with pytest.raises(sr.StackFailure) as caught:
        migration.HttpScopeMigration.validate_evidence(evidence)
    assert caught.value.code == "pilot_evidence_invalid"


def test_natural_turn_requires_steps_and_uses_unique_conversation_ids(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    instance = _instance(tmp_path, fake)
    requests = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(self.payload).encode("utf-8")

    payloads = iter((
        {"turn_id": "turn-empty", "final_kind": "answer", "steps_summary": []},
        {
            "turn_id": "turn-one",
            "final_kind": "answer",
            "steps_summary": [{"executor": "get_now", "ok": True}],
        },
        {
            "turn_id": "turn-two",
            "final_kind": "answer",
            "steps_summary": [{"executor": "get_now", "ok": True}],
        },
    ))

    def urlopen(request, timeout):
        assert timeout == 180
        requests.append(json.loads(request.data.decode("utf-8")))
        return Response(next(payloads))

    monkeypatch.setattr(migration.urllib.request, "urlopen", urlopen)
    with pytest.raises(sr.StackFailure) as caught:
        instance._natural_turn()
    assert caught.value.code == "pilot_turn_failed"
    assert instance._natural_turn()["steps"] == 1
    assert instance._natural_turn()["steps"] == 1
    conversation_ids = [request["conversation_id"] for request in requests]
    assert len(set(conversation_ids)) == 3


def test_cutover_requires_root_for_real_systemctl(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    instance = migration.HttpScopeMigration(
        systemctl=sr.Systemctl(service_user="roberto"),
        reconciler=FakeReconciler(fake),
        user_unit_dir=tmp_path / "user-units",
        evidence_path=tmp_path / "pilot.json",
        service_user="roberto",
    )
    monkeypatch.setattr(migration.os, "geteuid", lambda: 1000)
    with pytest.raises(sr.StackFailure) as caught:
        instance.cutover(tmp_path / "unused.json")
    assert caught.value.code == "cutover_authority_required"


def test_root_cutover_cli_requires_explicit_service_user(monkeypatch, capsys):
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    assert migration.main(["cutover", "--evidence", "/tmp/unused.json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "service_user_required"


def test_root_migration_uses_service_user_runtime_lock(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    monkeypatch.setattr(migration.os, "getuid", lambda: 0)
    monkeypatch.setattr(
        migration.pwd, "getpwnam",
        lambda _name: type("Identity", (), {
            "pw_uid": 1234,
            "pw_gid": 1234,
            "pw_dir": str(tmp_path / "home"),
        })(),
    )
    instance = migration.HttpScopeMigration(
        systemctl=fake,
        reconciler=FakeReconciler(fake),
        service_user="metnos-test",
    )
    assert instance.lock_path == Path(
        "/run/user/1234/metnos-stack-reconcile.lock"
    )


def test_cutover_uses_evidence_and_leaves_only_user_target(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    contract_sha = instance.prepare()["sha256"]
    evidence = tmp_path / "proof.json"
    evidence.write_text(json.dumps(_pilot_evidence(instance, contract_sha)))
    result = instance.cutover(evidence)
    assert result["ok"] is True
    assert fake.system_active is False
    assert fake.system_enabled is False
    assert fake.target_active is True


def test_cutover_failure_restores_enabled_system_baseline(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    baseline = set(fake.user_active)
    fake.fail_user_start = True
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    contract_sha = instance.prepare()["sha256"]
    evidence = tmp_path / "proof.json"
    evidence.write_text(json.dumps(_pilot_evidence(instance, contract_sha)))
    with pytest.raises(sr.StackFailure):
        instance.cutover(evidence)
    assert fake.system_active is True
    assert fake.system_enabled is True
    assert fake.target_active is False
    assert fake.user_active == baseline


def test_cutover_rejects_contract_changed_after_pilot(monkeypatch, tmp_path):
    fake = FakeSystemctl()
    instance = _instance(tmp_path, fake)
    _wire_lock_and_health(monkeypatch, tmp_path)
    evidence = tmp_path / "proof.json"
    evidence.write_text(json.dumps(_pilot_evidence(instance, "0" * 64)))
    with pytest.raises(sr.StackFailure) as caught:
        instance.cutover(evidence)
    assert caught.value.code == "pilot_evidence_stale"
    assert fake.system_active is True
    assert fake.target_active is False

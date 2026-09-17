"""Contratto del gestore centralizzato dei servizi Metnos."""
from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from types import SimpleNamespace

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

import services_registry as registry
from http_render import render_template
import pytest


def _show(*, unit: str, load: str = "loaded", active: str = "active"):
    stdout = "\n".join((
        f"Id={unit}",
        f"LoadState={load}",
        f"ActiveState={active}",
        "SubState=running" if active == "active" else "SubState=dead",
        "MainPID=42",
        "ActiveEnterTimestamp=Wed 2026-07-15 10:00:00 CEST",
        "UnitFileState=enabled",
    ))
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def test_catalog_keys_and_targets_are_closed_and_unique():
    services = registry.catalog()
    assert len({service.key for service in services}) == len(services)
    assert {
        "http", "playwright", "llm", "searxng", "photon", "i18n",
        "durable_workloads",
    } <= {
        service.key for service in services
    }
    assert "issues" not in {service.key for service in services}
    assert registry.get("i18n").required is True
    assert "fast.micro" in registry.get("llm").description
    # Il traduttore usa il workload `translation.i18n`, che il registro
    # risolve su `wise`: la descrizione citava un livello fast che non ha
    # alcun carico (ADR 0207, emendamento al censimento).
    assert "wise" in registry.get("i18n").description
    assert registry.get("llm").endpoint_env == "METNOS_LLM_URL"
    assert registry.get("durable_workloads").health_policy == "application"
    for service in services:
        assert service.targets
        assert all(target.scope in {"system", "user"}
                   for target in service.targets)
        assert all(target.unit.endswith((".service", ".timer"))
                   for target in service.targets)


def test_readiness_catalog_preserves_initial_installer_profile(monkeypatch, tmp_path):
    import executor_birth_ownership_chain as ownership

    monkeypatch.setattr(ownership, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", tmp_path / "absent")
    monkeypatch.setattr(
        ownership, "inspect_ownership_chain_state_v1",
        lambda: pytest.fail("no chain should be opened on the fresh installer"),
    )
    assert registry.readiness_catalog() is registry.SERVICES


def test_readiness_catalog_preserves_verified_empty_chain(monkeypatch, tmp_path):
    import executor_birth_ownership_chain as ownership
    import executor_birth_service_catalog as catalog

    monkeypatch.setattr(ownership, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", tmp_path)
    state = ownership._mint_initial_ownership_chain_state_v1(tmp_path)
    monkeypatch.setattr(ownership, "inspect_ownership_chain_state_v1", lambda: state)
    monkeypatch.setattr(
        catalog, "capture_current_service_catalog_v1",
        lambda _: pytest.fail("an initial chain has no required distribution"),
    )
    assert registry.readiness_catalog() is registry.SERVICES


def test_readiness_catalog_has_no_store_mutation_authority():
    from contract_boundary_guard import scan_file

    source = Path(registry.__file__)
    facts = scan_file(source, repository_root=source.parent.parent)
    reader = next(fact for fact in facts if fact.scope == "readiness_catalog")
    assert reader.capabilities == ()
    assert not reader.closed_dynamic_boundary


def _verified_readiness_chain(monkeypatch, tmp_path):
    import executor_birth_ownership_chain as ownership
    import executor_birth_service_catalog as catalog

    class VerifiedChain:
        required_distribution = SimpleNamespace(installation_root=str(tmp_path))

    class PublicStore:
        def read_required_window_v1(self):
            return VerifiedChain()

    entries = tuple(SimpleNamespace(
        unit_name=item.unit_name, external_unit_name=item.external_unit_name,
        scope=catalog._entry_scope(item.class_name),
    ) for item in catalog.SERVICE_SOURCE_V1)
    monkeypatch.setattr(registry._C, "PATH_ROOT", tmp_path)
    monkeypatch.setattr(ownership, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", tmp_path)
    monkeypatch.setattr(ownership, "VerifiedOwnershipWindowV1", VerifiedChain)
    (tmp_path / ownership.REQUIRED_HEAD_BASENAME).write_bytes(b"required")
    monkeypatch.setattr(ownership, "OwnershipChainStore", PublicStore)
    monkeypatch.setattr(
        ownership, "inspect_ownership_chain_state_v1",
        lambda: pytest.fail("a required chain must use the public cold reader"),
    )

    def capture(distribution):
        assert distribution is VerifiedChain.required_distribution
        return SimpleNamespace(catalog=SimpleNamespace(entries=entries))

    monkeypatch.setattr(catalog, "capture_current_service_catalog_v1", capture)
    return VerifiedChain


def test_installed_catalog_unifies_observation_control_and_policy(
    monkeypatch, tmp_path,
):
    _verified_readiness_chain(monkeypatch, tmp_path)
    profile = {spec.key: spec for spec in registry.readiness_catalog()}
    for key in ("playwright", "telegram", "side_display", "i18n", "durable_workloads"):
        original = registry._BY_KEY[key]
        assert profile[key].targets == (registry.ServiceTarget(original.targets[0].unit, "system"),)
        assert original.targets[0].scope == "user"
        assert profile[key].required == original.required
        assert profile[key].base_url == original.base_url
        assert registry.get(key) == profile[key]
    for key in ("http", "llm", "searxng", "photon"):
        assert profile[key].targets == (registry._BY_KEY[key].targets[-1],)
    assert registry.catalog() == registry.readiness_catalog()
    assert registry.stack_scope() == "system"
    rule = registry.render_polkit_rule("metnos-test")
    for service in profile.values():
        assert service.targets[0].unit in registry.system_units()
        assert service.targets[0].unit in rule
    assert 'true && unit === "metnos.target" && verb === "restart"' in rule


def test_readiness_catalog_rejects_another_release_root(monkeypatch, tmp_path):
    import executor_birth_service_catalog as catalog

    _verified_readiness_chain(monkeypatch, tmp_path)
    monkeypatch.setattr(registry._C, "PATH_ROOT", tmp_path / "different-release")
    monkeypatch.setattr(
        catalog, "capture_current_service_catalog_v1",
        lambda _: pytest.fail("wrong installation must stop before catalog capture"),
    )
    with pytest.raises(ValueError, match="root mismatch"):
        registry.readiness_catalog()


@pytest.mark.parametrize("key", ["telegram", "playwright", "durable_workloads"])
def test_control_uses_signed_system_targets(monkeypatch, tmp_path, key):
    _verified_readiness_chain(monkeypatch, tmp_path)
    unit = registry.get(key).targets[0].unit
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "show" in command:
            return _show(unit=unit)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(registry.subprocess, "run", run)
    monkeypatch.setattr(registry, "_record_desired_state", lambda *_: None)
    assert registry.control(key, "restart") == (True, "")
    assert calls[-1] == ["systemctl", "--no-block", "restart", unit]
    assert all("--user" not in command for command in calls)


def test_lre_configuration_restarts_installed_system_worker(monkeypatch, tmp_path):
    _verified_readiness_chain(monkeypatch, tmp_path)
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "show" in command:
            return _show(unit="metnos-durable-worker.service")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(registry.subprocess, "run", run)
    assert registry.configure_lre_feature(True) == (True, "")
    assert registry.read_feature_configuration(environ={}).enabled is True
    assert calls[-1] == [
        "systemctl", "--no-block", "restart", "metnos-durable-worker.service",
    ]


def test_snapshots_use_installed_targets(monkeypatch, tmp_path):
    _verified_readiness_chain(monkeypatch, tmp_path)
    observed = []

    def snapshot(service, _probe, *_args):
        observed.extend(service.targets)
        return {"key": service.key, "status": "running"}

    monkeypatch.setattr(registry, "_safe_snapshot", snapshot)
    assert len(registry.snapshots(probe_endpoints=False)) == len(registry.SERVICES)
    assert observed and all(target.scope == "system" for target in observed)


def test_readiness_catalog_never_falls_back_after_chain_failure(monkeypatch, tmp_path):
    import executor_birth_ownership_chain as ownership

    monkeypatch.setattr(ownership, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", tmp_path)
    monkeypatch.setattr(
        ownership, "inspect_ownership_chain_state_v1",
        lambda: (_ for _ in ()).throw(ownership.OwnershipChainError("invalid-chain")),
    )
    with pytest.raises(ownership.OwnershipChainError):
        registry.readiness_catalog()


def test_readiness_catalog_never_falls_back_after_cold_chain_failure(
    monkeypatch, tmp_path,
):
    import executor_birth_ownership_chain as ownership

    _verified_readiness_chain(monkeypatch, tmp_path)

    def invalid_chain(_self):
        raise ownership.OwnershipChainError("invalid-required-chain")

    monkeypatch.setattr(
        ownership.OwnershipChainStore, "read_required_window_v1", invalid_chain,
    )
    with pytest.raises(ownership.OwnershipChainError):
        registry.readiness_catalog()


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() != 0,
    reason="real root/nonroot permissions: run in a disposable root guest",
)
def test_readiness_nonroot_does_not_read_root_only_mutation_lock(monkeypatch):
    """Exercise real UID/DAC and consumer routing, not mocked chain crypto."""
    import multiprocessing
    import tempfile
    import executor_birth_ownership_chain as ownership

    with tempfile.TemporaryDirectory(prefix="metnos-readiness-test-", dir="/run") as name:
        root = Path(name)
        root.chmod(0o755)
        _verified_readiness_chain(monkeypatch, root)
        lock = root / ownership.REQUIRED_HEAD_LOCK_BASENAME
        lock.write_bytes(b"\0")
        lock.chmod(0o600)
        before = lock.stat()
        assert (before.st_uid, before.st_gid, before.st_mode & 0o777) == (0, 0, 0o600)
        parent, child = multiprocessing.Pipe(duplex=False)

        def read_as_service():
            try:
                os.setgroups([])
                os.setresgid(65534, 65534, 65534)
                os.setresuid(65534, 65534, 65534)
                assert os.geteuid() == 65534
                with pytest.raises(PermissionError):
                    lock.read_bytes()
                # The previous inspector fails at its real lock reader.
                with pytest.raises(ownership.OwnershipChainError) as failure:
                    ownership._require_required_head_lock_metadata_v1(
                        root, root_owned=True,
                    )
                assert failure.value.detail == "required lock metadata"
                profile = {spec.key: spec for spec in registry.readiness_catalog()}
                assert profile["playwright"].targets[0].scope == "system"
                child.send(("ok", os.geteuid()))
            except BaseException as exc:
                child.send(("error", repr(exc)))
            finally:
                child.close()

        process = multiprocessing.get_context("fork").Process(target=read_as_service)
        process.start()
        child.close()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("nonroot readiness reader did not terminate")
        assert process.exitcode == 0
        assert parent.poll(2)
        assert parent.recv() == ("ok", 65534)
        parent.close()
        after = lock.stat()
        assert (after.st_ino, after.st_uid, after.st_gid, after.st_mode, after.st_mtime_ns) == (
            before.st_ino, before.st_uid, before.st_gid, before.st_mode, before.st_mtime_ns,
        )


def test_readiness_catalog_rejects_missing_signed_service(monkeypatch, tmp_path):
    import executor_birth_service_catalog as catalog

    _verified_readiness_chain(monkeypatch, tmp_path)
    monkeypatch.setattr(
        catalog, "capture_current_service_catalog_v1",
        lambda _: SimpleNamespace(catalog=SimpleNamespace(entries=())),
    )
    with pytest.raises(ValueError, match="uniquely signed"):
        registry.readiness_catalog()


def test_endpoints_have_one_canonical_default(monkeypatch):
    monkeypatch.delenv("METNOS_SEARXNG_URL", raising=False)
    assert registry.endpoint("searxng") == "http://127.0.0.1:8888"
    monkeypatch.setenv("METNOS_SEARXNG_URL", "http://search.internal:9999/")
    assert registry.endpoint("searxng") == "http://search.internal:9999"
    assert registry.endpoint("not_in_catalog") == ""


def test_polkit_rule_is_derived_and_strict():
    rule = registry.render_polkit_rule("metnos-test")
    assert 'subject.user !== "metnos-test"' in rule
    for unit in registry.system_units():
        assert f'"{unit}"' in rule
    assert '"start", "stop", "restart"' in rule
    assert "*" not in rule
    assert "manage-unit-files" not in rule


def test_resolve_target_uses_loaded_alternative(monkeypatch):
    def fake_run(cmd, **kwargs):
        unit = next(arg for arg in cmd if arg.endswith(".service"))
        if "--user" in cmd:
            return _show(unit=unit, load="not-found", active="inactive")
        return _show(unit=unit)

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    row = registry.resolve_target(registry.get("http"))
    assert row["scope"] == "system"
    assert row["active_state"] == "active"


def test_resolve_target_prefers_active_system_baseline_over_loaded_user_unit(
        monkeypatch):
    def fake_run(cmd, **kwargs):
        unit = next(arg for arg in cmd if arg.endswith(".service"))
        if "--user" in cmd:
            return _show(unit=unit, load="loaded", active="inactive")
        return _show(unit=unit, load="loaded", active="active")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    row = registry.resolve_target(registry.get("http"))
    assert row["scope"] == "system"
    assert row["active_state"] == "active"


def test_llm_resolves_supported_system_installation(monkeypatch):
    """Upgrade hosts may still own llama-server as a system unit."""
    def fake_run(cmd, **kwargs):
        unit = next(arg for arg in cmd if arg.endswith(".service"))
        if unit == "metnos-llm.service":
            return _show(unit=unit, load="not-found", active="inactive")
        if unit == "llama-server.service" and "--user" not in cmd:
            return _show(unit=unit, load="loaded", active="active")
        raise AssertionError(f"unexpected target: {cmd}")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    row = registry.resolve_target(registry.get("llm"))
    assert row["unit"] == "llama-server.service"
    assert row["scope"] == "system"
    assert row["active_state"] == "active"


def test_durable_worker_disabled_by_policy_is_running_and_healthy(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec, **_kwargs: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": "42",
            "active_since": "", "unit_state": "enabled",
        },
    )
    import durable_workloads.service as durable_service

    monkeypatch.setattr(durable_service, "health_snapshot", lambda: {
        "state": "degraded", "enabled": False,
        "reason_code": "feature_disabled",
    })
    row = registry.snapshot_one(registry.get("durable_workloads"))
    assert (row["status"], row["healthy"], row["health_detail"]) == (
        "running", True, "feature_disabled",
    )
    assert row["feature_converged"] is True
    assert row["health_message_key"] == (
        "UI_SERVICES_LRE_HEALTH_FEATURE_DISABLED"
    )


def test_durable_worker_detects_config_process_mismatch(monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec, **_kwargs: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": "42",
            "active_since": "", "unit_state": "enabled",
        },
    )
    import durable_workloads.service as durable_service

    monkeypatch.setattr(durable_service, "health_snapshot", lambda: {
        "state": "ready", "enabled": True, "reason_code": "none",
    })

    row = registry.snapshot_one(registry.get("durable_workloads"))

    assert row["status"] == "degraded"
    assert row["healthy"] is False
    assert row["feature_converged"] is False
    assert row["health_detail"] == "feature_state_mismatch"


def _installed_lre(monkeypatch):
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
        },
    )


def test_lre_feature_enable_is_persisted_and_restarts_exact_unit(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    _installed_lre(monkeypatch)
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.configure_lre_feature(True) == (True, "")
    assert registry.read_feature_configuration(environ={}).enabled is True
    assert seen == [[
        "systemctl", "--user", "--no-block", "restart",
        "metnos-durable-worker.service",
    ]]


def test_lre_feature_environment_override_cannot_be_changed_from_ui(
        monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_DURABLE_WORKLOADS_ENABLED", "0")
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    _installed_lre(monkeypatch)
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("systemctl must not run")
        ),
    )

    ok, detail = registry.configure_lre_feature(True)

    assert ok is False
    assert "environment" in detail
    assert registry.read_feature_configuration(environ={}).enabled is False


def test_lre_feature_enable_rolls_back_if_restart_is_rejected(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    _installed_lre(monkeypatch)
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="restart rejected",
        ),
    )

    assert registry.configure_lre_feature(True) == (
        False, "restart rejected",
    )
    assert registry.read_feature_configuration(environ={}).enabled is False


def test_lre_feature_disable_stays_closed_if_restart_is_rejected(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(True)
    _installed_lre(monkeypatch)
    seen = []

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        code = 1 if "restart" in cmd else 0
        return subprocess.CompletedProcess(
            cmd, code, stdout="", stderr="restart rejected" if code else "",
        )

    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.configure_lre_feature(False) == (
        False, "restart rejected",
    )
    assert registry.read_feature_configuration(environ={}).enabled is False
    assert [command[-2] for command in seen] == ["restart", "stop"]


def test_lre_feature_control_rejects_a_target_outside_the_catalog(
        monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec: {
            "unit": "unrelated.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
        },
    )
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("systemctl must not run")
        ),
    )

    ok, detail = registry.configure_lre_feature(True)

    assert ok is False
    assert detail == "LRE service unit is not installed"
    assert registry.read_feature_configuration(environ={}).enabled is False


def test_user_manager_gets_explicit_bus_environment(monkeypatch):
    monkeypatch.setenv("METNOS_SERVICE_USER", "test-user")
    monkeypatch.setattr(
        registry.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1234),
    )
    cmd, env = registry._systemctl(
        registry.ServiceTarget("metnos-test.service", "user"), "show",
    )
    assert cmd[:2] == ["systemctl", "--user"]
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1234/bus"


def test_root_uses_declared_account_for_user_manager(monkeypatch):
    monkeypatch.setenv("METNOS_SERVICE_USER", "test-user")
    monkeypatch.setattr(
        registry.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1234),
    )
    monkeypatch.setattr(registry.os, "geteuid", lambda: 0)
    cmd, env = registry._systemctl(
        registry.ServiceTarget("metnos-test.service", "user"), "show",
    )
    assert cmd[:5] == [
        "runuser", "--user", "test-user", "--", "systemctl",
    ]
    assert cmd[5:] == ["--user", "show"]
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"


def test_control_rejects_unknown_unit_without_systemctl(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("systemctl must not run")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    assert registry.control("../../ssh", "restart") == (
        False, "invalid service action",
    )
    assert called is False


def test_required_i18n_timer_cannot_be_stopped(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("systemctl must not run")

    monkeypatch.setattr(
        registry, "resolve_target", lambda _: {
            "unit": "metnos-i18n-translator.timer", "scope": "user",
            "load_state": "loaded", "active_state": "active",
        },
    )
    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert registry.control("i18n", "stop") == (
        False, "service action is unavailable from this control plane",
    )
    assert called is False
    assert registry.desired_state("i18n") == "running"


def test_integrated_component_control_stays_on_catalog_target(monkeypatch):
    monkeypatch.setattr(
        registry, "resolve_target", lambda _: {
            "unit": "metnos-playwright.service", "scope": "user",
            "load_state": "loaded",
        },
    )
    recorded = []
    monkeypatch.setattr(
        registry, "_record_desired_state",
        lambda key, state: recorded.append((key, state)),
    )
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    assert registry.control("playwright", "restart") == (True, "")
    assert seen["cmd"][-3:] == [
        "--no-block", "restart", "metnos-playwright.service",
    ]
    assert recorded == [("playwright", "running")]


def test_control_is_non_blocking_and_uses_resolved_target(monkeypatch):
    spec = registry.ServiceSpec(
        "standalone", "Standalone", "Standalone test service", "Test",
        (registry.ServiceTarget("standalone.service", "user"),),
    )
    monkeypatch.setattr(
        registry, "resolve_target", lambda _: {
            "unit": "standalone.service", "scope": "user",
            "load_state": "loaded",
        },
    )
    monkeypatch.setitem(registry._BY_KEY, "standalone", spec)
    monkeypatch.setattr(registry, "catalog", lambda: (spec,))
    monkeypatch.setattr(registry, "_record_desired_state", lambda *_: None)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)
    ok, detail = registry.control("standalone", "restart")
    assert ok is True and detail == ""
    assert seen["cmd"][-3:] == [
        "--no-block", "restart", "standalone.service",
    ]


def test_template_renders_catalog_and_missing_services(monkeypatch):
    rows = []
    for spec in registry.catalog():
        row = {
            **registry.snapshot_one(spec, probe_endpoint=False),
            "load_state": "not-found", "installed": False,
            "actionable": False, "status": "missing",
        }
        rows.append(row)
    import i18n

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "it")
    html_it = render_template(
        "services.html", services=registry.localized(rows, "it"), notice="",
    )
    assert "SearXNG" in html_it
    assert "Server geografico" in html_it
    assert "Stato operativo dei servizi" in html_it
    assert "Non installato" in html_it
    assert "missing:UI_SERVICES" not in html_it
    assert html_it.index("<h2>Nucleo</h2>") < html_it.index(
        "<h2>Navigazione web</h2>",
    )

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
    html_en = render_template(
        "services.html", services=registry.localized(rows, "en"), notice="",
    )
    assert "Geo server" in html_en
    assert "Service operational status" in html_en
    assert "Not installed" in html_en
    assert "missing:UI_SERVICES" not in html_en


def test_template_distinguishes_start_from_restart():
    common = {
        "group": "Test", "description": "Service test", "scope": "user",
        "sub_state": "dead", "healthy": None, "health_detail": "",
        "installed": True, "unit": "test.service", "main_pid": "0",
        "managed_by": "", "desired_state": "running",
        "in_desired_state": False, "active_since": "",
        "actionable": True, "allowed_actions": ["start", "stop", "restart"],
    }
    rows = [
        {
            **common, "key": "stopped", "label": "Stopped service",
            "status": "stopped", "active_state": "inactive",
        },
        {
            **common, "key": "running", "label": "Running service",
            "status": "running", "active_state": "active",
        },
    ]
    html = render_template(
        "services.html", services=rows, notice="", notifications=[],
    )
    stopped = html.split('id="service-stopped"', 1)[1].split("</article>", 1)[0]
    running = html.split('id="service-running"', 1)[1].split("</article>", 1)[0]
    assert "/admin/services/stopped/start" in stopped
    assert "/admin/services/stopped/stop" not in stopped
    assert "/admin/services/stopped/restart" not in stopped
    assert "/admin/services/running/start" not in running
    assert "/admin/services/running/stop" in running
    assert "/admin/services/running/restart" in running


def test_template_exposes_the_closed_lre_feature_control(monkeypatch, tmp_path):
    monkeypatch.delenv("METNOS_DURABLE_WORKLOADS_ENABLED", raising=False)
    monkeypatch.setattr(registry._C, "PATH_USER_CONFIG", tmp_path)
    registry.write_feature_configuration(False)
    monkeypatch.setattr(
        registry, "resolve_target", lambda _spec, **_kwargs: {
            "unit": "metnos-durable-worker.service", "scope": "user",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "main_pid": "42",
            "active_since": "", "unit_state": "enabled",
        },
    )
    import durable_workloads.service as durable_service

    monkeypatch.setattr(durable_service, "health_snapshot", lambda: {
        "state": "degraded", "enabled": False,
        "reason_code": "feature_disabled",
    })
    row = registry.snapshot_one(registry.get("durable_workloads"))
    import i18n

    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "it")
    italian = render_template(
        "services.html", services=registry.localized([row], "it"),
        notice="", notifications=[],
    )
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
    english = render_template(
        "services.html", services=registry.localized([row], "en"),
        notice="", notifications=[],
    )

    assert "/admin/services/durable_workloads/feature/enable" in italian
    assert "Attiva LRE" in italian
    assert "feature_disabled" not in italian
    assert "Enable LRE" in english
    assert "missing:UI_SERVICES" not in italian + english
    for rendered in (italian, english):
        card = rendered.split('id="service-durable_workloads"', 1)[1].split("</article>", 1)[0]
        assert 'class="dot disabled"' in card
        assert 'class="dot running"' not in card
        assert '<details class="service-process">' in card
        assert card.index('role="status"') < card.index('<details')


@pytest.mark.parametrize('lang', ['it', 'en'])
@pytest.mark.parametrize('overrides, expected', [
    ({}, 'running'),
    ({'feature_enabled': False, 'health_detail': 'feature_disabled'}, 'disabled'),
    ({'feature_enabled': False, 'feature_converged': False}, 'transitioning'),
    ({'feature_converged': False, 'healthy': False}, 'transitioning'),
    ({'feature_converged': None, 'healthy': None}, 'transitioning'),
    ({'status': 'transitioning'}, 'transitioning'),
    ({'feature_config_valid': False}, 'failed'),
    ({'feature_config_valid': None}, 'degraded'),
    ({'status': 'failed', 'feature_enabled': False}, 'failed'),
    ({'status': 'degraded', 'healthy': False}, 'degraded'),
    ({'feature_enabled': False, 'status': 'degraded', 'healthy': False}, 'degraded'),
    ({'healthy': None}, 'degraded'),
    ({'feature_enabled': None}, 'degraded'),
    ({'installed': False, 'status': 'missing'}, 'missing'),
])
def test_lre_indicator_requires_verified_functional_readiness(monkeypatch, lang, overrides, expected):
    import i18n

    monkeypatch.setattr(i18n._C, 'INSTANCE_LANG', lang)
    row = {
        'key': 'durable_workloads', 'label': 'LRE', 'description': '',
        'group': 'Test', 'scope': 'system', 'status': 'running',
        'active_state': 'active', 'sub_state': 'running', 'healthy': True,
        'health_detail': '', 'health_message_key': '', 'installed': True,
        'unit': 'metnos-durable-worker.service', 'main_pid': '42',
        'desired_state': 'running', 'in_desired_state': True,
        'managed_by': '', 'active_since': '', 'actionable': False,
        'allowed_actions': [], 'feature_enabled': True,
        'feature_config_valid': True, 'feature_converged': True,
        'feature_configurable': False, **overrides,
    }
    before = dict(row)
    rendered = render_template('services.html', services=[row], notice='', notifications=[])
    card = rendered.split('id="service-durable_workloads"', 1)[1].split('</article>', 1)[0]
    assert f'class="dot {expected}"' in card
    assert row == before  # Presentation never changes supervision/health semantics.
    assert 'missing:UI_' not in card
    if row['installed']:
        main, technical = card.split('<details class="service-process">', 1)
        assert 'role="status"' in main
        assert ('Dettagli tecnici' if lang == 'it' else 'Technical details') in technical
        if expected == 'disabled':
            assert ('Disattivato' if lang == 'it' else 'Disabled') in main
        if expected != 'running':
            assert 'class="chip ok"' not in card


def test_snapshot_failure_is_isolated(monkeypatch):
    broken = registry.catalog()[0]

    def fake_snapshot(spec, *, probe_endpoint=True, deadline_at=None):
        if spec is broken:
            raise RuntimeError("broken probe")
        return {"key": spec.key, "installed": True}

    monkeypatch.setattr(registry, "snapshot_one", fake_snapshot)
    rows = registry.snapshots()
    assert len(rows) == len(registry.catalog())
    assert rows[0]["status"] == "failed"
    assert rows[1]["key"] == registry.catalog()[1].key


def test_snapshot_pool_is_reused(monkeypatch):
    monkeypatch.setattr(registry, "snapshot_one",
                        lambda spec, *, probe_endpoint=True, deadline_at=None:
                        {"key": spec.key, "installed": True})
    first = registry._snapshot_pool()
    registry.snapshots(probe_endpoints=False)
    second = registry._snapshot_pool()
    assert first is second

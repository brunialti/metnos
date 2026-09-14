"""Managed dependencies remain signed, consented, and retried once."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent_runtime
import orchestration
from loader import ManagedDependency, _managed_dependencies


def _start_dialog(package_id: str) -> dict:
    from executors.run_processes import run_processes

    return {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": run_processes._approval_dialog([package_id], "133700000000000000"),
    }


def test_dependency_is_resolved_from_manifest_not_executor_output():
    executor = SimpleNamespace(managed_dependencies=(
        ManagedDependency("thermal_provider", "Vendor.Sensor"),
    ))
    observation = {
        "ok": False,
        "error_class": "managed_dependency_inactive",
        "managed_dependency": "thermal_provider",
        "package_id": "Attacker.Command",
    }

    assert agent_runtime._declared_managed_package(
        executor, observation) == "Vendor.Sensor"


def test_manifest_dependency_shape_is_closed():
    assert _managed_dependencies([{
        "key": "thermal_provider",
        "package_id": "Vendor.Sensor",
    }]) == (ManagedDependency("thermal_provider", "Vendor.Sensor"),)

    for malformed in (
        {"key": "thermal_provider", "package_id": "Vendor.Sensor"},
        [{"key": "thermal_provider", "package_id": "Vendor.Sensor",
          "command": "cmd.exe"}],
        [{"key": "thermal_provider", "package_id": "../tool.exe"}],
    ):
        try:
            _managed_dependencies(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted malformed dependency: {malformed!r}")


def test_provider_dependency_has_one_closed_lazy_interface():
    dependency = _managed_dependencies([{
        "key": "hardware_sensor_provider",
        "package_id": "Vendor.Sensor",
        "mode": "provider",
        "interface": "hardware_sensors_v1",
        "domains_arg": "sensor_domains",
        "sensor_types_arg": "sensor_types",
        "assembly": "Vendor.SensorLib.dll",
        "entry_type": "Vendor.Sensor.Computer",
    }])[0]

    assert dependency == ManagedDependency(
        "hardware_sensor_provider",
        "Vendor.Sensor",
        "provider",
        "hardware_sensors_v1",
        "sensor_domains",
        "sensor_types",
        "Vendor.SensorLib.dll",
        "Vendor.Sensor.Computer",
    )
    for field, value in (
        ("mode", "process"),
        ("interface", "../script"),
        ("domains_arg", "sensor-domains"),
        ("sensor_types_arg", "sensor-types"),
        ("assembly", "../Sensor.dll"),
        ("entry_type", "Vendor..Computer"),
    ):
        row = {
            "key": "hardware_sensor_provider",
            "package_id": "Vendor.Sensor",
            "mode": "provider",
            "interface": "hardware_sensors_v1",
            "domains_arg": "sensor_domains",
            "sensor_types_arg": "sensor_types",
            "assembly": "Vendor.SensorLib.dll",
            "entry_type": "Vendor.Sensor.Computer",
        }
        row[field] = value
        try:
            _managed_dependencies([row])
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid provider dependency: {row!r}")


def test_provider_dependency_never_enters_process_start_flow():
    executor = SimpleNamespace(managed_dependencies=(ManagedDependency(
        "hardware_sensor_provider", "Vendor.Sensor", "provider",
        "hardware_sensors_v1", "sensor_domains", "sensor_types",
        "Vendor.SensorLib.dll", "Vendor.Sensor.Computer",
    ),))
    observation = {
        "error_class": "managed_dependency_inactive",
        "managed_dependency": "hardware_sensor_provider",
    }

    assert agent_runtime._declared_managed_package(executor, observation) == ""


@pytest.mark.parametrize("observed_device", [None, "immutable-device-id"])
def test_start_dialog_is_bound_to_one_exact_retry(monkeypatch, observed_device):
    monkeypatch.setattr(
        "executors.run_processes.run_processes._machine_name",
        lambda: "PC-TEST",
    )
    result = _start_dialog("Vendor.Sensor")
    if observed_device:
        result["needs_inputs"]["on_complete"]["target_device"] = observed_device
    bound = agent_runtime._bind_managed_dependency_resume(
        result,
        package_id="Vendor.Sensor",
        resume_tool="get_processes",
        resume_args={
            "sensor_domains": ["cpu"],
            "sensor_types": ["temperature"],
            "top": 1,
        },
        target_device="PC-TEST",
        starter_tool="run_processes",
    )

    callback = bound["needs_inputs"]["on_complete"]
    assert callback["type"] == "managed_dependency_resume"
    assert callback["resume"] == {
        "tool": "get_processes",
        "args": {
            "sensor_domains": ["cpu"],
            "sensor_types": ["temperature"],
            "top": 1,
        },
    }
    assert callback["target_device"] == (observed_device or "PC-TEST")
    assert set(callback["branches"]) == {"once", "until_restart", "always"}


def test_tampered_start_branch_is_rejected():
    result = _start_dialog("Vendor.Sensor")
    result["needs_inputs"]["on_complete"]["branches"]["until_restart"][
        "args"]["programs"] = ["Other.Package"]

    assert agent_runtime._bind_managed_dependency_resume(
        result,
        package_id="Vendor.Sensor",
        resume_tool="get_processes",
        resume_args={
            "sensor_domains": ["cpu"],
            "sensor_types": ["temperature"],
        },
        target_device="PC-TEST",
        starter_tool="run_processes",
    ) is None


def test_managed_starter_is_selected_by_unique_capability_hint():
    starter = SimpleNamespace(
        name="implementation_can_be_renamed",
        capabilities=[{"name": "system:admin",
                       "hint": ["managed-package-start"]}],
    )
    unrelated = SimpleNamespace(
        name="another_executor",
        capabilities=[{"name": "system:read", "hint": ["inventory"]}],
    )

    assert agent_runtime._unique_executor_with_capability_hint(
        [unrelated, starter], "managed-package-start") is starter
    assert agent_runtime._unique_executor_with_capability_hint(
        [starter, SimpleNamespace(
            name="ambiguous",
            capabilities=[{"hint": ["managed-package-start"]}],
        )], "managed-package-start") is None


def test_completion_starts_then_retries_once_on_same_device(monkeypatch):
    calls = []
    waits = []

    def invoke(tool, args, **kwargs):
        calls.append((tool, args, kwargs.get("target_device")))
        if tool == "run_processes":
            return {"ok": True, "results": [{"package_id": "Vendor.Sensor"}]}
        return {
            "ok": True,
            "health": {"thermal": {"available": True, "cpu_c": 51.2}},
            "_ran_on_device": "PC-TEST",
        }

    monkeypatch.setattr(orchestration, "_esegui_ramo", invoke)
    monkeypatch.setattr(orchestration.time, "sleep", waits.append)
    monkeypatch.setattr(
        orchestration, "_fmt_health_block",
        lambda health, host="": f"{host}:{health['thermal']['cpu_c']}",
    )
    callback = {
        "branches": {
            "until_restart": {
                "tool": "run_processes",
                "args": {"programs": ["Vendor.Sensor"], "lifetime": "session"},
            },
        },
        "resume": {
            "tool": "get_processes",
            "args": {
                "sensor_domains": ["cpu"],
                "sensor_types": ["temperature"],
                "top": 1,
            },
        },
        "target_device": "PC-TEST",
        "owner_user_id": "owner-1",
    }

    rendered = orchestration._process_managed_dependency_resume(
        callback, {"decision": "until_restart"}, actor="host", channel="http")

    assert rendered == "PC-TEST:51.2"
    assert [call[:2] for call in calls] == [
        ("run_processes", callback["branches"]["until_restart"]["args"]),
        ("get_processes", callback["resume"]["args"]),
    ]
    assert all(call[2] == "PC-TEST" for call in calls)
    assert waits == [orchestration._MANAGED_DEPENDENCY_SETTLE_S]

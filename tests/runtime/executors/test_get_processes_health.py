"""Test FIX 2 (6/5/2026): get_processes(include_health=true).

Verifica che il flag aggiunga il dict top-level `health` con quattro
sezioni (load/memory/disk/services), che senza flag il comportamento
resti invariato, e che `services_extra` accodi unit aggiuntivi.

I test sono robusti rispetto all'ambiente: la sezione load richiede
`os.getloadavg` (POSIX), memory e disk richiedono /proc/meminfo e
/proc/mounts. Su sistemi non-Linux i campi `available=False` sono
dichiarati, ma i test girano comunque (skip condizionale).
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

_EXEC_DIR = Path(__file__).resolve().parents[3] / "executors/get_processes"
sys.path.insert(0, str(_EXEC_DIR))


def test_include_health_false_omits_health_dict():
    """Default: niente sezione health nell'output."""
    from get_processes import invoke
    r = invoke({"top": 5})
    assert r["ok"] is True
    assert "health" not in r
    assert "entries" in r


def test_include_health_true_adds_health_dict_with_four_sections():
    """Con flag: dict health con load, memory, disk, services."""
    from get_processes import invoke
    r = invoke({"top": 3, "include_health": True})
    assert r["ok"] is True
    assert "health" in r
    h = r["health"]
    for k in ("load", "memory", "disk", "services", "collected_at"):
        assert k in h, f"sezione mancante: {k}"


@pytest.mark.skipif(not Path("/proc/uptime").exists(),
                     reason="non-Linux: /proc/uptime mancante")
def test_health_load_has_uptime_and_loadavg():
    from get_processes import invoke
    r = invoke({"top": 1, "include_health": True})
    load = r["health"]["load"]
    assert load["available"] is True
    assert "1m" in load and "5m" in load and "15m" in load
    assert load["uptime_s"] >= 0


@pytest.mark.skipif(not Path("/proc/meminfo").exists(),
                     reason="non-Linux: /proc/meminfo mancante")
def test_health_memory_pct_in_range():
    from get_processes import invoke
    r = invoke({"top": 1, "include_health": True})
    mem = r["health"]["memory"]
    assert mem["available"] is True
    assert mem["total_mb"] > 0
    assert 0.0 <= mem["pct"] <= 100.0
    assert mem["used_mb"] + mem["free_mb"] <= mem["total_mb"] + 1  # rounding tol


@pytest.mark.skipif(not Path("/proc/mounts").exists(),
                     reason="non-Linux: /proc/mounts mancante")
def test_health_disk_includes_root_and_skips_virtuals():
    from get_processes import invoke
    r = invoke({"top": 1, "include_health": True})
    disks = r["health"]["disk"]
    # Almeno il root dev'esserci (filesystem reale).
    mounts = {d["mount"] for d in disks}
    assert "/" in mounts, f"root non trovato fra: {sorted(mounts)}"
    # Nessun fstype virtuale.
    fstypes = {d["fstype"] for d in disks}
    assert not (fstypes & {"tmpfs", "devtmpfs", "proc", "sysfs"})


def test_health_services_includes_metnos_units():
    """Le unit canoniche sono presenti; nessuno stato co-host è inventato."""
    from get_processes import invoke
    r = invoke({"top": 1, "include_health": True})
    services = r["health"]["services"]
    names = {s["name"] for s in services}
    assert "metnos-http" in names
    assert "metnos-telegram-daemon" in names
    # Una chiamata diretta non vive nel processo HTTP e non possiede quindi
    # l'osservazione runtime-owned del loop co-hosted.
    assert "scheduler" not in names
    # Status valore atteso (active/inactive/failed/unknown/...)
    for s in services:
        assert isinstance(s["status"], str)


def test_scheduler_status_comes_from_runtime_observation_not_recent_runs():
    from get_processes import invoke

    observation = {
        "state": "running", "healthy": True,
        "reason_code": "loop_active", "cohost": "http",
        "heartbeat_at": "2026-07-29T12:00:00Z",
        "heartbeat_age_s": 0.4, "jobs_total": 15, "jobs_enabled": 9,
        "jobs_running": 0, "last_run_at": "", "last_run_status": "",
    }
    result = invoke({
        "top": 1, "include_health": True,
        "scheduler_health": observation,
    })
    scheduler = next(
        row for row in result["health"]["services"]
        if row["name"] == "scheduler"
    )
    assert scheduler["status"] == "active"
    assert scheduler["healthy"] is True
    assert scheduler["reason_code"] == "loop_active"
    # L'assenza di un run recente non cambia la salute del loop.
    assert scheduler["last_run_at"] == ""


def test_scheduler_failure_preserves_attested_reason():
    from get_processes import invoke

    result = invoke({
        "top": 1, "include_health": True,
        "scheduler_health": {
            "state": "failed", "healthy": False,
            "reason_code": "loop_failed", "error_class": "RuntimeError",
            "error_summary": "storage unavailable",
        },
    })
    scheduler = next(
        row for row in result["health"]["services"]
        if row["name"] == "scheduler"
    )
    assert scheduler["status"] == "failed"
    assert scheduler["healthy"] is False
    assert scheduler["reason_code"] == "loop_failed"
    assert scheduler["error_class"] == "RuntimeError"


def test_services_extra_accodato():
    """`services_extra` aggiunge unit a quelli base."""
    from get_processes import invoke
    r = invoke({"top": 1, "include_health": True,
                "services_extra": ["headscaled"]})
    names = {s["name"] for s in r["health"]["services"]}
    assert "headscaled" in names
    assert "metnos-http" in names  # base preservati


def test_services_extra_must_be_list():
    """services_extra non lista → ok:false."""
    from get_processes import invoke
    r = invoke({"include_health": True, "services_extra": "headscaled"})
    assert r["ok"] is False
    assert "services_extra" in r["failed"][0]["error"]


def test_health_collected_at_recent():
    """collected_at è un timestamp recente (entro ±10s)."""
    import time
    from get_processes import invoke
    before = int(time.time())
    r = invoke({"include_health": True, "top": 1})
    after = int(time.time())
    ts = r["health"]["collected_at"]
    assert before - 1 <= ts <= after + 1


def test_processes_still_there_when_include_health_true():
    """Il flag non altera la sezione processes."""
    from get_processes import invoke
    r = invoke({"top": 5, "include_health": True})
    assert r["ok_count"] >= 1
    assert len(r["entries"]) >= 1
    # Schema invariato.
    e = r["entries"][0]
    for k in ("pid", "ppid", "name", "cmd", "user", "cpu_pct", "mem_pct"):
        assert k in e


def test_windows_thermal_lhm_identifiers_are_semantically_classified():
    from get_processes import _normalise_windows_thermal

    result = _normalise_windows_thermal({
        "source": "managed_provider",
        "sensors": [
            {"name": "Core 0", "identifier": "/intelcpu/0/temperature/0",
             "value_c": 51.2},
            {"name": "Package", "identifier": "/intelcpu/0/temperature/5",
             "value_c": 59.8},
            {"name": "Hot Spot", "identifier": "/gpu-nvidia/0/temperature/1",
             "value_c": 63},
            {"name": "Composite", "identifier": "/nvme/0/temperature/0",
             "value_c": 42},
        ],
    })

    assert result["available"] is True
    assert result["quality"] == "hardware_sensor"
    assert result["cpu_c"] == 59.8  # massimo package/core, conservativo
    assert result["gpu_c"] == 63.0
    assert result["nvme_c"] == 42.0


def test_windows_acpi_zone_is_never_claimed_as_cpu_temperature():
    from get_processes import _normalise_windows_thermal

    result = _normalise_windows_thermal({
        "source": "windows_acpi",
        "sensors": [{
            "name": "ACPI\\ThermalZone\\TZ00_0",
            "identifier": "ACPI\\ThermalZone\\TZ00_0",
            "value_c": 31.9,
        }],
    })

    assert result["available"] is True
    assert result["quality"] == "generic_zone"
    assert result["sensors"][0]["kind"] == "acpi"
    assert "cpu_c" not in result


def test_windows_thermal_probe_is_one_bounded_powershell_call(monkeypatch):
    import get_processes as gp

    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=(
                '{"source":"windows_acpi","sensors":'
                '[{"name":"TZ00","identifier":"ACPI/TZ00",'
                '"value_c":31.9}]}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(gp.shutil, "which", lambda name: "powershell.exe")
    monkeypatch.setattr(gp.subprocess, "run", fake_run)
    monkeypatch.setattr(gp, "managed_provider_result", lambda key: None)

    hardware = gp._normalise_hardware_sensors(
        gp.managed_provider_result("hardware_sensor_provider"),
        ("cpu",),
        ("temperature",),
    )
    result = gp._read_thermal_windows(hardware)

    assert result["available"] is True
    assert "cpu_c" not in result
    assert observed["argv"][:4] == [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive"]
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 6.0
    assert "env" not in observed["kwargs"]


def test_windows_thermal_uses_typed_provider_without_a_subprocess(monkeypatch):
    import get_processes as gp

    monkeypatch.setattr(gp, "managed_provider_result", lambda key: {
        "ok": True,
        "payload": {"sensors": [{
            "domain": "cpu",
            "kind": "temperature",
            "name": "CPU Package",
            "identifier": "/intelcpu/0/temperature/0",
            "value": 55.5,
            "unit": "°C",
        }]},
    })
    monkeypatch.setattr(
        gp.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider success must not spawn PowerShell")),
    )

    hardware = gp._normalise_hardware_sensors(
        gp.managed_provider_result("hardware_sensor_provider"),
        ("cpu",),
        ("temperature",),
    )
    result = gp._read_thermal_windows(hardware)

    assert result["available"] is True
    assert result["source"] == "managed_provider"
    assert result["cpu_c"] == 55.5


def test_windows_thermal_probe_timeout_is_explicit(monkeypatch):
    import get_processes as gp

    monkeypatch.setattr(gp.shutil, "which", lambda name: "powershell.exe")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=6)

    monkeypatch.setattr(gp.subprocess, "run", timeout)
    result = gp._read_thermal_windows()
    assert result == {
        "available": False,
        "source": "none",
        "quality": "unavailable",
        "reason_code": "thermal_probe_timeout",
    }


def test_required_windows_thermal_reports_only_abstract_dependency(monkeypatch):
    import json
    import get_processes as gp

    monkeypatch.setattr(gp.os, "name", "nt")
    monkeypatch.setattr(gp, "_ps_snapshot", lambda: [{
        "pid": 1, "ppid": 0, "name": "System", "cmd": "System",
        "user": "", "cpu_pct": 0.0, "mem_pct": 0.0,
        "started_at": "",
    }])
    monkeypatch.setattr(gp, "_collect_health", lambda *args, **kwargs: {
        "thermal": {
            "available": False,
            "source": "none",
            "reason_code": "no_supported_sensor",
        },
        "hardware_sensors": {
            "available": False,
            "source": "none",
            "sensors": [],
            "reason_code": "no_supported_sensor",
        },
    })

    result = gp.invoke({
        "sensor_domains": ["cpu"],
        "sensor_types": ["temperature"],
        "top": 1,
    })

    assert result["ok"] is False
    assert result["error_class"] == "resource_unavailable"
    assert result["error_code"] == "hardware_sensor_provider_unavailable"
    assert result["managed_dependency"] == "hardware_sensor_provider"
    assert "LibreHardwareMonitor" not in json.dumps(result)


def test_required_windows_thermal_succeeds_when_provider_is_available(monkeypatch):
    import get_processes as gp

    monkeypatch.setattr(gp.os, "name", "nt")
    monkeypatch.setattr(gp, "_ps_snapshot", lambda: [{
        "pid": 1, "ppid": 0, "name": "System", "cmd": "System",
        "user": "", "cpu_pct": 0.0, "mem_pct": 0.0,
        "started_at": "",
    }])
    monkeypatch.setattr(gp, "_collect_health", lambda *args, **kwargs: {
        "thermal": {
            "available": True,
            "source": "managed_provider",
            "cpu_c": 53.4,
        },
        "hardware_sensors": {
            "available": True,
            "source": "managed_provider",
            "sensors": [],
        },
    })

    result = gp.invoke({
        "sensor_domains": ["cpu"],
        "sensor_types": ["temperature"],
        "top": 1,
    })

    assert result["ok"] is True
    assert result["health"]["thermal"]["cpu_c"] == 53.4
    assert "managed_dependency" not in result


def test_windows_thermal_fallback_is_native_and_never_downloads():
    import get_processes as gp

    script = gp._WINDOWS_THERMAL_PS
    assert "root\\OpenHardwareMonitor" not in script
    assert "MSAcpi_ThermalZoneTemperature" in script
    assert "LibreHardwareMonitorLib.dll" not in script
    assert "Invoke-WebRequest" not in script
    assert "Start-BitsTransfer" not in script
    assert "winget" not in script.lower()


def test_acpi_temperature_rendering_is_honest_and_i18n():
    import i18n
    import orchestration

    health = {
        "thermal": {
            "available": True,
            "source": "windows_acpi",
            "quality": "generic_zone",
            "sensors": [{"kind": "acpi", "value_c": 31.9}],
        },
    }
    with i18n.language_context("it"):
        it = orchestration._fmt_health_block(
            health, host="PC-TEST", sections={"thermal"})
    with i18n.language_context("en"):
        en = orchestration._fmt_health_block(
            health, host="PC-TEST", sections={"thermal"})

    assert "ACPI 31.9°C" in it
    assert "CPU 31.9°C" not in it
    assert "Stato PC-TEST" in it
    assert "ACPI 31.9°C" in en
    assert "CPU 31.9°C" not in en
    assert "PC-TEST status" in en


def test_sensor_messages_are_bilingual_in_shipped_i18n_seed():
    import sqlite3

    seed = Path(__file__).resolve().parents[3] / "install/data/i18n_seed.sqlite"
    with sqlite3.connect(seed) as conn:
        rows = dict(conn.execute(
            "SELECT lang, text FROM i18n "
            "WHERE key='MSG_HEALTH_THERMAL_UNAVAILABLE'"
        ).fetchall())
        headers = dict(conn.execute(
            "SELECT lang, text FROM i18n WHERE key='MSG_HEALTH_THERMAL'"
        ).fetchall())
        provider_errors = dict(conn.execute(
            "SELECT lang, text FROM i18n "
            "WHERE key='ERR_HARDWARE_SENSOR_PROVIDER_UNAVAILABLE'"
        ).fetchall())
    assert rows == {
        "it": "(sensori di temperatura non disponibili su questo sistema.)",
        "en": "(temperature sensors are not available on this system.)",
    }
    assert headers == {
        "it": "**Temperature**: {body}",
        "en": "**Temperatures**: {body}",
    }
    assert provider_errors == {
        "it": "Nessun provider compatibile ha restituito i dati dei sensori hardware richiesti.",
        "en": "No compatible provider returned the requested hardware sensor data.",
    }

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
from pathlib import Path

import pytest

_EXEC_DIR = Path("/opt/myclaw/executors/get_processes")
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
    """Almeno i 3 unit Metnos canonici sono interrogati (status libero)."""
    from get_processes import invoke
    r = invoke({"top": 1, "include_health": True})
    services = r["health"]["services"]
    names = {s["name"] for s in services}
    assert "metnos-http" in names
    assert "metnos-telegram-daemon" in names
    assert "metnos-scheduler" in names
    # Status valore atteso (active/inactive/failed/unknown/...)
    for s in services:
        assert isinstance(s["status"], str)


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

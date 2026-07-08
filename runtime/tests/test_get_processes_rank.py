"""test_get_processes_rank.py — ranking robusto a CPU non-misurata (§2.8).

Regressione live 8/7: su Windows `tasklist` non fornisce la CPU%; il codice la
forzava a 0.0 → "0% ovunque" (valore FASULLO) e l'ordinamento "top per CPU" era
privo di senso. Ora cpu_pct=None (onesto) e il ranking ripiega sulla memoria
reale senza crashare confrontando None con float.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "executors", "get_processes"))

import get_processes as gp


def test_rank_none_cpu_falls_back_to_memory():
    # Windows-like: cpu non misurata → ranking per mem_kb reale, niente crash.
    procs = [
        {"pid": 1, "cpu_pct": None, "mem_kb": 500},
        {"pid": 2, "cpu_pct": None, "mem_kb": 9000},
        {"pid": 3, "cpu_pct": None, "mem_kb": 100},
    ]
    procs.sort(key=gp._rank_key, reverse=True)
    assert [p["pid"] for p in procs] == [2, 1, 3]


def test_rank_uses_cpu_when_measured():
    # POSIX-like: cpu misurata → ranking per CPU.
    procs = [
        {"pid": 1, "cpu_pct": 3.0},
        {"pid": 2, "cpu_pct": 50.0},
        {"pid": 3, "cpu_pct": 1.0},
    ]
    procs.sort(key=gp._rank_key, reverse=True)
    assert [p["pid"] for p in procs] == [2, 1, 3]


def test_rank_mixed_measured_over_unmeasured():
    # Chi ha la CPU nota sta sopra chi non ce l'ha (dato piu' informativo).
    procs = [
        {"pid": 1, "cpu_pct": None, "mem_kb": 10 ** 9},
        {"pid": 2, "cpu_pct": 0.1, "mem_kb": 1},
    ]
    procs.sort(key=gp._rank_key, reverse=True)
    assert procs[0]["pid"] == 2, "cpu-nota (anche bassa) sopra cpu-ignota"


def test_tasklist_snapshot_never_fakes_zero_cpu():
    # Il parser tasklist NON deve emettere cpu_pct/mem_pct = 0.0 fasulli.
    import csv as _csv
    import io as _io
    sample = ('"chrome.exe","1234","Console","1","123.456 K"\r\n'
              '"code.exe","5678","Console","1","98.000 K"\r\n')

    class _FakeRun:
        returncode = 0
        stdout = sample
        stderr = ""

    orig = gp.subprocess.run
    gp.subprocess.run = lambda *a, **k: _FakeRun()
    try:
        rows = gp._tasklist_snapshot()
    finally:
        gp.subprocess.run = orig
    assert rows, "parsing tasklist deve produrre righe"
    for r in rows:
        assert r["cpu_pct"] is None, "CPU non misurata → None, mai 0.0 fasullo"
        assert r["mem_pct"] is None, "MEM% non calcolabile → None"
        assert r["mem_kb"] > 0, "la memoria assoluta e' reale (da tasklist)"

"""Test del path subprocess: il runtime invoca executors come subprocess.

Verifica che `python3 executors/get_inputs/get_inputs.py < json` produca
un'observation ben formata. Replica il contratto di `invoke_executor`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNTIME = _ROOT / "runtime"
_CODE = _ROOT / "executors" / "get_inputs" / "get_inputs.py"


@pytest.fixture
def isolated_dialog_dir(tmp_path, monkeypatch):
    """Forza dialog_pending nel subprocess via env var (creato apposta)."""
    # Il modulo dialog_pending non legge env vars per il path; useremo
    # invece HOME redirezionato cosi' DIALOG_DIR finisce in tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    yield tmp_path


def test_subprocess_input_required(isolated_dialog_dir):
    """Esecuzione completa via subprocess. Output JSON parsabile."""
    payload = json.dumps({
        "title": "smoke",
        "dialog": [
            {"var": "x", "prompt": "?", "schema": {"kind": "text"}},
        ],
        "fmt": "dialogue",
    })
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_RUNTIME) + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run(
        ["python3", str(_CODE)],
        input=payload, capture_output=True, text=True, timeout=10, env=env,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    out = json.loads(res.stdout)
    assert out["ok"] is True
    assert out["decision"] == "input_required"
    assert out["step_total"] == 1


def test_subprocess_validates_args(isolated_dialog_dir):
    """Args invalidi rejected via subprocess."""
    payload = json.dumps({"title": "x"})  # missing dialog
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_RUNTIME) + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run(
        ["python3", str(_CODE)],
        input=payload, capture_output=True, text=True, timeout=10, env=env,
    )
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert out["ok"] is False
    assert "dialog" in out["error"]

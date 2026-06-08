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
    # Assert STRUTTURALE language-independent: la fixture isola HOME (per
    # dialog_pending) e questo svuota il DB i18n del subprocess → il testo
    # sarebbe `<missing:ERR_DIALOG_INVALID>`. In produzione (single-user, HOME
    # coerente) il DB i18n è seedato; qui asseriamo l'error_class strutturale.
    assert out["error_class"] == "invalid_args"
    assert out["error"]  # non vuoto (§2.8)


# ── invoke_executor inietta METNOS_ACTOR / METNOS_CHANNEL (12/5/2026) ──
#
# Regression: la pipeline find_events_empty -> get_inputs -> send_messages
# perdeva il dialog state perche' invoke_executor non propagava actor /
# channel come env vars, e get_inputs defaultava a "host" / "" -> sender_id
# diverso da quello con cui l'HTTP cercava il pending alla risposta utente.

# ── invoke_executor inietta METNOS_ACTOR / METNOS_CHANNEL (12/5/2026) ──
#
# Regression: la pipeline find_events_empty -> get_inputs -> send_messages
# perdeva il dialog state perche' invoke_executor non propagava actor /
# channel come env vars, e get_inputs defaultava a sender_id='host' invece
# di '<channel>:<actor>'. Il consumer HTTP cercava il pending alla risposta
# utente con un sender_id diverso e non lo trovava.
#
# Il test verifica l'env propagation lanciando lo stesso path subprocess
# che usa `agent_runtime.invoke_executor` (Python script con stdin JSON +
# env), in modo da non importare `agent_runtime` nel processo pytest e
# inquinare la cache i18n / dialog_pending tra test diversi.

def test_subprocess_injects_actor_channel_env(isolated_dialog_dir):
    """Con METNOS_ACTOR + METNOS_CHANNEL nell'env il dialog state finisce
    sotto `<channel>_<actor>/`, non sotto `host/`. Mirror diretto di cosa
    fa `agent_runtime.invoke_executor(actor=..., channel=...)`.
    """
    payload = json.dumps({
        "title": "smoke env",
        "dialog": [{"var": "confirm", "prompt": "?",
                      "schema": {"kind": "yes_no"}}],
        "fmt": "dialogue",
    })
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_RUNTIME) + os.pathsep + env.get("PYTHONPATH", "")
    env["METNOS_ACTOR"] = "alice"
    env["METNOS_CHANNEL"] = "telegram"
    res = subprocess.run(
        ["python3", str(_CODE)],
        input=payload, capture_output=True, text=True, timeout=10, env=env,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    out = json.loads(res.stdout)
    assert out["ok"] is True, f"executor failed: {out}"
    assert out["decision"] == "input_required"
    dialog_id = out.get("dialog_id")
    assert dialog_id

    # dialog_pending._safe_sender sostituisce ':' con '_'.
    base = isolated_dialog_dir / ".local" / "share" / "metnos" / "get_inputs"
    expected = base / "telegram_alice" / f"{dialog_id}.json"
    assert expected.exists(), (
        f"dialog state non trovato in {expected}. "
        f"Contenuto base dir: {list(base.iterdir()) if base.exists() else 'inesistente'}"
    )
    fallback = base / "host" / f"{dialog_id}.json"
    assert not fallback.exists(), (
        f"dialog state finito (anche) sotto fallback {fallback}: "
        f"env propagation rotta"
    )


def test_subprocess_no_env_falls_back_to_host(isolated_dialog_dir):
    """Senza env vars il dialog state defaulta a sender_id='host'."""
    payload = json.dumps({
        "title": "smoke env default",
        "dialog": [{"var": "x", "prompt": "?",
                      "schema": {"kind": "text"}}],
        "fmt": "dialogue",
    })
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_RUNTIME) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("METNOS_ACTOR", None)
    env.pop("METNOS_CHANNEL", None)
    res = subprocess.run(
        ["python3", str(_CODE)],
        input=payload, capture_output=True, text=True, timeout=10, env=env,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    out = json.loads(res.stdout)
    assert out["ok"] is True
    dialog_id = out.get("dialog_id")
    assert dialog_id

    base = isolated_dialog_dir / ".local" / "share" / "metnos" / "get_inputs"
    expected = base / "host" / f"{dialog_id}.json"
    assert expected.exists(), (
        f"dialog state non trovato in {expected}. "
        f"Contenuto base dir: {list(base.iterdir()) if base.exists() else 'inesistente'}"
    )


def test_invoke_executor_injects_actor_channel_env(tmp_path, monkeypatch):
    """Verifica diretta in-process: `agent_runtime.invoke_executor(
    actor=..., channel=...)` deve aggiungere METNOS_ACTOR / METNOS_CHANNEL
    all'env del subprocess. Patcha `subprocess.run` in agent_runtime per
    intercettare l'env senza eseguire realmente l'executor (zero rischio
    di pollution della cache i18n / dialog_pending fra test).
    """
    import sys as _sys
    _sys.path.insert(0, str(_RUNTIME))
    import agent_runtime  # noqa: E402
    from loader import Executor  # noqa: E402

    ex = Executor(
        name="get_inputs",
        version="1.0",
        description="",
        affinity=[],
        args_schema={},
        capabilities=[],
        tests=[],
        code_path=_CODE,
        manifest_path=_CODE.parent / "manifest.toml",
        signed_by="test",
    )

    captured_env: dict = {}

    class FakeCompleted:
        stdout = '{"ok": true, "decision": "stub"}'
        stderr = ""
        returncode = 0

    def fake_run(cmd, *a, **kw):  # noqa: ARG001
        captured_env.update(kw.get("env") or {})
        return FakeCompleted()

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)

    agent_runtime.invoke_executor(
        ex, {"title": "x", "dialog": []}, timeout_s=10,
        actor="alice", channel="telegram", turn_id="t1",
    )
    assert captured_env.get("METNOS_ACTOR") == "alice"
    assert captured_env.get("METNOS_CHANNEL") == "telegram"
    assert captured_env.get("METNOS_TURN_ID") == "t1"

    captured_env.clear()
    agent_runtime.invoke_executor(
        ex, {"title": "x", "dialog": []}, timeout_s=10,
    )
    # Default: no actor/channel in env (backward-safe per call site non aggiornati).
    assert "METNOS_ACTOR" not in captured_env
    assert "METNOS_CHANNEL" not in captured_env

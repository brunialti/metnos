"""C7 Area-2 CP1+CP2 — la CHIUSURA dello shim basta agli executor files sul device.

Simula il layout del DEVICE: una dir con i soli file del bundle shim (albero
`backends/` incluso) + la dir dell'executor, e un python subprocess col repo
BANDITO da sys.path. Prova che:
  1. `import find_files/read_files/list_dirs/move_files/delete_files` NON
     solleva ModuleNotFoundError a module-load (il lazy-gw di CP2 è ciò che
     lo rende possibile per find/read/write);
  2. una invoke REALE client=local funziona end-to-end nel layout device
     (find su un albero temp → entries).
Se qualcuno aggiunge un import top-level fuori chiusura, questo test lo
scopre PRIMA di un ModuleNotFoundError remoto (§2.8).
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
_REPO = _RUNTIME.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

# Stessa lista del bundle (agent_server.shim_bundle) — il test di
# test_agent_server_remote verifica che il bundle la spedisca; qui la usiamo
# per costruire il layout device senza tirare su il server aiohttp.
_SHIM_SOURCES = {
    "executor_helpers.py": _RUNTIME / "executor_helpers.py",
    "messages.py": _RUNTIME / "device_shim" / "messages.py",
    "path_alias.py": _RUNTIME / "path_alias.py",
    "backends/__init__.py": _RUNTIME / "backends" / "__init__.py",
    "backends/files/__init__.py": _RUNTIME / "backends" / "files" / "__init__.py",
    "backends/files/local.py": _RUNTIME / "backends" / "files" / "local.py",
    "platform_policy.py": _RUNTIME / "platform_policy.py",
    "config.py": _RUNTIME / "config.py",
}

_EXECUTORS = ("find_files", "read_files", "write_files",
              "move_files", "delete_files", "list_dirs")


def _build_device_layout(tmp_path: Path) -> tuple[Path, Path]:
    shim = tmp_path / "shim"
    for rel, src in _SHIM_SOURCES.items():
        dest = shim / Path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    execs = tmp_path / "executors"
    execs.mkdir()
    for name in _EXECUTORS:
        shutil.copyfile(_REPO / "executors" / name / f"{name}.py",
                        execs / f"{name}.py")
    return shim, execs


def _device_python(code: str, shim: Path, execs: Path, cwd: Path) -> dict:
    """Esegue `code` in un python col REPO bandito da sys.path (come sul
    device: solo shim + dir executor + stdlib)."""
    prelude = (
        "import sys\n"
        f"_banned = {str(_REPO)!r}\n"
        "sys.path = [p for p in sys.path if _banned not in p]\n"
        f"sys.path.insert(0, {str(execs)!r})\n"
        f"sys.path.insert(0, {str(shim)!r})\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", prelude + code],
        capture_output=True, text=True, cwd=str(cwd), timeout=60,
        # Contratto REALE del client (sandbox_*: METNOS_RUNTIME=shim_dir):
        # il bootstrap degli executor lo usa per il sys.path del runtime.
        env={"PATH": "/usr/bin:/bin", "HOME": str(cwd),
             "METNOS_RUNTIME": str(shim),
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def test_all_file_executors_import_on_device(tmp_path):
    shim, execs = _build_device_layout(tmp_path)
    code = (
        "import find_files, read_files, write_files, "
        "move_files, delete_files, list_dirs\n"
        "print('IMPORT_OK')\n"
    )
    r = _device_python(code, shim, execs, tmp_path)
    assert r["rc"] == 0 and "IMPORT_OK" in r["out"], r["err"][-800:]


def test_find_files_invoke_on_device_layout(tmp_path):
    shim, execs = _build_device_layout(tmp_path)
    data = tmp_path / "dati"
    data.mkdir()
    (data / "a.txt").write_text("x")
    (data / "b.log").write_text("y")
    args = {"base_path": str(data), "pattern": "*.txt", "client": "local"}
    code = (
        "import json, find_files\n"
        f"out = find_files.invoke(json.loads({json.dumps(json.dumps(args))}))\n"
        "print(json.dumps({'ok': out.get('ok'), "
        "'n': len(out.get('entries') or [])}))\n"
    )
    r = _device_python(code, shim, execs, tmp_path)
    assert r["rc"] == 0, r["err"][-800:]
    payload = json.loads(r["out"].strip().splitlines()[-1])
    assert payload == {"ok": True, "n": 1}, r["out"]


def test_gw_client_degrades_honestly_on_device(tmp_path):
    """client=google_workspace sul device: il lazy-import assente degrada a
    errore STRUTTURATO (ok:False, ERR_NOT_APPLICABLE) — mai un traceback al
    runner, mai un crash a module-load (§2.8)."""
    shim, execs = _build_device_layout(tmp_path)
    code = (
        "import json, find_files\n"
        "out = find_files.invoke({'base_path': '/x', "
        "'client': 'google_workspace'})\n"
        "print(json.dumps({'ok': out.get('ok'), "
        "'has_error': bool(out.get('error'))}))\n"
    )
    r = _device_python(code, shim, execs, tmp_path)
    assert r["rc"] == 0, r["err"][-800:]
    payload = json.loads(r["out"].strip().splitlines()[-1])
    assert payload == {"ok": False, "has_error": True}, r["out"]

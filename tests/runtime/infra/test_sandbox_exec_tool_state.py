"""Un tool montato senza i suoi dati e' un tool che risponde SBAGLIATO (18/8/2026).

`apt-get` tiene il catalogo dei pacchetti sotto `/var/lib/apt`. Nella sandbox
il binario c'era e la cartella no: ogni ricerca tornava «pacchetto non
trovato», che non e' una capacita' assente ma una risposta falsa — la specie
di guasto piu' difficile da ricondurre alla causa, perche' somiglia a un
catalogo che non contiene quel pacchetto.

Trovato dal vivo: `install_packages` non risolveva NESSUN pacchetto su Linux.

Run: `python3 -m pytest tests/runtime/infra/test_sandbox_exec_tool_state.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "runtime") not in sys.path:
    sys.path.insert(0, str(ROOT / "runtime"))

import sandbox  # noqa: E402


def test_un_gestore_di_pacchetti_vede_il_proprio_catalogo():
    """Il fatto che mancava: chi dichiara apt-get riceve /var/lib/apt."""
    paths = sandbox._exec_tool_resources(["apt-get"])
    if not Path("/var/lib/apt").exists():
        return  # su una macchina senza apt non c'e' niente da concedere
    assert Path("/var/lib/apt") in paths


def test_un_nome_sconosciuto_non_concede_niente():
    """Estendere un manifest non deve poter allargare la sandbox inventando
    un nome: e' la stessa regola di `_system_read_resources`."""
    assert sandbox._exec_tool_resources(["qualcosa-di-inventato"]) == []
    assert sandbox._exec_tool_resources([]) == []
    assert sandbox._exec_tool_resources(["winget"]) == []


def test_solo_percorsi_esistenti_e_senza_ripetizioni():
    """bwrap fallisce su un percorso assente, e un bind ripetuto e' rumore."""
    paths = sandbox._exec_tool_resources(["apt-get", "apt-cache", "apt-get"])
    assert all(p.exists() for p in paths)
    assert len(paths) == len(set(paths))


def test_niente_di_scrivibile_passa_da_qui():
    """La concessione e' di sola LETTURA per costruzione: agire richiede
    privilegi che la sandbox non da', e non deve poterli dare per sbaglio."""
    argv = sandbox._build_bwrap_args(
        code_path=ROOT / "executors" / "install_packages" / "install_packages.py",
        capabilities=[{"name": "code:exec", "hint": ["apt-get"]}],
    )
    if "/var/lib/apt" not in argv:
        return
    posizione = argv.index("/var/lib/apt")
    assert argv[posizione - 1] == "--ro-bind", argv[posizione - 3:posizione + 2]

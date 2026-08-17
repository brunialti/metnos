"""I test di NASCITA di ogni executor conforme girano nella suite (17/8/2026).

Lo standard (ADR 0193) prescrive due comandi prima di firmare un executor:

    python3 runtime/executor_standard.py executors/<nome>/manifest.toml
    python3 runtime/test_runner.py       executors/<nome>/manifest.toml

Il primo la firma lo applica gia' da sola: `sign.py` rifiuta un manifest non
conforme. Il secondo no, e nessuno lo eseguiva: i test di nascita erano scritti,
firmati, e mai fatti girare.

Che cosa e' costato: `get_location` era rosso 1/4 da chissa' quando, e nessuno
poteva saperlo. Un mio `install_packages` appena scritto ne aveva uno rosso, e
l'ho trovato solo perche' Roberto mi ha chiesto di applicare lo standard.

Costa 27 secondi per 84 executor. Un manifest che dichiara un test e non lo
supera e' un contratto che promette cio' che non mantiene, e questa e' la
guardia che lo dice subito invece che fra sei mesi.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "runtime" / "test_runner.py"


def _conformi():
    """Gli executor che DICHIARANO lo standard: solo loro sono vincolati.

    Un legacy senza dichiarazione resta fuori per scelta dello standard
    stesso, che applica le regole nuove in modo osservativo ai vecchi e
    bloccante ai dichiarati conformi.
    """
    for d in sorted(ROOT.joinpath("executors").iterdir()):
        manifest = d / "manifest.toml"
        if not manifest.is_file():
            continue
        try:
            man = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            continue
        if man.get("executor_standard") and man.get("tests"):
            yield d.name, manifest


_CASI = list(_conformi())


def test_ci_sono_executor_da_verificare():
    """Una scansione vuota passerebbe senza verificare niente."""
    assert len(_CASI) > 50, f"solo {len(_CASI)} executor conformi trovati"


@pytest.mark.parametrize("nome,manifest", _CASI,
                         ids=[n for n, _ in _CASI])
def test_i_test_di_nascita_passano(nome, manifest):
    esito = subprocess.run(
        [sys.executable, str(RUNNER), str(manifest)],
        capture_output=True, text=True, timeout=300, cwd=ROOT,
    )
    if esito.returncode != 0:
        righe = [ln for ln in esito.stdout.splitlines()
                 if ln.strip().startswith("X") or "FAIL" in ln
                 or "atteso" in ln or "passati" in ln]
        pytest.fail(f"{nome}: test di nascita rossi\n"
                    + "\n".join(righe[:12]))

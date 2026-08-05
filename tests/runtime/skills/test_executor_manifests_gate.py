"""Anti-regressione (§10.6): la suite executor-manifest
(`tests/tools/run_executor_manifests.py`)
NON deve piu' restare ORFANA dal loop di test.

Storia (7/7/2026): 12 regressioni silenziose si erano accumulate perche'
il vecchio `run_all_tests.py` era referenziato SOLO in `export-public.sh` — mai nel gate
pytest, il baseline tracciato. Questo test lo fa girare COME parte di pytest:
un fallimento di QUALSIASI test-manifest executor fa fallire il baseline, cosi'
il drift emerge subito (non a mesi di distanza).

Costo: ~90s (75 executor × test_runner in subprocess). E' il prezzo della
copertura; se serve deselezionarlo in un giro rapido: `-k "not manifests_gate"`.
"""
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_all_executor_manifests_green():
    r = subprocess.run(
        [sys.executable, "tests/tools/run_executor_manifests.py"],
        cwd=str(_REPO), capture_output=True, text=True,
    )
    # Coda dello stdout = riga SUMMARY "X/Y test passati su N executor".
    tail = "\n".join(r.stdout.strip().splitlines()[-30:])
    assert r.returncode == 0, (
        "Suite executor-manifest NON verde (run_executor_manifests.py):\n" + tail
    )

"""Probe whether a declared interruption point leaves the state it names.

``append_context_transition`` declares two interruption points, and the first
of them, ``after_context_transition_temporary``, names the moment when the
staged temporary exists and has not been published yet. A ``finally`` clause
unlinks that temporary on the way out of the exception, so the point erases the
very state it was created to expose. This probe interrupts there and looks.

A real power loss never runs that clause, so the product recovers; the proof is
what the clause destroys. Exit codes: 0 the point leaves its state and the
resume publishes, 1 it leaves nothing, 2 the probe could not run and says why.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
sys.path.insert(0, str(RADICE / "tests" / "portable"))
os.environ.setdefault("METNOS_INSTALL_ROOT", str(RADICE))

try:
    import test_executor_birth_ownership_chain as AIUTI
    import executor_birth_ownership_chain as CATENA
    from executor_birth_cutover import CurrentReceiptProof
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura della catena non disponibile: {errore}")
    raise SystemExit(2)

GIUNTURA = "after_context_transition_temporary"


def _autorita():
    """Call the scaffolding fixture body without a pytest session."""
    funzione = getattr(AIUTI.authority, "__wrapped__", None)
    if funzione is None:
        return None
    return funzione()


def principale() -> int:
    if GIUNTURA not in Path(CATENA.__file__).read_text():
        print(f"la giuntura {GIUNTURA} non esiste piu': sonda da riscrivere")
        return 2
    autorita = _autorita()
    if autorita is None:
        print("non riesco a costruire l'autorita' della scena")
        return 2
    base = Path(tempfile.mkdtemp(prefix="sonda-giuntura-"))
    try:
        deposito = AIUTI.initialize_test_store(base / "chain-v1", autorita)
        prova = CurrentReceiptProof((), {})
        codificato, atteso = AIUTI.context_transition(proof=prova)
        cartella = deposito.root / CATENA.CONTEXT_TRANSITIONS_DIRECTORY_V1

        def interrompi(punto):
            if punto == GIUNTURA:
                raise CATENA._OwnershipChainCrashForTest(punto)

        try:
            deposito.append_context_transition(
                codificato, expected_proof=prova, _crash_seam=interrompi,
            )
        except CATENA._OwnershipChainCrashForTest:
            pass
        else:
            print("la giuntura non e' stata attraversata")
            return 2

        resti = sorted(percorso.name for percorso in cartella.iterdir())
        print(f"interrotto alla giuntura '{GIUNTURA}'")
        print("  promette: il temporaneo scritto, non ancora pubblicato")
        print("  cosa resta:", resti if resti else "NIENTE")
        if not resti:
            print()
            print("ESITO: la giuntura NON lascia lo stato che nomina. Il")
            print("`finally` cancella il temporaneo uscendo dall'eccezione,")
            print("quindi una prova che si interrompe li' non vede nulla e")
            print("non distingue «interrotto» da «mai iniziato».")
            print("Una vera caduta di corrente quel ramo non lo esegue: e' la")
            print("PROVA a essere impossibile, non il prodotto a essere rotto.")
            return 1
        ripresa = deposito.append_context_transition(
            codificato, expected_proof=prova,
        )
        dopo = sorted(percorso.name for percorso in cartella.iterdir())
        print("  ripresa:", "pubblica" if ripresa == atteso else "ESITO ALTRO")
        print("  cosa resta dopo:", dopo)
        print()
        print("ESITO: la giuntura lascia il temporaneo e la ripresa pubblica.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(principale())

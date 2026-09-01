"""Probe transaction-directory publication around first-record staging.

The V2 coordinator must never expose an empty committed transaction directory.
This probe interrupts its unpublished staging directory before the first
record, then measures whether the resolver and the writer agree on recovery.

Exit codes: 0 the window is closed, 1 the asymmetry is present, 2 the probe
could not run and says why.
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

if os.name == "nt":  # the coordinator roles under probe are POSIX-only
    print("questa sonda misura ruoli POSIX e non gira su Windows")
    raise SystemExit(2)

try:
    import test_executor_birth_ownership_coordinator_v2 as AIUTI
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorError,
        _append_ownership_transaction_locked_for_test_v2,
        _deployment_lock_for_test_v1,
        _resolve_ownership_coordinator_locked_for_test_v2,
    )
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura del coordinatore V2 non disponibile: {errore}")
    raise SystemExit(2)


def _scena(base: Path):
    """Build a coordinator root holding exactly one bound successor claim."""
    radice = base / "ownership"
    presa = _deployment_lock_for_test_v1(radice)
    sessione = presa.__enter__()
    cartella = AIUTI.make_coordinator_root(radice)
    rivendicazione = AIUTI.bound_claim(
        release_sequence=1, previous_head_id=None,
        closed_build_id=AIUTI.D("3"), source_id=AIUTI.D("2"),
        previous_closed_build_id=None, previous_cutover_id=None,
    )
    atto = AIUTI.transaction_records(
        rivendicazione, end_sequence=0, previous_closed_build_id=None,
        previous_cutover_id=None, cutover_id=AIUTI.D("4"), head_id=AIUTI.D("5"),
    )[0]
    AIUTI.write_claim(cartella, rivendicazione)
    return presa, sessione, radice, cartella, rivendicazione, atto


def _misura(etichetta, azione):
    try:
        azione()
    except OwnershipCoordinatorError as errore:
        print(f"  {etichetta}: RIFIUTA  {errore.code} / {errore.detail!r}")
        return "rifiuta"
    except Exception as errore:  # noqa: BLE001
        print(f"  {etichetta}: INATTESO {type(errore).__name__}: {errore}")
        return "inatteso"
    print(f"  {etichetta}: ACCETTA")
    return "accetta"


def principale() -> int:
    base = Path(tempfile.mkdtemp(prefix="sonda-finestra-transazione-"))
    try:
        print("caso 1 — la cartella staged resta vuota prima del primo atto")
        presa, sessione, radice, cartella, rivendicazione, atto = _scena(base / "a")

        def interrompi(stadio):
            if stadio == "transaction_directory_staged":
                raise InterruptedError(stadio)

        try:
            _append_ownership_transaction_locked_for_test_v2(
                sessione, radice, atto, _crash_seam=interrompi,
            )
        except InterruptedError:
            pass
        else:
            print("  la frontiera staged non e' stata attraversata")
            presa.__exit__(None, None, None)
            return 2
        lettore = _misura(
            "lettore            ",
            lambda: _resolve_ownership_coordinator_locked_for_test_v2(sessione, radice),
        )
        scrittore = _misura(
            "scrittore          ",
            lambda: _append_ownership_transaction_locked_for_test_v2(
                sessione, radice, atto,
            ),
        )
        dopo = _misura(
            "lettore dopo la cura",
            lambda: _resolve_ownership_coordinator_locked_for_test_v2(sessione, radice),
        )
        presa.__exit__(None, None, None)

        print()
        print("caso 2 — la cartella comune resta vuota, nessuna transazione dentro")
        presa2, sessione2, radice2, cartella2, _, _ = _scena(base / "b")
        (cartella2 / "transactions-v2").mkdir(mode=0o755)
        comune = _misura(
            "lettore            ",
            lambda: _resolve_ownership_coordinator_locked_for_test_v2(
                sessione2, radice2,
            ),
        )
        presa2.__exit__(None, None, None)

        print()
        if comune != "accetta":
            print("ESITO: anche la cartella comune vuota blocca il lettore.")
            return 1
        if lettore == "accetta" and scrittore == "accetta" and dopo == "accetta":
            print("ESITO: finestra chiusa — staging e pubblicazione concordano.")
            return 0
        if lettore == "rifiuta" and scrittore == "accetta" and dopo == "accetta":
            print("ESITO: ASIMMETRIA — il lettore rifiuta uno stato che lo")
            print("scrittore sa curare da solo. Se la sequenza legge prima di")
            print("scrivere, quella cura non viene mai raggiunta.")
            return 1
        print("ESITO: misura fuori dalle due ipotesi, vedi le righe sopra.")
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(principale())

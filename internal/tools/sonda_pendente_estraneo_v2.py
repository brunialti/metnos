"""Probe what V2 provisioning does with a complete but foreign pending plan.

Recovering a V2 material plan discards the pending staging file whenever it
cannot be accepted. Two very different states reach that branch: a torn write
left by a power loss, which is safe to drop, and a whole well-formed plan bound
to another transaction header, which is confidential material. This probe
writes the second and reports whether it is destroyed without a word.

Exit codes: 0 the two states are told apart, 1 both are discarded alike,
2 the probe could not run and says why.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
sys.path.insert(0, str(RADICE / "tests" / "portable"))
sys.path.insert(0, str(RADICE))
os.environ.setdefault("METNOS_INSTALL_ROOT", str(RADICE))

if os.name == "nt":
    print("questa sonda misura ruoli POSIX e non gira su Windows")
    raise SystemExit(2)

try:
    import test_birth_authority_provisioning_v2 as AIUTI
    from rm0008_2b import support
    from install import birth_authority_provisioner as PROV
    from executor_birth_secure_fs import _BirthObjectRole
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura di posa V2 non disponibile: {errore}")
    raise SystemExit(2)


class _Sostituzioni:
    """The smallest stand-in for the fixture the scaffolding expects."""

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []

    def setattr(self, target: object, name: str, value: object) -> None:
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def close(self) -> None:
        while self._undo:
            target, name, value = self._undo.pop()
            setattr(target, name, value)


def principale() -> int:
    base_tmp = Path(tempfile.mkdtemp(prefix="sonda-pendente-estraneo-"))
    sostituzioni = _Sostituzioni()
    try:
        base = support.make_config(base_tmp)
        transazione = "0" * 32
        intestazione = AIUTI._build_transaction_header_v2(
            transaction_id=transazione,
            provisioner_build_id="build-v2",
            claim=AIUTI._claim(),
            distribution=AIUTI._distribution(),
            previous_set=AIUTI._prepared(),
        )
        altra = AIUTI._build_transaction_header_v2(
            transaction_id=transazione,
            provisioner_build_id="build-altro",
            claim=AIUTI._claim(),
            distribution=AIUTI._distribution(),
            previous_set=AIUTI._prepared(),
        )
        atteso = AIUTI._material_plan(intestazione)
        estraneo = AIUTI._material_plan(altra)
        if estraneo == atteso or not estraneo.encode():
            print("le due intestazioni non producono piani distinti")
            return 2

        layout = support.open_layout(sostituzioni, base)
        with layout.birth_session as sessione:
            with sessione.global_lock(exclusive=True, create=True):
                giornale = PROV._TransactionJournalV1.transition_v2(
                    sessione, transazione,
                )
                giornale.create_root()
                giornale.write_header(intestazione)
                sessione.create_file_exclusive(
                    giornale.root_components + (
                        f".material-plan-v2.pending.{transazione}",
                    ),
                    estraneo.encode(),
                    role=_BirthObjectRole.birth_confidential,
                )
                impronta = hashlib.sha256(estraneo.encode()).hexdigest()
                print("pendente posato: piano COMPLETO e ben formato,")
                print(f"legato a un'altra intestazione  sha256:{impronta[:16]}…")
                try:
                    esito = giornale.ensure_material_plan_v2(lambda: atteso)
                except Exception as errore:  # noqa: BLE001
                    print("esito: RIFIUTA ->", type(errore).__name__,
                          getattr(errore, "code", ""))
                    print()
                    print("ESITO: i due stati sono distinti — un pendente")
                    print("completo ma estraneo non viene distrutto in silenzio.")
                    return 0
                inventario = sorted(sessione.inventory(
                    giornale.root_components,
                ))
                print("esito: ACCETTA, e scrive il piano atteso"
                      if esito == atteso else f"esito: ACCETTA -> {esito}")
                print("inventario finale:", inventario)
        print()
        print("ESITO: il piano estraneo e' stato SCARTATO IN SILENZIO.")
        print("Una scrittura troncata e un piano intero legato altrove finiscono")
        print("nello stesso ramo, e il secondo e' materiale confidenziale.")
        return 1
    finally:
        sostituzioni.close()
        shutil.rmtree(base_tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(principale())

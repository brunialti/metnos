"""Probe what V2 provisioning does with complete but conflicting staging.

Recovering a staged object discards the pending file whenever it cannot be
accepted. Two very different states reach that branch: a torn write left by a
power loss, which is safe to drop, and a whole well-formed object that belongs
somewhere else, which is confidential material. This probe writes the second at
the two places that stage it — the material plan and one plan payload — and
reports whether it is destroyed without a word.

Exit codes: 0 both places tell the two states apart, 1 at least one discards
them alike, 2 the probe could not run and says why.
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
    from test_birth_authority_provisioning_v2 import (
        _materialize_material_plan_v2,
    )
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


def _caso_uno() -> int:
    """A complete plan bound to another header, staged as this one's pending."""
    base_tmp = Path(tempfile.mkdtemp(prefix="sonda-piano-estraneo-"))
    sostituzioni = _Sostituzioni()
    try:
        base = support.make_config(base_tmp)
        transazione = "0" * 32
        intestazione = _intestazione(transazione, "build-v2")
        altra = _intestazione(transazione, "build-altro")
        atteso = AIUTI._material_plan(intestazione)
        estraneo = AIUTI._material_plan(altra)
        if estraneo == atteso or not estraneo.encode():
            print("  le due intestazioni non producono piani distinti")
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
                print("  posato: piano intero, ben formato, altra intestazione")
                try:
                    giornale.ensure_material_plan_v2(lambda: atteso)
                except Exception as errore:  # noqa: BLE001
                    print("  esito: RIFIUTA ->", type(errore).__name__,
                          getattr(errore, "code", ""))
                    return 0
                print("  esito: ACCETTA, e il piano estraneo non c'e' piu'")
                return 1
    finally:
        sostituzioni.close()
        shutil.rmtree(base_tmp, ignore_errors=True)


def _caso_due() -> int:
    """A complete payload with other content, staged as a plan object."""
    from test_birth_authority_provisioning_v2 import (
        _materialize_material_plan_v2,
    )

    base_tmp = Path(tempfile.mkdtemp(prefix="sonda-payload-estraneo-"))
    sostituzioni = _Sostituzioni()
    try:
        base = support.make_config(base_tmp)
        transazione = "0" * 32
        intestazione = _intestazione(transazione, "build-v2")
        piano = AIUTI._material_plan(intestazione)
        layout = support.open_layout(sostituzioni, base)
        with layout.birth_session as sessione:
            with sessione.global_lock(exclusive=True, create=True):
                giornale = PROV._TransactionJournalV1.transition_v2(
                    sessione, transazione,
                )
                giornale.create_root()
                giornale.write_header(intestazione)
                piano = giornale.ensure_material_plan_v2(lambda: piano)
                radice = giornale.root_components + ("authority-set",)
                sessione.create_directory_exclusive(
                    radice, role=_BirthObjectRole.birth_integrity_only,
                )
                sessione.create_directory_exclusive(
                    radice + ("admission",),
                    role=_BirthObjectRole.birth_confidential,
                )
                pendente = (
                    ".payload-pending-00000000000000000001-" + transazione
                )
                sessione.create_file_exclusive(
                    radice + ("admission", pendente), b"1",
                    role=_BirthObjectRole.birth_confidential,
                )
                print("  posato: 1 byte intero e ben formato, contenuto altro")
                print('  atteso b"0", posato b"1", ruolo confidenziale')
                try:
                    _materialize_material_plan_v2(sessione, giornale, piano)
                except Exception as errore:  # noqa: BLE001
                    print("  esito: RIFIUTA ->", type(errore).__name__,
                          getattr(errore, "code", ""))
                    return 0
                resta = pendente in sessione.inventory(
                    radice + ("admission",),
                )
                print("  esito: ACCETTA; il pendente esiste ancora:", resta)
                return 1 if not resta else 0
    finally:
        sostituzioni.close()
        shutil.rmtree(base_tmp, ignore_errors=True)


def _intestazione(transazione: str, costruzione: str):
    return AIUTI._build_transaction_header_v2(
        transaction_id=transazione,
        provisioner_build_id=costruzione,
        claim=AIUTI._claim(),
        distribution=AIUTI._distribution(),
        previous_set=AIUTI._prepared(),
    )


def principale() -> int:
    print("caso 1 — il piano del materiale")
    uno = _caso_uno()
    print()
    print("caso 2 — un oggetto del piano")
    due = _caso_due()
    print()
    if 2 in (uno, due):
        print("ESITO: la sonda non ha potuto misurare, vedi sopra.")
        return 2
    if uno == 0 and due == 0:
        print("ESITO: in tutt'e due i posti i due stati sono distinti — una")
        print("scrittura troncata si scarta, un oggetto intero che appartiene")
        print("altrove no.")
        return 0
    posti = [nome for nome, esito in (("piano", uno), ("oggetto", due))
             if esito != 0]
    print("ESITO: SCARTATO IN SILENZIO in:", ", ".join(posti))
    print("Una scrittura troncata e un oggetto intero che appartiene altrove")
    print("finiscono nello stesso ramo, e il secondo e' confidenziale.")
    return 1


if __name__ == "__main__":
    raise SystemExit(principale())

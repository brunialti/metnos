"""Probe that every declared interruption point converges to the same world.

Crossing the certificate boundary and the head boundary declare several
interruption points. Two of
them are exercised for recovery today; the rest are named by other tests but
never resumed. This probe crosses the boundary once without interruption and
keeps the resulting ownership tree, then crosses it again once per declared
point, interrupting there and resuming, and compares the tree it ends with.

An interruption that converges to a different world is a defect; a point never
crossed in this scene is reported as such, not counted as a pass.

Exit codes: 0 every point crossed here converges, 1 one does not, 2 the probe
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

if os.name == "nt":
    print("questa sonda misura una scena POSIX e non gira su Windows")
    raise SystemExit(2)

try:
    import test_executor_birth_ownership_coordinator_v2 as AIUTI
    from executor_birth_ownership_chain import (
        _OwnershipChainCrashForTest, _OwnershipChainStoreForTest,
    )
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorStateV1,
        _certificate_ready_material_v2,
        _cross_certificate_boundary_locked_for_test_v2,
        _deployment_lock_for_test_v1,
    )
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura del coordinatore non disponibile: {errore}")
    raise SystemExit(2)

D = AIUTI.D


# The chain module converts any other exception raised inside its publication
# into an ordinary error, so an interruption must speak its own language or it
# stops being an interruption.
_Interruzione = _OwnershipChainCrashForTest


def _giunture() -> tuple[str, ...]:
    """Every interruption point the crossing can reach, read from the code."""
    import re

    nomi: set[str] = set()
    for modulo in sorted((RADICE / "runtime").glob("executor_birth_*.py")):
        nomi.update(
            re.findall(r'_crash_seam\("([a-z_]+)"\)', modulo.read_text()),
        )
    return tuple(sorted(nomi))


def _scena_prima(base: Path):
    """The first release: the certificate is installed at the anchor."""
    radice = base / "ownership"
    autorita = AIUTI.portable_authorities()
    presa = _deployment_lock_for_test_v1(radice)
    sessione = presa.__enter__()
    cartella = AIUTI.make_coordinator_root(radice)
    rivendicazione = AIUTI.bound_claim(
        release_sequence=1, previous_head_id=None, closed_build_id=D("3"),
        source_id=D("2"), previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    atti = AIUTI.transaction_records(
        rivendicazione, end_sequence=1, previous_closed_build_id=None,
        previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
    )
    AIUTI.write_claim(cartella, rivendicazione)
    AIUTI.write_transaction(cartella, rivendicazione, atti)
    completo = atti[-1]
    materiale = _certificate_ready_material_v2(
        completo, authorities=autorita,
        prerequisite=AIUTI._startup_prerequisite_for_test(D("1"), D("2")),
        observe_maintenance=lambda: completo.maintenance_proof,
        crossing_receipt=AIUTI.dominant_receipt(
            completo, catalog_id=AIUTI.proof_catalog_id(),
        ),
    )
    return presa, sessione, radice, autorita, materiale, None, (radice,)


def _scena_seguente(base: Path):
    """A later release: the certificate is appended to the chain instead."""
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    radice = base / "ownership"
    autorita = AIUTI.portable_authorities()
    deposito = _OwnershipChainStoreForTest._initialize_with_authorities(
        base / "chain-v1", autorita.public,
    )
    presa = _deployment_lock_for_test_v1(radice)
    sessione = presa.__enter__()
    cartella = AIUTI.make_coordinator_root(radice)
    prima = AIUTI.bound_claim(
        release_sequence=1, previous_head_id=None, closed_build_id=D("3"),
        source_id=D("2"), previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    atti_prima = AIUTI.transaction_records(
        prima, end_sequence=6, previous_closed_build_id=None,
        previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
    )
    AIUTI.write_claim(cartella, prima)
    AIUTI.write_transaction(cartella, prima, atti_prima)
    ultimo = atti_prima[-1]
    rivendicazione = AIUTI.bound_claim(
        release_sequence=2, previous_head_id=ultimo.head_id,
        closed_build_id=D("a"), source_id=D("b"),
        previous_closed_build_id=ultimo.closed_build_id,
        previous_cutover_id=ultimo.cutover_id,
    )
    atti = AIUTI.transaction_records(
        rivendicazione, end_sequence=1,
        previous_closed_build_id=ultimo.closed_build_id,
        previous_cutover_id=ultimo.cutover_id,
        cutover_id=D("6"), head_id=D("7"),
    )
    AIUTI.write_claim(cartella, rivendicazione)
    AIUTI.write_transaction(cartella, rivendicazione, atti)
    completo = atti[-1]
    materiale = _certificate_ready_material_v2(
        completo, authorities=autorita,
        prerequisite=AIUTI._startup_prerequisite_for_test(D("1"), D("2")),
        observe_maintenance=lambda: completo.maintenance_proof,
        crossing_receipt=AIUTI.dominant_receipt(
            completo, catalog_id=AIUTI.proof_catalog_id(),
        ),
    )
    return (
        presa, sessione, radice, autorita, materiale, deposito,
        (radice, deposito.root),
    )


def _fotografia(radici) -> tuple[tuple[str, bytes], ...]:
    """Every regular file under the observed roots, by path and content."""
    voci = []
    for radice in radici:
        for percorso in sorted(radice.rglob("*")):
            if percorso.is_file() and not percorso.is_symlink():
                voci.append((
                    f"{radice.name}/{percorso.relative_to(radice)}",
                    percorso.read_bytes(),
                ))
    return tuple(sorted(voci))


def _attraversa(scena, base: Path, giuntura: str | None):
    """Cross once, optionally dying at one point and resuming after it."""
    (
        presa, sessione, radice, autorita, materiale, deposito, radici,
    ) = scena(base)
    extra = {} if deposito is None else {"chain_store": deposito}
    try:
        raggiunta = False
        if giuntura is not None:
            def interrompi(punto):
                nonlocal raggiunta
                if punto == giuntura:
                    raggiunta = True
                    raise _Interruzione(punto)

            try:
                _cross_certificate_boundary_locked_for_test_v2(
                    sessione, radice, materiale, authorities=autorita,
                    _crash_seam=interrompi, **extra,
                )
            except _Interruzione:
                pass
            if not raggiunta:
                return None, None
        finale = _cross_certificate_boundary_locked_for_test_v2(
            sessione, radice, materiale, authorities=autorita, **extra,
        )
        return finale, _fotografia(radici)
    finally:
        presa.__exit__(None, None, None)


def _misura(nome: str, scena, base: Path) -> tuple[int, list[str]]:
    print(f"scena «{nome}»")
    atteso, riferimento = _attraversa(scena, base / "riferimento", None)
    if atteso is None or atteso.state is not (
        OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED
    ):
        print("  l'attraversamento senza interruzioni non arriva in fondo")
        return -1, []
    print(f"  riferimento: {len(riferimento)} file")
    divergenti: list[str] = []
    attraversate = 0
    for giuntura in _giunture():
        try:
            esito, fotografia = _attraversa(scena, base / giuntura, giuntura)
        except Exception as errore:  # noqa: BLE001
            print(f"  {giuntura:34} NON RIPRENDE: "
                  f"{type(errore).__name__} {str(errore)[:34]}")
            divergenti.append(f"{nome}/{giuntura}")
            continue
        if esito is None:
            continue
        attraversate += 1
        if fotografia == riferimento and esito.state is atteso.state:
            print(f"  {giuntura:34} converge")
            continue
        print(f"  {giuntura:34} DIVERGE")
        soli_qui = {n for n, _ in fotografia} - {n for n, _ in riferimento}
        soli_la = {n for n, _ in riferimento} - {n for n, _ in fotografia}
        if soli_qui:
            print(f"    file in piu':   {sorted(soli_qui)}")
        if soli_la:
            print(f"    file mancanti:  {sorted(soli_la)}")
        if not soli_qui and not soli_la:
            print("    stessi nomi, contenuto diverso")
        divergenti.append(f"{nome}/{giuntura}")
    print(f"  giunture attraversate qui: {attraversate}")
    return attraversate, divergenti


def _misura_testa(base: Path) -> tuple[int, list[str]]:
    """Drive the head crossing through every declared point, using its own scene."""
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorStateV1,
    )

    print("scena «attraversamento della testa»")
    if not hasattr(AIUTI, "_complete_initial_head_crossing_v2"):
        print("  impalcatura assente: non misuro")
        return 0, []
    divergenti: list[str] = []
    attraversate = 0
    for giuntura in _giunture():
        cartella = base / giuntura
        cartella.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            esito, ripetuto, grafo, testa, _distribuzione, _certificato = (
                AIUTI._complete_initial_head_crossing_v2(cartella, giuntura)
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as errore:  # noqa: BLE001 - pytest outcomes too
            if "DID NOT RAISE" in str(errore):
                # The point exists but this scene never reaches it.
                continue
            print(f"  {giuntura:28} NON RIPRENDE: "
                  f"{type(errore).__name__} {str(errore)[:34]}")
            divergenti.append(f"testa/{giuntura}")
            continue
        attraversate += 1
        coerente = (
            esito == ripetuto == grafo.transactions[-1].latest
            and esito.state is OwnershipCoordinatorStateV1.HEAD_REQUIRED
            and testa.head_id == esito.head_id
        )
        print(f"  {giuntura:28} {'converge' if coerente else 'DIVERGE'}")
        if not coerente:
            divergenti.append(f"testa/{giuntura}")
    print(f"  giunture attraversate qui: {attraversate}")
    return attraversate, divergenti


def _misura_topologia(base: Path) -> tuple[int, list[str]]:
    """Drive the preservation of a replaced unit through every declared point."""
    try:
        import test_executor_birth_legacy_neutralizer as VICINI
        import executor_birth_legacy_neutralizer as NEUTRALIZZATORE
    except Exception as errore:  # noqa: BLE001
        print(f"scena «conservazione»: impalcatura assente ({errore})")
        return 0, []

    print("scena «conservazione dell'unita' sostituita»")

    class _Caduta(Exception):
        pass

    def _giro(giuntura):
        radice = VICINI._tree(Path(tempfile.mkdtemp(dir=base)))
        unita = radice / "systemd" / "metnos-http.service"
        unita.write_bytes(b"precedente")
        passo = VICINI._Step(
            "legacy-service-http-system", "preserve_replaced_system_unit",
            "systemd/metnos-http.service",
        )
        sostituzione = {("system", passo.locator): b"frammento firmato"}
        raggiunta = False
        if giuntura is not None:
            def interrompi(osservata):
                nonlocal raggiunta
                if osservata == giuntura:
                    raggiunta = True
                    raise _Caduta(osservata)

            try:
                NEUTRALIZZATORE.neutralize_for_test_v1(
                    VICINI._capability(radice), [passo],
                    replacement_fragments=sostituzione,
                    _crash_seam=interrompi,
                )
            except _Caduta:
                pass
            if not raggiunta:
                return None
        VICINI._apply(radice, [passo], sostituzione)
        conservata = unita.with_name(
            unita.name + NEUTRALIZZATORE.PRESERVED_EXTENSION_V1,
        )
        return (
            unita.exists(),
            conservata.read_bytes() if conservata.exists() else None,
        )

    riferimento = _giro(None)
    if riferimento is None:
        print("  il giro senza interruzioni non arriva in fondo")
        return 0, []
    divergenti: list[str] = []
    attraversate = 0
    for giuntura in _giunture():
        try:
            osservato = _giro(giuntura)
        except Exception as errore:  # noqa: BLE001
            print(f"  {giuntura:32} NON RIPRENDE: "
                  f"{type(errore).__name__} {str(errore)[:30]}")
            divergenti.append(f"conservazione/{giuntura}")
            continue
        if osservato is None:
            continue
        attraversate += 1
        if osservato == riferimento:
            print(f"  {giuntura:32} converge")
            continue
        print(f"  {giuntura:32} DIVERGE  {osservato} invece di {riferimento}")
        divergenti.append(f"conservazione/{giuntura}")
    print(f"  giunture attraversate qui: {attraversate}")
    return attraversate, divergenti


def principale() -> int:
    base = Path(tempfile.mkdtemp(prefix="sonda-convergenza-"))
    try:
        totale = 0
        divergenti: list[str] = []
        for nome, scena in (
            ("prima uscita, certificato all'ancora", _scena_prima),
            ("uscita seguente, certificato in catena", _scena_seguente),
        ):
            attraversate, guasti = _misura(nome, scena, base / nome[:12])
            print()
            if attraversate < 0:
                return 2
            totale += attraversate
            divergenti.extend(guasti)
        attraversate, guasti = _misura_testa(base / "testa")
        print()
        totale += attraversate
        divergenti.extend(guasti)
        cartella = base / "topologia"
        cartella.mkdir(mode=0o755, parents=True, exist_ok=True)
        attraversate, guasti = _misura_topologia(cartella)
        print()
        totale += attraversate
        divergenti.extend(guasti)
        if divergenti:
            print("ESITO: non convergono:", ", ".join(divergenti))
            return 1
        if not totale:
            print("ESITO: nessuna giuntura attraversata: la sonda non misura.")
            return 2
        print(f"ESITO: le {totale} giunture attraversate nelle quattro scene")
        print("convergono tutte allo stesso mondo dell'attraversamento intero.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(principale())

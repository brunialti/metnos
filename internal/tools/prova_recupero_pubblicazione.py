"""Targeted tests for the incomplete-publication recovery primitive.

Every case builds its own store on a copy: the primitive exists precisely
because the real container must not be touched by hand, so its tests must not
touch it either.  The positive case is one shape; everything else is a refusal,
because a recovery that handles variants is a recovery that removes things
nobody inspected.

Run:  python3 internal/tools/prova_recupero_pubblicazione.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))

from manifest_inventory import ContractId, ManifestOrigin  # noqa: E402
import executor_birth_publication_recovery as R  # noqa: E402

CASI = []


def caso(nome):
    def deco(fn):
        CASI.append((nome, fn))
        return fn
    return deco


def identita(nome: str = "orfano") -> ContractId:
    return ContractId(ManifestOrigin.BUILTIN, f"{nome}/manifest.toml")


def autorizza(cid: ContractId, base: Path) -> R.AutorizzazioneRecupero:
    """Issue an authorization for a fixture identity, bound to this root.

    Production issues these only from the authoring inventory and against the
    productive root; the fixture door is separate on purpose, and the tests
    below check that a caller cannot reach the seal from outside.
    """
    return R._autorizzazione_di_prova(cid, base)


def contenitore_incompleto(radice: Path, cid: ContractId) -> Path:
    """The exact shape an interrupted first publication leaves behind."""
    cartella = radice / cid.storage_key
    (cartella / "generations").mkdir(parents=True)
    (cartella / "writer.lock").write_text("1")
    return cartella


@caso("forma esatta, senza applicare: osserva e non tocca nulla")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    esito = R.ispeziona_contenitore_incompleto(autorizza(cid, base), store_root=base)
    errori = []
    if esito.rimosso:
        errori.append("ha dichiarato una rimozione non chiesta")
    if not cartella.exists():
        errori.append("ha rimosso senza che gli fosse chiesto")
    return errori


@caso("forma esatta, applicando: rimuove e la radice resta valida")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    esito = R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    errori = []
    if not esito.rimosso:
        errori.append("non ha dichiarato la rimozione")
    if cartella.exists():
        errori.append("il contenitore e' ancora li'")
    if not base.is_dir():
        errori.append("la radice del negozio e' stata danneggiata")
    return errori


@caso("contenitore con binding.json: rifiuta")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    (cartella / "binding.json").write_text("{}")
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "contenitore_non_incompleto"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un contenitore con binding"]


@caso("contenitore con current: rifiuta")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    (cartella / "current").write_text("sha256:" + "a" * 64)
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "contenitore_non_incompleto"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un contenitore con un puntatore corrente"]


@caso("generations non vuota: rifiuta e non rimuove nulla")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    (cartella / "generations" / ("b" * 64)).mkdir()
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        errori = ([] if exc.code == "generazioni_non_vuote"
                  else [f"codice inatteso: {exc.code}"])
        if not cartella.exists():
            errori.append("ha rimosso pur rifiutando")
        return errori
    return ["ha accettato un contenitore con generazioni"]


@caso("oggetto inatteso nel contenitore: rifiuta")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    (cartella / "staging").mkdir()
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "oggetti_inattesi"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un contenitore con oggetti in piu'"]


@caso("collegamento al posto del contenitore: rifiuta")
def _(base: Path) -> list[str]:
    cid = identita()
    altrove = base / "altrove"
    (altrove / "generations").mkdir(parents=True)
    (altrove / "writer.lock").write_text("1")
    os.symlink(altrove, base / cid.storage_key)
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        errori = ([] if exc.code == "contenitore_non_ordinario"
                  else [f"codice inatteso: {exc.code}"])
        if not altrove.exists():
            errori.append("ha seguito il collegamento e rimosso il bersaglio")
        return errori
    return ["ha accettato un collegamento come contenitore"]


@caso("generations e' un collegamento: rifiuta")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = base / cid.storage_key
    cartella.mkdir(parents=True)
    fuori = base / "fuori"; fuori.mkdir()
    os.symlink(fuori, cartella / "generations")
    (cartella / "writer.lock").write_text("1")
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        errori = ([] if exc.code == "generazioni_non_ordinarie"
                  else [f"codice inatteso: {exc.code}"])
        if not fuori.exists():
            errori.append("ha seguito il collegamento")
        return errori
    return ["ha accettato una generations collegata"]


@caso("contenitore assente: rifiuta invece di inventare")
def _(base: Path) -> list[str]:
    try:
        R.rimuovi_contenitore_incompleto(autorizza(identita("mai"), base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "contenitore_assente"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un contenitore inesistente"]


@caso("senza autorizzazione dall'inventario: rifiuta, e un percorso non basta")
def _(base: Path) -> list[str]:
    cid = identita()
    contenitore_incompleto(base, cid)
    try:
        R.rimuovi_contenitore_incompleto(
            str(base / cid.storage_key), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "autorizzazione_assente"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un percorso al posto di un'autorizzazione"]


@caso("R1 il lucchetto globale e' quello della copia, non del negozio configurato")
def _(base: Path) -> list[str]:
    cid = identita()
    contenitore_incompleto(base, cid)
    from contract_store import _catalog_lock_path
    visti = []
    vero = R.catalog_admission_lock if hasattr(R, "catalog_admission_lock") else None
    import contract_store
    originale = contract_store.catalog_admission_lock

    def spia(*, store_root=None, **kw):
        visti.append(store_root)
        return originale(store_root=store_root, **kw)

    contract_store.catalog_admission_lock = spia
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    finally:
        contract_store.catalog_admission_lock = originale
    errori = []
    if not visti:
        errori.append("il lucchetto globale non e' stato preso")
    elif visti[0] is None:
        errori.append("il lucchetto globale e' andato al negozio configurato")
    elif Path(visti[0]).resolve() != base.resolve():
        errori.append(f"radice del lucchetto globale: {visti[0]}, attesa {base}")
    # e la sua sidecar deve cadere dentro la copia, non nel negozio configurato
    sidecar = _catalog_lock_path(base)
    if not str(sidecar).startswith(str(base.parent.parent)):
        errori.append(f"la sidecar del lucchetto e' fuori dalla copia: {sidecar}")
    return errori


@caso("R2 l'ispezione non scrive: nessun oggetto creato")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = base / cid.storage_key
    (cartella / "generations").mkdir(parents=True)   # senza writer.lock
    prima = sorted(p.name for p in cartella.iterdir())
    R.ispeziona_contenitore_incompleto(autorizza(cid, base), store_root=base)
    dopo = sorted(p.name for p in cartella.iterdir())
    return ([] if prima == dopo
            else [f"l'ispezione ha creato oggetti: {set(dopo) - set(prima)}"])


@caso("R3 un'identita' non inventariata non ottiene autorizzazione")
def _(base: Path) -> list[str]:
    cid = identita("mai-dichiarato-da-nessuno")
    contenitore_incompleto(base, cid)
    try:
        R.autorizza_dall_inventario(cid.value, store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code in {"contratto_non_inventariato",
                                   "inventario_autoriale_con_problemi"}
                else [f"codice inatteso: {exc.code}"])
    return ["ha autorizzato un contratto che l'inventario non dichiara"]


@caso("R4 un errore tardivo lascia una forma che il tentativo dopo riconosce")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    # interrompe DOPO la rinomina, che e' il punto d'impegno
    vero_rmdir = os.rmdir
    stato = {"visti": 0}

    def rmdir_che_fallisce(percorso, *a, **kw):
        stato["visti"] += 1
        if stato["visti"] >= 2:
            raise OSError(5, "interruzione iniettata")
        return vero_rmdir(percorso, *a, **kw)

    os.rmdir = rmdir_che_fallisce
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except Exception:
        pass
    finally:
        os.rmdir = vero_rmdir
    errori = []
    if cartella.exists():
        errori.append("il contenitore e' ancora al proprio nome: nessun impegno")
    ritirati = [p.name for p in base.iterdir()
                if p.name.startswith(R.PREFISSO_RITIRO)]
    if not ritirati:
        errori.append("nessuna forma di ritiro riconoscibile dopo l'arresto")
    return errori


@caso("R5 una sostituzione fra i due passaggi non tocca il bersaglio estraneo")
def _(base: Path) -> list[str]:
    cid = identita()
    contenitore_incompleto(base, cid)
    estraneo = base / "estraneo"
    (estraneo / "generations").mkdir(parents=True)
    (estraneo / "writer.lock").write_text("1")
    (estraneo / "prezioso.txt").write_text("da non toccare")
    # sostituisce il contenitore con un collegamento all'estraneo
    import shutil
    shutil.rmtree(base / cid.storage_key)
    os.symlink(estraneo, base / cid.storage_key)
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError:
        pass
    errori = []
    if not (estraneo / "prezioso.txt").exists():
        errori.append("ha toccato il bersaglio estraneo")
    if not (estraneo / "generations").exists():
        errori.append("ha rimosso generations nel bersaglio estraneo")
    return errori


@caso("R6 collisione SINCRONIZZATA sul nome di ritiro: nessuna sostituzione")
def _(base: Path) -> list[str]:
    """The collision has to appear AFTER the check, or the test proves nothing.

    Creating the retired name up front only exercises the early refusal; the
    property under test is that the rename itself will not replace, so the
    name is occupied between the verification and the commit point.
    """
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    occupato = base / (R.PREFISSO_RITIRO + cid.storage_key)
    vero_scrivi = R._scrivi_ricevuta

    def scrivi_e_occupa(percorso, documento):
        vero_scrivi(percorso, documento)
        occupato.mkdir()                       # subito prima della rinomina
        (occupato / "segno.txt").write_text("preesistente")

    R._scrivi_ricevuta = scrivi_e_occupa
    try:
        return _verifica_collisione(cid, base, cartella, occupato)
    finally:
        R._scrivi_ricevuta = vero_scrivi


def _verifica_collisione(cid, base: Path, cartella: Path,
                         occupato: Path) -> list[str]:
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        errori = ([] if exc.code == "nome_di_ritiro_occupato"
                  else [f"codice inatteso: {exc.code}"])
        if not (occupato / "segno.txt").exists():
            errori.append("ha sostituito il nome occupato")
        if not cartella.exists():
            errori.append("ha perso il contenitore originale")
        return errori
    return ["ha accettato una collisione sul nome di ritiro"]


@caso("R7 ripresa dal solo ritirato, con provenienza: completa")
def _(base: Path) -> list[str]:
    cid = identita()
    contenitore_incompleto(base, cid)
    aut = autorizza(cid, base)
    # simula un arresto subito dopo il punto d'impegno
    vero = R._pulisci_e_rimuovi
    R._pulisci_e_rimuovi = lambda *a, **k: (_ for _ in ()).throw(
        OSError(5, "arresto iniettato"))
    try:
        R.rimuovi_contenitore_incompleto(aut, store_root=base)
    except Exception:
        pass
    finally:
        R._pulisci_e_rimuovi = vero
    errori = []
    ritirato = base / (R.PREFISSO_RITIRO + cid.storage_key)
    if not ritirato.exists():
        errori.append("il punto d'impegno non e' durevole")
        return errori
    esito = R.rimuovi_contenitore_incompleto(aut, store_root=base)
    if ritirato.exists():
        errori.append("la ripresa non ha completato")
    if not esito.rimosso:
        errori.append("la ripresa non dichiara la rimozione")
    return errori


@caso("R7 ritirato senza provenienza: rifiuta invece di riprendere")
def _(base: Path) -> list[str]:
    cid = identita()
    ritirato = base / (R.PREFISSO_RITIRO + cid.storage_key)
    (ritirato / "generations").mkdir(parents=True)
    (ritirato / "writer.lock").write_text("1")
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        errori = ([] if exc.code == "ritirato_senza_provenienza"
                  else [f"codice inatteso: {exc.code}"])
        if not ritirato.exists():
            errori.append("ha rimosso senza provenienza")
        return errori
    return ["ha ripreso un ritirato che nessuna ricevuta rivendica"]


@caso("R8 voce comparsa dopo la verifica: blocca e la lascia intatta")
def _(base: Path) -> list[str]:
    cid = identita()
    cartella = contenitore_incompleto(base, cid)
    vero = R._verifica_forma
    stato = {"visti": 0}

    def verifica_e_intrufola(fd):
        vero(fd)
        stato["visti"] += 1
        if stato["visti"] == 1:      # subito dopo la prima verifica
            (cartella / "tardiva.txt").write_text("comparsa dopo")

    R._verifica_forma = verifica_e_intrufola
    try:
        R.rimuovi_contenitore_incompleto(autorizza(cid, base), store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        errori = ([] if exc.code in {"voce_tardiva", "oggetti_inattesi"}
                  else [f"codice inatteso: {exc.code}"])
    except Exception as exc:
        errori = [f"eccezione inattesa: {type(exc).__name__}"]
    else:
        errori = ["ha rimosso pur essendo comparsa una voce"]
    finally:
        R._verifica_forma = vero
    ritirato = base / (R.PREFISSO_RITIRO + cid.storage_key)
    superstite = (cartella / "tardiva.txt").exists() or \
        (ritirato / "tardiva.txt").exists()
    if not superstite:
        errori.append("la voce tardiva e' stata eliminata")
    return errori


@caso("R9 il sigillo non e' raggiungibile da chi importa il modulo")
def _(base: Path) -> list[str]:
    errori = []
    if hasattr(R, "_TOKEN"):
        errori.append("il sigillo e' ancora un attributo del modulo")
    try:
        R.AutorizzazioneRecupero(identita(), identita().storage_key, (0, 0),
                                 object())
        errori.append("un oggetto qualunque e' stato accettato come sigillo")
    except R.RecuperoPubblicazioneError as exc:
        if exc.code != "autorizzazione_non_emessa":
            errori.append(f"codice inatteso: {exc.code}")
    return errori


@caso("R9 autorizzazione di un'altra radice: rifiutata")
def _(base: Path) -> list[str]:
    cid = identita()
    altra = base / "altra"; altra.mkdir()
    contenitore_incompleto(base, cid)
    aut = autorizza(cid, altra)          # emessa contro un'altra radice
    try:
        R.rimuovi_contenitore_incompleto(aut, store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "radice_non_autorizzata"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un'autorizzazione emessa per un'altra radice"]


@caso("R10 seconda esecuzione dopo successo: stesso esito, non 'mai esistito'")
def _(base: Path) -> list[str]:
    cid = identita()
    contenitore_incompleto(base, cid)
    aut = autorizza(cid, base)
    primo = R.rimuovi_contenitore_incompleto(aut, store_root=base)
    secondo = R.rimuovi_contenitore_incompleto(aut, store_root=base)
    errori = []
    if not (primo.rimosso and secondo.rimosso):
        errori.append("la ripetizione non dichiara lo stesso esito")
    if primo != secondo:
        errori.append(f"esiti diversi: {primo} vs {secondo}")
    return errori


@caso("R10 contenitore mai esistito: rifiuta, non finge successo")
def _(base: Path) -> list[str]:
    try:
        R.rimuovi_contenitore_incompleto(autorizza(identita("mai2"), base),
                                         store_root=base)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "contenitore_assente"
                else [f"codice inatteso: {exc.code}"])
    return ["ha dichiarato successo su un contenitore mai esistito"]


def main() -> int:
    fallimenti = 0
    for nome, fn in CASI:
        with tempfile.TemporaryDirectory() as tmp:
            # The store root is nested, as it is in production
            # (.../contract-publications/v1): receipts then live beside THIS
            # case's store instead of in a shared parent, where recycled inode
            # numbers let one case read another's provenance.
            radice = Path(tmp) / "contract-publications" / "v1"
            radice.mkdir(parents=True)
            try:
                errori = fn(radice)
            except Exception as exc:  # noqa: BLE001
                errori = [f"eccezione {type(exc).__name__}: {exc}"]
        print(f"{'ROSSO' if errori else 'verde'}  {nome}"
              + ("  -> " + "; ".join(errori) if errori else ""))
        fallimenti += bool(errori)
    print()
    print("ESITO:", "tutte verdi" if not fallimenti else f"{fallimenti} PROVE ROSSE")
    return 1 if fallimenti else 0


if __name__ == "__main__":
    sys.exit(main())

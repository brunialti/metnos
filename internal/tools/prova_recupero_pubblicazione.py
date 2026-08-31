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
    esito = R.recupera_contenitore_incompleto(cid, store_root=base)
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
    esito = R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(cid, store_root=base, applica=True)
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
        R.recupera_contenitore_incompleto(identita("mai"), store_root=base,
                                          applica=True)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "contenitore_non_ordinario"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un contenitore inesistente"]


@caso("il chiamante non puo' passare un percorso: solo un ContractId")
def _(base: Path) -> list[str]:
    cid = identita()
    contenitore_incompleto(base, cid)
    try:
        R.recupera_contenitore_incompleto(
            str(base / cid.storage_key), store_root=base, applica=True)
    except R.RecuperoPubblicazioneError as exc:
        return ([] if exc.code == "identita_non_canonica"
                else [f"codice inatteso: {exc.code}"])
    return ["ha accettato un percorso al posto di un'identita'"]


def main() -> int:
    fallimenti = 0
    for nome, fn in CASI:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                errori = fn(Path(tmp))
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

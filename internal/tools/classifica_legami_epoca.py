"""Classify every dependency that names a Birth admission context.

The census (O15) counts what names the current set.  It does not say what has to
happen to each of those records when the epoch changes, and "twelve live
dependencies" is not a plan.  This tool answers, for one dependency at a time
and by a closed rule, which of three futures it has:

``epoca_storica``
    The record attests an act that was completed under the epoch in force at
    the time, and the generation it covers is no longer current.  It stays
    exactly as it is.  Re-pointing it would assert that a check happened under
    an epoch under which it never happened, which is not a migration but a
    forgery of provenance.

``nuova_epoca``
    The record backs a generation that IS current.  A new epoch cannot simply
    inherit it: the generation has to be re-attested under the new epoch before
    the transition can be declared complete.

``cessa_di_essere_corrente``
    The record backs a current generation whose contract is being retired, so
    the right outcome is that the generation stops being current - not that the
    record is rewritten.

Anything the rule cannot decide is ``non_classificato`` and **blocks**: an
unclassified dependency is the one case where declaring F4 would be a claim
nobody checked.

Exit codes:
    0  every dependency classified, and none needs an action before F4
    1  the tool could not run
    2  at least one dependency is unclassified: F4 cannot be declared
    3  every dependency classified, but some need action (re-attestation or
       retirement) before F4 can be declared

Usage:
    python3 internal/tools/classifica_legami_epoca.py
    python3 internal/tools/classifica_legami_epoca.py --radice-nascita DIR \
        --negozio DIR --stato-nascita DIR [--ritirati NOME ...]
"""
from __future__ import annotations

import argparse
import base64
import binascii
import glob
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK = 0
EXIT_SELF = 1
EXIT_NON_CLASSIFICATO = 2
EXIT_AZIONE = 3

STORICA = "epoca_storica"
NUOVA = "nuova_epoca"
CESSA = "cessa_di_essere_corrente"
IGNOTA = "non_classificato"


@dataclass
class Legame:
    """One dependency, its evidence, and the class the rule assigns it."""

    tipo: str                 # ricevuta_ammissione | ricevuta_produttore
    locazione: str
    contesto: str
    contratto: str = ""
    generazione: str = ""
    corrente: bool | None = None
    stato: str = ""
    classe: str = IGNOTA
    motivo: str = ""
    discorde: str = ""
    ritirato: bool = False
    prove: dict = field(default_factory=dict)


def contesto_corrente(radice: Path) -> str:
    prep = json.loads((radice / "prepared-v1.json").read_text())
    materiale = json.loads(
        (radice / prep["authority_set"] / "context" / "material-v1.json").read_text()
    )
    return materiale["prepared_admission_context_id"]


def _classifica(legame: Legame) -> Legame:
    """The closed rule.  Three admitted futures, and one refusal.

    ``cessa_di_essere_corrente`` is decided by the authenticated retirement
    tombstone of the contract, never by an argument: a caller able to declare a
    contract retired would be able to reduce the work F4 requires, which is an
    authority and not a diagnostic input.
    """
    if legame.discorde:
        legame.classe = IGNOTA
        legame.motivo = f"identita' o stato discordanti - {legame.discorde}"
        return legame
    if legame.corrente is None:
        legame.classe = IGNOTA
        legame.motivo = ("non si e' potuto stabilire se la generazione coperta "
                         "sia quella corrente")
        return legame
    if legame.ritirato:
        legame.classe = CESSA
        legame.motivo = ("il contratto porta una lapide di ritiro autenticata: "
                         "la generazione cessa di essere corrente, il legame "
                         "non si riscrive")
        return legame
    if not legame.corrente:
        legame.classe = STORICA
        legame.motivo = ("attesta una generazione superata: l'atto e' concluso "
                         "e resta legato all'epoca sotto cui e' avvenuto")
        return legame
    legame.classe = NUOVA
    legame.motivo = ("sostiene la generazione corrente: prima della "
                     "transizione va riattestata sotto la nuova epoca")
    return legame


def stato_autenticato_dei_contratti(negozio: Path, installazione: Path) -> dict:
    """Contract id -> (generazione corrente autenticata | ritiro | errore).

    The first version read ``binding.json``, ``current`` and the receipts as
    plain files.  That is reproducible on the observed bytes and proves nothing
    against a divergence between what a locator says and what is signed, which
    is precisely the divergence a transition has to survive.  Here the
    inventory and the current generation come from the productive primitives of
    the store, and a contract whose current state cannot be authenticated is
    never given a class: it blocks.
    """
    import sys as _sys
    radice_codice = str(installazione / "runtime")
    if radice_codice not in _sys.path:
        _sys.path.insert(0, radice_codice)
    os.environ.setdefault("METNOS_INSTALL_ROOT", str(installazione))
    from manifest_inventory import inventory_store_manifests
    from contract_store import ContractRetirement, current_contract
    from sign import list_trusted_publics

    inventario = inventory_store_manifests(store_root=negozio)
    fidate = list_trusted_publics()
    stato: dict = {}
    for ref in inventario.manifests:
        chiave = str(ref.contract_id)
        try:
            osservato = current_contract(
                ref, trusted_publics=fidate, store_root=negozio
            )
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            stato[chiave] = ("errore", f"{getattr(exc, 'code', type(exc).__name__)}")
            continue
        if isinstance(osservato, ContractRetirement):
            stato[chiave] = ("ritiro", str(osservato.previous_generation_id or ""))
        else:
            stato[chiave] = ("corrente",
                             str(osservato.generation_id or "").removeprefix("sha256:"))
    return stato


def legami_del_negozio(negozio: Path, contesto: str, stato: dict) -> list[Legame]:
    """Admission receipts naming the context, keyed by composite identity.

    Identity is ``(contract_id, generation_id)``.  Indexing on the digest alone
    would let two contracts that happen to share a generation digest borrow each
    other's twin, and the F4 contract already uses the pair.  Every place the
    pair appears - the path, the document, and later the envelope - has to
    agree; a divergence is refused rather than resolved by preferring one
    source.
    """
    risultato: list[Legame] = []
    for cartella in sorted(negozio.glob("*")):
        if not cartella.is_dir():
            continue
        try:
            binding = json.loads((cartella / "binding.json").read_text())
            contratto = str(binding.get("contract_id") or cartella.name)
        except (OSError, json.JSONDecodeError):
            contratto = cartella.name
        for percorso in sorted(cartella.glob("admission-receipts/*.json")):
            dal_percorso = percorso.name.removesuffix(".json")
            try:
                documento = json.loads(percorso.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                risultato.append(Legame(
                    tipo="ricevuta_ammissione", locazione=str(percorso),
                    contesto="(illeggibile)", contratto=contratto,
                    generazione=dal_percorso,
                    motivo=f"lettura fallita: {exc}",
                ))
                continue
            if documento.get("admission_context_id") != contesto:
                continue
            dal_documento = str(
                documento.get("generation_id", "")
            ).removeprefix("sha256:")
            contratto_documento = str(documento.get("contract_id") or contratto)
            legame = Legame(
                tipo="ricevuta_ammissione",
                locazione=str(percorso),
                contesto=contesto,
                contratto=contratto,
                generazione=dal_percorso,
                stato=str(documento.get("approved_lifecycle") or ""),
            )
            # Divergence between the three places the identity appears is a
            # refusal, not something to reconcile.
            if dal_documento and dal_documento != dal_percorso:
                legame.discorde = (f"generazione: percorso={dal_percorso[:16]} "
                                   f"documento={dal_documento[:16]}")
            elif contratto_documento != contratto:
                legame.discorde = (f"contratto: locatore={contratto} "
                                   f"documento={contratto_documento}")
            voce = stato.get(contratto)
            if voce is None:
                legame.discorde = legame.discorde or (
                    "il contratto non compare nell'inventario autenticato"
                )
            elif voce[0] == "errore":
                legame.discorde = legame.discorde or (
                    f"stato corrente non autenticabile: {voce[1]}"
                )
            else:
                legame.ritirato = voce[0] == "ritiro"
                legame.corrente = (voce[1] == dal_percorso)
                legame.prove = {"generazione_corrente": voce[1],
                                "fonte": "current_contract"}
            risultato.append(legame)
    return risultato


def legami_dello_stato(stato_db: Path, contesto: str,
                       per_gemello: dict[tuple[str, str], Legame],
                       rifiuti: list[str]) -> list[Legame]:
    """Producer receipts, under a closed terminal rule.

    A producer receipt is not an independent fact: it is the birth side of the
    commit whose contract side is the admission receipt.  Three outcomes, and
    no fourth:

    * a **valid terminal rejection** carries no admission receipt - it is not a
      dependency at all, and saying "unreadable" about it was wrong;
    * a **valid conclusion** carries one, and is paired by the composite
      identity ``(contract_id, generation_id)``;
    * anything else - an incoherent state, an envelope that does not parse, a
      pair with no twin - is ``non_classificato`` and blocks.

    The previous version let a `committed` row with a broken envelope that did
    not repeat the context in clear fall into a side list and leave the verdict
    green.  A conclusion we cannot read is exactly the case that must not pass.
    """
    percorso = stato_db / "producer_receipts.sqlite"
    if not percorso.exists():
        return []
    risultato: list[Legame] = []
    conn = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True)
    try:
        colonne = [d[1] for d in conn.execute(
            "PRAGMA table_info('birth_producer_receipts')"
        )]
        for riga in conn.execute("select rowid, * from birth_producer_receipts"):
            rid, valori = riga[0], riga[1:]
            documento = dict(zip(colonne, valori))
            locazione = f"{percorso}#birth_producer_receipts:{rid}"
            situazione = str(documento.get("state") or "")
            busta = documento.get("terminal_envelope")
            grezzo = (b"" if busta is None
                      else busta if isinstance(busta, bytes) else str(busta).encode())

            interno = None
            if grezzo:
                try:
                    interno = json.loads(grezzo)
                except json.JSONDecodeError:
                    interno = None

            # A properly concluded refusal carries the key with a null value
            # and names why it refused.  It admitted nothing, so it is not a
            # dependency - but a refusal WITHOUT a reason is still incoherent
            # and must block rather than be waved through.
            if situazione == "rejected" and interno is not None \
                    and interno.get("admission_receipt") is None:
                motivo_rifiuto = (documento.get("rejection_code")
                                  or interno.get("error_code"))
                if motivo_rifiuto:
                    rifiuti.append(f"{locazione} ({motivo_rifiuto})")
                    continue
                risultato.append(Legame(
                    tipo="ricevuta_produttore", locazione=locazione,
                    contesto=contesto, stato=situazione,
                    discorde="rifiuto terminale senza codice di rifiuto",
                ))
                continue

            if interno is None:
                if not grezzo and situazione not in {"committed", "rejected"}:
                    continue
                risultato.append(Legame(
                    tipo="ricevuta_produttore", locazione=locazione,
                    contesto=contesto, stato=situazione,
                    discorde="busta terminale non interpretabile",
                ))
                continue

            codificata = interno.get("admission_receipt")
            if codificata is None:
                risultato.append(Legame(
                    tipo="ricevuta_produttore", locazione=locazione,
                    contesto=contesto, stato=situazione,
                    discorde=("conclusione senza ricevuta di ammissione in "
                              f"stato {situazione or '(assente)'}"),
                ))
                continue
            try:
                ricevuta = json.loads(base64.b64decode(codificata))
            except (binascii.Error, json.JSONDecodeError, TypeError, ValueError):
                risultato.append(Legame(
                    tipo="ricevuta_produttore", locazione=locazione,
                    contesto=contesto, stato=situazione,
                    discorde="ricevuta di ammissione non decodificabile",
                ))
                continue

            if ricevuta.get("admission_context_id") != contesto:
                continue

            generazione = str(ricevuta.get("generation_id", "")).removeprefix("sha256:")
            contratto = str(ricevuta.get("contract_id", ""))
            pubblicazione = interno.get("publication") or {}
            legame = Legame(
                tipo="ricevuta_produttore", locazione=locazione,
                contesto=contesto, contratto=contratto,
                generazione=generazione, stato=situazione,
                prove={"scade": str(documento.get("expires_at") or "")},
            )
            if situazione != "committed":
                legame.discorde = (f"conclusione con ricevuta ma stato "
                                   f"{situazione or '(assente)'}")
            else:
                # The envelope repeats the identity: it has to agree with the
                # receipt it carries.
                dalla_pubblicazione = str(
                    pubblicazione.get("current_generation_id", "")
                ).removeprefix("sha256:")
                contratto_pubblicazione = str(pubblicazione.get("contract_id") or contratto)
                if dalla_pubblicazione and dalla_pubblicazione != generazione:
                    legame.discorde = ("generazione discorde fra ricevuta e "
                                       "pubblicazione nella stessa busta")
                elif contratto_pubblicazione != contratto:
                    legame.discorde = ("contratto discorde fra ricevuta e "
                                       "pubblicazione nella stessa busta")
                else:
                    gemello = per_gemello.get((contratto, generazione))
                    if gemello is None:
                        legame.discorde = ("nessun gemello con identita' "
                                           f"({contratto}, {generazione[:16]})")
                    else:
                        legame.corrente = gemello.corrente
                        legame.ritirato = gemello.ritirato
                        legame.discorde = gemello.discorde
                        legame.prove["gemello"] = gemello.locazione
            risultato.append(legame)
    finally:
        conn.close()
    return risultato


def classifica(radice: Path, negozio: Path, stato_db: Path,
               installazione: Path, stato: dict | None = None):
    """``stato`` is a seam for the tests only.

    Production always derives it from the store's own primitives; a fixture may
    hand it in so a test can exercise the rule without publishing signed
    contracts.  It is never reachable from the command line, so a caller cannot
    use it to declare a contract retired.
    """
    contesto = contesto_corrente(radice)
    if stato is None:
        stato = stato_autenticato_dei_contratti(negozio, installazione)
    del_negozio = legami_del_negozio(negozio, contesto, stato)
    per_gemello = {
        (l.contratto, l.generazione): l
        for l in del_negozio if l.contratto and l.generazione
    }
    rifiuti: list[str] = []
    dello_stato = legami_dello_stato(stato_db, contesto, per_gemello, rifiuti)
    tutti = [_classifica(l) for l in del_negozio + dello_stato]
    return contesto, tutti, rifiuti, len(stato)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radice-nascita",
                    default=os.path.expanduser("~/.config/metnos/birth"))
    ap.add_argument("--negozio", default=os.path.expanduser(
        "~/.local/state/metnos/contract-publications/v1"))
    ap.add_argument("--stato-nascita", default=os.path.expanduser(
        "~/.local/state/metnos/birth"))
    ap.add_argument("--radice-installazione", default="/opt/metnos",
                    help="l'albero da cui i contratti sono stati pubblicati: "
                         "serve ad autenticare le generazioni correnti")
    args = ap.parse_args(argv)

    try:
        contesto, legami, rifiuti, contratti = classifica(
            Path(args.radice_nascita), Path(args.negozio),
            Path(args.stato_nascita), Path(args.radice_installazione),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"CLASSIFICAZIONE NON ESEGUIBILE: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_SELF

    print(f"contesto corrente : {contesto}")
    print(f"contratti autenticati dall'inventario produttivo: {contratti}")
    print(f"legami esaminati  : {len(legami)}")
    if rifiuti:
        print(f"rifiuti terminali validi (non sono legami): {len(rifiuti)}")
    print()
    for legame in legami:
        print(f"[{legame.classe}] {legame.tipo}")
        print(f"    {legame.locazione}")
        print(f"    contratto={legame.contratto or '(ignoto)'} "
              f"generazione={legame.generazione[:16] or '(ignota)'} "
              f"corrente={legame.corrente} ritirato={legame.ritirato} "
              f"stato={legame.stato or '-'}")
        print(f"    perche': {legame.motivo}")

    conteggio: dict[str, int] = {}
    for legame in legami:
        conteggio[legame.classe] = conteggio.get(legame.classe, 0) + 1
    print("\n== CLASSIFICAZIONE ==")
    for classe in (STORICA, NUOVA, CESSA, IGNOTA):
        print(f"  {classe:26} {conteggio.get(classe, 0)}")

    print("\n== LIMITE DICHIARATO ==")
    print("  la firma delle ricevute di ammissione NON viene verificata: il")
    print("  verificatore appartiene all'autorita' di nascita sigillata, che")
    print("  durante la transizione non e' attiva. Sono autenticati inventario,")
    print("  generazione corrente e ritiro; l'identita' composta e' confrontata")
    print("  fra percorso, documento e busta.")

    if conteggio.get(IGNOTA):
        print("\nESITO: almeno un legame NON e' classificato. "
              "F4 non puo' essere dichiarata.")
        return EXIT_NON_CLASSIFICATO
    if conteggio.get(NUOVA) or conteggio.get(CESSA):
        print("\nESITO: classificazione completa, ma alcuni legami richiedono "
              "un'azione prima di F4.")
        return EXIT_AZIONE
    print("\nESITO: ogni legame resta legato all'epoca storica. "
          "Nessun ripuntamento e' necessario, e nessuno sarebbe lecito.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

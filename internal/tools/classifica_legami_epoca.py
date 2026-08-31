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
    prove: dict = field(default_factory=dict)


def contesto_corrente(radice: Path) -> str:
    prep = json.loads((radice / "prepared-v1.json").read_text())
    materiale = json.loads(
        (radice / prep["authority_set"] / "context" / "material-v1.json").read_text()
    )
    return materiale["prepared_admission_context_id"]


def _classifica(legame: Legame, ritirati: set[str]) -> Legame:
    """The closed rule.  Three admitted futures, and one refusal."""
    if legame.corrente is None:
        legame.classe = IGNOTA
        legame.motivo = ("non si e' potuto stabilire se la generazione coperta "
                         "sia quella corrente")
        return legame
    if not legame.corrente:
        legame.classe = STORICA
        legame.motivo = ("attesta una generazione superata: l'atto e' concluso "
                         "e resta legato all'epoca sotto cui e' avvenuto")
        return legame
    if legame.contratto in ritirati:
        legame.classe = CESSA
        legame.motivo = ("sostiene la generazione corrente di un contratto in "
                         "ritiro: deve cessare di essere corrente, non essere "
                         "riscritta")
        return legame
    legame.classe = NUOVA
    legame.motivo = ("sostiene la generazione corrente: prima della "
                     "transizione va riattestata sotto la nuova epoca")
    return legame


def legami_del_negozio(negozio: Path, contesto: str) -> list[Legame]:
    """Every admission receipt that names the context, with its generation."""
    risultato: list[Legame] = []
    for cartella in sorted(negozio.glob("*")):
        if not cartella.is_dir():
            continue
        try:
            corrente = (cartella / "current").read_text().strip()
        except OSError:
            corrente = ""
        try:
            binding = json.loads((cartella / "binding.json").read_text())
            contratto = str(binding.get("contract_id") or cartella.name)
        except (OSError, json.JSONDecodeError):
            contratto = cartella.name
        for percorso in sorted(cartella.glob("admission-receipts/*.json")):
            try:
                documento = json.loads(percorso.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                risultato.append(Legame(
                    tipo="ricevuta_ammissione", locazione=str(percorso),
                    contesto="(illeggibile)", contratto=contratto,
                    motivo=f"lettura fallita: {exc}",
                ))
                continue
            if documento.get("admission_context_id") != contesto:
                continue
            generazione = percorso.name.removesuffix(".json")
            risultato.append(Legame(
                tipo="ricevuta_ammissione",
                locazione=str(percorso),
                contesto=contesto,
                contratto=contratto,
                generazione=generazione,
                corrente=(corrente.removeprefix("sha256:") == generazione
                          if corrente else None),
                stato=str(documento.get("approved_lifecycle") or ""),
                prove={"current": corrente},
            ))
    return risultato


def legami_dello_stato(stato: Path, contesto: str,
                       per_generazione: dict[str, Legame],
                       non_interpretabili: list[str]) -> list[Legame]:
    """Producer receipts whose terminal envelope embeds an admission receipt.

    A producer receipt is not an independent fact: it is the birth side of the
    same commit whose contract side is the admission receipt.  Its future is
    therefore the future of that commit, and the tool says so instead of
    inventing a separate rule for it.
    """
    percorso = stato / "producer_receipts.sqlite"
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
            busta = documento.get("terminal_envelope")
            if busta is None:
                continue
            grezzo = busta if isinstance(busta, bytes) else str(busta).encode()
            generazione = contratto = precedente = ""
            leggibile = True
            suo = False
            try:
                interno = json.loads(grezzo)
                # The admission receipt travels base64-encoded inside the
                # envelope, so the context is NOT literally present in the raw
                # bytes.  Filtering on a raw substring worked on the real data
                # only because other fields repeat the context in clear: the
                # decoded receipt is the authority, and the literal match is
                # kept solely as a safety net for an envelope we cannot parse.
                ricevuta = json.loads(base64.b64decode(interno["admission_receipt"]))
                suo = ricevuta.get("admission_context_id") == contesto
                generazione = str(ricevuta.get("generation_id", "")).removeprefix("sha256:")
                contratto = str(ricevuta.get("contract_id", ""))
                precedente = str(
                    interno.get("publication", {}).get("previous_generation_id", "")
                ).removeprefix("sha256:")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError,
                    binascii.Error):
                leggibile = False
                generazione = ""
                # An envelope we cannot read is attributed to this context only
                # if it names it in clear; otherwise it belongs to no epoch we
                # can decide about, and is reported separately rather than
                # silently dropped.
                suo = contesto.encode() in grezzo
                if not suo:
                    non_interpretabili.append(
                        f"{percorso}#birth_producer_receipts:{rid}"
                    )
            if not suo:
                continue
            gemello = per_generazione.get(generazione)
            risultato.append(Legame(
                tipo="ricevuta_produttore",
                locazione=f"{percorso}#birth_producer_receipts:{rid}",
                contesto=contesto,
                contratto=contratto or (gemello.contratto if gemello else ""),
                generazione=generazione,
                corrente=gemello.corrente if gemello else None,
                stato=str(documento.get("state") or ""),
                prove={"gemello": gemello.locazione if gemello else "",
                       "generazione_precedente": precedente,
                       "scade": str(documento.get("expires_at") or "")},
            ))
    finally:
        conn.close()
    return risultato


def classifica(radice: Path, negozio: Path, stato: Path,
               ritirati: set[str]) -> tuple[str, list[Legame]]:
    contesto = contesto_corrente(radice)
    del_negozio = legami_del_negozio(negozio, contesto)
    per_generazione = {l.generazione: l for l in del_negozio if l.generazione}
    non_interpretabili: list[str] = []
    dello_stato = legami_dello_stato(
        stato, contesto, per_generazione, non_interpretabili
    )
    # A producer receipt whose twin was not found stays undecided rather than
    # being guessed: the whole point of the rule is that nothing is assigned a
    # future by default.
    tutti = [_classifica(l, ritirati) for l in del_negozio + dello_stato]
    return contesto, tutti, non_interpretabili


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radice-nascita",
                    default=os.path.expanduser("~/.config/metnos/birth"))
    ap.add_argument("--negozio", default=os.path.expanduser(
        "~/.local/state/metnos/contract-publications/v1"))
    ap.add_argument("--stato-nascita", default=os.path.expanduser(
        "~/.local/state/metnos/birth"))
    ap.add_argument("--ritirati", nargs="*", default=[],
                    help="contratti in ritiro: la loro generazione corrente cessa")
    args = ap.parse_args(argv)

    try:
        contesto, legami, non_interpretabili = classifica(
            Path(args.radice_nascita), Path(args.negozio),
            Path(args.stato_nascita), set(args.ritirati),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"CLASSIFICAZIONE NON ESEGUIBILE: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_SELF

    print(f"contesto corrente: {contesto}")
    print(f"legami esaminati : {len(legami)}")
    if non_interpretabili:
        print(f"buste non interpretabili e non attribuibili a questo contesto: "
              f"{len(non_interpretabili)}")
    print()
    for legame in legami:
        print(f"[{legame.classe}] {legame.tipo}")
        print(f"    {legame.locazione}")
        print(f"    contratto={legame.contratto or '(ignoto)'} "
              f"generazione={legame.generazione[:16] or '(ignota)'} "
              f"corrente={legame.corrente} stato={legame.stato or '-'}")
        print(f"    perche': {legame.motivo}")

    conteggio = {}
    for legame in legami:
        conteggio[legame.classe] = conteggio.get(legame.classe, 0) + 1
    print("\n== CLASSIFICAZIONE ==")
    for classe in (STORICA, NUOVA, CESSA, IGNOTA):
        print(f"  {classe:26} {conteggio.get(classe, 0)}")

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

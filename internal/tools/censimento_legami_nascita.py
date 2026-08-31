"""C3 - census of what, outside the Birth root, still names the current set.

The first version of this tool produced a number nobody could defend: it counted
raw substring hits instead of records, truncated the tally with a display limit,
searched both the prefixed and the bare form of the same digest, re-read as raw
bytes the very databases it had already queried column by column, and swallowed
read errors so that incomplete coverage looked like absence.  This one is built
around the four properties that were missing.

**One canonical form.**  Every identifier is reduced to its bare lowercase hex
digest.  ``sha256:<hex>``, ``p-<hex>`` and ``<hex>`` are three surfaces of one
identifier, so they are searched once and reported once, with the surfaces
actually observed recorded alongside.  Matching is bounded by a hex-boundary
rule, so a digest is never found inside a longer hex run.

**One record per fact-bearing location.**  A file that names an identifier is
one record.  A database row that names it is one record, whatever the number of
columns or surfaces involved.  Nothing depends on how much the tool decided to
print.

**No object is examined twice.**  A database queried structurally is never also
read as a blob of bytes.

**Fail-closed.**  Every root, database, table and file inside the perimeter that
cannot be read is recorded as unexamined, the census declares itself incomplete
and the process exits non-zero.  An unreadable object is never silently absent,
and an error is never counted as a finding.

Exit codes:
    0  census complete; the number of records is the measure
    1  the tool could not run at all (bad perimeter, unreadable Birth root)
    2  census INCOMPLETE: at least one object in the perimeter was unreadable

Usage:
    python3 internal/tools/censimento_legami_nascita.py
    python3 internal/tools/censimento_legami_nascita.py --radice-nascita DIR --perimetro DIR [DIR...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

ESTENSIONI_SQLITE = (".sqlite", ".db", ".sqlite3")
DIRECTORY_ESCLUSE = {".git", "__pycache__", "node_modules", ".venv"}
BLOCCO_LETTURA = 1 << 20  # 1 MiB, letto a blocchi con sovrapposizione

EXIT_OK = 0
EXIT_SELF = 1
EXIT_INCOMPLETO = 2


@dataclass(frozen=True)
class Identificativo:
    """One identifier in its canonical form, plus where it came from."""

    canonico: str          # bare lowercase hex
    ruolo: str             # set_id, prepared_context_epoch, producer, ...
    superficie_originale: str


@dataclass
class Record:
    """One fact-bearing location that names at least one identifier."""

    classe: str            # viva | archiviata
    tipo: str              # file | riga_sqlite
    locazione: str         # path, or path#tabella:rowid
    identificativi: set[str] = field(default_factory=set)
    superfici: set[str] = field(default_factory=set)
    colonne: set[str] = field(default_factory=set)


@dataclass
class NonEsaminato:
    """Something inside the perimeter that could not be read."""

    oggetto: str
    motivo: str


def forma_canonica(valore: str) -> str | None:
    """Reduce a surface form to the bare lowercase hex digest it carries."""
    grezzo = valore.strip().lower()
    for prefisso in ("sha256:", "p-"):
        if grezzo.startswith(prefisso):
            grezzo = grezzo[len(prefisso):]
    return grezzo if re.fullmatch(r"[0-9a-f]{16,128}", grezzo) else None


def acquisisci_identificativi(radice: Path) -> list[Identificativo]:
    """Read the identifiers from the Birth root itself; never hard-code them."""
    marcatore = radice / "prepared-v1.json"
    prep = json.loads(marcatore.read_text())
    insieme = radice / prep["authority_set"]
    materiale = json.loads(
        (insieme / "context" / "material-v1.json").read_text()
    )

    grezzi: list[tuple[str, str]] = [
        ("set_id", prep["set_id"]),
        ("author_store_public_inventory_sha256",
         prep["author_store_public_inventory_sha256"]),
        ("context_material_sha256", prep["context_material_sha256"]),
        ("set_json_sha256", prep["set_json_sha256"]),
        ("transaction_id", prep["transaction_id"]),
        ("prepared_admission_context_id",
         materiale["prepared_admission_context_id"]),
        ("prepared_context_epoch", materiale["prepared_context_epoch"]),
    ]
    produttori = insieme / "producers"
    if produttori.is_dir():
        for voce in sorted(produttori.iterdir()):
            grezzi.append(("producer", voce.name))

    risultato: dict[str, Identificativo] = {}
    for ruolo, valore in grezzi:
        canonico = forma_canonica(valore)
        if canonico is None:
            raise ValueError(f"identificativo non canonicalizzabile: {ruolo}={valore!r}")
        # Two roles may share a digest; the first one named wins, and the
        # duplicate is not a second identifier to search for.
        risultato.setdefault(canonico, Identificativo(canonico, ruolo, valore))
    return sorted(risultato.values(), key=lambda i: i.ruolo)


def _regex_identificativi(identificativi: list[Identificativo]) -> re.Pattern[str]:
    """One bounded alternation, so a digest never matches inside a longer run."""
    alternativa = "|".join(re.escape(i.canonico) for i in identificativi)
    return re.compile(f"(?<![0-9a-f])({alternativa})(?![0-9a-f])")


def _superfici_in(testo: str, canonico: str) -> set[str]:
    """Name the surface forms actually present around a canonical digest."""
    superfici = set()
    if re.search(f"(?<![0-9a-f])sha256:{canonico}(?![0-9a-f])", testo):
        superfici.add("sha256:")
    if re.search(f"(?<![0-9a-z-]){re.escape('p-')}{canonico}(?![0-9a-f])", testo):
        superfici.add("p-")
    if re.search(f"(?<![0-9a-f:\\-])({canonico})(?![0-9a-f])", testo):
        superfici.add("nuda")
    return superfici or {"nuda"}


def classe_di(percorso: Path, radice_nascita: Path) -> str:
    """Live dependency, or archived copy of a previous root."""
    genitore = radice_nascita.parent
    for parte in percorso.parts:
        if parte.startswith(radice_nascita.name + ".") and str(genitore) in str(percorso):
            return "archiviata"
    return "viva"


def esamina_sqlite(
    percorso: Path, rx: re.Pattern[str], identificativi: list[Identificativo],
    radice_nascita: Path, non_esaminati: list[NonEsaminato],
) -> list[Record]:
    """Query one database row by row.  An unreadable table is never absence."""
    try:
        conn = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True)
        tabelle = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'"
        )]
    except sqlite3.Error as exc:
        # An open error is not a finding: it is missing coverage.
        non_esaminati.append(NonEsaminato(str(percorso), f"apertura: {exc}"))
        return []

    per_riga: dict[tuple[str, int], Record] = {}
    try:
        for tabella in tabelle:
            try:
                colonne = [d[1] for d in conn.execute(
                    f"PRAGMA table_info('{tabella}')"
                )]
                # A WITHOUT ROWID table has no rowid at all.  The previous
                # version let the resulting error fall into a bare `continue`,
                # so twenty-two tables looked like "no links" instead of "not
                # examined".  Here the ordinal position becomes the row
                # identity, and the record says which identity it used.
                try:
                    righe = list(conn.execute(f"select rowid, * from '{tabella}'"))
                    chiavi = [r[0] for r in righe]
                    valori_righe = [r[1:] for r in righe]
                    forma_chiave = "rowid"
                except sqlite3.OperationalError:
                    righe = list(conn.execute(f"select * from '{tabella}'"))
                    chiavi = list(range(len(righe)))
                    valori_righe = righe
                    forma_chiave = "ord"
                for rowid, valori in zip(chiavi, valori_righe):
                    for colonna, valore in zip(colonne, valori):
                        if isinstance(valore, bytes):
                            valore = valore.decode("utf-8", "ignore")
                        if not isinstance(valore, str):
                            continue
                        trovati = set(rx.findall(valore))
                        if not trovati:
                            continue
                        chiave = (tabella, rowid)
                        rec = per_riga.get(chiave)
                        if rec is None:
                            rec = Record(
                                classe=classe_di(percorso, radice_nascita),
                                tipo="riga_sqlite",
                                locazione=(
                                    f"{percorso}#{tabella}:"
                                    f"{'' if forma_chiave == 'rowid' else 'ord'}{rowid}"
                                ),
                            )
                            per_riga[chiave] = rec
                        rec.identificativi |= trovati
                        rec.colonne.add(f"{tabella}.{colonna}")
                        for canonico in trovati:
                            rec.superfici |= _superfici_in(valore, canonico)
            except sqlite3.Error as exc:
                non_esaminati.append(
                    NonEsaminato(f"{percorso}#{tabella}", f"lettura tabella: {exc}")
                )
    finally:
        conn.close()
    return list(per_riga.values())


def esamina_file(
    percorso: Path, rx: re.Pattern[str], radice_nascita: Path,
    non_esaminati: list[NonEsaminato],
) -> Record | None:
    """Read one file in overlapping blocks: nothing is skipped for its size."""
    trovati: set[str] = set()
    superfici: set[str] = set()
    coda = ""
    try:
        with percorso.open("rb") as fh:
            while True:
                blocco = fh.read(BLOCCO_LETTURA)
                if not blocco:
                    break
                testo = coda + blocco.decode("utf-8", "ignore")
                for canonico in set(rx.findall(testo)):
                    trovati.add(canonico)
                    superfici |= _superfici_in(testo, canonico)
                coda = testo[-200:]
    except OSError as exc:
        non_esaminati.append(NonEsaminato(str(percorso), f"lettura file: {exc}"))
        return None
    if not trovati:
        return None
    rec = Record(
        classe=classe_di(percorso, radice_nascita),
        tipo="file",
        locazione=str(percorso),
    )
    rec.identificativi = trovati
    rec.superfici = superfici
    return rec


def censisci(radice_nascita: Path, perimetro: list[Path]):
    identificativi = acquisisci_identificativi(radice_nascita)
    rx = _regex_identificativi(identificativi)
    non_esaminati: list[NonEsaminato] = []
    record: list[Record] = []

    # Pass one: every database, queried structurally.  Their paths are then
    # excluded from the byte pass, so no object is examined twice.
    gia_interrogati: set[Path] = set()
    for radice in perimetro:
        if not radice.exists():
            non_esaminati.append(NonEsaminato(str(radice), "radice assente"))
            continue
        for percorso in sorted(radice.rglob("*")):
            if any(x in percorso.parts for x in DIRECTORY_ESCLUSE):
                continue
            if radice_nascita == percorso or radice_nascita in percorso.parents:
                continue
            if not percorso.is_file() or percorso.is_symlink():
                continue
            if percorso.suffix in ESTENSIONI_SQLITE:
                gia_interrogati.add(percorso)
                record.extend(esamina_sqlite(
                    percorso, rx, identificativi, radice_nascita, non_esaminati,
                ))

    # Pass two: every other file, read as text.
    letti = 0
    for radice in perimetro:
        if not radice.exists():
            continue
        for percorso in sorted(radice.rglob("*")):
            if any(x in percorso.parts for x in DIRECTORY_ESCLUSE):
                continue
            if radice_nascita == percorso or radice_nascita in percorso.parents:
                continue
            if not percorso.is_file() or percorso.is_symlink():
                continue
            if percorso in gia_interrogati:
                continue
            letti += 1
            rec = esamina_file(percorso, rx, radice_nascita, non_esaminati)
            if rec is not None:
                record.append(rec)
    return identificativi, record, non_esaminati, letti


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radice-nascita",
                    default=os.path.expanduser("~/.config/metnos/birth"))
    ap.add_argument("--perimetro", nargs="*", default=[
        os.path.expanduser("~/.local/state/metnos"),
        os.path.expanduser("~/.local/share/metnos"),
        os.path.expanduser("~/.config/metnos"),
        "/opt/metnos",
    ])
    ap.add_argument("--silenzioso", action="store_true")
    args = ap.parse_args(argv)

    radice = Path(args.radice_nascita)
    perimetro = [Path(p) for p in args.perimetro]
    try:
        identificativi, record, non_esaminati, letti = censisci(radice, perimetro)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"CENSIMENTO NON ESEGUIBILE: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_SELF

    if not args.silenzioso:
        print("== IDENTIFICATIVI ACQUISITI DALLA RADICE (forma canonica) ==")
        for i in identificativi:
            print(f"  {i.ruolo:38} {i.canonico}")
        print("\n== PERIMETRO ==")
        for p in perimetro:
            print(f"  {p} {'(esiste)' if p.exists() else '(ASSENTE)'}")
        print(f"\n== RECORD SEMANTICI UNIVOCI ==  (file letti: {letti})")
        for rec in sorted(record, key=lambda r: (r.classe, r.tipo, r.locazione)):
            superfici = ",".join(sorted(rec.superfici))
            colonne = (" colonne=" + ",".join(sorted(rec.colonne))) if rec.colonne else ""
            print(f"  [{rec.classe}/{rec.tipo}] {rec.locazione}")
            print(f"      identificativi={len(rec.identificativi)}"
                  f" superfici={superfici}{colonne}")

    vive = [r for r in record if r.classe == "viva"]
    archiviate = [r for r in record if r.classe == "archiviata"]
    file_rec = [r for r in record if r.tipo == "file"]
    righe_rec = [r for r in record if r.tipo == "riga_sqlite"]
    # A "fatto" is one identifier observed in one class: the same identifier
    # seen in a file and in a database row of the same class is one fact under
    # two representations, and is counted once here and twice above.
    fatti = {(r.classe, ident) for r in record for ident in r.identificativi}

    print("\n== ESITO ==")
    print(f"  record semantici univoci : {len(record)}"
          f"   (file: {len(file_rec)}, righe SQLite: {len(righe_rec)})")
    print(f"  di cui dipendenze VIVE   : {len(vive)}")
    print(f"  di cui copie ARCHIVIATE  : {len(archiviate)}")
    print(f"  fatti distinti (classe x identificativo): {len(fatti)}")
    if non_esaminati:
        print(f"\n  NON ESAMINATI: {len(non_esaminati)} — LA MISURA E' INCOMPLETA")
        for ne in non_esaminati[:20]:
            print(f"    {ne.oggetto}: {ne.motivo}")
        print("\n  ESITO: INCOMPLETO. Nessuna conclusione quantitativa e' lecita.")
        return EXIT_INCOMPLETO

    print("\n  copertura completa: ogni oggetto del perimetro e' stato esaminato.")
    if vive:
        print("  I5 (\"rifare l'insieme costa poco\") NON regge: esistono dipendenze vive.")
    else:
        print("  Nessuna dipendenza viva PER QUESTO PERIMETRO e questi identificativi.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

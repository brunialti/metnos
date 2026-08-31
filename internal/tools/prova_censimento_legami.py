"""Targeted tests for the C3 census.

Each test builds a fixture Birth root and perimeter, so nothing here touches the
real installation.  The five cases are exactly the ones the previous version got
wrong: more than five hits in one database, both surfaces of one digest inside a
single value, a database that must not be read twice, an unreadable object that
must make the census fail closed, and one identifier represented in two places.

Run:  python3 internal/tools/prova_censimento_legami.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import censimento_legami_nascita as C  # noqa: E402

CTX = "f" * 64          # prepared_admission_context_id
EPOCA = "e" * 64        # prepared_context_epoch
SET_ID = "5" * 64
INVENTARIO = "a" * 64
MATERIALE = "b" * 64
SET_JSON = "c" * 64
TRANSAZIONE = "d" * 32
PRODUTTORE = "9" * 64


def costruisci_radice(base: Path) -> Path:
    radice = base / "birth"
    insieme = radice / "authority-sets" / SET_ID
    (insieme / "context").mkdir(parents=True)
    (insieme / "producers" / f"p-{PRODUTTORE}").mkdir(parents=True)
    (radice / "prepared-v1.json").write_text(json.dumps({
        "set_id": SET_ID,
        "authority_set": f"authority-sets/{SET_ID}",
        "author_store_public_inventory_sha256": INVENTARIO,
        "context_material_sha256": MATERIALE,
        "set_json_sha256": SET_JSON,
        "transaction_id": TRANSAZIONE,
    }))
    (insieme / "context" / "material-v1.json").write_text(json.dumps({
        "prepared_admission_context_id": f"sha256:{CTX}",
        "prepared_context_epoch": f"sha256:{EPOCA}",
    }))
    return radice


def esegui(radice: Path, perimetro: list[Path]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = C.main(["--radice-nascita", str(radice),
                     "--perimetro", *[str(p) for p in perimetro]])
    return rc, buf.getvalue()


def db_con_righe(percorso: Path, n: int, valore) -> None:
    conn = sqlite3.connect(percorso)
    conn.execute("create table ricevute (id integer primary key, busta text)")
    for i in range(n):
        conn.execute("insert into ricevute (busta) values (?)",
                     (valore(i),))
    conn.commit()
    conn.close()


def estrai(testo: str, etichetta: str) -> int:
    """Read the number that follows a label, not the last number on the line."""
    for riga in testo.splitlines():
        if etichetta in riga:
            coda = riga.split(etichetta, 1)[1]
            m = re.search(r"(\d+)", coda)
            if m:
                return int(m.group(1))
    raise AssertionError(f"etichetta {etichetta!r} assente")


CASI = []


def caso(nome):
    def deco(fn):
        CASI.append((nome, fn))
        return fn
    return deco


@caso("piu' di cinque riscontri in un solo database: nessun troncamento a 5")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    # 9 righe, ognuna con due colonne che nominano il contesto: la vecchia
    # versione ne avrebbe contate 5.
    db_con_righe(dati / "ricevute.sqlite", 9, lambda i: f"busta sha256:{CTX} n{i}")
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0")
    if estrai(out, "record semantici univoci") != 9:
        errori.append(f"record != 9: {estrai(out, 'record semantici univoci')}")
    return errori


@caso("forma prefissata e nuda nello stesso valore: un solo record")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    # Lo stesso digest, due superfici, un solo fatto.
    (dati / "misto.json").write_text(
        json.dumps({"a": f"sha256:{CTX}", "b": CTX})
    )
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0")
    if estrai(out, "record semantici univoci") != 1:
        errori.append("le due superfici non sono state unificate")
    if "sha256:" not in out or "nuda" not in out:
        errori.append("le superfici osservate non sono riportate")
    return errori


@caso("database gia' interrogato: non viene riletto come testo")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    db_con_righe(dati / "unico.sqlite", 1, lambda i: f"sha256:{CTX}")
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0")
    if estrai(out, "record semantici univoci") != 1:
        errori.append("il database e' stato contato due volte")
    if estrai(out, "righe SQLite") != 1 or "file: 0" not in out:
        errori.append("il database compare anche come file")
    return errori


@caso("oggetto illeggibile: la misura si dichiara incompleta e esce 2")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    (dati / "buono.json").write_text(f"sha256:{CTX}")
    rotto = dati / "rotto.sqlite"
    rotto.write_bytes(b"questo non e' un database sqlite")
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_INCOMPLETO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_INCOMPLETO}")
    if "LA MISURA E' INCOMPLETA" not in out:
        errori.append("l'incompletezza non e' dichiarata")
    if "Nessuna conclusione quantitativa" not in out:
        errori.append("manca il divieto di concludere")
    # e l'errore non deve essere contato come riscontro
    if estrai(out, "record semantici univoci") != 1:
        errori.append("l'errore di apertura e' stato contato come legame")
    return errori


@caso("stesso identificativo in due rappresentazioni: 2 record, 1 fatto")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    (dati / "ricevuta.json").write_text(f"sha256:{CTX}")
    db_con_righe(dati / "stato.sqlite", 1, lambda i: CTX)
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0")
    if estrai(out, "record semantici univoci") != 2:
        errori.append("le due rappresentazioni non danno 2 record")
    if estrai(out, "fatti distinti") != 1:
        errori.append("le due rappresentazioni non danno 1 fatto")
    return errori


@caso("copia archiviata distinta dalla dipendenza viva")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    archivio = base / "birth.archiviata"
    archivio.mkdir()
    (archivio / "vecchio.json").write_text(f"sha256:{CTX}")
    viva = base / "stato"; viva.mkdir()
    (viva / "ricevuta.json").write_text(f"sha256:{CTX}")
    rc, out = esegui(radice, [base])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0")
    if estrai(out, "dipendenze VIVE") != 1:
        errori.append("la dipendenza viva non e' 1")
    if estrai(out, "copie ARCHIVIATE") != 1:
        errori.append("la copia archiviata non e' 1")
    return errori


@caso("digest dentro una sequenza esadecimale piu' lunga: nessun falso positivo")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    (dati / "piu_lungo.txt").write_text(CTX + "abc")   # 64 f seguiti da esadecimali
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0")
    if estrai(out, "record semantici univoci") != 0:
        errori.append("un digest dentro una sequenza piu' lunga e' stato accettato")
    return errori


@caso("tabella WITHOUT ROWID: censita, non dichiarata illeggibile")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    dati = base / "dati"; dati.mkdir()
    conn = sqlite3.connect(dati / "senza_rowid.sqlite")
    conn.execute("create table voci (chiave text primary key, busta text) without rowid")
    for i in range(3):
        conn.execute("insert into voci values (?, ?)", (f"k{i}", f"sha256:{CTX}"))
    conn.commit(); conn.close()
    rc, out = esegui(radice, [dati])
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa 0 (la tabella e' leggibile)")
    if estrai(out, "record semantici univoci") != 3:
        errori.append(f"record != 3: {estrai(out, 'record semantici univoci')}")
    if "ord" not in out:
        errori.append("l'identita' di riga usata non e' dichiarata")
    return errori


@caso("radice del perimetro assente: incompleto, non 'nessun legame'")
def _(base: Path) -> list[str]:
    radice = costruisci_radice(base)
    rc, out = esegui(radice, [base / "non-esiste"])
    errori = []
    if rc != C.EXIT_INCOMPLETO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_INCOMPLETO}")
    if "radice assente" not in out:
        errori.append("la radice assente non e' segnalata")
    return errori


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

"""Targeted tests for the epoch-dependency classifier.

The rule has three admitted futures and one refusal, and the refusal is the
part that matters: a dependency the rule cannot decide must block F4 rather
than be given a default.  Every case here is built on a fixture; nothing reads
the installation.

Run:  python3 internal/tools/prova_classifica_legami_epoca.py
"""
from __future__ import annotations

import base64
import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classifica_legami_epoca as C  # noqa: E402

CTX = "sha256:" + "f" * 64
SET_ID = "5" * 64


def radice_nascita(base: Path) -> Path:
    radice = base / "birth"
    insieme = radice / "authority-sets" / SET_ID
    (insieme / "context").mkdir(parents=True)
    (radice / "prepared-v1.json").write_text(json.dumps({
        "set_id": SET_ID, "authority_set": f"authority-sets/{SET_ID}",
    }))
    (insieme / "context" / "material-v1.json").write_text(json.dumps({
        "prepared_admission_context_id": CTX,
        "prepared_context_epoch": "sha256:" + "e" * 64,
    }))
    return radice


def pubblicazione(negozio: Path, contratto: str, generazioni: list[str],
                  corrente: str, ricevute: dict[str, str]) -> None:
    """One publication: its generations, its pointer, and its receipts."""
    cartella = negozio / f"pub-{contratto.replace('/', '_').replace(':', '_')}"
    (cartella / "generations").mkdir(parents=True)
    for g in generazioni:
        (cartella / "generations" / g).mkdir()
    (cartella / "current").write_text(f"sha256:{corrente}")
    (cartella / "binding.json").write_text(json.dumps({"contract_id": contratto}))
    (cartella / "admission-receipts").mkdir()
    for generazione, contesto in ricevute.items():
        (cartella / "admission-receipts" / f"{generazione}.json").write_text(
            json.dumps({
                "admission_context_id": contesto,
                "approved_lifecycle": "active",
                "contract_id": contratto,
                "generation_id": f"sha256:{generazione}",
            })
        )


def stato_nascita(base: Path, buste: list[dict | None]) -> Path:
    stato = base / "stato"
    stato.mkdir(exist_ok=True)
    conn = sqlite3.connect(stato / "producer_receipts.sqlite")
    conn.execute(
        "create table birth_producer_receipts "
        "(receipt_id text, state text, expires_at text, terminal_envelope blob)"
    )
    for busta in buste:
        grezzo = None if busta is None else json.dumps(busta).encode()
        conn.execute(
            "insert into birth_producer_receipts values (?,?,?,?)",
            ("r", "committed", "2026-08-30T12:17:49Z", grezzo),
        )
    conn.commit()
    conn.close()
    return stato


def busta(contratto: str, generazione: str, precedente: str,
          contesto: str = CTX) -> dict:
    ricevuta = {
        "admission_context_id": contesto, "contract_id": contratto,
        "generation_id": f"sha256:{generazione}",
    }
    return {
        "admission_receipt":
            base64.b64encode(json.dumps(ricevuta).encode()).decode(),
        "publication": {
            "contract_id": contratto,
            "current_generation_id": f"sha256:{generazione}",
            "previous_generation_id": f"sha256:{precedente}",
        },
    }


def esegui(radice: Path, negozio: Path, stato: Path,
           ritirati: list[str] | None = None) -> tuple[int, str]:
    buf = io.StringIO()
    argomenti = ["--radice-nascita", str(radice), "--negozio", str(negozio),
                 "--stato-nascita", str(stato)]
    if ritirati:
        argomenti += ["--ritirati", *ritirati]
    with redirect_stdout(buf):
        rc = C.main(argomenti)
    return rc, buf.getvalue()


def conta(testo: str, classe: str) -> int:
    for riga in testo.splitlines():
        if riga.strip().startswith(classe):
            return int(riga.split()[-1])
    raise AssertionError(f"classe {classe!r} assente dal riepilogo")


CASI = []


def caso(nome):
    def deco(fn):
        CASI.append((nome, fn))
        return fn
    return deco


@caso("zero dipendenze: nessun legame, uscita verde")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    stato = stato_nascita(base, [])
    rc, out = esegui(radice, negozio, stato)
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa {C.EXIT_OK}")
    if conta(out, C.STORICA) or conta(out, C.NUOVA) or conta(out, C.IGNOTA):
        errori.append("ha classificato legami inesistenti")
    return errori


@caso("una dipendenza su generazione superata: epoca storica, verde")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:uno", ["aaa", "bbb"], "bbb", {"aaa": CTX})
    stato = stato_nascita(base, [])
    rc, out = esegui(radice, negozio, stato)
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa {C.EXIT_OK}")
    if conta(out, C.STORICA) != 1:
        errori.append("la generazione superata non e' storica")
    return errori


@caso("una dipendenza sulla generazione CORRENTE: nuova epoca, richiede azione")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:due", ["aaa", "bbb"], "aaa", {"aaa": CTX})
    stato = stato_nascita(base, [])
    rc, out = esegui(radice, negozio, stato)
    errori = []
    if rc != C.EXIT_AZIONE:
        errori.append(f"uscita {rc}, attesa {C.EXIT_AZIONE}")
    if conta(out, C.NUOVA) != 1:
        errori.append("la generazione corrente non richiede la nuova epoca")
    if conta(out, C.STORICA) != 0:
        errori.append("una generazione corrente e' stata dichiarata storica")
    return errori


@caso("contratto in ritiro: cessa di essere corrente, non viene riscritto")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:tre", ["aaa"], "aaa", {"aaa": CTX})
    stato = stato_nascita(base, [])
    rc, out = esegui(radice, negozio, stato, ritirati=["builtin:tre"])
    errori = []
    if rc != C.EXIT_AZIONE:
        errori.append(f"uscita {rc}, attesa {C.EXIT_AZIONE}")
    if conta(out, C.CESSA) != 1:
        errori.append("il contratto in ritiro non e' stato riconosciuto")
    if conta(out, C.NUOVA) != 0:
        errori.append("un contratto in ritiro chiede la nuova epoca")
    return errori


@caso("molte dipendenze miste: ciascuna con la sua classe")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:a", ["a1", "a2"], "a2", {"a1": CTX})
    pubblicazione(negozio, "builtin:b", ["b1", "b2"], "b1", {"b1": CTX})
    pubblicazione(negozio, "builtin:c", ["c1"], "c1", {"c1": CTX})
    stato = stato_nascita(base, [busta("builtin:a", "a1", "a2")])
    rc, out = esegui(radice, negozio, stato, ritirati=["builtin:c"])
    errori = []
    if rc != C.EXIT_AZIONE:
        errori.append(f"uscita {rc}, attesa {C.EXIT_AZIONE}")
    for classe, atteso in ((C.STORICA, 2), (C.NUOVA, 1), (C.CESSA, 1)):
        if conta(out, classe) != atteso:
            errori.append(f"{classe}: {conta(out, classe)}, atteso {atteso}")
    return errori


@caso("ricevuta produttore senza gemello: NON classificata, F4 bloccata")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    # la busta nomina una generazione che nessuna pubblicazione conosce
    stato = stato_nascita(base, [busta("builtin:ignoto", "zzz", "yyy")])
    rc, out = esegui(radice, negozio, stato)
    errori = []
    if rc != C.EXIT_NON_CLASSIFICATO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_NON_CLASSIFICATO}")
    if conta(out, C.IGNOTA) != 1:
        errori.append("il legame senza gemello e' stato classificato lo stesso")
    if "F4 non puo' essere dichiarata" not in out:
        errori.append("il blocco di F4 non e' dichiarato")
    return errori


@caso("busta illeggibile: NON classificata invece di essere ignorata")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    stato = base / "stato"; stato.mkdir()
    conn = sqlite3.connect(stato / "producer_receipts.sqlite")
    conn.execute("create table birth_producer_receipts "
                 "(receipt_id text, state text, expires_at text, terminal_envelope blob)")
    conn.execute("insert into birth_producer_receipts values (?,?,?,?)",
                 ("r", "committed", "x", CTX.encode() + b" non e' json"))
    conn.commit(); conn.close()
    rc, out = esegui(radice, negozio, stato)
    errori = []
    if rc != C.EXIT_NON_CLASSIFICATO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_NON_CLASSIFICATO}")
    if conta(out, C.IGNOTA) != 1:
        errori.append("una busta illeggibile non blocca")
    return errori


@caso("contesto diverso: la dipendenza non appartiene a questa epoca")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    altro = "sha256:" + "b" * 64
    pubblicazione(negozio, "builtin:altro", ["aaa"], "aaa", {"aaa": altro})
    stato = stato_nascita(base, [])
    rc, out = esegui(radice, negozio, stato)
    errori = []
    if rc != C.EXIT_OK:
        errori.append(f"uscita {rc}, attesa {C.EXIT_OK}")
    if "legami esaminati : 0" not in out:
        errori.append("ha raccolto una dipendenza di un altro contesto")
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

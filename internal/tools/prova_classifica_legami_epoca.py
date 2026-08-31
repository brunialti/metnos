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
           autenticato: dict | None = None) -> tuple[int, str]:
    """Run the rule with an injected authenticated state.

    The retirement class is no longer reachable from the command line: it is
    decided by the contract's own tombstone, so a fixture declares it here the
    way the store would, and never as a caller argument.
    """
    contesto, legami, rifiuti, contratti = C.classifica(
        radice, negozio, stato, Path("/non-usato"), stato=autenticato or {},
    )
    conteggio = {}
    for l in legami:
        conteggio[l.classe] = conteggio.get(l.classe, 0) + 1
    righe = [f"legami esaminati  : {len(legami)}",
             f"rifiuti terminali validi (non sono legami): {len(rifiuti)}"]
    for l in legami:
        righe.append(f"[{l.classe}] {l.tipo} {l.locazione} perche': {l.motivo}")
    righe.append("== CLASSIFICAZIONE ==")
    for classe in (C.STORICA, C.NUOVA, C.CESSA, C.IGNOTA):
        righe.append(f"  {classe:26} {conteggio.get(classe, 0)}")
    if conteggio.get(C.IGNOTA):
        rc = C.EXIT_NON_CLASSIFICATO
        righe.append("F4 non puo' essere dichiarata")
    elif conteggio.get(C.NUOVA) or conteggio.get(C.CESSA):
        rc = C.EXIT_AZIONE
    else:
        rc = C.EXIT_OK
    return rc, "\n".join(righe)


def autenticato(**contratti) -> dict:
    """Fixture for what the store would authenticate: id -> (classe, valore)."""
    return {k.replace("__", ":"): v for k, v in contratti.items()}


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


@caso("zero dipendenze: nessun legame, verde")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    rc, out = esegui(radice, negozio, stato_nascita(base, []), {})
    return [] if rc == C.EXIT_OK and conta(out, C.STORICA) == 0 else [f"uscita {rc}"]


@caso("generazione superata: storica, verde")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:uno", ["aaa", "bbb"], "bbb", {"aaa": CTX})
    rc, out = esegui(radice, negozio, stato_nascita(base, []),
                     {"builtin:uno": ("corrente", "bbb")})
    errori = []
    if rc != C.EXIT_OK: errori.append(f"uscita {rc}, attesa 0")
    if conta(out, C.STORICA) != 1: errori.append("non e' storica")
    return errori


@caso("generazione CORRENTE: nuova epoca, richiede azione")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:due", ["aaa", "bbb"], "aaa", {"aaa": CTX})
    rc, out = esegui(radice, negozio, stato_nascita(base, []),
                     {"builtin:due": ("corrente", "aaa")})
    errori = []
    if rc != C.EXIT_AZIONE: errori.append(f"uscita {rc}, attesa {C.EXIT_AZIONE}")
    if conta(out, C.NUOVA) != 1: errori.append("non richiede la nuova epoca")
    return errori


@caso("ritiro AUTENTICATO: cessa; il chiamante non puo' dichiararlo")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:tre", ["aaa"], "aaa", {"aaa": CTX})
    rc, out = esegui(radice, negozio, stato_nascita(base, []),
                     {"builtin:tre": ("ritiro", "aaa")})
    errori = []
    if rc != C.EXIT_AZIONE: errori.append(f"uscita {rc}, attesa {C.EXIT_AZIONE}")
    if conta(out, C.CESSA) != 1: errori.append("il ritiro non e' stato riconosciuto")
    # e non esiste piu' un modo per dichiararlo da riga di comando
    import argparse, io as _io, contextlib
    ap_err = _io.StringIO()
    with contextlib.redirect_stderr(ap_err):
        try:
            C.main(["--ritirati", "builtin:tre"])
            errori.append("--ritirati e' ancora accettato")
        except SystemExit:
            pass
    return errori


@caso("stesso digest, due contratti: il gemello NON viene condiviso")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    # due contratti con la STESSA generazione di prova
    pubblicazione(negozio, "builtin:a", ["gg"], "gg", {"gg": CTX})
    pubblicazione(negozio, "builtin:b", ["gg", "hh"], "hh", {"gg": CTX})
    # la busta nomina il contratto b: deve accoppiarsi solo con quello
    rc, out = esegui(radice, negozio, stato_nascita(base, [busta("builtin:b", "gg", "hh")]),
                     {"builtin:a": ("corrente", "gg"), "builtin:b": ("corrente", "hh")})
    errori = []
    if rc != C.EXIT_AZIONE:
        errori.append(f"uscita {rc}, attesa {C.EXIT_AZIONE}")
    # a/gg e' corrente -> nuova epoca; b/gg e' superata -> storica; la busta segue b
    if conta(out, C.NUOVA) != 1:
        errori.append("il contratto a non e' stato riconosciuto corrente")
    if conta(out, C.STORICA) != 2:
        errori.append("la busta non ha seguito il gemello del proprio contratto")
    return errori


@caso("generazione discorde fra percorso e documento: non classificato")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:d", ["aaa", "bbb"], "bbb", {"aaa": CTX})
    # riscrive il documento con una generazione diversa da quella del nome
    ric = negozio / "pub-builtin_d" / "admission-receipts" / "aaa.json"
    doc = json.loads(ric.read_text()); doc["generation_id"] = "sha256:zzz"
    ric.write_text(json.dumps(doc))
    rc, out = esegui(radice, negozio, stato_nascita(base, []),
                     {"builtin:d": ("corrente", "bbb")})
    errori = []
    if rc != C.EXIT_NON_CLASSIFICATO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_NON_CLASSIFICATO}")
    if "generazione: percorso" not in out:
        errori.append("la discordanza non e' nominata")
    return errori


@caso("stato corrente non autenticabile: non classificato, blocco")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:e", ["aaa"], "aaa", {"aaa": CTX})
    rc, out = esegui(radice, negozio, stato_nascita(base, []),
                     {"builtin:e": ("errore", "code_digest_mismatch")})
    errori = []
    if rc != C.EXIT_NON_CLASSIFICATO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_NON_CLASSIFICATO}")
    if "non autenticabile" not in out:
        errori.append("il difetto di autenticazione non e' nominato")
    return errori


@caso("rifiuto terminale valido: non e' un legame")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    stato = base / "stato"; stato.mkdir()
    conn = sqlite3.connect(stato / "producer_receipts.sqlite")
    conn.execute("create table birth_producer_receipts (receipt_id text, state text, "
                 "expires_at text, rejection_code text, terminal_envelope blob)")
    conn.execute("insert into birth_producer_receipts values (?,?,?,?,?)",
                 ("r", "rejected", "x", "property_runner_unavailable",
                  json.dumps({"admission_receipt": None,
                              "error_code": "property_runner_unavailable"}).encode()))
    conn.commit(); conn.close()
    rc, out = esegui(radice, negozio, stato, {})
    errori = []
    if rc != C.EXIT_OK: errori.append(f"uscita {rc}, attesa 0")
    if "rifiuti terminali validi (non sono legami): 1" not in out:
        errori.append("il rifiuto valido non e' stato escluso come tale")
    if conta(out, C.IGNOTA) != 0:
        errori.append("un rifiuto valido e' stato chiamato illeggibile")
    return errori


@caso("conclusione invalida SENZA testo del contesto: blocca comunque")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    stato = base / "stato"; stato.mkdir()
    conn = sqlite3.connect(stato / "producer_receipts.sqlite")
    conn.execute("create table birth_producer_receipts (receipt_id text, state text, "
                 "expires_at text, rejection_code text, terminal_envelope blob)")
    # busta committed che non e' JSON e non nomina il contesto in chiaro:
    # la versione precedente la lasciava passare in silenzio.
    conn.execute("insert into birth_producer_receipts values (?,?,?,?,?)",
                 ("r", "committed", "x", None, b"non e' json, e non nomina nulla"))
    conn.commit(); conn.close()
    rc, out = esegui(radice, negozio, stato, {})
    errori = []
    if rc != C.EXIT_NON_CLASSIFICATO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_NON_CLASSIFICATO}")
    if "non interpretabile" not in out:
        errori.append("la busta illeggibile non e' nominata")
    return errori


@caso("rifiuto terminale senza codice: incoerente, blocca")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    stato = base / "stato"; stato.mkdir()
    conn = sqlite3.connect(stato / "producer_receipts.sqlite")
    conn.execute("create table birth_producer_receipts (receipt_id text, state text, "
                 "expires_at text, rejection_code text, terminal_envelope blob)")
    conn.execute("insert into birth_producer_receipts values (?,?,?,?,?)",
                 ("r", "rejected", "x", None,
                  json.dumps({"admission_receipt": None}).encode()))
    conn.commit(); conn.close()
    rc, out = esegui(radice, negozio, stato, {})
    errori = []
    if rc != C.EXIT_NON_CLASSIFICATO:
        errori.append(f"uscita {rc}, attesa {C.EXIT_NON_CLASSIFICATO}")
    if "senza codice di rifiuto" not in out:
        errori.append("il rifiuto senza ragione non e' nominato")
    return errori


@caso("contesto diverso: la dipendenza non appartiene a questa epoca")
def _(base: Path) -> list[str]:
    radice = radice_nascita(base)
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "builtin:altro", ["aaa"], "aaa",
                  {"aaa": "sha256:" + "b" * 64})
    rc, out = esegui(radice, negozio, stato_nascita(base, []),
                     {"builtin:altro": ("corrente", "aaa")})
    return [] if rc == C.EXIT_OK and "legami esaminati  : 0" in out else [f"uscita {rc}"]


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

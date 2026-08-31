"""Targeted tests for the epoch-dependency classifier.

The rule has three admitted futures and one refusal, and the refusal is what
these tests are about: a dependency whose authority cannot be established must
block rather than receive a default.  After the third review the classifier no
longer reads authority out of JSON, so the fixtures here are **signed** with
keys the fake authority holds, and each negative case breaks exactly one of
those signatures.

Nothing reads the installation: every case builds its own store, its own
producer database and its own keyring.

Run:  python3 internal/tools/prova_classifica_legami_epoca.py
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)
import classifica_legami_epoca as C  # noqa: E402
from executor_birth_receipts import (  # noqa: E402
    AdmissionCheck, AdmissionKind, AdmittedCheckStatus, ApprovedLifecycle,
    IssuerKey, IssuerRegistry, RevisionClass, issue_admission_receipt,
    issue_producer_receipt,
)
from executor_birth_identity import ExecutorOrigin, RevisionAuthor  # noqa: E402
from manifest_inventory import ContractId, ManifestOrigin  # noqa: E402
from contract_store import encode_binding  # noqa: E402

CTX = "sha256:" + "f" * 64
ISTANTE = "2026-08-30T12:00:00Z"
SCADENZA = "2026-08-30T13:00:00Z"
EMITTENTE = "p-" + "1" * 64


def gen(marchio: str) -> str:
    """A generation digest of the real length, readable in a test."""
    return (marchio * 64)[:64]


def chiave() -> tuple[str, Ed25519PrivateKey]:
    privata = Ed25519PrivateKey.generate()
    identificativo = "birth-ed25519-v1-sha256-" + "c" * 64
    return identificativo, privata


def autorita_finta(contesto: str = CTX) -> tuple[C.Autorita, dict]:
    """A fake authority holding one admission key and one producer key."""
    id_amm, priv_amm = chiave()
    id_pro, priv_pro = chiave()
    id_pro = "birth-ed25519-v1-sha256-" + "d" * 64
    registro = IssuerRegistry({EMITTENTE: (
        IssuerKey(id_pro, priv_pro.public_key(),
                  frozenset(ExecutorOrigin), frozenset({RevisionAuthor.HUMAN})),
    )})
    aut = C.Autorita(
        contesto=contesto,
        chiavi_ammissione={id_amm: priv_amm.public_key()},
        registro_produttori=registro,
    )
    return aut, {"id_amm": id_amm, "priv_amm": priv_amm,
                 "id_pro": id_pro, "priv_pro": priv_pro}


def ammissione(chiavi: dict, *, contratto: str, generazione: str,
               contesto: str = CTX, byte_produttore: bytes | None = None,
               richiesta: str = "sha256:" + "3" * 64) -> bytes:
    """A signed admission receipt whose chain is coherent by construction.

    ``producer_receipt_hash`` is the real hash of the producer bytes the row
    will carry, and ``birth_request_id`` is the request the envelope and the
    row will name: a fixture with placeholder values would be refused by the
    chain check, and a positive test has to exercise the productive form.
    """
    from executor_birth_producer_store import producer_receipt_hash
    hash_produttore = ("sha256:" + "5" * 64 if byte_produttore is None
                       else producer_receipt_hash(byte_produttore))
    controllo = AdmissionCheck("v1", AdmittedCheckStatus.PASSED, "sha256:" + "0" * 64)
    return issue_admission_receipt(
        policy_version="v1", contract_id=identita(contratto),
        generation_id=f"sha256:{generazione}", candidate_id="sha256:" + "1" * 64,
        semantic_core_id="sha256:" + "2" * 64, admission_context_id=contesto,
        birth_request_id=richiesta,
        authoring_journal_hash="sha256:" + "4" * 64, predecessor_id=None,
        producer_receipt_hash=hash_produttore,
        revision_class=RevisionClass.CODE_REVISION,
        check_results={"manifest_lint": controllo},
        semantic_review_hash=None, approval_hash=None,
        approved_lifecycle=ApprovedLifecycle.ACTIVE, kind=AdmissionKind.ADMISSION,
        issued_at=ISTANTE, key_id=chiavi["id_amm"],
        private_key=chiavi["priv_amm"],
    )


def produttore(chiavi: dict) -> bytes:
    return issue_producer_receipt(
        issuer_id=EMITTENTE, executor_origin=ExecutorOrigin.BUILTIN,
        revision_authorship=RevisionAuthor.HUMAN,
        objective_hash="sha256:" + "6" * 64,
        candidate_source_id="sha256:" + "7" * 64,
        issued_at=ISTANTE, expires_at=SCADENZA, nonce="a" * 32,
        key_id=chiavi["id_pro"], private_key=chiavi["priv_pro"],
    )


def identita(nome: str) -> ContractId:
    """A real ContractId: the store addresses directories by its storage key."""
    return ContractId(ManifestOrigin.BUILTIN, f"{nome}/manifest.toml")


def pubblicazione(negozio: Path, contratto: str, generazioni: list[str],
                  corrente: str, ricevute: dict[str, bytes]) -> Path:
    identificativo = identita(contratto)
    cartella = negozio / identificativo.storage_key
    (cartella / "generations").mkdir(parents=True)
    for g in generazioni:
        (cartella / "generations" / g).mkdir()
    (cartella / "current").write_text(f"sha256:{corrente}")
    # The productive encoder decides the canonical bytes; guessing them here
    # would be a second implementation of the store's own format.
    (cartella / "binding.json").write_bytes(encode_binding(identificativo))
    (cartella / "admission-receipts").mkdir()
    for generazione, byte in ricevute.items():
        (cartella / "admission-receipts" / f"{generazione}.json").write_bytes(byte)
    return cartella


def riga(stato_dir: Path, righe: list[dict]) -> Path:
    stato_dir.mkdir(exist_ok=True)
    conn = sqlite3.connect(stato_dir / "producer_receipts.sqlite")
    # The productive columns, not a reduced parallel schema: a fixture missing
    # the mandatory fields would stay green precisely because the check has
    # nothing to compare.
    conn.execute(
        "create table birth_producer_receipts (receipt_id text, "
        "receipt_hash text, request_id text, state text, registered_at text, "
        "rejection_code text, issuer_id text, objective_hash text, "
        "candidate_source_id text, executor_origin text, "
        "revision_authorship text, expires_at text, result_binding text, "
        "encoded blob, terminal_envelope blob, terminal_auth blob)"
    )
    for r in righe:
        conn.execute(
            "insert into birth_producer_receipts values "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.get("receipt_id"), r.get("receipt_hash"), r.get("request_id"),
             r.get("state"), r.get("registered_at", ISTANTE),
             r.get("rejection_code"), r.get("issuer_id"),
             r.get("objective_hash"), r.get("candidate_source_id"),
             r.get("executor_origin"), r.get("revision_authorship"),
             r.get("expires_at"), r.get("result_binding"),
             r.get("encoded"), r.get("terminal_envelope"),
             r.get("terminal_auth")))
    conn.commit(); conn.close()
    return stato_dir


def riga_coerente(chiavi: dict, *, byte_produttore: bytes, encoded: bytes,
                  firma: bytes, richiesta: str, stato: str = "committed") -> dict:
    """A durable row whose mandatory columns agree with what is signed."""
    from executor_birth_producer_store import producer_receipt_hash
    from executor_birth_operational import _terminal_binding
    return {
        "state": stato, "request_id": richiesta,
        "receipt_hash": producer_receipt_hash(byte_produttore),
        "issuer_id": EMITTENTE, "objective_hash": "sha256:" + "6" * 64,
        "candidate_source_id": "sha256:" + "7" * 64,
        "executor_origin": "builtin", "revision_authorship": "human",
        "expires_at": SCADENZA, "result_binding": _terminal_binding(encoded),
        "encoded": byte_produttore, "terminal_envelope": encoded,
        "terminal_auth": firma,
    }


def busta(chiavi: dict, *, contratto: str, generazione: str,
          precedente: str, contesto: str = CTX,
          richiesta: str = "sha256:" + "3" * 64,
          byte_produttore: bytes | None = None,
          ammissione_byte: bytes | None = None) -> tuple[bytes, bytes]:
    byte = ammissione_byte if ammissione_byte is not None else ammissione(
        chiavi, contratto=contratto, generazione=generazione, contesto=contesto,
        byte_produttore=byte_produttore, richiesta=richiesta,
    )
    interno = {
        "schema_version": 1, "request_id": richiesta,
        "signing_key_id": chiavi["id_amm"],
        "admission_receipt": base64.b64encode(byte).decode(),
        "publication": {"contract_id": identita(contratto).value,
                        "current_generation_id": f"sha256:{generazione}",
                        "previous_generation_id": f"sha256:{precedente}"},
    }
    encoded = json.dumps(interno).encode()
    firma = chiavi["priv_amm"].sign(C.DOMINIO_TERMINALE + encoded)
    return encoded, firma


def stato_di(*coppie) -> dict:
    """What the store would authenticate: contract value -> (kind, value)."""
    return {identita(nome).value: (genere, valore)
            for nome, genere, valore in coppie}


def esegui(negozio: Path, stato_dir: Path, aut: C.Autorita,
           stato: dict | None = None):
    contesto, legami, rifiuti, bloccanti, fuori = C.classifica(
        RADICE, RADICE, negozio, stato_dir, autorita=aut, stato_finto=stato or {},
    )
    conteggio: dict[str, int] = {}
    for l in legami:
        conteggio[l.classe] = conteggio.get(l.classe, 0) + 1
    return {"legami": legami, "rifiuti": rifiuti, "bloccanti": bloccanti,
            "fuori": fuori, "conteggio": conteggio,
            "motivi": " | ".join(l.motivo for l in legami)}


CASI = []


def caso(nome):
    def deco(fn):
        CASI.append((nome, fn))
        return fn
    return deco


@caso("nascita firmata su generazione superata: storica")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "uno", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="uno", generazione=gen("a"))})
    STATO = stato_di(("uno", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if e["conteggio"].get(C.STORICA) != 1:
        errori.append(f"storiche: {e['conteggio']}")
    if e["conteggio"].get(C.IGNOTA):
        errori.append(f"ignote: {e['motivi']}")
    return errori


@caso("firma AdmissionReceipt errata: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    byte = bytearray(ammissione(k, contratto="due", generazione=gen("a")))
    byte[-1] ^= 0xFF                       # una firma sola, guastata
    pubblicazione(negozio, "due", [gen("a"), gen("b")], gen("b"), {gen("a"): bytes(byte)})
    STATO = stato_di(("due", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append("una firma guasta non ha bloccato")
    if "non e' verificabile" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("contesto alterato: la ricevuta non e' piu' di questa epoca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    altro = "sha256:" + "b" * 64
    pubblicazione(negozio, "tre", [gen("a")], gen("a"),
                  {gen("a"): ammissione(k, contratto="tre",
                                     generazione=gen("a"), contesto=altro)})
    STATO = stato_di(("tre", "corrente", gen("a")))
    e = esegui(negozio, base / "stato", aut, STATO)
    return [] if not e["legami"] else [f"raccolta una ricevuta di un altro contesto: {e['motivi']}"]


@caso("generazione discorde fra percorso e ricevuta firmata: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    # la ricevuta firma 'bbb' ma viene riposta sotto il nome 'aaa'
    pubblicazione(negozio, "qua", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="qua", generazione=gen("b"))})
    STATO = stato_di(("qua", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append("la discordanza non ha bloccato")
    if "generazione: percorso" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("firma Producer errata: blocca se la riga pretende questo contesto")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "cin", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="cin", generazione=gen("a"))})
    buoni = produttore(k)
    prod = bytearray(buoni); prod[-1] ^= 0xFF     # una firma sola, guastata
    encoded, firma = busta(k, contratto="cin", generazione=gen("a"),
                           precedente=gen("b"), byte_produttore=buoni)
    r = riga_coerente(k, byte_produttore=buoni, encoded=encoded, firma=firma,
                      richiesta="sha256:" + "3" * 64)
    r["encoded"] = bytes(prod)          # solo la firma Producer e' guastata
    stato = riga(base / "stato", [r])
    STATO = stato_di(("cin", "corrente", gen("b")))
    e = esegui(negozio, stato, aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"la firma produttore guasta non ha bloccato: {e['conteggio']}")
    if "ricevuta produttore non e' verificabile" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("richiesta diversa fra riga e busta firmata: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "sei", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="sei", generazione=gen("a"))})
    buoni = produttore(k)
    encoded, firma = busta(k, contratto="sei", generazione=gen("a"),
                           precedente=gen("b"), richiesta="sha256:" + "a" * 64,
                           byte_produttore=buoni)
    stato = riga(base / "stato", [riga_coerente(
        k, byte_produttore=buoni, encoded=encoded, firma=firma,
        richiesta="sha256:" + "b" * 64)])   # la riga nomina un'altra richiesta
    STATO = stato_di(("sei", "corrente", gen("b")))
    e = esegui(negozio, stato, aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"la richiesta discorde non ha bloccato: {e['conteggio']}")
    if "richiesta discorde" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("terminal_auth errata: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "set", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="set", generazione=gen("a"))})
    buoni = produttore(k)
    encoded, firma = busta(k, contratto="set", generazione=gen("a"),
                           precedente=gen("b"), byte_produttore=buoni)
    guasta = bytearray(firma); guasta[-1] ^= 0xFF
    stato = riga(base / "stato", [riga_coerente(
        k, byte_produttore=buoni, encoded=encoded, firma=bytes(guasta),
        richiesta="sha256:" + "3" * 64)])
    STATO = stato_di(("set", "corrente", gen("b")))
    e = esegui(negozio, stato, aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"la firma della busta guasta non ha bloccato: {e['conteggio']}")
    if "firma della busta terminale non valida" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("busta firmata con identita' diversa dal gemello: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "ott", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="ott", generazione=gen("a"))})
    # la busta porta un contratto che nel negozio non ha quella ricevuta
    buoni = produttore(k)
    encoded, firma = busta(k, contratto="estraneo", generazione=gen("e"),
                           precedente=gen("f"), byte_produttore=buoni)
    stato = riga(base / "stato", [riga_coerente(
        k, byte_produttore=buoni, encoded=encoded, firma=firma,
        richiesta="sha256:" + "3" * 64)])
    STATO = stato_di(("ott", "corrente", gen("b")))
    e = esegui(negozio, stato, aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"l'identita' estranea non ha bloccato: {e['conteggio']}")
    if "nessun gemello verificato" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("directory inattesa SENZA ricevute del contesto: blocca lo stesso")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "nov", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="nov", generazione=gen("a"))})
    # una pubblicazione interrotta: nessun binding, nessuna ricevuta
    orfana = negozio / "orfana"; (orfana / "generations").mkdir(parents=True)
    STATO = stato_di(("nov", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if not e["bloccanti"]:
        errori.append("una directory che l'inventario non possiede non blocca")
    if e["conteggio"].get(C.STORICA) != 1:
        errori.append("il legame legittimo e' andato perso")
    return errori


@caso("generazione corrente: nuova epoca, non storica")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "die", [gen("a"), gen("b")], gen("a"),
                  {gen("a"): ammissione(k, contratto="die", generazione=gen("a"))})
    STATO = stato_di(("die", "corrente", gen("a")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if e["conteggio"].get(C.NUOVA) != 1:
        errori.append(f"la generazione corrente non chiede la nuova epoca: {e['conteggio']}")
    return errori


@caso("percorso V2: una ricevuta filed sotto il contesto viene vista")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    cartella = pubblicazione(negozio, "vdue", [gen("a"), gen("b")], gen("b"), {})
    # la stessa forma dichiarata dal protocollo:
    # admission-receipts-v2/<generazione>/<contesto>.json
    v2 = cartella / "admission-receipts-v2" / gen("a")
    v2.mkdir(parents=True)
    (v2 / f"{CTX.removeprefix('sha256:')}.json").write_bytes(
        ammissione(k, contratto="vdue", generazione=gen("a"))
    )
    STATO = stato_di(("vdue", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if e["conteggio"].get(C.STORICA) != 1:
        errori.append(f"la ricevuta V2 non e' stata vista: {e['conteggio']}")
    return errori


@caso("percorso V2 con contesto discorde dalla ricevuta firmata: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    cartella = pubblicazione(negozio, "vtre", [gen("a"), gen("b")], gen("b"), {})
    # riposta sotto un contesto diverso da quello che la ricevuta firma
    v2 = cartella / "admission-receipts-v2" / gen("a")
    v2.mkdir(parents=True)
    (v2 / f"{'b' * 64}.json").write_bytes(
        ammissione(k, contratto="vtre", generazione=gen("a"))
    )
    STATO = stato_di(("vtre", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"il contesto discorde nel percorso non ha bloccato: {e['conteggio']}")
    if "contesto: percorso" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("V1 e V2 su contesti DIVERSI: due atti distinti, non fusi")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    cartella = pubblicazione(negozio, "vqua", [gen("a"), gen("b")], gen("b"),
                             {gen("a"): ammissione(k, contratto="vqua",
                                                   generazione=gen("a"))})
    # il V2 appartiene a un contesto diverso: e' un atto distinto, e questo
    # censimento, legato al proprio insieme, non lo raccoglie
    altro = "sha256:" + "c" * 64
    v2 = cartella / "admission-receipts-v2" / gen("a")
    v2.mkdir(parents=True)
    (v2 / f"{altro.removeprefix('sha256:')}.json").write_bytes(
        ammissione(k, contratto="vqua", generazione=gen("a"), contesto=altro)
    )
    STATO = stato_di(("vqua", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    # solo la V1 di questo contesto e' un legame nostro; la V2 di un altro
    # contesto e' un atto distinto e resta fuori
    if e["conteggio"].get(C.STORICA) != 1:
        errori.append(f"classi inattese: {e['conteggio']}")
    if e["conteggio"].get(C.IGNOTA):
        errori.append(f"ha bloccato invece di distinguere: {e['motivi']}")
    return errori


@caso("seconda esecuzione identica: stesso esito, nessuna deriva")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "idem", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="idem",
                                        generazione=gen("a"))})
    STATO = stato_di(("idem", "corrente", gen("b")))
    primo = esegui(negozio, base / "stato", aut, STATO)
    secondo = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    if primo["conteggio"] != secondo["conteggio"]:
        errori.append(f"esiti diversi: {primo['conteggio']} vs {secondo['conteggio']}")
    if [l.locazione for l in primo["legami"]] != [l.locazione for l in secondo["legami"]]:
        errori.append("l'ordine o l'insieme delle locazioni e' cambiato")
    return errori


@caso("molte dipendenze correnti: ciascuna chiede la nuova epoca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    coppie = []
    for nome in ("mua", "mub", "muc"):
        pubblicazione(negozio, nome, [gen("a")], gen("a"),
                      {gen("a"): ammissione(k, contratto=nome,
                                            generazione=gen("a"))})
        coppie.append((nome, "corrente", gen("a")))
    e = esegui(negozio, base / "stato", aut, stato_di(*coppie))
    errori = []
    if e["conteggio"].get(C.NUOVA) != 3:
        errori.append(f"le tre correnti non chiedono la nuova epoca: {e['conteggio']}")
    return errori


@caso("byte Admission diversi fra negozio e busta: blocca")
def _(base: Path) -> list[str]:
    from executor_birth_receipts import issue_producer_receipt
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    def prod(nonce: str) -> bytes:
        return issue_producer_receipt(
            issuer_id=EMITTENTE, executor_origin=ExecutorOrigin.BUILTIN,
            revision_authorship=RevisionAuthor.HUMAN,
            objective_hash="sha256:" + "6" * 64,
            candidate_source_id="sha256:" + "7" * 64,
            issued_at=ISTANTE, expires_at=SCADENZA, nonce=nonce,
            key_id=k["id_pro"], private_key=k["priv_pro"])
    # due produttori DAVVERO diversi: il costruttore e' deterministico, e con
    # lo stesso nonce le due ricevute sarebbero byte identiche
    X, Y = prod("a" * 32), prod("b" * 32)
    ric = "sha256:" + "3" * 64
    nel_negozio = ammissione(k, contratto="bad", generazione=gen("a"),
                             byte_produttore=X, richiesta=ric)
    altra = ammissione(k, contratto="bad", generazione=gen("a"),
                       byte_produttore=Y, richiesta=ric)
    pubblicazione(negozio, "bad", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): nel_negozio})
    encoded, firma = busta(k, contratto="bad", generazione=gen("a"),
                           precedente=gen("b"), richiesta=ric,
                           ammissione_byte=altra)
    stato = riga(base / "stato", [riga_coerente(
        k, byte_produttore=X, encoded=encoded, firma=firma, richiesta=ric)])
    e = esegui(negozio, stato, aut, stato_di(("bad", "corrente", gen("b"))))
    return ([] if e["conteggio"].get(C.IGNOTA) == 1
            else [f"i byte diversi non hanno bloccato: {e['conteggio']}"])


@caso("codici di rifiuto diversi fra riga e busta: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    ric = "sha256:" + "3" * 64
    interno = {"schema_version": 1, "request_id": ric,
               "signing_key_id": k["id_amm"], "admission_receipt": None,
               "error_code": "codice_busta"}
    encoded = json.dumps(interno).encode()
    firma = k["priv_amm"].sign(C.DOMINIO_TERMINALE + encoded)
    buoni = produttore(k)
    r = riga_coerente(k, byte_produttore=buoni, encoded=encoded, firma=firma,
                      richiesta=ric, stato="rejected")
    r["rejection_code"] = "codice_riga"      # discorde da quello firmato
    r["result_binding"] = None
    e = esegui(negozio, riga(base / "stato", [r]), aut, {})
    return ([] if e["conteggio"].get(C.IGNOTA) == 1
            else [f"due codici diversi accettati come un rifiuto: {e['conteggio']}"])


@caso("rifiuto terminale coerente: e' un rifiuto, non un ignoto")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    ric = "sha256:" + "3" * 64
    interno = {"schema_version": 1, "request_id": ric,
               "signing_key_id": k["id_amm"], "admission_receipt": None,
               "error_code": "property_runner_unavailable"}
    encoded = json.dumps(interno).encode()
    firma = k["priv_amm"].sign(C.DOMINIO_TERMINALE + encoded)
    r = riga_coerente(k, byte_produttore=produttore(k), encoded=encoded,
                      firma=firma, richiesta=ric, stato="rejected")
    r["rejection_code"] = "property_runner_unavailable"
    r["result_binding"] = None               # come impone il Producer store
    e = esegui(negozio, riga(base / "stato", [r]), aut, {})
    errori = []
    if e["conteggio"].get(C.IGNOTA):
        errori.append(f"un rifiuto coerente e' stato dichiarato ignoto: {e['motivi']}")
    if len(e["rifiuti"]) != 1:
        errori.append(f"non e' stato contato come rifiuto: {len(e['rifiuti'])}")
    return errori


@caso("ricevuta raggiunta per collegamento: BLOCCA, non sparisce")
def _(base: Path) -> list[str]:
    import os
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    cartella = pubblicazione(negozio, "lnk", [gen("a"), gen("b")], gen("b"), {})
    fuori = base / "fuori.json"
    fuori.write_bytes(ammissione(k, contratto="lnk", generazione=gen("a")))
    os.symlink(fuori, cartella / "admission-receipts" / f"{gen('a')}.json")
    e = esegui(negozio, base / "stato", aut, stato_di(("lnk", "corrente", gen("b"))))
    errori = []
    if not e["bloccanti"]:
        errori.append("il collegamento e' stato saltato invece di bloccare")
    if e["conteggio"].get(C.STORICA):
        errori.append("una ricevuta raggiunta per collegamento e' stata accettata")
    return errori


@caso("non verificabile sulla generazione CORRENTE: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    guasta = bytearray(ammissione(k, contratto="cur", generazione=gen("a"),
                                  contesto="sha256:" + "b" * 64))
    guasta[-1] ^= 0xFF
    pubblicazione(negozio, "cur", [gen("a")], gen("a"), {gen("a"): bytes(guasta)})
    e = esegui(negozio, base / "stato", aut, stato_di(("cur", "corrente", gen("a"))))
    return ([] if e["conteggio"].get(C.IGNOTA) == 1
            else [f"il dubbio sulla generazione corrente non ha bloccato: {e['conteggio']}"])


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

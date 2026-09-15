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
from contract_store import PublicationResult, encode_binding  # noqa: E402
from executor_birth_shadow import (  # noqa: E402
    BirthOutcome, BirthReport,
)
from executor_birth_operational import (  # noqa: E402
    BirthResult, _terminal_envelope,
)


class _NucleoFinto:
    """The envelope builder reads one attribute from the core, and one only."""

    __slots__ = ("admission_key_id",)

    def __init__(self, identificativo: str) -> None:
        self.admission_key_id = identificativo

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
                 "id_pro": id_pro, "priv_pro": priv_pro,
                 "registro": registro}


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


def riga(stato_dir: Path, righe: list[dict], contratto: str | None = None,
         chiavi: dict | None = None) -> Path:
    """Build the durable rows by CROSSING the real Producer store APIs.

    Naming the columns is not the same as crossing the APIs: a hand-written
    CREATE TABLE has no keys, no constraints, no schema version and no
    migration, and it happily accepted forms the productive schema forbids.
    Here the positive path goes through ``get_or_issue_and_claim_producer_receipt``
    and ``finalize_producer_receipt``, which create receipt, issuance, claim and
    conclusion together; a negative case then alters one column of the database
    those APIs produced.
    """
    from datetime import datetime, timezone
    from executor_birth_producer_store import (
        ProducerReceiptBinding, finalize_producer_receipt,
        get_or_issue_and_claim_producer_receipt,
    )

    stato_dir.mkdir(exist_ok=True)
    percorso = stato_dir / "producer_receipts.sqlite"
    # The instant must sit inside the receipts' own validity window and carry
    # the precision the store demands: "now" would be outside both.
    adesso = datetime.fromisoformat(ISTANTE.replace("Z", "+00:00"))
    legame = ProducerReceiptBinding(
        objective_hash="sha256:" + "6" * 64,
        candidate_source_id="sha256:" + "7" * 64,
        executor_origin=ExecutorOrigin.BUILTIN,
        revision_authorship=RevisionAuthor.HUMAN,
    )
    registro = (chiavi or {}).get("registro")
    for r in righe:
        emessa = get_or_issue_and_claim_producer_receipt(
            request_id=r["request_id"], issuer_id=EMITTENTE,
            capability_id="cap",
            contract_id=identita(contratto).value if contratto else "builtin:x/manifest.toml",
            binding=legame, registry=registro, now=adesso, db_path=percorso,
            issue=lambda r=r: r.get("issue_encoded", r["encoded"]),
        )
        finalize_producer_receipt(
            emessa, registry=registro, binding=legame,
            request_id=r["request_id"], now=adesso, db_path=percorso,
            result_binding=r.get("result_binding"),
            rejection_code=r.get("rejection_code"),
            terminal_envelope=r.get("terminal_envelope"),
            terminal_auth=r.get("terminal_auth"),
        )
        # A negative case alters a single column of what the APIs produced.
        alterazioni = {k: v for k, v in r.items()
                       if k in {"encoded", "receipt_hash", "request_id",
                                "issuer_id", "objective_hash",
                                "candidate_source_id", "executor_origin",
                                "revision_authorship", "expires_at"}}
        alterazioni_emissione = r.get("alterazioni_emissione") or {}
        if alterazioni or alterazioni_emissione:
            conn = sqlite3.connect(percorso)
            # A mutation that does not happen must make the test red, not leave
            # it green on the positive case: neither the error nor a zero row
            # count may be swallowed.
            for tabella, mutazioni in (
                ("birth_producer_receipts", alterazioni),
                ("birth_producer_issuance", alterazioni_emissione),
            ):
                for colonna, valore in mutazioni.items():
                    cursore = conn.execute(
                        f"update {tabella} set {colonna} = ? "
                        "where request_id = ?", (valore, r["request_id"]))
                    if cursore.rowcount != 1:
                        conn.close()
                        raise AssertionError(
                            f"la mutazione su {tabella}.{colonna} ha toccato "
                            f"{cursore.rowcount} righe, attesa 1")
            conn.commit(); conn.close()
    return stato_dir


def busta_rifiuto(chiavi: dict, *, contratto: str, richiesta: str,
                  codice: str) -> tuple[bytes, bytes]:
    """A refusal envelope built by the productive builder, like a commit one."""
    encoded = _terminal_envelope(
        _NucleoFinto(chiavi["id_amm"]),
        BirthResult(
            request_id=richiesta,
            report=BirthReport(
                schema_version=1, contract_id=identita(contratto),
                candidate_id=None, semantic_core_id=None,
                admission_context_id=None, revision_class=None,
                changed_dimensions=(), checks=(),
                outcome=BirthOutcome.REJECTED, error_code=codice,
            ),
            publication=None, error_code=codice,
        ),
        None,
    )
    return encoded, chiavi["priv_amm"].sign(C.DOMINIO_TERMINALE + encoded)


def riga_coerente(chiavi: dict, *, byte_produttore: bytes, encoded: bytes,
                  firma: bytes, richiesta: str, stato: str = "committed") -> dict:
    """A durable row whose mandatory columns agree with what is signed."""
    from executor_birth_producer_store import producer_receipt_hash
    from executor_birth_operational import _terminal_binding
    import json as _json
    return {
        "state": stato, "request_id": richiesta,
        "receipt_id": _json.loads(byte_produttore)["receipt_id"],
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
    # The productive builder, not a reduced parallel form: the classifier now
    # consumes the canonical V2 envelope through the product's own decoder, so
    # a fixture that emitted anything else would prove nothing.  The builder
    # reads exactly one attribute from the core, which is why a stand-in works.
    encoded = _terminal_envelope(
        _NucleoFinto(chiavi["id_amm"]),
        BirthResult(
            request_id=richiesta,
            report=BirthReport(
                schema_version=1, contract_id=identita(contratto),
                candidate_id="sha256:" + "1" * 64,
                semantic_core_id="sha256:" + "2" * 64,
                admission_context_id=contesto,
                revision_class=RevisionClass.CODE_REVISION,
                changed_dimensions=(), checks=(),
                outcome=BirthOutcome.ADMITTED, error_code=None,
            ),
            publication=PublicationResult(
                contract_id=identita(contratto),
                previous_generation_id=f"sha256:{precedente}",
                current_generation_id=f"sha256:{generazione}",
                operation="commit_birth_snapshot", repeated=False,
            ),
            error_code=None,
        ),
        byte,
    )
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
    # the receipt signs one generation but is filed under another
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
    # the APIs issue a VALID receipt; the corruption is applied afterwards to
    # the stored column, which is what a negative case must exercise
    r["issue_encoded"] = buoni
    r["encoded"] = bytes(prod)
    stato = riga(base / "stato", [r], "cin", k)
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
        richiesta="sha256:" + "b" * 64)], "sei", k)  # row names another request
    STATO = stato_di(("sei", "corrente", gen("b")))
    e = esegui(negozio, stato, aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"la richiesta discorde non ha bloccato: {e['conteggio']}")
    if "richiesta discorde" not in e["motivi"] and \
            "non canonica V2" not in e["motivi"]:
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
        richiesta="sha256:" + "3" * 64)], "set", k)
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
    # the envelope names a contract the store has no such receipt for
    buoni = produttore(k)
    encoded, firma = busta(k, contratto="estraneo", generazione=gen("e"),
                           precedente=gen("f"), byte_produttore=buoni)
    stato = riga(base / "stato", [riga_coerente(
        k, byte_produttore=buoni, encoded=encoded, firma=firma,
        richiesta="sha256:" + "3" * 64)], "ott", k)
    STATO = stato_di(("ott", "corrente", gen("b")))
    e = esegui(negozio, stato, aut, STATO)
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"l'identita' estranea non ha bloccato: {e['conteggio']}")
    # The signed publication names a different contract from durable issuance.
    # The shared decoder must reject that binding before any twin lookup.
    if "busta terminale non canonica V2" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
    return errori


@caso("directory inattesa SENZA ricevute del contesto: blocca lo stesso")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    pubblicazione(negozio, "nov", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="nov", generazione=gen("a"))})
    # an interrupted publication: no binding, no receipts
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
    # the exact shape the protocol declares:
    # admission-receipts-v2/<generation>/<context>.json
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
    # filed under a context other than the one the receipt signs
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
    # the V2 belongs to a different context: a distinct act, which this
    # census, bound to its own set, does not collect
    altro = "sha256:" + "c" * 64
    v2 = cartella / "admission-receipts-v2" / gen("a")
    v2.mkdir(parents=True)
    (v2 / f"{altro.removeprefix('sha256:')}.json").write_bytes(
        ammissione(k, contratto="vqua", generazione=gen("a"), contesto=altro)
    )
    STATO = stato_di(("vqua", "corrente", gen("b")))
    e = esegui(negozio, base / "stato", aut, STATO)
    errori = []
    # only the V1 of this context is ours; the V2 of another context is a
    # distinct act and stays out
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
    # two genuinely different producers: the builder is deterministic, so the
    # same nonce would make the two receipts byte-identical
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
        k, byte_produttore=X, encoded=encoded, firma=firma, richiesta=ric)], "bad", k)
    e = esegui(negozio, stato, aut, stato_di(("bad", "corrente", gen("b"))))
    return ([] if e["conteggio"].get(C.IGNOTA) == 1
            else [f"i byte diversi non hanno bloccato: {e['conteggio']}"])


@caso("codici di rifiuto diversi fra riga e busta: blocca")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    ric = "sha256:" + "3" * 64
    encoded, firma = busta_rifiuto(k, contratto="rif",
                                   richiesta=ric, codice="codice_busta")
    buoni = produttore(k)
    r = riga_coerente(k, byte_produttore=buoni, encoded=encoded, firma=firma,
                      richiesta=ric, stato="rejected")
    r["rejection_code"] = "codice_riga"      # differs from the signed one
    r["result_binding"] = None
    pubblicazione(negozio, "rif", [gen("a")], gen("a"), {})
    e = esegui(negozio, riga(base / "stato", [r], "rif", k), aut,
               stato_di(("rif", "corrente", gen("a"))))
    return ([] if e["conteggio"].get(C.IGNOTA) == 1
            else [f"due codici diversi accettati come un rifiuto: {e['conteggio']}"])


@caso("rifiuto terminale coerente: e' un rifiuto, non un ignoto")
def _(base: Path) -> list[str]:
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    ric = "sha256:" + "3" * 64
    encoded, firma = busta_rifiuto(k, contratto="coe",
                                   richiesta=ric, codice="property_runner_unavailable")
    r = riga_coerente(k, byte_produttore=produttore(k), encoded=encoded,
                      firma=firma, richiesta=ric, stato="rejected")
    r["rejection_code"] = "property_runner_unavailable"
    r["result_binding"] = None               # as the Producer store requires
    pubblicazione(negozio, "coe", [gen("a")], gen("a"), {})
    e = esegui(negozio, riga(base / "stato", [r], "coe", k), aut,
               stato_di(("coe", "corrente", gen("a"))))
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


@caso("richiesta alterata nella sola catena di emissione: blocca")
def _(base: Path) -> list[str]:
    """The durable chain must be bound, not merely present.

    Everything is built by the real APIs and is coherent; then one column of
    ``birth_producer_issuance`` is changed.  A census that reads the issuance
    only to learn the contract would still call this an authenticated
    conclusion.
    """
    aut, k = autorita_finta()
    negozio = base / "negozio"; negozio.mkdir()
    ric = "sha256:" + "3" * 64
    buoni = produttore(k)
    pubblicazione(negozio, "emi", [gen("a"), gen("b")], gen("b"),
                  {gen("a"): ammissione(k, contratto="emi", generazione=gen("a"),
                                        byte_produttore=buoni, richiesta=ric)})
    encoded, firma = busta(k, contratto="emi", generazione=gen("a"),
                           precedente=gen("b"), richiesta=ric,
                           byte_produttore=buoni)
    r = riga_coerente(k, byte_produttore=buoni, encoded=encoded, firma=firma,
                      richiesta=ric)
    r["alterazioni_emissione"] = {"request_id": "sha256:" + "9" * 64}
    stato = riga(base / "stato", [r], "emi", k)
    e = esegui(negozio, stato, aut, stato_di(("emi", "corrente", gen("b"))))
    errori = []
    if e["conteggio"].get(C.IGNOTA) != 1:
        errori.append(f"la catena alterata non ha bloccato: {e['conteggio']}")
    if e["rifiuti"]:
        errori.append(f"e' stato contato come rifiuto: {len(e['rifiuti'])}")
    if "discorde fra emissione durevole e riga" not in e["motivi"]:
        errori.append(f"motivo inatteso: {e['motivi']}")
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

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
import re
import sqlite3
import stat
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


@dataclass
class Autorita:
    """Everything the verification needs, acquired once, read-only.

    The context and the verifier keys come from the prepared set itself, not
    from a parallel JSON reader: declaring a limit does not make a verdict
    probative, and the public keys were already there.  Nothing is rebuilt from
    ``PATH_RUNTIME``, so this acquisition does not depend on the distribution
    matching the set - which is precisely the obstacle a transition exists to
    resolve.
    """

    contesto: str
    chiavi_ammissione: dict
    registro_produttori: object


def prepara_percorsi(codice: Path, installazione: Path) -> None:
    """Two roots, and they are not the same one.

    The Birth modules exist only in the RM-0008 line, so the code is imported
    from that tree; the contracts, however, were published from the running
    installation, and their code digests only verify against it.  Collapsing
    the two made four contracts fail authentication for no reason but the
    wrong root.
    """
    import sys as _sys
    radice_codice = str(codice / "runtime")
    if radice_codice not in _sys.path:
        _sys.path.insert(0, radice_codice)
    os.environ.setdefault("METNOS_INSTALL_ROOT", str(installazione))


def acquisisci_autorita(codice: Path, installazione: Path) -> Autorita:
    prepara_percorsi(codice, installazione)
    from executor_birth_prepared_root import open_prepared_root_session_v1
    from executor_birth_prepared_set import (
        AUTHORITY_SETS_BASENAME_V1, authority_registry_v1, load_prepared_set_v1,
    )
    from executor_birth_keystore import _load_birth_keystore_in_session
    from executor_birth_receipts import IssuerKey, IssuerRegistry
    from executor_birth_identity import ExecutorOrigin
    from executor_birth_bootstrap import _producer_capabilities_for_bootstrap
    from executor_birth_producer_table_v1 import (
        producer_author_v1, producer_store_name_v1,
    )

    sessione = open_prepared_root_session_v1()
    with sessione:
        with sessione.global_lock(exclusive=False, create=False):
            preparato = load_prepared_set_v1(sessione)
            luogo = (AUTHORITY_SETS_BASENAME_V1, preparato.set_id)
            registro = authority_registry_v1(sessione, luogo)
            ammissione = _load_birth_keystore_in_session(
                luogo + ("admission",), sessione
            )
            # The issuer identity, the store name and the author all come
            # from the closed capability table, exactly as the productive
            # assembly derives them: no name, origin or author is chosen here.
            voci: dict = {}
            for capacita in _producer_capabilities_for_bootstrap():
                nome = producer_store_name_v1(
                    capacita.producer_id, capacita.operation
                )
                if nome not in registro["producers"]:
                    continue
                caricato = _load_birth_keystore_in_session(
                    luogo + ("producers", nome), sessione
                )
                autore = producer_author_v1(
                    capacita.producer_id, capacita.operation
                )
                voci.setdefault(capacita.producer_id, []).extend(
                    IssuerKey(identificativo, chiave,
                              frozenset(ExecutorOrigin), frozenset({autore}))
                    for identificativo, chiave in caricato.verifier_keys.items()
                )
    return Autorita(
        contesto=str(preparato.prepared_admission_context_id),
        chiavi_ammissione=dict(ammissione.verifier_keys),
        registro_produttori=IssuerRegistry(
            {k: tuple(v) for k, v in voci.items()}
        ),
    )


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


_ESADECIMALE_64 = re.compile(r"[0-9a-f]{64}\Z")


def _ricevute_del_contratto(cartella: Path):
    """Every admission receipt of a contract, on both layouts.

    V1 files a receipt as ``admission-receipts/<generation>.json``; from the
    epoch transition on, receipts are filed as
    ``admission-receipts-v2/<generation>/<context>.json`` so a second epoch can
    add its own receipt for the same generation without replacing the first.
    A census that only reads V1 keeps answering "nothing to do" while the
    writing happens somewhere it does not look - which is the one failure a
    census must not have.

    Yields ``(path, generation, context_or_empty)``: for V2 the path itself
    carries both identities, and the caller checks them against the signed
    receipt instead of trusting either.
    """
    for percorso in sorted(cartella.glob("admission-receipts/*.json")):
        yield percorso, percorso.name.removesuffix(".json"), ""
    radice_v2 = cartella / "admission-receipts-v2"
    if not radice_v2.is_dir():
        return
    for per_generazione in sorted(radice_v2.iterdir()):
        if not per_generazione.is_dir():
            continue
        for percorso in sorted(per_generazione.glob("*.json")):
            yield (percorso, per_generazione.name,
                   percorso.name.removesuffix(".json"))


def legami_del_negozio(negozio: Path, autorita: Autorita,
                       stato_finto: dict | None = None
                       ) -> tuple[list[Legame], list[str], list[str]]:
    """Admission receipts, reached and authenticated through the store itself.

    The scan starts from the refs the productive inventory accepted, demands
    zero inventory problems, and reaches each contract's directory with the
    store's own primitive.  Deriving the contract from a raw ``binding.json``
    let the current state be authenticated while the path the receipt came from
    was not - an unexpected directory could contribute a receipt nobody
    inventoried.  Anything the inventory does not own blocks the whole census,
    even when it holds no receipt of the context we are looking for.
    """
    from manifest_inventory import inventory_store_manifests
    from contract_store import (
        ContractRetirement, _existing_contract_directory, current_contract,
    )
    from executor_birth_receipts import verify_admission_receipt
    from sign import list_trusted_publics

    bloccanti: list[str] = []
    fuori_ambito: list[str] = []
    inventario = inventory_store_manifests(store_root=negozio)
    if inventario.problems:
        for problema in inventario.problems:
            bloccanti.append(f"problema d'inventario: {problema}")
    fidate = list_trusted_publics()

    risultato: list[Legame] = []
    cartelle_viste: set[Path] = set()
    for ref in inventario.manifests:
        contratto = ref.contract_id.value
        try:
            cartella = _existing_contract_directory(
                ref.contract_id, store_root=negozio
            )
        except Exception as exc:  # noqa: BLE001
            bloccanti.append(f"{contratto}: directory non raggiungibile ({exc})")
            continue
        cartelle_viste.add(Path(cartella))
        if stato_finto is not None:
            # Test seam: a fixture cannot sign whole generations, so it states
            # what the store would authenticate.  Production never reaches
            # here, and the seam is not exposed on the command line, so no
            # caller can use it to declare a contract retired.
            genere, valore = stato_finto.get(contratto, ("errore", "assente"))
            ritirato = genere == "ritiro"
            generazione_corrente = valore if genere == "corrente" else ""
            errore_stato = valore if genere == "errore" else ""
        else:
            try:
                osservato = current_contract(
                    ref, trusted_publics=fidate, store_root=negozio
                )
                ritirato = isinstance(osservato, ContractRetirement)
                generazione_corrente = (
                    "" if ritirato
                    else str(osservato.generation_id or "").removeprefix("sha256:")
                )
                errore_stato = ""
            except Exception as exc:  # noqa: BLE001
                ritirato = False
                generazione_corrente = ""
                errore_stato = str(getattr(exc, "code", type(exc).__name__))

        for percorso, dal_percorso, dal_percorso_contesto in _ricevute_del_contratto(
                Path(cartella)):
            legame = Legame(
                tipo="ricevuta_ammissione", locazione=str(percorso),
                contesto=autorita.contesto, contratto=contratto,
                generazione=dal_percorso,
            )
            try:
                ricevuta = verify_admission_receipt(
                    percorso.read_bytes(),
                    verifier_keys=autorita.chiavi_ammissione,
                )
            except Exception as exc:  # noqa: BLE001
                # A receipt this set cannot verify is not automatically a
                # defect: the store also holds receipts of previous epochs,
                # signed by keyrings this set does not carry.  It becomes a
                # refusal only when its unverified content claims OUR context,
                # because that is somebody asserting this epoch without the
                # authority to do so.  Either way it is counted, never dropped.
                try:
                    pretende = json.loads(percorso.read_bytes()).get(
                        "admission_context_id"
                    ) == autorita.contesto
                except Exception:  # noqa: BLE001
                    # Corrupted bytes must block, not crash the census: the
                    # doubt falls on the side of refusing.
                    pretende = True
                if not pretende:
                    fuori_ambito.append(str(percorso))
                    continue
                legame.discorde = (
                    "ricevuta che pretende questo contesto ma non e' "
                    f"verificabile: {getattr(exc, 'code', type(exc).__name__)}"
                )
                risultato.append(legame)
                continue
            if str(ricevuta.admission_context_id) != autorita.contesto:
                continue
            legame.stato = str(getattr(ricevuta, "approved_lifecycle", "") or "")
            dal_documento = str(
                getattr(ricevuta, "generation_id", "") or ""
            ).removeprefix("sha256:")
            grezzo_contratto = getattr(ricevuta, "contract_id", None)
            contratto_documento = str(
                getattr(grezzo_contratto, "value", grezzo_contratto) or ""
            )
            if dal_percorso_contesto and dal_percorso_contesto != \
                    autorita.contesto.removeprefix("sha256:"):
                # V2 carries the context in the path as well: a receipt filed
                # under one context and signed for another is a divergence, not
                # a receipt of somebody else's epoch.
                legame.discorde = ("contesto: percorso="
                                   f"{dal_percorso_contesto[:16]} "
                                   f"ricevuta={autorita.contesto[7:23]}")
            elif dal_documento and dal_documento != dal_percorso:
                legame.discorde = (f"generazione: percorso={dal_percorso[:16]} "
                                   f"documento={dal_documento[:16]}")
            elif contratto_documento and contratto_documento != contratto:
                legame.discorde = (f"contratto: inventario={contratto} "
                                   f"documento={contratto_documento}")
            elif errore_stato:
                legame.discorde = f"stato corrente non autenticabile: {errore_stato}"
            else:
                legame.ritirato = ritirato
                legame.corrente = (generazione_corrente == dal_percorso)
                legame.prove = {"generazione_corrente": generazione_corrente,
                                "byte": percorso.read_bytes(),
                                "fonte": "current_contract + verify_admission_receipt"}
            risultato.append(legame)

    # R7: nothing in the store may live outside the inventory.
    for voce in sorted(Path(negozio).iterdir()):
        # lstat, never stat: a symlink to an inventoried directory must not be
        # able to slip past the check by resolving onto an owned path.
        stato_voce = voce.lstat()
        if stat.S_ISLNK(stato_voce.st_mode):
            bloccanti.append(f"collegamento nel negozio: {voce.name}")
        elif not stat.S_ISDIR(stato_voce.st_mode):
            bloccanti.append(f"oggetto non-directory nel negozio: {voce.name}")
        elif voce not in cartelle_viste:
            bloccanti.append(f"directory inattesa nel negozio: {voce.name}")
    return risultato, bloccanti, fuori_ambito


DOMINIO_TERMINALE = b"metnos.executor-birth.terminal/v1\0"


def _verifica_busta_terminale(encoded: bytes, firma: bytes | None,
                              chiavi: dict) -> str:
    """Verify the terminal envelope with the key its own header names.

    The key id travels inside the signed envelope, and the verifier is chosen
    from the set's admission keyring: an envelope cannot nominate a key the set
    does not hold.  Returns an empty string when the signature stands, or the
    reason it does not.
    """
    if firma is None:
        return "busta terminale senza firma"
    try:
        interno = json.loads(encoded)
    except json.JSONDecodeError:
        return "busta terminale non interpretabile"
    identificativo = interno.get("signing_key_id")
    chiave = chiavi.get(identificativo)
    if chiave is None:
        return f"chiave della busta assente dall'insieme: {identificativo}"
    try:
        chiave.verify(bytes(firma), DOMINIO_TERMINALE + bytes(encoded))
    except Exception:  # noqa: BLE001 - any failure is a refusal
        return "firma della busta terminale non valida"
    return ""


def _catena_riga_busta(documento, interno, produttore) -> str:
    """Bind the durable row to the envelope and to the signed Producer receipt.

    Reuses the store's own canonical hash and binding: recomputing either here
    would be a second implementation of a value the product already defines.
    """
    from executor_birth_producer_store import producer_receipt_hash
    from executor_birth_operational import _terminal_binding

    atteso = producer_receipt_hash(bytes(documento["encoded"]))
    if documento.get("receipt_hash") not in (None, atteso):
        return "receipt_hash della riga discorde dai byte Producer firmati"
    for campo, colonna in (("issuer_id", "issuer_id"),
                           ("objective_hash", "objective_hash"),
                           ("candidate_source_id", "candidate_source_id"),
                           ("expires_at", "expires_at")):
        valore = getattr(produttore, campo, None)
        osservato = documento.get(colonna)
        if valore is not None and osservato is not None \
                and str(valore) != str(osservato):
            return f"{colonna} discorde fra riga e ricevuta Producer firmata"
    for campo, colonna in (("executor_origin", "executor_origin"),
                           ("revision_authorship", "revision_authorship")):
        valore = getattr(produttore, campo, None)
        osservato = documento.get(colonna)
        valore = getattr(valore, "value", valore)
        if valore is not None and osservato is not None \
                and str(valore) != str(osservato):
            return f"{colonna} discorde fra riga e ricevuta Producer firmata"
    busta = documento.get("terminal_envelope")
    grezzo = bytes(busta) if isinstance(busta, (bytes, bytearray)) \
        else str(busta).encode()
    legame_atteso = _terminal_binding(grezzo)
    osservato = documento.get("result_binding")
    if osservato is not None and str(osservato) != str(legame_atteso):
        return "result_binding discorde dal legame canonico della busta"
    return ""


def _catena_ammissione(ammissione, documento, interno, byte_busta) -> str:
    """Bind the Admission receipt to the Producer bytes and to the request."""
    from executor_birth_producer_store import producer_receipt_hash

    atteso = producer_receipt_hash(bytes(documento["encoded"]))
    dichiarato = str(getattr(ammissione, "producer_receipt_hash", "") or "")
    if dichiarato != atteso:
        return ("producer_receipt_hash dell'ammissione non e' l'hash dei byte "
                "Producer presenti nella riga")
    richieste = {
        str(getattr(ammissione, "birth_request_id", "") or ""),
        str(interno.get("request_id") or ""),
        str(documento.get("request_id") or ""),
    } - {""}
    if len(richieste) > 1:
        return ("richiesta discorde fra ammissione, busta e riga: "
                + ", ".join(sorted(r[:16] for r in richieste)))
    return ""


def _pretende_il_contesto(documento, contesto: str) -> bool:
    """Does this row's envelope claim our context?  Read without verifying.

    This decides scope, never authority: a row that claims our epoch has to be
    proven, and a row that cannot even be read is treated as claiming it, so
    the doubt falls on the side of blocking.
    """
    busta = documento.get("terminal_envelope")
    if busta is None:
        return False
    grezzo = bytes(busta) if isinstance(busta, (bytes, bytearray)) \
        else str(busta).encode()
    try:
        interno = json.loads(grezzo)
        codificata = interno.get("admission_receipt")
        if codificata is None:
            return contesto.encode() in grezzo
        ricevuta = json.loads(base64.b64decode(codificata))
        return ricevuta.get("admission_context_id") == contesto
    except (json.JSONDecodeError, binascii.Error, TypeError, ValueError):
        return True


def legami_dello_stato(stato_db: Path, autorita: Autorita,
                       per_gemello: dict[tuple[str, str], Legame],
                       rifiuti: list[str],
                       fuori_ambito: list[str]) -> list[Legame]:
    """Producer rows, using BOTH proofs the row actually carries.

    A row holds the signed Producer receipt in ``encoded`` and the signature of
    the terminal envelope in ``terminal_auth``.  Reading only ``state`` and the
    envelope meant a row edited in the database could still be called a valid
    conclusion or a valid refusal, which is the one thing this census must not
    allow.  Both signatures are verified, and every field that appears in more
    than one place - request, state, contract, generation, admission bytes -
    has to agree across row, envelope and verified twin.

    A terminal refusal missing any of those proofs is not excluded: it blocks.
    """
    from datetime import datetime, timezone
    from executor_birth_receipts import verify_producer_receipt

    def _istante_firmato(grezzo: bytes) -> datetime:
        """The verification instant comes from the receipt's own signed field.

        ``registered_at`` is a mutable database column: deriving the instant
        from it let anyone with write access decide whether a signature was
        temporally valid, and an unparsable value silently became "now".  The
        signed ``issued_at`` cannot be edited without breaking the signature,
        so it is the only honest source; an unreadable one blocks.
        """
        interno = json.loads(grezzo)
        return datetime.fromisoformat(
            str(interno["issued_at"]).replace("Z", "+00:00")
        )

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
            legame = Legame(tipo="ricevuta_produttore", locazione=locazione,
                            contesto=autorita.contesto, stato=situazione)

            # An invalid durable timestamp is a defect of the row, not a
            # detail to skip: it blocks even though it is not what we verify.
            grezzo_data = str(documento.get("registered_at") or "")
            if grezzo_data:
                try:
                    datetime.fromisoformat(grezzo_data.replace("Z", "+00:00"))
                except ValueError:
                    legame.discorde = "registered_at durevole non interpretabile"
                    risultato.append(legame)
                    continue

            # (1) the Producer receipt itself, signed, against the set registry
            codificata = documento.get("encoded")
            if codificata is None:
                legame.discorde = "riga senza ricevuta produttore firmata"
                risultato.append(legame)
                continue
            try:
                produttore = verify_producer_receipt(
                    bytes(codificata), registry=autorita.registro_produttori,
                    now=_istante_firmato(bytes(codificata)),
                )
            except Exception as exc:  # noqa: BLE001
                # Symmetric to the admission side: the store also holds rows of
                # previous epochs, whose producer keys this set does not carry.
                # Such a row is out of scope and is counted; it becomes a
                # refusal only if its envelope claims OUR context.
                if not _pretende_il_contesto(documento, autorita.contesto):
                    fuori_ambito.append(locazione)
                    continue
                legame.discorde = ("riga che pretende questo contesto ma la "
                                   "ricevuta produttore non e' verificabile: "
                                   f"{getattr(exc, 'code', type(exc).__name__)}")
                risultato.append(legame)
                continue
            # the signed receipt must match the durable columns
            for campo, colonna in (("receipt_id", "receipt_id"),
                                   ("request_id", "request_id")):
                atteso = getattr(produttore, campo, None)
                osservato = documento.get(colonna)
                if atteso is not None and osservato is not None \
                        and str(atteso) != str(osservato):
                    legame.discorde = (f"{colonna} discorde fra riga e ricevuta "
                                       "produttore firmata")
            if legame.discorde:
                risultato.append(legame)
                continue

            busta = documento.get("terminal_envelope")
            if busta is None:
                if situazione in {"committed", "rejected"}:
                    legame.discorde = f"stato {situazione} senza busta terminale"
                    risultato.append(legame)
                continue
            grezzo = bytes(busta) if isinstance(busta, (bytes, bytearray)) \
                else str(busta).encode()

            # (2) the envelope signature, with the key the envelope names
            motivo = _verifica_busta_terminale(
                grezzo, documento.get("terminal_auth"), autorita.chiavi_ammissione
            )
            if motivo:
                legame.discorde = motivo
                risultato.append(legame)
                continue
            interno = json.loads(grezzo)

            if interno.get("request_id") is not None \
                    and documento.get("request_id") is not None \
                    and str(interno["request_id"]) != str(documento["request_id"]):
                legame.discorde = "richiesta discorde fra riga e busta"
                risultato.append(legame)
                continue

            # (2-bis) the chain: three authenticated acts are one conclusion
            # only if the values that appear in more than one of them agree.
            # Verifying each signature separately and then pairing on identity
            # alone let a fixture with a fabricated producer hash and mismatched
            # requests pass as two historical links - a reproducible false green.
            motivo = _catena_riga_busta(documento, interno, produttore)
            if motivo:
                legame.discorde = motivo
                risultato.append(legame)
                continue

            codificata_ammissione = interno.get("admission_receipt")
            if codificata_ammissione is None:
                motivo_rifiuto = (documento.get("rejection_code")
                                  or interno.get("error_code"))
                if situazione == "rejected" and motivo_rifiuto:
                    # A refusal that concluded properly, and whose two
                    # signatures stand, admitted nothing: not a dependency.
                    rifiuti.append(f"{locazione} ({motivo_rifiuto})")
                    continue
                legame.discorde = ("conclusione senza ricevuta di ammissione in "
                                   f"stato {situazione or '(assente)'}")
                risultato.append(legame)
                continue

            try:
                byte_ammissione = base64.b64decode(codificata_ammissione)
            except (binascii.Error, TypeError, ValueError):
                legame.discorde = "ricevuta di ammissione non decodificabile"
                risultato.append(legame)
                continue
            from executor_birth_receipts import verify_admission_receipt
            try:
                ammissione = verify_admission_receipt(
                    byte_ammissione, verifier_keys=autorita.chiavi_ammissione
                )
            except Exception as exc:  # noqa: BLE001
                legame.discorde = ("ricevuta di ammissione nella busta non "
                                   f"verificabile: {getattr(exc, 'code', type(exc).__name__)}")
                risultato.append(legame)
                continue
            if str(ammissione.admission_context_id) != autorita.contesto:
                continue
            motivo = _catena_ammissione(
                ammissione, documento, interno, byte_ammissione
            )
            if motivo:
                legame.discorde = motivo
                risultato.append(legame)
                continue

            generazione = str(
                getattr(ammissione, "generation_id", "") or ""
            ).removeprefix("sha256:")
            # ``contract_id`` is a ContractId, not a string: str() would give
            # its repr and no key would ever match.
            grezzo_contratto = getattr(ammissione, "contract_id", None)
            contratto = str(getattr(grezzo_contratto, "value", grezzo_contratto) or "")
            legame.generazione = generazione
            legame.contratto = contratto
            if situazione != "committed":
                legame.discorde = ("conclusione con ricevuta ma stato "
                                   f"{situazione or '(assente)'}")
                risultato.append(legame)
                continue
            pubblicazione = interno.get("publication") or {}
            dalla_pubblicazione = str(
                pubblicazione.get("current_generation_id", "")
            ).removeprefix("sha256:")
            if dalla_pubblicazione and dalla_pubblicazione != generazione:
                legame.discorde = ("generazione discorde fra ricevuta e "
                                   "pubblicazione nella stessa busta")
            elif str(pubblicazione.get("contract_id") or contratto) != contratto:
                legame.discorde = ("contratto discorde fra ricevuta e "
                                   "pubblicazione nella stessa busta")
            else:
                gemello = per_gemello.get(
                    (contratto, generazione, autorita.contesto)
                )
                if gemello is None:
                    legame.discorde = ("nessun gemello verificato con identita' "
                                       f"({contratto}, {generazione[:16]})")
                else:
                    legame.corrente = gemello.corrente
                    legame.ritirato = gemello.ritirato
                    legame.discorde = gemello.discorde
                    legame.prove = {"gemello": gemello.locazione,
                                    "emittente": str(getattr(produttore, "issuer_id", ""))}
            risultato.append(legame)
    finally:
        conn.close()
    return risultato


def classifica(codice: Path, installazione: Path, negozio: Path,
               stato_db: Path, autorita: "Autorita | None" = None,
               stato_finto: dict | None = None):
    """``autorita`` and ``stato_finto`` are seams for the tests only."""
    if autorita is None:
        autorita = acquisisci_autorita(codice, installazione)
    del_negozio, bloccanti, fuori_ambito = legami_del_negozio(
        negozio, autorita, stato_finto
    )
    # Identity is the TRIPLE (contract, generation, context), which the epoch
    # specification froze: a V1 historical receipt and a V2 current one for the
    # same generation are two distinct acts, not one fact to reconcile.  Only a
    # repetition of the same triple has to agree, and it has to agree byte for
    # byte - anything less would let one act stand in for another.
    per_gemello: dict[tuple[str, str, str], Legame] = {}
    for legame in del_negozio:
        if not (legame.contratto and legame.generazione) or legame.discorde:
            continue
        chiave = (legame.contratto, legame.generazione, legame.contesto)
        gia = per_gemello.get(chiave)
        if gia is None:
            per_gemello[chiave] = legame
        elif gia.prove.get("byte") != legame.prove.get("byte"):
            gia.discorde = legame.discorde = (
                "due ricevute con la stessa tripla non hanno gli stessi byte"
            )
            per_gemello.pop(chiave, None)
    rifiuti: list[str] = []
    dello_stato = legami_dello_stato(
        stato_db, autorita, per_gemello, rifiuti, fuori_ambito
    )
    tutti = [_classifica(l) for l in del_negozio + dello_stato]
    return autorita.contesto, tutti, rifiuti, bloccanti, fuori_ambito


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--negozio", default=os.path.expanduser(
        "~/.local/state/metnos/contract-publications/v1"))
    ap.add_argument("--stato-nascita", default=os.path.expanduser(
        "~/.local/state/metnos/birth"))
    ap.add_argument("--radice-codice",
                    default=str(Path(__file__).resolve().parents[2]),
                    help="the tree that carries the RM-0008 runtime modules")
    ap.add_argument("--radice-installazione", default="/opt/metnos",
                    help="the installation the contracts were published from")
    args = ap.parse_args(argv)

    try:
        contesto, legami, rifiuti, bloccanti, fuori_ambito = classifica(
            Path(args.radice_codice), Path(args.radice_installazione),
            Path(args.negozio), Path(args.stato_nascita),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"CLASSIFICAZIONE NON ESEGUIBILE: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_SELF

    print(f"contesto (dall'insieme preparato): {contesto}")
    print(f"legami esaminati  : {len(legami)}")
    print(f"rifiuti terminali autenticati (non sono legami): {len(rifiuti)}")
    print(f"ricevute di altre epoche, non verificabili con questo insieme: "
          f"{len(fuori_ambito)}")
    if bloccanti:
        print(f"anomalie del negozio che bloccano: {len(bloccanti)}")
        for voce in bloccanti[:10]:
            print(f"    {voce}")
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

    if bloccanti:
        print("\nESITO: il negozio contiene oggetti che l'inventario non "
              "possiede. F4 non puo' essere dichiarata.")
        return EXIT_NON_CLASSIFICATO
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

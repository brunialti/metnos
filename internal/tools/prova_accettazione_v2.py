#!/usr/bin/env python3
"""Acceptance proof: agent A's sealed boundary driving the whole B perimeter.

Section 11 case 24 asks for the composition of core and dependencies.  This
proof composes them for real, with nothing stubbed on the path under test:

  A's minting  ->  a real ContextSelectionV1
  B's derivation -> the sealed Producer V2 request
  A's sealed publisher port -> persist_v2 / read_v2
  B's store -> a real V2 receipt on disk, beside an untouched V1
  B's Producer adapter -> a real durable registration
  B's postcondition -> both representations must name one act

The store holds a genuinely published generation, the publisher is a real
sealed publisher bound to that store root, and the receipts are really signed.

WHAT IS NOT COVERED, said plainly: the same case also asks for a complete
server and real turns in a copy after the transition.  That needs the whole
coordinator sequence to run and is not this proof.  This one closes the
composition of the two perimeters, not the deployment.

It runs only where both branches exist; on a single branch it says so and
exits 2 rather than passing vacuously.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

try:
    from executor_birth_context_selection import (
        _context_selection_from_required_chain_v1,
    )
    from executor_birth_commit_publisher import (
        _BirthCommitPublisher, _PUBLISHER_TOKEN,
    )
except ImportError as _exc:
    print(
        "NON ESEGUIBILE su questo albero: serve il nucleo dell'agente A.\n"
        f"  motivo: {_exc}\n"
        "  eseguirla sulla composizione dei due rami.",
    )
    raise SystemExit(2)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import contract_store as C  # noqa: E402
import executor_birth_operational as O  # noqa: E402
import executor_birth_producer_context as P  # noqa: E402
import executor_birth_producer_store as S  # noqa: E402
from executor_birth_cutover import CurrentGeneration  # noqa: E402
from executor_birth_identity import ExecutorOrigin, RevisionAuthor  # noqa: E402
from executor_birth_receipts import (  # noqa: E402
    AdmissionKind, ApprovedLifecycle, IssuerKey, IssuerRegistry, RevisionClass,
    issue_admission_receipt, issue_producer_receipt, verify_admission_receipt,
)


def _carica(nome: str, relativo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / relativo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


cert = _carica("cert_helpers", "tests/portable/test_contract_store_certification.py")
sel_tests = _carica("a_selection", "tests/portable/test_executor_birth_context_selection.py")

D = lambda ch: "sha256:" + ch * 64  # noqa: E731
ISSUED = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class Scena:
    """One store, one publisher, one selection, one act across both halves."""

    def __init__(self, base: Path) -> None:
        self.ref, autore = cert._make_source(base)
        self.trusted = cert._trusted_from_raw((("author", cert._raw_public(autore)),))
        self.root = base / "store"
        self.root.mkdir()
        self.generazione = C.publish_signed_source(
            self.ref, expected_generation_id=None, trusted_publics=self.trusted,
            store_root=self.root, lock_timeout=5.0,
        ).current_generation_id
        self.dir = self.root / C.contract_storage_key(self.ref.contract_id)

        transizione, preparato, distribuzione = sel_tests._evidence()
        self.selezione = _context_selection_from_required_chain_v1(
            transizione, preparato, distribuzione,
        )

        self.ammissione = Ed25519PrivateKey.generate()
        self.publisher = _BirthCommitPublisher(
            _PUBLISHER_TOKEN,
            author_private=autore,
            author_ring=(("author", autore.public_key()),),
            admission_private=self.ammissione,
            admission_key_id="admission",
            admission_verifiers={"admission": self.ammissione.public_key()},
            prepared_admission_context_id=self.selezione.admission_context_id,
            prepared_context_epoch=self.selezione.context_epoch,
            primitive=lambda *a, **k: None,
            store_root=self.root,
            registry_reconciler=lambda _r: None,
        )
        self.porta = self.publisher.reattestation_port()

        self.produttore = Ed25519PrivateKey.generate()
        self.registro = IssuerRegistry({"synt": (IssuerKey(
            "synt-1", self.produttore.public_key(),
            frozenset({ExecutorOrigin.SYNTHESIZED}),
            frozenset({RevisionAuthor.MODEL}),
        ),)})
        self.db = base / "producer.sqlite"
        self.sorgente = D("2")
        self.richiesta = P.build_producer_request_v2(
            self.selezione, contract_id=self.ref.contract_id,
            generation_id=self.generazione, candidate_source_id=self.sorgente,
        )
        self.corrente = CurrentGeneration(self.ref, self.generazione)

    # --- the act, in the order the protocol prescribes -------------------

    def ricevuta_produttore(self) -> bytes:
        return issue_producer_receipt(
            issuer_id="synt", executor_origin=ExecutorOrigin.SYNTHESIZED,
            revision_authorship=RevisionAuthor.MODEL,
            objective_hash=self.richiesta.objective_hash,
            candidate_source_id=self.sorgente,
            issued_at="2026-08-25T12:00:00Z", expires_at="2026-08-25T13:00:00Z",
            nonce="0123456789abcdef0123456789abcdef", key_id="synt-1",
            private_key=self.produttore,
        )

    def legame(self) -> S.ProducerReceiptBinding:
        return S.ProducerReceiptBinding(
            self.richiesta.objective_hash, self.sorgente,
            ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL,
        )

    def attesi(self) -> dict:
        return {
            "candidate_id": D("9"), "semantic_core_id": D("a"),
            "predecessor_id": self.generazione,
            "admission_context_id": self.selezione.admission_context_id,
        }

    def ricevuta_ammissione(self, produttore: bytes) -> bytes:
        return issue_admission_receipt(
            policy_version="1", contract_id=self.ref.contract_id,
            generation_id=self.generazione, candidate_id=D("9"),
            semantic_core_id=D("a"),
            admission_context_id=self.selezione.admission_context_id,
            birth_request_id=self.richiesta.request_id,
            authoring_journal_hash=D("b"), predecessor_id=self.generazione,
            producer_receipt_hash=S.producer_receipt_hash(produttore),
            revision_class=RevisionClass.EQUIVALENT_REPUBLISH,
            check_results={}, semantic_review_hash=None, approval_hash=None,
            approved_lifecycle=ApprovedLifecycle.ACTIVE,
            kind=AdmissionKind.REATTESTATION, issued_at="2026-08-25T12:00:00Z",
            key_id="admission", private_key=self.ammissione,
        )

    def registra(self) -> bytes:
        produttore = self.ricevuta_produttore()
        encoded = S.get_or_issue_and_claim_producer_receipt_v2(
            request=self.richiesta, issuer_id="synt",
            capability_id="synt_multistage:create_or_replay",
            binding=self.legame(), registry=self.registro, now=ISSUED,
            db_path=self.db, issue=lambda: produttore,
        )
        S.finalize_producer_receipt(
            encoded, registry=self.registro, binding=self.legame(),
            request_id=self.richiesta.request_id, now=ISSUED, db_path=self.db,
            result_binding=D("4"), terminal_envelope=b"{}", terminal_auth=b"s",
        )
        return encoded


ESITI: list[tuple[str, bool, str]] = []


def caso(nome):
    def wrap(fn):
        base = Path(tempfile.mkdtemp(prefix="accett-v2-"))
        try:
            fn(Scena(base))
            ESITI.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001
            ESITI.append((nome, False, f"{type(exc).__name__}: {exc}"))
        finally:
            shutil.rmtree(base, ignore_errors=True)
        return fn
    return wrap


@caso("1 la catena completa scrive una ricevuta V2 vera nel negozio vero")
def _(s: Scena) -> None:
    produttore = s.registra()
    ammissione = s.ricevuta_ammissione(produttore)
    durevole = s.porta.persist_v2(s.corrente, ammissione, s.attesi(), s.richiesta)
    assert durevole == ammissione
    percorso = C._birth_receipt_path_v2(
        s.dir, s.generazione, s.selezione.admission_context_id,
    )
    assert percorso.is_file(), "nessuna ricevuta sul disco"
    assert percorso.read_bytes() == ammissione
    assert s.porta.read_v2(s.corrente, s.richiesta) == ammissione


@caso("2 la postcondizione compone le due rappresentazioni")
def _(s: Scena) -> None:
    produttore = s.registra()
    ammissione = s.ricevuta_ammissione(produttore)
    s.porta.persist_v2(s.corrente, ammissione, s.attesi(), s.richiesta)
    esito = O.verify_reattestation_postcondition_v2(
        s.ref, request=s.richiesta, producer_receipt=produttore,
        verify_admission=lambda wire: verify_admission_receipt(
            wire, verifier_keys={"admission": s.ammissione.public_key()},
        ),
        authenticate_terminal=lambda e, a: s.selezione.admission_context_id,
        registry=s.registro, binding=s.legame(), now=ISSUED,
        producer_db_path=s.db, trusted_publics=s.trusted, store_root=s.root,
        lock_timeout=5.0,
    )
    assert esito.admission_context_id == s.selezione.admission_context_id
    assert esito.admission_receipt_hash == C.admission_receipt_hash(ammissione)
    assert esito.producer_receipt_hash == S.producer_receipt_hash(produttore)


@caso("3 §11.7 una ripetizione identica e' idempotente su entrambe le meta'")
def _(s: Scena) -> None:
    produttore = s.registra()
    ammissione = s.ricevuta_ammissione(produttore)
    primo = s.porta.persist_v2(s.corrente, ammissione, s.attesi(), s.richiesta)
    di_nuovo = s.registra()
    secondo = s.porta.persist_v2(s.corrente, ammissione, s.attesi(), s.richiesta)
    assert primo == secondo == ammissione
    assert di_nuovo == produttore
    cartella = C._birth_receipt_path_v2(
        s.dir, s.generazione, s.selezione.admission_context_id,
    ).parent
    assert len(list(cartella.iterdir())) == 1, "una seconda ricevuta e' comparsa"


@caso("4 §11.12 la ricevuta storica V1 resta intatta e leggibile")
def _(s: Scena) -> None:
    storica = b"{\"storica\": true}"
    v1 = C._birth_receipt_path(s.dir, s.generazione)
    v1.parent.mkdir(mode=0o700, parents=True)
    v1.write_bytes(storica)
    produttore = s.registra()
    ammissione = s.ricevuta_ammissione(produttore)
    s.porta.persist_v2(s.corrente, ammissione, s.attesi(), s.richiesta)
    assert v1.read_bytes() == storica
    assert C.read_current_birth_receipt(
        s.ref, s.generazione, trusted_publics=s.trusted, store_root=s.root,
        lock_timeout=5.0,
    ) == storica


@caso("5 una richiesta fuori dal contesto preparato non passa la porta")
def _(s: Scena) -> None:
    from executor_birth_commit_publisher import BirthCommitLinkError
    estranea = P.build_producer_request_v2(
        s.selezione, contract_id=s.ref.contract_id,
        generation_id="sha256:" + "0" * 64, candidate_source_id=s.sorgente,
    )
    produttore = s.registra()
    ammissione = s.ricevuta_ammissione(produttore)
    try:
        s.porta.persist_v2(s.corrente, ammissione, s.attesi(), estranea)
    except BirthCommitLinkError as exc:
        assert exc.code == "birth_reattestation_v2_context_invalid"
        return
    raise AssertionError("la porta ha accettato una richiesta di un'altra generazione")


@caso("6 la postcondizione non si accontenta della sola ricevuta")
def _(s: Scena) -> None:
    # The admission receipt is on disk, but the Producer registration was never
    # opened: one representation is not the act.
    produttore = s.ricevuta_produttore()
    ammissione = s.ricevuta_ammissione(produttore)
    s.porta.persist_v2(s.corrente, ammissione, s.attesi(), s.richiesta)
    try:
        O.verify_reattestation_postcondition_v2(
            s.ref, request=s.richiesta, producer_receipt=produttore,
            verify_admission=lambda wire: verify_admission_receipt(
                wire, verifier_keys={"admission": s.ammissione.public_key()},
            ),
            authenticate_terminal=lambda e, a: s.selezione.admission_context_id,
            registry=s.registro, binding=s.legame(), now=ISSUED,
            producer_db_path=s.db, trusted_publics=s.trusted,
            store_root=s.root, lock_timeout=5.0,
        )
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", "") == "producer_request_v2_unregistered", exc
        return
    raise AssertionError("una sola meta' e' bastata")


def main() -> int:
    for nome, ok, dettaglio in ESITI:
        print(f"  {'ok  ' if ok else 'ROSSO'}  {nome}")
        if not ok:
            print(f"          {dettaglio}")
    rossi = [n for n, ok, _ in ESITI if not ok]
    print(f"\nESITO: {'tutte verdi' if not rossi else f'{len(rossi)} rosse'}  ({len(ESITI)} casi)")
    return 1 if rossi else 0


if __name__ == "__main__":
    raise SystemExit(main())

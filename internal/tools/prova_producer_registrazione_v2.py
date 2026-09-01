#!/usr/bin/env python3
"""Proofs for the V2 Producer registration bound to a birth context.

The durable transaction is real: a SQLite store, a real Ed25519 issuer, real
receipts.  What is injected is the terminal authenticator, because this module
must not hold the sealed Birth key; the proofs exercise both a cooperating and
a refusing authenticator.

The stand-in for ``ContextSelectionV1`` is the declared one used by the other
B proofs: agent A's module does not exist yet.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

MODULE = "executor_birth_context_selection"


@dataclass(frozen=True, slots=True)
class _StandInSelection:
    transition_id: str
    set_id: str
    admission_context_id: str
    context_epoch: str
    distribution: object = None


_stand_in = types.ModuleType(MODULE)
_stand_in.ContextSelectionV1 = _StandInSelection
sys.modules[MODULE] = _stand_in

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import executor_birth_producer_context as P  # noqa: E402
import executor_birth_producer_store as S  # noqa: E402
from executor_birth_identity import ExecutorOrigin, RevisionAuthor  # noqa: E402
from executor_birth_receipts import (  # noqa: E402
    IssuerKey, IssuerRegistry, ReceiptError, issue_producer_receipt,
)

D2 = "sha256:" + "2" * 64
RESULT = "sha256:" + "4" * 64
CTX_A = "sha256:" + "a" * 64
CTX_B = "sha256:" + "b" * 64
GEN = "sha256:" + "9" * 64
CONTRACT = "executor:test/example"
ISSUED = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
CAPABILITY = "synt_multistage:create_or_replay"
INVOLUCRO = b"{\"envelope\": true}"
FIRMA = b"firma"


class _Contract:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:  # pragma: no cover - must never be used
        return "REPR-NOT-VALUE"

    __repr__ = __str__


EPOCA_2 = "sha256:" + "e" * 64
EPOCA_3 = "sha256:" + "f" * 64


def _richiesta(context: str = CTX_A, epoca: str = EPOCA_2,
               contratto: str = CONTRACT, sorgente: str = D2):
    # set_id bare hex, context_epoch a digest: the delivered forms.
    return P.build_producer_request_v2(
        _StandInSelection("sha256:" + "1" * 64, "3" * 64, context, epoca),
        contract_id=_Contract(contratto), generation_id=GEN,
        candidate_source_id=sorgente,
    )


class Banco:
    def __init__(self, base: Path) -> None:
        self.db = base / "producer.sqlite"
        self.chiave = Ed25519PrivateKey.generate()
        self.registro = IssuerRegistry({"synt": (IssuerKey(
            "synt-1", self.chiave.public_key(),
            frozenset({ExecutorOrigin.SYNTHESIZED}),
            frozenset({RevisionAuthor.MODEL}),
        ),)})

    def ricevuta(self, objective: str) -> bytes:
        return issue_producer_receipt(
            issuer_id="synt", executor_origin=ExecutorOrigin.SYNTHESIZED,
            revision_authorship=RevisionAuthor.MODEL, objective_hash=objective,
            candidate_source_id=D2, issued_at="2026-08-25T12:00:00Z",
            expires_at="2026-08-25T13:00:00Z",
            nonce="0123456789abcdef0123456789abcdef", key_id="synt-1",
            private_key=self.chiave,
        )

    def legame(self, objective: str) -> S.ProducerReceiptBinding:
        return S.ProducerReceiptBinding(
            objective, D2, ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL,
        )

    def apri(self, richiesta, *, encoded=None) -> bytes:
        encoded = encoded if encoded is not None else self.ricevuta(richiesta.objective_hash)
        return S.get_or_issue_and_claim_producer_receipt_v2(
            request=richiesta, issuer_id="synt", capability_id=CAPABILITY,
            binding=self.legame(richiesta.objective_hash), registry=self.registro,
            now=ISSUED, db_path=self.db, issue=lambda: encoded,
        )

    def chiudi(self, richiesta, encoded: bytes, *, involucro=INVOLUCRO, firma=FIRMA):
        return S.finalize_producer_receipt(
            encoded, registry=self.registro,
            binding=self.legame(richiesta.objective_hash),
            request_id=richiesta.request_id, now=ISSUED, db_path=self.db,
            result_binding=RESULT, terminal_envelope=involucro, terminal_auth=firma,
        )

    def verifica(self, richiesta, encoded: bytes, *, autentica=None):
        return S.verify_terminal_registration_v2(
            encoded, request=richiesta, registry=self.registro,
            binding=self.legame(richiesta.objective_hash), now=ISSUED,
            db_path=self.db,
            authenticate_terminal=autentica or (lambda e, a: CTX_A),
        )


ESITI: list[tuple[str, bool, str]] = []


def caso(nome):
    def wrap(fn):
        base = Path(tempfile.mkdtemp(prefix="prova-prod-v2-"))
        try:
            fn(Banco(base))
            ESITI.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001
            ESITI.append((nome, False, f"{type(exc).__name__}: {exc}"))
        finally:
            shutil.rmtree(base, ignore_errors=True)
        return fn
    return wrap


def _rifiuta(codice, fn):
    try:
        fn()
    except ReceiptError as exc:
        assert exc.code == codice, f"atteso {codice}, ottenuto {exc.code}"
        return
    raise AssertionError(f"nessun rifiuto: atteso {codice}")


@caso("1 la registrazione V2 si apre e si chiude sulla richiesta sigillata")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    claim = b.chiudi(r, encoded)
    assert claim.state == "committed"
    esito = b.verifica(r, encoded)
    assert esito.state == "committed"
    assert esito.terminal_envelope == INVOLUCRO and esito.terminal_auth == FIRMA
    assert esito.result_binding == RESULT


@caso("2 una ripetizione identica rinnova, non apre una seconda transazione")
def _(b: Banco) -> None:
    r = _richiesta()
    primo = b.apri(r)
    secondo = b.apri(r, encoded=primo)
    assert primo == secondo
    import sqlite3
    with sqlite3.connect(b.db) as db:
        assert db.execute("SELECT count(*) FROM birth_producer_receipts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM birth_producer_issuance").fetchone()[0] == 1


@caso("3 un legame che nomina un altro obiettivo non e' questo atto")
def _(b: Banco) -> None:
    r = _richiesta()
    altro = b.legame("sha256:" + "7" * 64)
    _rifiuta(
        "producer_request_v2_objective_conflict",
        lambda: S.get_or_issue_and_claim_producer_receipt_v2(
            request=r, issuer_id="synt", capability_id=CAPABILITY, binding=altro,
            registry=b.registro, now=ISSUED, db_path=b.db, issue=lambda: b"x",
        ),
    )


@caso("3-bis §9 un legame con un'altra sorgente e' rifiutato")
def _(b: Banco) -> None:
    r = _richiesta()
    altra = S.ProducerReceiptBinding(
        r.objective_hash, "sha256:" + "8" * 64,
        ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL,
    )
    _rifiuta(
        "producer_request_v2_source_conflict",
        lambda: S.get_or_issue_and_claim_producer_receipt_v2(
            request=r, issuer_id="synt", capability_id=CAPABILITY, binding=altra,
            registry=b.registro, now=ISSUED, db_path=b.db, issue=lambda: b"x",
        ),
    )


@caso("4 una richiesta forgiata non e' una richiesta")
def _(b: Banco) -> None:
    @dataclass(frozen=True, slots=True)
    class Sosia:
        request_id: str
        objective_hash: str
        contract_id: str
        admission_context_id: str

    r = _richiesta()
    sosia = Sosia(r.request_id, r.objective_hash, r.contract_id, r.admission_context_id)
    _rifiuta(
        "producer_request_v2_untrusted",
        lambda: S.get_or_issue_and_claim_producer_receipt_v2(
            request=sosia, issuer_id="synt", capability_id=CAPABILITY,
            binding=b.legame(r.objective_hash), registry=b.registro, now=ISSUED,
            db_path=b.db, issue=lambda: b"x",
        ),
    )


@caso("5 §11.18 una registrazione V1 non e' verificabile come V2")
def _(b: Banco) -> None:
    # A V1 caller is free to pick its own request identity; that identity is
    # not the V2 one, so the V2 verifier finds no registration for it.
    r = _richiesta()
    encoded = b.ricevuta(r.objective_hash)
    S.get_or_issue_and_claim_producer_receipt(
        request_id="sha256:" + "5" * 64, issuer_id="synt", capability_id=CAPABILITY,
        contract_id=CONTRACT, binding=b.legame(r.objective_hash),
        registry=b.registro, now=ISSUED, db_path=b.db, issue=lambda: encoded,
    )
    _rifiuta("producer_request_v2_unregistered", lambda: b.verifica(r, encoded))


@caso("6 §11.18 la ricevuta di un'altra epoca non si lega a questa")
def _(b: Banco) -> None:
    # The receipt of epoch 2 carries epoch 2's objective, so it cannot even be
    # bound to epoch 3: the refusal lands before any lookup.
    prima = _richiesta(epoca=EPOCA_2)
    encoded = b.apri(prima)
    b.chiudi(prima, encoded)
    seconda = _richiesta(epoca=EPOCA_3)
    assert seconda.request_id != prima.request_id
    assert seconda.objective_hash != prima.objective_hash
    _rifiuta("producer_receipt_binding_invalid", lambda: b.verifica(seconda, encoded))


@caso("6-bis §11.18 una ricevuta della nuova epoca mai registrata non passa")
def _(b: Banco) -> None:
    # Same shape, but the binding now agrees: the refusal must come from the
    # absent registration, not from the receipt.
    prima = _richiesta(epoca=EPOCA_2)
    b.chiudi(prima, b.apri(prima))
    seconda = _richiesta(epoca=EPOCA_3)
    fresca = b.ricevuta(seconda.objective_hash)
    _rifiuta("producer_request_v2_unregistered", lambda: b.verifica(seconda, fresca))


@caso("7 una registrazione non terminale non passa per terminale")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    _rifiuta("producer_request_v2_not_terminal", lambda: b.verifica(r, encoded))


@caso("8 un rifiuto non e' un impegno")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    S.finalize_producer_receipt(
        encoded, registry=b.registro, binding=b.legame(r.objective_hash),
        request_id=r.request_id, now=ISSUED, db_path=b.db,
        rejection_code="respinta",
    )
    _rifiuta("producer_request_v2_not_terminal", lambda: b.verifica(r, encoded))


@caso("8-bis un rifiuto CON involucro resta un rifiuto")
def _(b: Banco) -> None:
    # The store lets a rejection carry a terminal envelope.  Without an
    # explicit state check the envelope alone would look like a commitment,
    # so this case is the one that keeps the two checks independent.
    r = _richiesta()
    encoded = b.apri(r)
    S.finalize_producer_receipt(
        encoded, registry=b.registro, binding=b.legame(r.objective_hash),
        request_id=r.request_id, now=ISSUED, db_path=b.db,
        rejection_code="respinta", terminal_envelope=INVOLUCRO,
        terminal_auth=FIRMA,
    )
    _rifiuta("producer_request_v2_not_terminal", lambda: b.verifica(r, encoded))


@caso("9 senza involucro terminale non c'e' registrazione terminale")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    S.finalize_producer_receipt(
        encoded, registry=b.registro, binding=b.legame(r.objective_hash),
        request_id=r.request_id, now=ISSUED, db_path=b.db, result_binding=RESULT,
    )
    _rifiuta("producer_request_v2_not_terminal", lambda: b.verifica(r, encoded))


@caso("10 un involucro non autenticato non vale")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    b.chiudi(r, encoded)

    def rifiuta(_e, _a):
        raise ValueError("firma non valida")

    _rifiuta(
        "producer_request_v2_terminal_unauthenticated",
        lambda: b.verifica(r, encoded, autentica=rifiuta),
    )


@caso("11 un involucro autenticato che dichiara un altro contesto e' rifiutato")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    b.chiudi(r, encoded)
    _rifiuta(
        "producer_request_v2_context_conflict",
        lambda: b.verifica(r, encoded, autentica=lambda e, a: CTX_B),
    )


@caso("12 l'autenticatore riceve i byte davvero conservati")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    b.chiudi(r, encoded, involucro=b"{\"altro\": 1}", firma=b"altra-firma")
    visti: list[tuple[bytes, bytes]] = []

    def spia(e, a):
        visti.append((e, a))
        return CTX_A

    b.verifica(r, encoded, autentica=spia)
    assert visti == [(b"{\"altro\": 1}", b"altra-firma")]


@caso("13 stessa identita' registrata su un altro contratto: conflitto")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.ricevuta(r.objective_hash)
    # The V1 entry point accepts a free identity: register this exact request
    # id against a different contract, then require V2 to refuse it.
    S.get_or_issue_and_claim_producer_receipt(
        request_id=r.request_id, issuer_id="synt", capability_id=CAPABILITY,
        contract_id="executor:test/altro", binding=b.legame(r.objective_hash),
        registry=b.registro, now=ISSUED, db_path=b.db, issue=lambda: encoded,
    )
    S.finalize_producer_receipt(
        encoded, registry=b.registro, binding=b.legame(r.objective_hash),
        request_id=r.request_id, now=ISSUED, db_path=b.db,
        result_binding=RESULT, terminal_envelope=INVOLUCRO, terminal_auth=FIRMA,
    )
    _rifiuta("producer_request_v2_binding_conflict", lambda: b.verifica(r, encoded))


@caso("14 un autenticatore che non e' chiamabile e' rifiutato")
def _(b: Banco) -> None:
    r = _richiesta()
    encoded = b.apri(r)
    b.chiudi(r, encoded)
    _rifiuta(
        "producer_receipt_invalid",
        lambda: S.verify_terminal_registration_v2(
            encoded, request=r, registry=b.registro,
            binding=b.legame(r.objective_hash), now=ISSUED, db_path=b.db,
            authenticate_terminal="non chiamabile",
        ),
    )


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

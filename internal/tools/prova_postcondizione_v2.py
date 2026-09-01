#!/usr/bin/env python3
"""Proofs for the V2 reattestation postcondition.

This is the first proof of the B perimeter that composes its two halves: a real
contract store with a really published generation, and a real durable Producer
transaction.  It is NOT the acceptance proof of section 11 case 24, which also
composes agent A's core; the stand-in for ``ContextSelectionV1`` is still the
declared one.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "internal/tools"))

import prova_producer_registrazione_v2 as PR  # noqa: E402
import prova_ricevute_v2 as RV  # noqa: E402

import executor_birth_operational as O  # noqa: E402
from executor_birth_receipts import ReceiptError  # noqa: E402

CTX_A = RV.CTX_A
CTX_B = RV.CTX_B


class Insieme:
    """One store, one producer database, one sealed request across both."""

    def __init__(self, base: Path) -> None:
        self.negozio = RV.Negozio(base)
        self.banco = PR.Banco(base)
        # One source identity across both halves: the Producer binding and
        # the sealed request must name the same act.
        self.richiesta = self.negozio.richiesta(CTX_A, sorgente=PR.D2)

    def scrivi_ammissione(self, *, context: str = CTX_A) -> bytes:
        return self.negozio.scrivi(context=context)

    def registra(self, *, terminale=True, involucro=PR.INVOLUCRO) -> bytes:
        encoded = self.banco.apri(self.richiesta)
        if terminale:
            self.banco.chiudi(self.richiesta, encoded, involucro=involucro)
        return encoded

    def verifica(self, producer: bytes, *, verifica_ammissione=None,
                 autentica=None, richiesta=None):
        return O.verify_reattestation_postcondition_v2(
            self.negozio.ref,
            request=richiesta if richiesta is not None else self.richiesta,
            producer_receipt=producer,
            verify_admission=verifica_ammissione or RV._verifica,
            authenticate_terminal=autentica or (lambda e, a: CTX_A),
            registry=self.banco.registro,
            binding=self.banco.legame(self.richiesta.objective_hash),
            now=PR.ISSUED,
            producer_db_path=self.banco.db,
            trusted_publics=self.negozio.trusted,
            store_root=self.negozio.root,
            lock_timeout=5.0,
        )


ESITI: list[tuple[str, bool, str]] = []


def caso(nome):
    def wrap(fn):
        base = Path(tempfile.mkdtemp(prefix="prova-post-v2-"))
        try:
            fn(Insieme(base))
            ESITI.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001
            ESITI.append((nome, False, f"{type(exc).__name__}: {exc}"))
        finally:
            shutil.rmtree(base, ignore_errors=True)
        return fn
    return wrap


def _rifiuta(atteso, fn, tipo=ValueError):
    try:
        fn()
    except tipo as exc:
        testo = getattr(exc, "code", None) or str(exc)
        assert testo.startswith(atteso), f"atteso {atteso}, ottenuto {testo}"
        return
    raise AssertionError(f"nessun rifiuto: atteso {atteso}")


@caso("1 le due rappresentazioni concordi soddisfano la postcondizione")
def _(i: Insieme) -> None:
    ammissione = i.scrivi_ammissione()
    producer = i.registra()
    esito = i.verifica(producer)
    assert esito.contract_id == i.negozio.ref.contract_id.value
    assert esito.generation_id == i.negozio.generation
    assert esito.admission_context_id == CTX_A


@caso("2 le impronte sono quelle dei byte CONSERVATI, non di quelli passati")
def _(i: Insieme) -> None:
    ammissione = i.scrivi_ammissione()
    producer = i.registra()
    esito = i.verifica(producer)
    import contract_store as C
    import executor_birth_producer_store as S
    assert esito.admission_receipt_hash == C.admission_receipt_hash(i.negozio.leggi())
    assert esito.admission_receipt_hash == C.admission_receipt_hash(ammissione)
    assert esito.producer_receipt_hash == S.producer_receipt_hash(producer)


@caso("3 senza ricevuta di ammissione non c'e' postcondizione")
def _(i: Insieme) -> None:
    producer = i.registra()
    _rifiuta("birth_postcondition_admission_missing", lambda: i.verifica(producer))


@caso("4 una ricevuta di ammissione non autenticabile ferma tutto")
def _(i: Insieme) -> None:
    i.scrivi_ammissione()
    producer = i.registra()

    def rifiuta(_encoded):
        raise ValueError("firma non valida")

    _rifiuta(
        "birth_postcondition_admission_unauthenticated",
        lambda: i.verifica(producer, verifica_ammissione=rifiuta),
    )


@caso("5 una ricevuta che nomina un'altra generazione e' rifiutata")
def _(i: Insieme) -> None:
    i.scrivi_ammissione()
    producer = i.registra()

    class Altra:
        contract_id = "x"
        generation_id = "sha256:" + "0" * 64
        admission_context_id = CTX_A

    _rifiuta(
        "birth_postcondition_admission_binding",
        lambda: i.verifica(producer, verifica_ammissione=lambda e: Altra()),
    )


@caso("6 una registrazione Producer non terminale ferma la postcondizione")
def _(i: Insieme) -> None:
    i.scrivi_ammissione()
    producer = i.registra(terminale=False)
    _rifiuta(
        "producer_request_v2_not_terminal",
        lambda: i.verifica(producer), tipo=ReceiptError,
    )


@caso("7 un involucro terminale che dichiara un altro contesto e' rifiutato")
def _(i: Insieme) -> None:
    i.scrivi_ammissione()
    producer = i.registra()
    _rifiuta(
        "producer_request_v2_context_conflict",
        lambda: i.verifica(producer, autentica=lambda e, a: CTX_B),
        tipo=ReceiptError,
    )


@caso("8 una richiesta forgiata non e' una richiesta")
def _(i: Insieme) -> None:
    i.scrivi_ammissione()
    producer = i.registra()

    class Sosia:
        contract_id = "x"
        generation_id = "y"
        admission_context_id = CTX_A
        request_id = "z"
        objective_hash = "w"

    _rifiuta(
        "birth_postcondition_request_untrusted",
        lambda: i.verifica(producer, richiesta=Sosia()),
    )


@caso("9 un verificatore non chiamabile e' rifiutato")
def _(i: Insieme) -> None:
    i.scrivi_ammissione()
    producer = i.registra()
    _rifiuta(
        "birth_postcondition_verifier_invalid",
        lambda: i.verifica(producer, verifica_ammissione="non chiamabile"),
    )


@caso("10 ammissione scritta in un contesto, richiesta in un altro: nessuna lettura")
def _(i: Insieme) -> None:
    # The receipt exists, but for another context: the V2 reader is bound to
    # the triple, so it must not find it and must not fall back.
    i.scrivi_ammissione(context=CTX_B)
    producer = i.registra()
    _rifiuta("birth_postcondition_admission_missing", lambda: i.verifica(producer))


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

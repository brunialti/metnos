#!/usr/bin/env python3
"""Integration proof: the B perimeter against agent A's REAL ContextSelectionV1.

Every other B proof installs a declared stand-in because A's loader module did
not exist.  It exists now, so this one mints a genuine sealed selection through
A's own minting path and drives the B derivation with it.  This is what turns
"the shapes look compatible" into evidence.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

try:
    from executor_birth_context_selection import (  # noqa: E402
        ContextSelectionV1,
        _context_selection_for_staged_reattestation_v1,
        _context_selection_from_required_chain_v1,
    )
except ImportError as _exc:  # the B branch alone does not carry A's module
    print(
        "NON ESEGUIBILE su questo albero: serve il modulo del caricatore di A.\n"
        f"  motivo: {_exc}\n"
        "  eseguirla sulla composizione dei due rami.",
    )
    raise SystemExit(2)

import executor_birth_producer_context as P  # noqa: E402

# Agent A's own test module carries the recipe for a real selection; reusing it
# means this proof cannot drift from what A actually builds.
_spec = importlib.util.spec_from_file_location(
    "a_selection_tests", ROOT / "tests/portable/test_executor_birth_context_selection.py",
)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

GEN = "sha256:" + "9" * 64


class _Contract:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:  # pragma: no cover
        return "REPR-NOT-VALUE"

    __repr__ = __str__


ESITI: list[tuple[str, bool, str]] = []


def caso(nome):
    def wrap(fn):
        try:
            fn()
            ESITI.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001
            ESITI.append((nome, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return wrap


def _selezione_vera():
    transition, prepared, distribution = A._evidence()
    return _context_selection_from_required_chain_v1(transition, prepared, distribution)


@caso("1 una selezione VERA di A costruisce una richiesta Producer V2")
def _() -> None:
    selezione = _selezione_vera()
    assert isinstance(selezione, ContextSelectionV1)
    r = P.build_producer_request_v2(
        selezione, contract_id=_Contract("origin/manifest.toml"), generation_id=GEN,
    )
    assert r.admission_context_id == selezione.admission_context_id
    assert r.transition_id == selezione.transition_id
    assert r.set_id == selezione.set_id
    assert r.context_epoch == selezione.context_epoch
    assert r.request_id != r.objective_hash


@caso("2 la derivazione e' deterministica sulla selezione vera")
def _() -> None:
    a = P.build_producer_request_v2(
        _selezione_vera(), contract_id=_Contract("o/m.toml"), generation_id=GEN,
    )
    b = P.build_producer_request_v2(
        _selezione_vera(), contract_id=_Contract("o/m.toml"), generation_id=GEN,
    )
    assert a.request_id == b.request_id and a.objective_hash == b.objective_hash


@caso("3 la selezione per la sola riattestazione in staging e' distinta")
def _() -> None:
    transition, prepared, distribution = A._evidence()
    richiesta = _context_selection_from_required_chain_v1(transition, prepared, distribution)
    staged = _context_selection_for_staged_reattestation_v1(transition, prepared, distribution)
    assert staged.staged_reattestation_only
    a = P.build_producer_request_v2(
        richiesta, contract_id=_Contract("o/m.toml"), generation_id=GEN,
    )
    b = P.build_producer_request_v2(
        staged, contract_id=_Contract("o/m.toml"), generation_id=GEN,
    )
    # Same authenticated facts: the derivation must agree, because the mode is
    # an authority marker and not part of the act's identity.
    assert a.request_id == b.request_id


@caso("4 il tipo e' nominale: un sosia della selezione vera e' rifiutato")
def _() -> None:
    from dataclasses import dataclass
    selezione = _selezione_vera()

    @dataclass(frozen=True, slots=True)
    class Sosia:
        transition_id: str
        set_id: str
        admission_context_id: str
        context_epoch: str

    sosia = Sosia(selezione.transition_id, selezione.set_id,
                  selezione.admission_context_id, selezione.context_epoch)
    try:
        P.build_producer_request_v2(
            sosia, contract_id=_Contract("o/m.toml"), generation_id=GEN,
        )
    except P.ProducerContextError as exc:
        assert exc.code == "producer_request_v2_invalid"
        return
    raise AssertionError("un sosia e' stato accettato")


@caso("5 la prova delle correnti restituisce il tipo che A consuma")
def _() -> None:
    import contract_store as C
    import inspect
    from executor_birth_cutover import CurrentReceiptProof
    sorgente = inspect.getsource(C.current_receipt_proof)
    assert "from executor_birth_cutover import CurrentReceiptProof" in sorgente
    assert not hasattr(C, "CurrentReceiptEntry"), "tipo duplicato ancora presente"
    assert CurrentReceiptProof.__module__ == "executor_birth_cutover"


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

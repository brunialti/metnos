#!/usr/bin/env python3
"""Proofs for the sealed V2 Producer request constructor.

SCOPE AND HONESTY OF THIS FILE.  The constructor depends on the nominal type
``ContextSelectionV1`` that agent A delivers in
``runtime/executor_birth_context_selection.py``.  While the two perimeters are
built in parallel that module does not exist yet, so these proofs install a
*declared stand-in* with the shape frozen at B1.

That stand-in makes these unit proofs meaningful for the derivation and the
refusals, and it does NOT prove the real composition.  The composition is the
job of the acceptance proof required by section 11 case 24, which runs against
A's real module.  Nothing here should be read as integration coverage.
"""

from __future__ import annotations

import hashlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
sys.path.insert(0, str(RUNTIME))

MODULE = "executor_birth_context_selection"


@dataclass(frozen=True, slots=True)
class _StandInSelection:
    """Stand-in for the frozen ``ContextSelectionV1`` shape."""

    transition_id: str
    set_id: str
    admission_context_id: str
    context_epoch: int
    distribution: object = None


def _install_stand_in() -> None:
    module = types.ModuleType(MODULE)
    module.ContextSelectionV1 = _StandInSelection
    sys.modules[MODULE] = module


def _remove_stand_in() -> None:
    sys.modules.pop(MODULE, None)


_install_stand_in()

import executor_birth_producer_context as P  # noqa: E402


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class _Contract:
    """Stands for ``ContractId``: repr and value deliberately disagree."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:  # pragma: no cover - must never be used
        return "REPR-NOT-VALUE"

    __repr__ = __str__


def _selection(**over):
    base = dict(
        transition_id=_sha("transition"),
        set_id=_sha("set"),
        admission_context_id=_sha("context"),
        context_epoch=2,
    )
    base.update(over)
    return _StandInSelection(**base)


def _build(selection=None, contract="origin/manifest.toml", generation=None):
    return P.build_producer_request_v2(
        selection if selection is not None else _selection(),
        contract_id=_Contract(contract),
        generation_id=generation if generation is not None else _sha("generation"),
    )


ESITI: list[tuple[str, bool, str]] = []


def prova(nome):
    def wrap(fn):
        try:
            fn()
            ESITI.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001 - the runner reports every failure
            ESITI.append((nome, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return wrap


def _rifiuta(codice, fn):
    try:
        fn()
    except P.ProducerContextError as exc:
        assert exc.code == codice, f"atteso {codice}, ottenuto {exc.code}"
        return
    raise AssertionError(f"nessun rifiuto: atteso {codice}")


@prova("1 identita' deterministica: un ritentativo identico deriva lo stesso id")
def _() -> None:
    a, b = _build(), _build()
    assert a.request_id == b.request_id
    assert a.objective_hash == b.objective_hash


@prova("2 separazione di dominio: richiesta e obiettivo non coincidono")
def _() -> None:
    r = _build()
    assert r.request_id != r.objective_hash


@prova("3 una transizione diversa produce una richiesta diversa")
def _() -> None:
    other = _selection(transition_id=_sha("altra-transizione"))
    assert _build().request_id != _build(other).request_id


@prova("4 §11.18 un'altra epoca non e' riutilizzabile")
def _() -> None:
    other = _selection(context_epoch=3)
    assert _build().request_id != _build(other).request_id
    assert _build().objective_hash != _build(other).objective_hash


@prova("5 un insieme target diverso produce una richiesta diversa")
def _() -> None:
    other = _selection(set_id=_sha("altro-insieme"))
    assert _build().request_id != _build(other).request_id


@prova("6 un contesto di ammissione diverso produce una richiesta diversa")
def _() -> None:
    other = _selection(admission_context_id=_sha("altro-contesto"))
    assert _build().request_id != _build(other).request_id


@prova("7 una generazione diversa produce una richiesta diversa")
def _() -> None:
    assert _build().request_id != _build(generation=_sha("altra")).request_id


@prova("8 un contratto diverso produce una richiesta diversa")
def _() -> None:
    assert _build().request_id != _build(contract="altro/manifest.toml").request_id


@prova("9 §11.18 il dominio V1 non collide col dominio V2")
def _() -> None:
    r = _build()
    campi = (
        b"origin/manifest.toml",
        _sha("generation").encode(),
        _sha("context").encode(),
        _sha("transition").encode(),
        _sha("set").encode(),
        (2).to_bytes(8, "big"),
    )
    v1 = P._hash(b"metnos.executor-birth.producer-request/v1\0", *campi)
    assert r.request_id != v1, "un dominio V1 collide con il V2"


@prova("10 un sosia strutturale non e' una selezione: il controllo e' nominale")
def _() -> None:
    @dataclass(frozen=True, slots=True)
    class Sosia:
        transition_id: str
        set_id: str
        admission_context_id: str
        context_epoch: int

    sosia = Sosia(_sha("transition"), _sha("set"), _sha("context"), 2)
    _rifiuta("producer_request_v2_invalid", lambda: _build(sosia))


@prova("11 nessun chiamante puo' passare un contesto")
def _() -> None:
    for chiave in ("admission_context_id", "transition_id", "set_id", "context_epoch"):
        try:
            P.build_producer_request_v2(
                _selection(), contract_id=_Contract("o/m.toml"),
                generation_id=_sha("g"), **{chiave: _sha("x")},
            )
        except TypeError:
            continue
        raise AssertionError(f"il costruttore ha accettato {chiave}")


@prova("12 il contratto e' letto da .value, mai dal repr")
def _() -> None:
    r = _build()
    assert r.contract_id == "origin/manifest.toml"
    assert "REPR-NOT-VALUE" not in r.contract_id
    atteso = P._hash(
        P._REQUEST_DOMAIN, b"origin/manifest.toml", _sha("generation").encode(),
        _sha("context").encode(), _sha("transition").encode(),
        _sha("set").encode(), (2).to_bytes(8, "big"),
    )
    assert r.request_id == atteso, "il pre-immagine non usa il valore del contratto"


@prova("13 impronte non canoniche rifiutate")
def _() -> None:
    for guasta in ("sha256:" + "A" * 64, "sha256:abc", "abc", "sha256:" + "g" * 64, None):
        _rifiuta(
            "producer_request_v2_invalid",
            lambda g=guasta: _build(_selection(transition_id=g)),
        )
    _rifiuta("producer_request_v2_invalid", lambda: _build(generation="sha256:zz"))


@prova("14 epoca: booleano, negativa e non intera rifiutate")
def _() -> None:
    for guasta in (True, False, -1, 1.0, "2", None, 1 << 62):
        _rifiuta(
            "producer_request_v2_invalid",
            lambda g=guasta: _build(_selection(context_epoch=g)),
        )


@prova("15 contratto senza .value rifiutato, e il repr non lo salva")
def _() -> None:
    class Senza:
        def __str__(self) -> str:
            return "origin/manifest.toml"

    _rifiuta(
        "producer_request_v2_invalid",
        lambda: P.build_producer_request_v2(
            _selection(), contract_id=Senza(), generation_id=_sha("g"),
        ),
    )


@prova("16 il sigillo impedisce la costruzione diretta")
def _() -> None:
    try:
        P.ProducerRequestV2(
            _sha("r"), _sha("o"), "o/m.toml", _sha("g"), _sha("c"),
            _sha("t"), 2, _sha("s"), object(),
        )
    except P.ProducerContextError as exc:
        assert exc.code == "producer_request_v2_untrusted"
        return
    raise AssertionError("una richiesta forgiata e' stata accettata")


@prova("17 modulo del caricatore assente: fallisce chiuso")
def _() -> None:
    _remove_stand_in()
    try:
        _rifiuta("producer_request_v2_selection_unavailable", _build)
    finally:
        _install_stand_in()


@prova("18 il caricatore che non espone un tipo fallisce chiuso")
def _() -> None:
    modulo = types.ModuleType(MODULE)
    modulo.ContextSelectionV1 = "non un tipo"
    sys.modules[MODULE] = modulo
    try:
        _rifiuta("producer_request_v2_selection_unavailable", _build)
    finally:
        _install_stand_in()


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

#!/usr/bin/env python3
"""Proofs for the reattestation routing between the V1 and V2 receipt paths.

What is under test is the routing and its refusals, not the store: the two
write paths are supplied as recording callables so each case can state exactly
which one was reached.  The store's own behaviour has its own proofs.

The birth core is the real one built by the existing operational fixture, so
the sealed request and the sealed core are genuine objects.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests/runtime/executors"))

MODULE = "executor_birth_context_selection"
if MODULE not in sys.modules:
    import types
    from dataclasses import dataclass as _dc

    @_dc(frozen=True, slots=True)
    class _StandInSelection:
        transition_id: str
        set_id: str
        admission_context_id: str
        context_epoch: str
        distribution: object = None

    _m = types.ModuleType(MODULE)
    _m.ContextSelectionV1 = _StandInSelection
    sys.modules[MODULE] = _m

import executor_birth_producer_context as P  # noqa: E402
import executor_birth_reattestation as R  # noqa: E402
from executor_birth_cutover import CurrentGeneration  # noqa: E402
from executor_birth_identity import ExecutorOrigin, RevisionAuthor  # noqa: E402
from executor_birth_producer_store import ProducerReceiptBinding  # noqa: E402

# The operational fixture reads its sample executor from a build-artifact path
# relative to the working directory.  Build that scene from this tree's own
# source instead of depending on an artifact that may or may not be present.
_SCENA = Path(tempfile.mkdtemp(prefix="scena-riatt-"))
_DEST = _SCENA / "dist/metnos-public/executors/consult_frontier"
_DEST.parent.mkdir(parents=True)
shutil.copytree(ROOT / "executors/consult_frontier", _DEST)
import os  # noqa: E402

os.chdir(_SCENA)

_spec = importlib.util.spec_from_file_location(
    "oper_fixture", ROOT / "tests/runtime/executors/test_executor_birth_operational.py",
)
OP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OP)

GEN = "sha256:" + "a" * 64
SORGENTE = "sha256:" + "2" * 64
CTX = "sha256:" + "c" * 64


def _selezione():
    Sel = sys.modules[MODULE].ContextSelectionV1
    return Sel("sha256:" + "1" * 64, "3" * 64, CTX, "sha256:" + "e" * 64)


def _richiesta_v2(riferimento, sorgente=SORGENTE):
    return P.build_producer_request_v2(
        _selezione(), contract_id=riferimento.contract_id,
        generation_id=GEN, candidate_source_id=sorgente,
    )


def _legame(sorgente=SORGENTE):
    return ProducerReceiptBinding(
        "sha256:" + "7" * 64, sorgente,
        ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL,
    )


class Registratore:
    """Records which path was reached, and with what."""

    def __init__(self) -> None:
        self.v1_persist = self.v1_read = 0
        self.v2_persist = self.v2_read = 0
        self.richieste: list[object] = []

    def persist(self, current, encoded, expected):
        self.v1_persist += 1
        return encoded

    def read(self, current):
        self.v1_read += 1
        return None

    def persist_v2(self, current, encoded, expected, request):
        self.v2_persist += 1
        self.richieste.append(request)
        return encoded

    def read_v2(self, current, request):
        self.v2_read += 1
        self.richieste.append(request)
        return None


ESITI: list[tuple[str, bool, str]] = []


def caso(nome):
    def wrap(fn):
        base = Path(tempfile.mkdtemp(prefix="prova-riatt-"))
        try:
            original, birth = OP._fixture(base, lambda *a, **k: None)
            fn(birth, original, Registratore())
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
    except R.BirthReattestationError as exc:
        assert exc.code == codice, f"atteso {codice}, ottenuto {exc.code}"
        return
    raise AssertionError(f"nessun rifiuto: atteso {codice}")


def _corrente(originale):
    return CurrentGeneration(originale.manifest_ref, GEN)


def _nucleo(birth, reg, *, con_v2=True, solo_uno=None):
    kw = {}
    if con_v2:
        kw = {"persist_v2": reg.persist_v2, "read_v2": reg.read_v2}
    if solo_uno == "persist":
        kw = {"persist_v2": reg.persist_v2}
    if solo_uno == "read":
        kw = {"read_v2": reg.read_v2}
    return R._sealed_reattestation_core_for_test(
        birth=birth, capture=lambda c: None, persist=reg.persist,
        read_receipt=reg.read, **kw,
    )


@caso("1 senza richiesta sigillata si usa la via V1")
def _(birth, originale, reg) -> None:
    richiesta = R._reattestation_request_for_test(
        "sha256:" + "5" * 64, _corrente(originale), b"receipt", "attore", "motivo", _legame(),
    )
    nucleo = _nucleo(birth, reg)
    nucleo.read_existing(richiesta)
    nucleo.persist_receipt(richiesta, b"x", {})
    assert (reg.v1_read, reg.v1_persist) == (1, 1)
    assert (reg.v2_read, reg.v2_persist) == (0, 0)


@caso("2 con richiesta sigillata si usa la via V2, e SOLO quella")
def _(birth, originale, reg) -> None:
    r = _richiesta_v2(originale.manifest_ref)
    richiesta = R._sealed_reattestation_request_v2(
        _corrente(originale), b"receipt", "attore", "motivo", _legame(), r,
    )
    nucleo = _nucleo(birth, reg)
    nucleo.read_existing(richiesta)
    nucleo.persist_receipt(richiesta, b"x", {})
    assert (reg.v2_read, reg.v2_persist) == (1, 1)
    assert (reg.v1_read, reg.v1_persist) == (0, 0), "ha toccato la via storica"
    assert reg.richieste == [r, r], "la richiesta sigillata non e' arrivata intatta"


@caso("3 il costruttore V2 prende l'identita' dalla richiesta, non dal chiamante")
def _(birth, originale, reg) -> None:
    r = _richiesta_v2(originale.manifest_ref)
    richiesta = R._sealed_reattestation_request_v2(
        _corrente(originale), b"receipt", "attore", "motivo", _legame(), r,
    )
    assert richiesta.request_id == r.request_id


@caso("4 identita' discordi fra richiesta e sigillo sono rifiutate")
def _(birth, originale, reg) -> None:
    r = _richiesta_v2(originale.manifest_ref)
    _rifiuta(
        "birth_reattestation_request_invalid",
        lambda: R._reattestation_request_for_test(
            "sha256:" + "5" * 64, _corrente(originale), b"receipt", "attore", "motivo",
            _legame(), r,
        ),
    )


@caso("5 sorgente discorde fra legame e richiesta rifiutata")
def _(birth, originale, reg) -> None:
    r = _richiesta_v2(originale.manifest_ref, sorgente="sha256:" + "8" * 64)
    _rifiuta(
        "birth_reattestation_request_invalid",
        lambda: R._sealed_reattestation_request_v2(
            _corrente(originale), b"receipt", "attore", "motivo", _legame(), r,
        ),
    )


@caso("6 una richiesta forgiata non e' una richiesta sigillata")
def _(birth, originale, reg) -> None:
    class Sosia:
        request_id = "sha256:" + "5" * 64
        candidate_source_id = SORGENTE

    _rifiuta(
        "birth_reattestation_request_invalid",
        lambda: R._sealed_reattestation_request_v2(
            _corrente(originale), b"receipt", "attore", "motivo", _legame(), Sosia(),
        ),
    )


@caso("6-bis un sosia senza identita' e' rifiutato, non esplode")
def _(birth, originale, reg) -> None:
    # The constructor reads request_id before building.  Without its own type
    # check a sosia lacking that attribute would raise AttributeError instead
    # of a named refusal, which is a worse failure than a rejection.
    class Muto:
        candidate_source_id = SORGENTE

    _rifiuta(
        "birth_reattestation_request_invalid",
        lambda: R._sealed_reattestation_request_v2(
            _corrente(originale), b"receipt", "attore", "motivo", _legame(), Muto(),
        ),
    )


@caso("7 nucleo senza porta V2 e richiesta V2: fallisce, non ripiega")
def _(birth, originale, reg) -> None:
    r = _richiesta_v2(originale.manifest_ref)
    richiesta = R._sealed_reattestation_request_v2(
        _corrente(originale), b"receipt", "attore", "motivo", _legame(), r,
    )
    nucleo = _nucleo(birth, reg, con_v2=False)
    _rifiuta("birth_reattestation_v2_port_missing", lambda: nucleo.read_existing(richiesta))
    _rifiuta(
        "birth_reattestation_v2_port_missing",
        lambda: nucleo.persist_receipt(richiesta, b"x", {}),
    )
    assert (reg.v1_read, reg.v1_persist) == (0, 0), "e' ripiegato sulla via storica"


@caso("8 mezza porta V2 non e' una porta")
def _(birth, originale, reg) -> None:
    for meta in ("persist", "read"):
        _rifiuta(
            "birth_reattestation_core_invalid",
            lambda m=meta: _nucleo(birth, reg, solo_uno=m),
        )


def main() -> int:
    for nome, ok, dettaglio in ESITI:
        print(f"  {'ok  ' if ok else 'ROSSO'}  {nome}")
        if not ok:
            print(f"          {dettaglio}")
    rossi = [n for n, ok, _ in ESITI if not ok]
    print(f"\nESITO: {'tutte verdi' if not rossi else f'{len(rossi)} rosse'}  ({len(ESITI)} casi)")
    os.chdir(ROOT)
    shutil.rmtree(_SCENA, ignore_errors=True)
    return 1 if rossi else 0


if __name__ == "__main__":
    raise SystemExit(main())

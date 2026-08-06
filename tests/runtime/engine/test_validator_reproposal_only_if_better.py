"""La ri-proposta del Validator si accetta solo se MIGLIORA (6/8/2026).

Quando il Validator boccia un piano, il motore ne chiede un altro all'LLM. La
richiesta e' CIECA: esclude l'impronta del piano fallito, non dice che cosa non
andava. Il piano che torna puo' quindi essere semplicemente DIVERSO — e
peggiore — e fino a oggi veniva accettato comunque.

Misurato il 6/8 su «trova i file .md nella cartella X e leggili»: il piano
grezzo (find_files + read_files) era corretto, il Validator lo bocciava per un
difetto suo, e il piano rifatto sceglieva `find_files_hash` — il cercatore di
DUPLICATI — chiudendo il turno con «Nessun risultato trovato» e due step
ok=True. Chiuso il difetto del Validator, resta questo: uno scambio al buio.

Il criterio e' quello che il re-propose dei verbi scoperti usa gia' poche righe
piu' su: si sostituisce solo se il conto degli errori scende.
"""
from __future__ import annotations

import pytest

from engine import dispatch, proposer as proposer_mod
from engine.types import Framework, Intent, StepSpec


class _StubExec:
    def __init__(self, name: str, args_schema: dict) -> None:
        self.name = name
        self.args_schema = args_schema
        self.affinity: list = []


_CATALOGO = [
    _StubExec("find_files", {
        "type": "object", "required": ["base_path"],
        "properties": {"base_path": {"type": "string"}}}),
    _StubExec("read_files", {
        "type": "object", "required": [],
        "requires_one_of": [["paths", "entries"]],
        "properties": {"paths": {"type": "array"},
                       "entries": {"type": "array"},
                       "from_step": {"type": "integer"}}}),
    _StubExec("find_files_hash", {
        "type": "object", "required": ["base_path"],
        "properties": {"base_path": {"type": "string"}}}),
]


def _piano(*passi) -> Framework:
    return Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in passi]
                     + [StepSpec(tool="final_answer", args={})])


_INVALIDO = (("find_files", {"base_path": "/x"}), ("read_files", {}))
_PEGGIORE = (("find_files_hash", {}), ("read_files", {}))
_VALIDO = (("find_files", {"base_path": "/x"}), ("read_files", {"from_step": 1}))


class _ProposerStub:
    """Primo piano alla proposta, secondo alla ri-proposta."""

    def __init__(self, primo, secondo) -> None:
        self._piani = [primo, secondo]
        self.chiamate = 0

    def propose(self, **_kw):
        piano = self._piani[min(self.chiamate, len(self._piani) - 1)]
        self.chiamate += 1
        return _piano(*piano)


def _esegui(monkeypatch, primo, secondo, query: str) -> list:
    # Query DIVERSA per test: L0 registra da sola il primo turno riuscito, e
    # un secondo test con lo stesso testo verrebbe servito dalla cache senza
    # mai chiamare il proposer.
    stub = _ProposerStub(primo, secondo)
    monkeypatch.setattr(proposer_mod, "get_proposer", lambda: stub)
    invocati: list = []

    def invoke(tool, args):
        invocati.append(tool)
        return {"ok": True, "entries": []}

    dispatch.run_turn(
        query=query,
        intent=Intent(verb="find", object="files",
                      actions=[{"verb": "find", "object": "files"},
                               {"verb": "read", "object": "files"}]),
        catalog=_CATALOGO, invoke_executor_cb=invoke,
        llm_call_wise=lambda *a, **k: "", llm_call_fast=lambda *a, **k: "")
    assert stub.chiamate >= 2, "la ri-proposta non e' stata chiesta"
    return invocati


def test_una_riproposta_non_migliore_viene_scartata(monkeypatch) -> None:
    invocati = _esegui(monkeypatch, _INVALIDO, _PEGGIORE,
                       "trova i file .md nella cartella /x e leggili")
    assert "find_files" in invocati
    assert "find_files_hash" not in invocati, \
        "un piano diverso e non migliore ha sostituito l'originale"


def test_una_riproposta_valida_sostituisce_il_piano(monkeypatch) -> None:
    """Il meccanismo serve: quando il rifacimento risolve davvero, vince lui."""
    invocati = _esegui(monkeypatch, _INVALIDO, _VALIDO,
                       "trova i documenti .txt nella cartella /y e leggili")
    assert invocati[:2] == ["find_files", "read_files"]


@pytest.mark.parametrize("piano", [_VALIDO])
def test_un_piano_valido_non_scomoda_l_llm(monkeypatch, piano) -> None:
    stub = _ProposerStub(piano, piano)
    monkeypatch.setattr(proposer_mod, "get_proposer", lambda: stub)
    dispatch.run_turn(
        query="trova i registri .log nella cartella /z e leggili",
        intent=Intent(verb="find", object="files"),
        catalog=_CATALOGO,
        invoke_executor_cb=lambda tool, args: {"ok": True, "entries": []},
        llm_call_wise=lambda *a, **k: "", llm_call_fast=lambda *a, **k: "")
    assert stub.chiamate == 1, "ri-proposta chiesta su un piano gia' valido"

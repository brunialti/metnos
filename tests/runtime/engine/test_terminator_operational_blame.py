"""Un guasto del servizio non si fa riparare all'utente (6/8/2026).

Turno reale `71d4ac52`: `open_sites` fallisce con `sidecar_contract_mismatch` —
il sidecar Playwright girava dal 2/8 e un modulo di confine era cambiato, quindi
la sua impronta non combaciava piu' — e la risposta all'utente e' stata «Non
posso risolvere: Operazione fallita: open_sites. Per procedere: **Aggiungi
dettagli concreti (percorso, nome, periodo)**». Cioe': un processo da riavviare,
presentato come una richiesta scritta male.

La classe c'era gia' («sidecar_down», «schema_too_old» stanno in
`OPERATIONAL_ERROR_CLASSES` da sempre); mancava solo questa. Il test non guarda
il caso: guarda la PROPRIETA' — nessun guasto operativo, presente o futuro, puo'
finire nell'azione che chiede dettagli all'utente.
"""
from __future__ import annotations

import pytest

from engine.terminator import SimpleTerminator
from engine.types import OPERATIONAL_ERROR_CLASSES, Intent, RunResult, StepRun


def _run_fallito(classe: str) -> RunResult:
    return RunResult(
        steps=[StepRun(step_idx=1, tool="open_sites", args={},
                       result={"ok": False, "error_class": classe,
                               "error": "Operazione fallita: open_sites."},
                       ok=False, latency_ms=1)],
        final_kind="error", ok_count=0)


@pytest.mark.parametrize("classe", sorted(OPERATIONAL_ERROR_CLASSES))
def test_un_guasto_operativo_non_chiede_dettagli_all_utente(classe) -> None:
    from messages import get as _msg

    risposta = SimpleTerminator().explain(
        query="apri il sito", intent=Intent(verb="open", object="sites"),
        failed_run=_run_fallito(classe), error_class="wrong_args")
    assert _msg("MSG_TERM_WRONG_ARGS_ACTION") not in risposta.final_text, (
        f"{classe}: guasto del servizio presentato come richiesta da correggere")


def test_il_contratto_del_sidecar_e_un_guasto_operativo() -> None:
    """Regressione puntuale del turno 71d4ac52: la classe deve stare
    nell'insieme, altrimenti anche il resto della catena (recovery compresa)
    tratta un processo stantio come un piano da rifare."""
    assert "sidecar_contract_mismatch" in OPERATIONAL_ERROR_CLASSES

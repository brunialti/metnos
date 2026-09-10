"""Il consenso e' una precondizione: non conta come il passo che naviga.

Turno reale `bbd60df5` (10/9/2026). La richiesta diceva «apri il sito, se
chiede quali cookie clicca su solo necessari, fai login e mostrami tutte le
fatture». Il pianificatore ha emesso un `act_sites` per la clausola dei cookie,
e quel passo — che non ha piu' un controllo da colpire, perche' il consenso e'
gia' risolto dal broker prima di ogni azione — ha occupato il posto della
navigazione: il piano non ha piu' reclutato il passo che sarebbe andato alle
fatture, e la lettura e' finita sulla pagina che il login aveva lasciato
aperta. La risposta elencava i movimenti recenti.

E' la stessa regola gia' applicata dentro il broker, portata dove il piano si
forma: un'azione che nomina il consenso non e' una navigazione. Il
riconoscimento e' quello del resolver, cosi' pianificatore ed executor
rispondono alla stessa domanda allo stesso modo.
"""
from __future__ import annotations

from engine import dispatch
from engine.types import Framework, Intent, StepSpec

QUERY = ("apri il sito x.test, se il sito chiede quali cookies clicca su solo "
         "necessari, fai login e mostrami tutte le fatture")


def _piano(*azioni: str) -> Framework:
    passi = [StepSpec(tool="open_sites", args={"urls": ["https://x.test"]}),
             StepSpec(tool="login_sites", args={"session_ids": ["s1"]})]
    passi += [StepSpec(tool="act_sites", args={"action": a,
                                               "session_ids": ["s1"]})
              for a in azioni]
    passi += [StepSpec(tool="read_sites", args={"session_ids": ["s1"]}),
              StepSpec(tool="final_answer", args={})]
    return Framework(steps=passi)


def _azioni(framework: Framework) -> list[str]:
    return [str((s.args or {}).get("action") or "")
            for s in framework.steps if s.tool == "act_sites"]


def _riscrivi(framework: Framework) -> Framework:
    return dispatch._ensure_site_session_precursor(
        framework, Intent(verb="read", object="sites"), QUERY, None)


def test_una_clausola_di_consenso_non_sostituisce_la_navigazione() -> None:
    azioni = _azioni(_riscrivi(_piano("accetta cookie solo necessari")))
    assert any("fatture" in a for a in azioni), azioni


def test_una_navigazione_vera_resta_l_unica_e_non_viene_duplicata() -> None:
    """Se il piano naviga gia', non se ne aggiunge una seconda."""
    azioni = _azioni(_riscrivi(_piano("vai alle fatture")))
    assert azioni == ["vai alle fatture"]


def test_consenso_e_navigazione_insieme_restano_due() -> None:
    azioni = _azioni(_riscrivi(
        _piano("accetta cookie solo necessari", "vai alle fatture")))
    assert azioni == ["accetta cookie solo necessari", "vai alle fatture"]

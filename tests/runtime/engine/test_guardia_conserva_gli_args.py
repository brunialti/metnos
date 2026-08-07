"""A guard that rebuilds a step must not drop what the planner declared.

`ensure_site_session_precursor` rebuilds the `act_sites` steps of a plan so the
session arrives through `from_step` instead of a literal id. Rebuilding used to
copy a CLOSED list of three arguments, so every field added to the executor
after that line was written was discarded here, in silence: the planner
declared it, the manifest documented it, the executor never saw it.

Measured on 2026-08-08 with `ambito`, and it had been true of `done_when` since
the day before — the goal went back to being one string, which is precisely the
shape the structured goal exists to leave behind.

The property does not name any field: whatever the planner declares survives,
and only the way the session arrives is replaced.
"""
from __future__ import annotations

from engine import dispatch
from engine.types import Framework, Intent, StepSpec


def _piano_con_navigazione(**args_extra) -> Framework:
    return Framework(steps=[
        StepSpec(tool="open_sites", args={"urls": ["https://x.test"]}),
        StepSpec(tool="login_sites", args={"session_ids": ["s1"]}),
        StepSpec(tool="act_sites", args={"action": "le fatture",
                                         "session_ids": ["s1"], **args_extra}),
        StepSpec(tool="final_answer", args={}),
    ])


def _act(framework: Framework) -> dict:
    passo = next(s for s in framework.steps if s.tool == "act_sites")
    return dict(passo.args or {})


def test_conserva_i_campi_del_fine_strutturato() -> None:
    framework = dispatch._ensure_site_session_precursor(
        _piano_con_navigazione(ambito="personale",
                               done_when="elenco con date"),
        Intent(verb="read", object="sites"),
        "mostrami le fatture sul sito", None)
    args = _act(framework)
    assert args.get("ambito") == "personale"
    assert args.get("done_when") == "elenco con date"


def test_la_sessione_arriva_dal_passo_precedente() -> None:
    """The one substitution the rebuild is there to make."""
    framework = dispatch._ensure_site_session_precursor(
        _piano_con_navigazione(), Intent(verb="read", object="sites"),
        "mostrami le fatture sul sito", None)
    args = _act(framework)
    assert "from_step" in args and "session_ids" not in args
    assert args.get("action") == "le fatture"

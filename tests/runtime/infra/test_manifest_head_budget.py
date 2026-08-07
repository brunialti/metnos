"""A manifest head that does not fit is truncated in silence.

The head of a manifest IS the tool's prompt: it is what the planner reads to
decide whether to pick the tool and how to call it. `render_head` caps it at
`RENDER_BUDGET` and cuts at a word boundary, so anything past the budget is
dropped — no error, no failing test, and a rate-limited warning in a log
nobody reads while writing a manifest.

The author has no way of noticing. Measured 2026-08-08: two heads were losing
the tail of their `NON:` chapter, which is exactly the part that keeps
sibling tools apart ("removing a preference is delete_preferences"). Whoever
wrote them believed the disambiguation was in place.

Note what is NOT a defect: the `OUT:` chapter is cut ON PURPOSE — it describes
the output shape for consumers, not for the planner — so a head that only
exceeds the budget once `OUT:` is counted is fine. What must fit is the part
the planner is meant to read.
"""
from __future__ import annotations

import pytest

from loader import load_catalog
from manifest_rules import RENDER_BUDGET, render_head


def _testa_utile(desc: str) -> str:
    """The part meant for the planner: everything before `OUT:`."""
    desc = str(desc or "").strip().replace("\n", " ")
    taglio = desc.find("OUT:")
    return (desc[:taglio] if taglio > 0 else desc).rstrip()


def _catalogo_a_capitoli():
    for executor in load_catalog():
        desc = str(getattr(executor, "description", "") or "")
        if "PATTERN:" in desc:
            yield getattr(executor, "name", "?"), desc


def test_nessuna_testa_perde_testo_destinato_al_pianificatore() -> None:
    perse = []
    for nome, desc in _catalogo_a_capitoli():
        utile = _testa_utile(desc)
        reso = render_head(desc).rstrip()
        if len(reso) < len(utile):
            perse.append((nome, len(utile), utile[len(reso):][:70]))
    assert not perse, (
        "teste troncate in silenzio (budget "
        f"{RENDER_BUDGET}): " + "; ".join(
            f"{nome} perde {len(utile) if isinstance(utile,str) else utile} "
            f"caratteri -> {perso!r}" for nome, utile, perso in perse))


def test_il_catalogo_non_e_vuoto() -> None:
    """Guard against a green run that measured nothing."""
    assert sum(1 for _ in _catalogo_a_capitoli()) > 20


@pytest.mark.parametrize("desc,atteso", [
    # `OUT:` is cut on purpose: a head long only because of it is fine.
    ("SCOPO: x. PATTERN: y(). NON: z. OUT: " + "q" * 500, True),
    # Losing the tail of NON: is not.
    ("SCOPO: x. PATTERN: y(). NON: " + "q" * 400 + ". OUT: r.", False),
])
def test_il_criterio_distingue_il_taglio_voluto_da_quello_subito(
        desc, atteso) -> None:
    intera = len(render_head(desc).rstrip()) >= len(_testa_utile(desc))
    assert intera is atteso


def test_il_generatore_rifiuta_una_testa_che_non_entra() -> None:
    """The same rule at the moment a manifest is WRITTEN, not only in review.

    A policy test protects the catalog that exists; synth writes new manifests
    on its own, and without a gate at generation it would keep producing the
    same silent truncation for every executor it makes. Asked for by the owner
    on 2026-08-08.
    """
    import executor_standard
    import generated_executor_contract as contract

    politica = "\n".join(
        f"{chiave} = " + ("true" if valore is True else
                          "false" if valore is False else repr(valore))
        for chiave, valore in contract.DEFAULT_EXECUTION_POLICY.items())
    modello = ('manifest_format = "1.0"\n'
               f'executor_standard = "{executor_standard.STANDARD_ID}"\n'
               'lifecycle = "active"\n[execution]\n' + politica +
               '\n[description]\nit = "%s"\n')

    # Cutting `OUT:` is deliberate: a head long only because of it is fine.
    contract.validate_generated_manifest_text(
        modello % ("SCOPO: x. PATTERN: y(). NON: z. OUT: " + "q" * 400))

    with pytest.raises(contract.GeneratedContractError, match="budget"):
        contract.validate_generated_manifest_text(
            modello % ("SCOPO: x. PATTERN: y(). NON: " + "q" * 400 + ". OUT: r."))

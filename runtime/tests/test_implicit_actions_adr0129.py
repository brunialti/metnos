"""Smoke ADR 0129 — pattern intent-implicit.

Verifica `vocab.detect_implicit_actions` su un corpus di query reali IT+EN.
Determinismo §7.9: niente LLM, solo lookup tabellare. Threshold:
  - auto:  conf >= 0.85
  - ask:   0.60 <= conf < 0.85
  - skip:  conf < 0.60 (entry non emessa)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vocab import detect_implicit_actions  # noqa: E402


# (query, expected_verbs, expected_strategy_or_None)
# expected_verbs = lista di verb canonici attesi (in ordine), [] se nessuno
CASES = [
    # Caso ADR 0129 turn live (c_mp1adlnc_7w04y4): pipeline propose+notify
    (
        "proponi per la prossima settimana 3 possibili orari per un appuntamento "
        "di una ora la mattina, dopo la scelta mandami una email con la scelta",
        ["create_events"],
        "auto",
    ),
    # Pipeline esplicita (2 mutating verbs + 2 nouns) — nessuna implicit
    ("crea evento domani e mandami una mail", [], None),
    ("manda mail a luca e fissa riunione con marco", [], None),
    ("schedule a meeting tomorrow and send me an email", [], None),
    # Mutating coverage gia presente sull'object — nessuna implicit
    ("cancella la mail di ieri", [], None),
    ("cancella tutti gli eventi di domani e mandami una mail", [], None),
    # Single-purpose senza mutating verb — fuori scope (ritorno [])
    ("proponi appuntamento mercoledi", [], None),
    ("appuntamento mercoledi alle 18 col dottore", [], None),
    # Read-only single object — niente
    ("cerca file pdf", [], None),
    ("trovami foto al mare", [], None),
    # 2 nouns, 1 mutating (orfano = events)
    (
        "proponi un appuntamento e mandami un riassunto via email",
        ["create_events"],
        "auto",
    ),
    (
        "manda mail a luca e proponi un appuntamento per giovedi",
        ["create_events"],
        None,  # ask o auto a seconda della heuristica producer_principal
    ),
    # EN variant — pipeline incompleta calendar
    (
        "propose 3 slots next week for an appointment and send me the chosen one by email",
        ["create_events"],
        "auto",
    ),
]


@pytest.mark.parametrize("query,expected_verbs,expected_strategy", CASES)
def test_detect_implicit_actions(query, expected_verbs, expected_strategy):
    out = detect_implicit_actions(query)
    got_verbs = [e["verb"] for e in out]
    assert got_verbs == expected_verbs, (
        f"verbs mismatch on {query!r}: got {got_verbs} expected {expected_verbs}"
    )
    if expected_strategy and out:
        assert out[0]["strategy"] == expected_strategy, (
            f"strategy mismatch on {query!r}: "
            f"got {out[0]['strategy']} expected {expected_strategy}"
        )


def test_payload_shape():
    out = detect_implicit_actions(
        "proponi appuntamento e mandami mail"
    )
    assert out, "expected at least 1 implicit action"
    e = out[0]
    for field in ("verb", "object", "noun_token", "verb_canonical",
                  "confidence", "strategy", "rationale"):
        assert field in e, f"missing field {field} in {e}"
    assert isinstance(e["confidence"], float)
    assert 0.0 <= e["confidence"] <= 1.0
    assert e["strategy"] in ("auto", "ask")  # skip = entry non emessa


def test_empty_query():
    assert detect_implicit_actions("") == []
    assert detect_implicit_actions(None) == []  # type: ignore[arg-type]


def test_undo_query_not_polluted():
    # Query di undo non devono emettere implicit_actions
    assert detect_implicit_actions("annulla l'ultima operazione") == []
    assert detect_implicit_actions("undo last turn") == []

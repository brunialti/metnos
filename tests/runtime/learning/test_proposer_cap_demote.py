"""test_proposer_cap_demote — l'LLM proposer non deve stringere un cap di
conteggio quando l'utente non ha indicato una quantità.

Bug live (turn 4648c5c3): «riassumi i file .md su github nel repo
brunialti/metnos» → il proposer mette `find_files_github(top_k=10)` → ne
considera 10 su 16 e annuncia «troppi per me in un solo passaggio: ne
considero 10». Niente nella query chiede 10: il cap è allucinato.

§2.1 «cap superiore = parametro ESPLICITO» + §7.9 (deterministico > LLM): senza
una quantità nella clausola il cap del proposer va tolto, così l'executor usa il
suo default (deliberato, più generoso). Questi test difendono la REGOLA generale
(ogni executor con cap), non il singolo caso github.
"""
import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")

from engine.dispatch import (  # noqa: E402
    _clause_requests_count, _demote_overtight_caps)

# Schemi minimi modellati sui manifest reali (default deliberati degli autori).
_GH = {"properties": {"top_k": {"type": "integer", "default": 500}}}
_IMG = {"properties": {"top_k": {"type": "integer", "default": 100}}}
_FILES = {"properties": {"max_results": {"type": "integer", "default": 1000}}}
_NODEF = {"properties": {"max_results": {"type": "integer"}}}


def _demoted(args, schema, clause):
    a = dict(args)
    _demote_overtight_caps(a, schema, clause)
    return a


# ── rilevatore di quantità ──────────────────────────────────────────────────

def test_no_quantity_clauses():
    for q in ("riassumi i file .md su github nel repo brunialti/metnos",
              "riassumi i file .md del 2026",         # anno, non un cap
              "elenca i file",
              "trova foto simili a questa",
              "i file modificati negli ultimi 7 giorni",  # finestra temporale
              "le ultime 24 ore di mail",                 # finestra temporale
              "ultime 2 settimane"):
        assert _clause_requests_count(q) is False, q


def test_explicit_quantity_clauses():
    for q in ("i primi 10 file .md", "primi dieci file", "solo 5 foto di Roma",
              "top 3 link", "first 5 files", "ultimi 20 messaggi non letti",
              "at most 15 results", "leggi le 3 mail di ieri",
              "i primi 100 file python"):
        assert _clause_requests_count(q) is True, q


# ── demote del cap ──────────────────────────────────────────────────────────

def test_strips_hallucinated_cap_below_default():
    # Il bug: top_k=10 su richiesta senza quantità → tolto (default 500 vince).
    assert _demoted({"repo": "x", "top_k": 10}, _GH,
                    "riassumi i file .md") == {"repo": "x"}
    assert _demoted({"max_results": 10}, _FILES, "elenca i file") == {}


def test_keeps_cap_when_user_gave_quantity():
    assert _demoted({"repo": "x", "top_k": 10}, _GH,
                    "i primi 10 file") == {"repo": "x", "top_k": 10}


def test_no_regression_retrieval_default():
    # find_images_indices: cap = default → resta; >default → resta (più
    # inclusivo); <default senza quantità → portato al default.
    assert _demoted({"top_k": 100}, _IMG, "trova foto simili") == {"top_k": 100}
    assert _demoted({"top_k": 150}, _IMG, "trova foto simili") == {"top_k": 150}
    assert _demoted({"top_k": 10}, _IMG, "trova foto simili") == {}


def test_unlimited_and_missing_default_are_left_alone():
    # 0 = illimitato (già massimo) → mai toccato.
    assert _demoted({"top_k": 0}, _GH, "elenca i file") == {"top_k": 0}
    # default non dichiarato → conservativo, non si può provare che sia più
    # inclusivo → si lascia il valore del proposer.
    assert _demoted({"max_results": 10}, _NODEF,
                    "elenca i file") == {"max_results": 10}


def test_non_integer_values_ignored():
    # bool non è un cap numerico; valori non-int restano.
    assert _demoted({"top_k": True}, _GH, "elenca") == {"top_k": True}
    assert _demoted({"top_k": "all"}, _GH, "elenca") == {"top_k": "all"}

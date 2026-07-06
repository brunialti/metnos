"""Bug 6/7: il proposer emetteva {names:["Mario"], all:true} → «cancella
Mario» PURGAVA tutto il registro (all vinceva su names). Difesa §2.9:
target esplicito + all = mutually-exclusive (mai delete più ampio del
richiesto). DB persons ISOLATO — MAI il registro reale.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "runtime"))
sys.path.insert(0, str(_REPO / "executors" / "delete_persons"))

import delete_persons  # noqa: E402


def test_all_plus_names_target_wins():
    # bug 6/7: all spurio NON purga — vince il target esplicito.
    targets, err = delete_persons._coalesce_targets(
        {"names": ["Mario"], "all": True})
    assert targets == ["Mario"] and err is None


def test_all_plus_name_singular_target_wins():
    targets, err = delete_persons._coalesce_targets(
        {"name": "Mario", "all": True})
    assert targets == ["Mario"] and err is None


def test_all_plus_entries_target_wins():
    targets, err = delete_persons._coalesce_targets(
        {"entries": [{"name": "Mario"}], "all": True})
    assert targets == ["Mario"] and err is None


def test_all_alone_is_purge_sentinel():
    targets, err = delete_persons._coalesce_targets({"all": True})
    assert targets == ["__ALL__"] and err is None


def test_names_alone_unchanged():
    targets, err = delete_persons._coalesce_targets({"names": ["Mario", "Lu"]})
    assert targets == ["Mario", "Lu"] and err is None

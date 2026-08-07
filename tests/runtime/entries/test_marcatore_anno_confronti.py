"""The assumed-year mark must not change what a comparison means.

A date whose year the source never showed comes back as `*2026-05-29`: the
mark says the year was assumed, not read. It is a statement ABOUT the date,
not part of it — so anything that reads the value AS a date has to look
underneath.

Found by rereading, not by the suite: `filter_entries._parse_iso_to_epoch`
raised on the mark and returned None, so a filter on a period dropped in
silence exactly the rows the mark existed to save — the recent ones, which are
precisely those a site writes without the year.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _executor(nome: str):
    sys.path.insert(0, str(_ROOT / "runtime"))
    spec = importlib.util.spec_from_file_location(
        f"{nome}_under_test", _ROOT / "executors" / nome / f"{nome}.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def filtro():
    return _executor("filter_entries")


@pytest.fixture(scope="module")
def ordina():
    return _executor("sort_entries")


def test_la_data_marcata_si_legge_come_data(filtro) -> None:
    assert (filtro._parse_iso_to_epoch("*2026-05-29")
            == filtro._parse_iso_to_epoch("2026-05-29"))


def test_un_valore_senza_marcatore_non_cambia(filtro) -> None:
    assert filtro._parse_iso_to_epoch("2026-05-29") is not None
    assert filtro._parse_iso_to_epoch("non una data") is None
    assert filtro._parse_iso_to_epoch("") is None


def test_il_marcatore_non_sposta_l_ordinamento(ordina) -> None:
    """Sorted as text, `*2026-...` would come before every unmarked date."""
    entries = [{"data": "2025-01-01"}, {"data": "*2026-05-29"},
               {"data": "2026-01-01"}]
    esito = ordina.invoke({"entries": entries, "by": "data",
                           "value_type": "date"})
    ordinate = [e["data"] for e in esito["entries"]]
    assert ordinate == ["2025-01-01", "2026-01-01", "*2026-05-29"]


def test_una_sola_definizione_del_marcatore() -> None:
    """Two definitions of the same convention drift apart in a month."""
    import executor_helpers
    import extract_entries

    assert (extract_entries._ASSUMED_YEAR_MARK
            is executor_helpers.ASSUMED_YEAR_MARK)


def test_il_lettore_lascia_stare_cio_che_non_e_marcato() -> None:
    from executor_helpers import date_text

    assert date_text("2026-05-29") == "2026-05-29"
    assert date_text("*2026-05-29") == "2026-05-29"
    assert date_text(None) is None
    assert date_text(1234) == 1234

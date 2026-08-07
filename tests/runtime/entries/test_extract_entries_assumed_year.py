"""A visible date never becomes an empty field (2026-08-07).

Sites commonly omit the year on recent rows and print it on the old ones
("5 nov - 7 nov" next to "30 dic 2025 - 4 gen 2026"). Asked for ISO 8601, the
local model dealt with the yearless rows in the two worst possible ways: it
either left the field empty — claiming "no date" about a row that showed one —
or filled in a year of its own invention. Measured on a real bookings page: the
three most recent rows came back blank, and those were exactly the ones the
request was about.

Owner's decision: fall back to the CURRENT year and prefix an asterisk to say
the year was assumed, not read; leave the field empty only when no date can be
derived at all. The interpretation is the model's (no month-name lexicon in
here); what this file pins is the contract around it.
"""
from __future__ import annotations

from datetime import date

import pytest

import extract_entries as ee


@pytest.mark.parametrize("value,expected", [
    ("*2026-11-05", "*2026-11-05"),          # already ISO under the marker
    ("*05/11/2026", "*2026-11-05"),          # normalised, marker kept
    ("2026-11-05", "2026-11-05"),            # a read year carries no marker
    ("", ""),                                 # nothing derivable stays empty
])
def test_il_marcatore_sopravvive_alla_normalizzazione(value, expected) -> None:
    assert ee._normalize_extracted_date("checkin_date", value) == expected


def test_il_marcatore_non_tocca_i_campi_non_data() -> None:
    """`*` is a date marker, not a general prefix: elsewhere it is content."""
    assert ee._normalize_extracted_date("destination", "*Rimini") == "*Rimini"


def test_un_marcatore_senza_data_non_diventa_un_valore() -> None:
    assert ee._normalize_extracted_date("checkin_date", "*") == ""


def test_il_prompt_dichiara_l_anno_corrente() -> None:
    """The model cannot know today's date: the fallback year is supplied.

    Without it the rule would be unusable and the model would go back to
    inventing a year or blanking the field.
    """
    prompt = ee._build_prompt(["destination", "checkin_date"], "", 100)
    assert str(date.today().year) in prompt
    assert "*" in prompt

"""Same page, same request, one answer (2026-08-07).

Turns 558b0e9f and 771d5a78 returned the very same bookings from the very same
site, and looked like two different products: one table had English column
names and ISO dates, the other Italian names and raw literals ("7 nov").

One defect, two symptoms. The field names are invented by the model, which
picked a language at random; and the date policy is keyed off those names, so
`data_inizio` — which also matches the datetime pattern through "inizio" —
took the datetime branch and never saw the date-only rules, the assumed-year
fallback among them.

The properties: a name that states its GRANULARITY (date) wins over one that
states its ROLE (start/end), and the inferred names are written in the
language of the instance, because they are the columns the user reads (§7.13).
"""
from __future__ import annotations

import pytest

import extract_entries as ee


@pytest.mark.parametrize("field", [
    "checkin_date", "checkout_date",     # role in English + explicit "date"
    "data_inizio", "data_fine",          # role in Italian + explicit "data"
    "date", "data",
])
def test_un_campo_che_dichiara_la_granularita_e_una_data(field) -> None:
    prompt = ee._build_prompt(["destination", field], "", 100)
    assert field in _regola_data(prompt), (
        f"{field} non ha ricevuto le regole dei campi DATA")


@pytest.mark.parametrize("field", ["start", "end", "when", "inizio", "fine"])
def test_un_ruolo_senza_granularita_resta_data_ora(field) -> None:
    prompt = ee._build_prompt(["destination", field], "", 100)
    assert field not in _regola_data(prompt)


def test_i_due_nomi_della_stessa_cosa_ricevono_la_stessa_regola() -> None:
    """The heart of the divergence: same thing, two languages, one behaviour."""
    inglese = _regola_data(ee._build_prompt(["checkin_date"], "", 100))
    italiano = _regola_data(ee._build_prompt(["data_inizio"], "", 100))
    assert bool(inglese) == bool(italiano) is True


def _regola_data(prompt: str) -> str:
    """The chunk of prompt that carries the date-only rules, if rendered."""
    for riga in prompt.splitlines():
        if "YYYY-MM-DD" in riga:
            return riga
    return ""


def test_il_prompt_dello_schema_fissa_la_lingua_dei_campi() -> None:
    """Without this the same request yields Italian or English columns at
    random: the names are user-facing, so they follow the instance language."""
    import prompt_loader

    it = prompt_loader.get("extract_entries_schema", "it",
                           max_fields=8, instruction="")
    en = prompt_loader.get("extract_entries_schema", "en",
                           max_fields=8, instruction="")
    assert "ITALIANO" in it and "destinazione" in it
    assert "ENGLISH" in en and "destination" in en

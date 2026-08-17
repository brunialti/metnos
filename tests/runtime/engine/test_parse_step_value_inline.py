"""Test inline del parser modulo-level `parse_step_value` (ADR 0090).

Garantisce che il parser viva al modulo, non solo come metodo della
ChannelDaemon, e che gestisca correttamente tutti i kind.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def test_yes_no_tolerant():
    from channels.daemon import parse_step_value
    assert parse_step_value("si", {"kind": "yes_no"}) == (True, True, "")
    assert parse_step_value("Sì", {"kind": "yes_no"}) == (True, True, "")
    assert parse_step_value("no", {"kind": "yes_no"}) == (True, False, "")
    ok, _, err = parse_step_value("forse", {"kind": "yes_no"})
    assert ok is False
    assert "sì" in err.lower() or "no" in err.lower()


def test_number_int_vs_float():
    from channels.daemon import parse_step_value
    ok, val, _ = parse_step_value("42", {"kind": "number"})
    assert ok and val == 42 and isinstance(val, int)
    ok, val, _ = parse_step_value("3.14", {"kind": "number"})
    assert ok and val == 3.14 and isinstance(val, float)
    ok, _, err = parse_step_value("abc", {"kind": "number"})
    assert ok is False


def test_date_iso_and_eu():
    from channels.daemon import parse_step_value
    assert parse_step_value("2026-05-04", {"kind": "date"}) == (True, "2026-05-04", "")
    ok, val, _ = parse_step_value("04/05/2026", {"kind": "date"})
    assert ok and val == "2026-05-04"
    ok, _, _ = parse_step_value("ieri", {"kind": "date"})
    assert ok is False


def test_choice_by_value_or_index():
    from channels.daemon import parse_step_value
    schema = {"kind": "choice", "choices": ["alpha", "beta", "gamma"]}
    assert parse_step_value("beta", schema) == (True, "beta", "")
    assert parse_step_value("BETA", schema) == (True, "beta", "")
    assert parse_step_value("2", schema) == (True, "beta", "")
    ok, _, err = parse_step_value("delta", schema)
    assert ok is False
    assert "alpha" in err and "beta" in err


def test_multi_choice_csv():
    from channels.daemon import parse_step_value
    schema = {"kind": "multi_choice", "choices": ["a", "b", "c", "d"]}
    ok, val, _ = parse_step_value("a, c", schema)
    assert ok and val == ["a", "c"]
    ok, val, _ = parse_step_value("1,4", schema)
    assert ok and val == ["a", "d"]


def test_empty_response_rejected():
    from channels.daemon import parse_step_value
    ok, _, err = parse_step_value("", {"kind": "text"})
    assert ok is False
    assert "vuota" in err.lower()


def test_text_credentials_passthrough():
    from channels.daemon import parse_step_value
    assert parse_step_value("hello", {"kind": "text"}) == (True, "hello", "")
    assert parse_step_value("hunter2", {"kind": "credentials"}) == (True, "hunter2", "")


def test_yes_no_legge_le_forme_dal_lessico(monkeypatch):
    """Autorita' unica: il parser di dialogo NON deve tenere un proprio elenco.

    La prova non e' che accetti le forme di oggi — le accetterebbe anche una
    lista congelata nel codice, ed e' esattamente il modo in cui questo test
    passava pur non verificando nulla (mutante M3b della revisione 16/8).
    La prova e' che SEGUA il lessico: si sostituisce il lessico a runtime e il
    parser deve cambiare comportamento senza che nessuno tocchi `daemon.py`.

    I letterali tecnici (`true`/`1`/`false`/`0`) restano nel codice perche'
    non sono lingua e non si traducono.

    Uguaglianza esatta, non contenimento: la risposta a un passo di dialogo
    e' una parola, e una frase intera che contiene «si» non e' una conferma.
    """
    from channels import daemon as _daemon
    from channels.daemon import parse_step_value
    import detection_lexicon as _dl

    for forma in _dl.forms("confirm.yes"):
        assert parse_step_value(forma, {"kind": "yes_no"})[:2] == (True, True), forma
    for forma in _dl.forms("confirm.no"):
        assert parse_step_value(forma, {"kind": "yes_no"})[:2] == (True, False), forma
    for tecnico, atteso in (("true", True), ("1", True),
                            ("false", False), ("0", False)):
        assert parse_step_value(tecnico, {"kind": "yes_no"})[:2] == (True, atteso)
    assert parse_step_value("va bene si", {"kind": "yes_no"})[0] is False

    # La prova vera: cambio il lessico, non il codice.
    finto = {"confirm.yes": ["zzyes"], "confirm.no": ["zzno"]}
    monkeypatch.setattr(_daemon, "_dl",
                        type("_L", (), {"forms": staticmethod(
                            lambda c: finto.get(c, []))})())
    assert parse_step_value("zzyes", {"kind": "yes_no"})[:2] == (True, True)
    assert parse_step_value("zzno", {"kind": "yes_no"})[:2] == (True, False)
    assert parse_step_value("si", {"kind": "yes_no"})[0] is False
    # I letterali tecnici non dipendono dal lessico e restano.
    assert parse_step_value("true", {"kind": "yes_no"})[:2] == (True, True)

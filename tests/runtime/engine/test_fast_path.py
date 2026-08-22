"""test_fast_path.py — fast path deterministico (ADR 0094).

Verifica:
- match positivi su tutti i pattern IT+EN per ora e data;
- robustezza al confine NL→determinismo (case-insensitive, punteggiatura,
  apostrofi tipografici, whitespace);
- match negativi (query simili ma non triviali);
- rendering del template con observation di get_now;
- args contengono timezone iniettato di default;
- gestione di observation con ok=False.
"""
from __future__ import annotations

import sys
from pathlib import Path


from fast_path import try_fast_path, _normalize


# Observation mock: simula esattamente l'output di get_now per il
# 2026-05-07 alle 14:35 in Europe/Rome.
_MOCK_OBS = {
    "ok": True,
    "content": "2026-05-07T14:35:12+02:00",
    "metadata": {
        "timezone": "Europe/Rome",
        "iso8601": "2026-05-07T14:35:12+02:00",
        "epoch": 1778329512.0,
    },
}


# ── Match positivi: ora ────────────────────────────────────────────

def test_che_ora_e_match():
    hit = try_fast_path("che ora e?", lang="it")
    assert hit is not None
    assert hit["executor"] == "get_now"
    assert hit["args"]["timezone"] == "Europe/Rome"


def test_che_ore_sono_match():
    hit = try_fast_path("Che ore sono?", lang="it")
    assert hit is not None
    assert hit["executor"] == "get_now"


def test_dimmi_l_ora_curly_apostrophe():
    """Apostrofi tipografici (autocorretti dai client mobile) devono matchare."""
    hit = try_fast_path("Dimmi l’ora.", lang="it")
    assert hit is not None
    assert hit["executor"] == "get_now"


def test_what_time_is_it_match():
    hit = try_fast_path("What time is it?", lang="en")
    assert hit is not None
    assert hit["executor"] == "get_now"


def test_whats_the_time_apostrophe():
    hit = try_fast_path("what's the time", lang="en")
    assert hit is not None


def test_ora_attuale():
    hit = try_fast_path("ora attuale", lang="it")
    assert hit is not None


def test_configured_timezone_is_direct_and_visible():
    hit = try_fast_path("Che ora è nel fuso configurato?", lang="it")
    assert hit is not None
    assert hit["executor"] == "get_now"
    assert hit["args"]["timezone"] == "Europe/Rome"
    rendered = hit["render"](_MOCK_OBS)
    assert "14:35" in rendered
    assert "Europe/Rome" in rendered


# ── Match positivi: data ───────────────────────────────────────────

def test_che_data_e_oggi():
    hit = try_fast_path("Che data e oggi?", lang="it")
    assert hit is not None
    assert hit["executor"] == "get_now"


def test_today_s_date():
    hit = try_fast_path("today's date", lang="en")
    assert hit is not None


def test_what_day_is_it():
    hit = try_fast_path("What day is it?", lang="en")
    assert hit is not None


def test_data_odierna():
    hit = try_fast_path("data odierna", lang="it")
    assert hit is not None


# ── Robustezza al confine NL→det ───────────────────────────────────

def test_uppercase_match():
    hit = try_fast_path("CHE ORE SONO", lang="it")
    assert hit is not None


def test_trailing_punctuation_match():
    for q in ["che ora e!", "che ora e.", "che ora e;", "che ora e:"]:
        hit = try_fast_path(q, lang="it")
        assert hit is not None, f"failed on {q!r}"


def test_leading_trailing_whitespace():
    hit = try_fast_path("   che ora e?   ", lang="it")
    assert hit is not None


def test_internal_double_spaces():
    hit = try_fast_path("che    ore   sono", lang="it")
    assert hit is not None


# ── Match negativi (devono fare fallback PLANNER) ──────────────────

def test_no_match_query_with_argument():
    """Query con argomento (timezone, qualifier) NON deve matchare.
    Il PLANNER deve gestire la richiesta arricchita."""
    assert try_fast_path("che ore sono in giappone", lang="it") is None
    assert try_fast_path("what time is it in tokyo", lang="en") is None


def test_no_match_action_query():
    """Query d'azione con verbo simile non matcha."""
    assert try_fast_path("dimmi che file ci sono", lang="it") is None
    assert try_fast_path("send me the time at 9am", lang="en") is None


def test_no_match_empty_query():
    assert try_fast_path("", lang="it") is None
    assert try_fast_path("   ", lang="it") is None


def test_no_match_unrelated():
    # Pattern che non devono matchare fast_path (no executor cablato).
    # `dove sono`/`where am i` sono stati promossi a get_location → rimossi.
    for q in ["che tempo fa", "leggi le mail",
              "what is the weather", "list files"]:
        assert try_fast_path(q, lang="it") is None, f"unexpected match: {q!r}"


def test_no_match_sentence_containing_pattern():
    """Una frase piu' lunga che CONTIENE un pattern non matcha (per design:
    solo match esatto sulla query normalizzata)."""
    assert try_fast_path("dimmi che ore sono e poi spegni il pc", lang="it") is None


# ── Rendering template ─────────────────────────────────────────────

def test_render_time_template_it():
    hit = try_fast_path("che ore sono", lang="it")
    assert hit is not None
    msg = hit["render"](_MOCK_OBS)
    assert "14:35" in msg
    # La risposta time NON mostra il fuso (richiesta Roberto 30/6: «togli (UTC)»).
    assert "Europe/Rome" not in msg
    assert msg.startswith("Sono le")


def test_render_time_template_en():
    hit = try_fast_path("what time is it", lang="en")
    assert hit is not None
    msg = hit["render"](_MOCK_OBS)
    assert "14:35" in msg
    assert "Europe/Rome" not in msg
    assert msg.startswith("It's")


def test_render_date_template_it():
    hit = try_fast_path("che data e oggi", lang="it")
    assert hit is not None
    msg = hit["render"](_MOCK_OBS)
    # 2026-05-07 e' un giovedi'
    assert "giovedi'" in msg
    assert "maggio" in msg
    assert "2026" in msg
    assert "7" in msg


def test_render_date_template_en():
    hit = try_fast_path("what date is it", lang="en")
    assert hit is not None
    msg = hit["render"](_MOCK_OBS)
    assert "Thursday" in msg
    assert "May" in msg
    assert "2026" in msg
    assert "7" in msg


def test_render_executor_failure():
    """Observation con ok=False deve produrre messaggio di errore leggibile,
    non crashare."""
    hit = try_fast_path("che ora e", lang="it")
    assert hit is not None
    err_obs = {"ok": False, "error": "unknown timezone 'Mars/Olympus'"}
    msg = hit["render"](err_obs)
    assert "Errore" in msg or "Error" in msg


def test_render_observation_malformed_iso():
    """Se iso8601 e' malformato, render fa fallback degenere senza crashare."""
    bad_obs = {
        "ok": True,
        "content": "not-an-iso-string",
        "metadata": {"timezone": "Europe/Rome", "iso8601": "not-an-iso"},
    }
    hit = try_fast_path("che ora e", lang="it")
    assert hit is not None
    msg = hit["render"](bad_obs)
    # Non crasha, ritorna stringa.
    assert isinstance(msg, str)


# ── Normalizer unitari ─────────────────────────────────────────────

def test_normalize_strips_punctuation():
    assert _normalize("Che Ore Sono?!") == "che ore sono"


def test_normalize_curly_apostrophe():
    assert _normalize("dimmi l’ora") == "dimmi l'ora"


def test_normalize_collapses_whitespace():
    assert _normalize("che   ora   e") == "che ora e"


def test_normalize_empty():
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# ─── try_seed_step (ADR 0099) ─────────────────────────────────────────


def test_seed_step_url_with_path_matches():
    from fast_path import try_seed_step
    r = try_seed_step("Dimmi i risultati su https://www.federvolley.it/calendario")
    assert r is not None
    assert r["executor"] == "read_urls_html"
    assert r["url"] == "https://www.federvolley.it/calendario"
    assert r["args"] == {"urls": ["https://www.federvolley.it/calendario"]}


def test_seed_step_bare_domain_matches():
    from fast_path import try_seed_step
    r = try_seed_step("cosa c'e' su https://example.com")
    assert r is not None
    assert r["url"] == "https://example.com"


def test_seed_step_no_url_no_match():
    from fast_path import try_seed_step
    assert try_seed_step("che ora e?") is None
    assert try_seed_step("trova file *.py") is None


def test_seed_step_strips_trailing_punctuation():
    from fast_path import try_seed_step
    r = try_seed_step("vai su https://example.com/page.html.")
    assert r["url"] == "https://example.com/page.html"
    r = try_seed_step("link: https://example.com/x;")
    assert r["url"] == "https://example.com/x"


def test_seed_step_empty_or_invalid():
    from fast_path import try_seed_step
    assert try_seed_step("") is None
    assert try_seed_step(None) is None
    # URL malformato (no dot in netloc)
    assert try_seed_step("vai su http://localhost") is None


def test_seed_step_http_and_https():
    from fast_path import try_seed_step
    r1 = try_seed_step("vedi http://old-site.org/x")
    assert r1 is not None
    r2 = try_seed_step("vedi https://new-site.org/y")
    assert r2 is not None


def test_seed_step_first_url_wins():
    from fast_path import try_seed_step
    r = try_seed_step("compara https://a.com/x con https://b.com/y")
    assert r["url"] == "https://a.com/x"

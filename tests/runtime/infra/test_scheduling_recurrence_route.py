"""Route deterministica scheduling (§7.9, bug live 10/6/2026).

«Every 30 min: read the new open issues…» finiva nel decomposer/engine v2
che eseguivano il CORPO del task SUBITO → «Pipeline malformata o argomenti
insufficienti». Fix: parse deterministico NL→schedule (grammatica target
CHIUSA daily@HH:MM | every_Nm) in `recurring_tasks.parse_recurrence_query`
+ predicati condivisi `tool_grammar.query_has_tasks_marker` /
`query_is_recurrence` (prima agent_runtime usava solo _TASKS_MARKERS:
"every 30 min" non iniettava i tool *_tasks nel pool del PLANNER).
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

from recurring_tasks import parse_recurrence_query
from tool_grammar import query_has_tasks_marker, query_is_recurrence

LIVE_QUERY = (
    "Every 30 min: read the new open issues of brunialti/metnos, for each "
    "search the db for similar resolved issues, classify and analyze with "
    "the frontier, then save the draft reply as 'prepared' and notify me."
)


# ── parse_recurrence_query: casi POSITIVI (parse pulito) ──────────────────

def test_live_query_every_30_min_en():
    out = parse_recurrence_query(LIVE_QUERY)
    assert out is not None
    assert out["when"] == "every_30m"
    assert out["query"].startswith("read the new open issues")
    assert "notify me" in out["query"]
    assert out["label"]


def test_ogni_30_minuti_it():
    out = parse_recurrence_query(
        "ogni 30 minuti leggi le issue aperte e avvisami")
    assert out == {
        "when": "every_30m",
        "query": "leggi le issue aperte e avvisami",
        "label": "leggi le issue aperte e avvisami",
    }


def test_ogni_giorno_con_orario():
    out = parse_recurrence_query("ogni giorno alle 8 controlla la posta")
    assert out is not None
    assert out["when"] == "daily@08:00"
    assert out["query"] == "controlla la posta"


def test_every_day_at_hhmm_en():
    out = parse_recurrence_query("every day at 8:30 check my mail")
    assert out is not None
    assert out["when"] == "daily@08:30"
    assert out["query"] == "check my mail"


def test_ogni_n_ore():
    out = parse_recurrence_query("ogni 2 ore fai il backup")
    assert out is not None
    assert out["when"] == "every_120m"


def test_mezzora():
    out = parse_recurrence_query("ogni mezz'ora controlla il server")
    assert out is not None
    assert out["when"] == "every_30m"


def test_daily_standalone_con_orario():
    out = parse_recurrence_query("controlla la posta daily alle 22")
    assert out is not None
    assert out["when"] == "daily@22:00"
    assert out["query"] == "controlla la posta"


# ── strip inquadramento «crea un task che <azione>» (§7.9 universale) ─────
# Memorizza SOLO l'azione, mai la richiesta di creazione (anti-ricorsione).

def test_framing_crea_un_task_it():
    out = parse_recurrence_query(
        "Crea un task ricorrente che ogni 30 minuti legge le issue aperte "
        "e mi avvisa")
    assert out is not None
    assert out["when"] == "every_30m"
    # niente verbo di creazione nel corpo memorizzato
    assert not out["query"].lower().startswith("crea")
    assert out["query"].startswith("legge le issue aperte")


def test_framing_create_a_task_en():
    out = parse_recurrence_query(
        "Create a recurring task that every 30 minutes reads the open issues")
    assert out is not None
    assert out["when"] == "every_30m"
    assert out["query"] == "reads the open issues"


def test_framing_verbo_diverso_senza_lista():
    # verbo non-«crea» → universale, nessuna lista hardcoded.
    out = parse_recurrence_query(
        "Schedula un job che ogni ora fa il backup del database")
    assert out is not None
    assert out["when"] == "every_60m"
    assert out["query"] == "fa il backup del database"


def test_framing_anti_overstrip_task_obliquo():
    # «task» è complemento obliquo, l'object è «issue» → NON strippare.
    out = parse_recurrence_query(
        "ogni 30 minuti leggi le issue del task che mi hai assegnato")
    assert out is not None
    assert out["when"] == "every_30m"
    assert out["query"] == "leggi le issue del task che mi hai assegnato"


# ── parse_recurrence_query: casi NEGATIVI (fallthrough, mai indovinare) ───

def test_interrogativa_non_schedula():
    # Domanda analitica che CITA una ricorrenza: NON registrare task.
    assert parse_recurrence_query("quante mail ricevo ogni giorno?") is None
    assert parse_recurrence_query(
        "how many issues do we get every day?") is None


def test_ogni_messaggio_non_temporale():
    # "ogni" + oggetto non temporale: nessuna ricorrenza.
    assert parse_recurrence_query("leggi ogni messaggio in inbox") is None


def test_ogni_giorno_senza_orario_ambiguo():
    # daily senza HH:MM non e' rappresentabile in modo pulito → None.
    assert parse_recurrence_query("ogni giorno fai il backup") is None


def test_ogni_settimana_non_rappresentabile():
    assert parse_recurrence_query("ogni settimana pulisci i log") is None


def test_corpo_vuoto():
    assert parse_recurrence_query("ogni 30 minuti") is None


def test_input_degeneri():
    assert parse_recurrence_query("") is None
    assert parse_recurrence_query(None) is None


def test_orario_invalido():
    assert parse_recurrence_query("ogni giorno alle 25 controlla") is None


# ── predicati condivisi tool_grammar ──────────────────────────────────────

def test_marker_every_30_min_en():
    # Bug live: agent_runtime usava solo _TASKS_MARKERS → False su questa.
    assert query_has_tasks_marker(LIVE_QUERY) is True
    assert query_is_recurrence(LIVE_QUERY) is True


def test_marker_parole_comuni_non_baitano():
    assert query_has_tasks_marker("cerca mail bookings") is False
    assert query_is_recurrence("leggi ogni messaggio in inbox") is False


def test_marker_task_senza_ricorrenza():
    # Gestione task (list/delete) ha marker ma NON e' una ricorrenza.
    assert query_has_tasks_marker("quanti task ho schedulato") is True
    assert query_is_recurrence("quanti task ho schedulato") is False

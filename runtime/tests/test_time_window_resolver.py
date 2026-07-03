"""time_window_resolver — «ultime 24 ore» → time_window='last-24h' (§7.9).

Bug live 11/6/2026 (faglia 2 del routing mail): «controlla tutte le mie
mailbox ultime 24 ore» eseguiva read_messages SENZA time_window — la
finestra espressa nella query non arrivava mai all'arg, ne' dal proposer
ne' (peggio) dai piani serviti da L1 champion / L0. Il resolver estrae la
finestra dalla QUERY in modo deterministico (regex IT+EN, zero LLM) e la
canonicalizza nel vocabolario core `today|yesterday|last-Nh|last-Nd`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = str(Path(__file__).resolve().parents[1])
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from time_window_resolver import (parse_query_time_window, resolve_time_window,
                                  _parse_absolute_year, _year_bounds_imap)

_SCHEMA_TW = {"properties": {"time_window": {"type": "string"},
                             "account": {"type": "string"}},
              "required": []}
_SCHEMA_NO_TW = {"properties": {"path": {"type": "string"}}, "required": []}
# Schema reale di read_messages (§manifest): time_window + since/before come
# arg TOP-LEVEL separati — necessario perche' il resolver e' schema-gated
# anche per l'anno assoluto (solo tool che dichiarano ENTRAMBI since/before).
_SCHEMA_TW_SINCE_BEFORE = {"properties": {
    "time_window": {"type": "string"},
    "since": {"type": "string"}, "before": {"type": "string"},
    "account": {"type": "string"}}, "required": []}


# ── parse: estrazione NL → spec canonica ──────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    # ore IT (la query del bug live, e varianti)
    ("controlla tutte le mie mailbox ultime 24 ore", "last-24h"),
    ("controlla tutte le mie mailbox nelle ultime 24h", "last-24h"),
    ("posta delle scorse 12 ore", "last-12h"),
    ("cosa ho ricevuto nell'ultima ora", "last-1h"),
    ("mail dell'ora scorsa", "last-1h"),
    ("le 48 ore passate", "last-48h"),
    # ore EN
    ("check all my email from the last 24 hours", "last-24h"),
    ("messages in the past 6 hrs", "last-6h"),
    ("emails from the last hour", "last-1h"),
    # giorni IT
    ("mail degli ultimi 3 giorni", "last-3d"),
    ("la posta degli scorsi 2 gg", "last-2d"),
    ("riepilogo dell'ultimo giorno", "last-1d"),
    ("i 3 giorni scorsi", "last-3d"),
    # giorni EN
    ("messages from the past 2 days", "last-2d"),
    ("files changed in the last 7 days", "last-7d"),
    # giorno di calendario
    ("le mail di oggi", "today"),
    ("emails received today", "today"),
    ("le mail di ieri", "yesterday"),
    ("what did I get yesterday", "yesterday"),
])
def test_parse_estrae_finestra(query, expected):
    assert parse_query_time_window(query) == expected


@pytest.mark.parametrize("query", [
    # N + oggetto NON temporale: «ultime 20 mail» e' un conteggio, non finestra
    "leggi le ultime 20 mail",
    "apri gli ultimi 3 documenti",
    # nessuna espressione temporale
    "controlla la posta",
    "elenca i file in /tmp",
    # fuori confine dichiarato: calendario-settimana ambiguo, numeri in lettere
    "la settimana scorsa",
    "last week emails",
    "le ultime ventiquattro ore",
    # parole contenenti sottostringhe (oggi/ieri dentro altre parole)
    "aggiungi un evento al pomeriggio",
    "",
])
def test_parse_noop_senza_finestra(query):
    assert parse_query_time_window(query) is None


def test_parse_conflitto_esplicita_vince_poi_leftmost():
    # Forma esplicita N+unita' > oggi/ieri (spesso discorsivi); a parita'
    # vince la prima in ordine di lettura. Deterministico.
    assert parse_query_time_window(
        "oggi voglio le mail delle ultime 48 ore") == "last-48h"
    assert parse_query_time_window("le mail di ieri e di oggi") == "yesterday"


def test_parse_n_zero_non_emette_finestra():
    assert parse_query_time_window("le ultime 0 ore") is None


# ── resolve: gating e override ────────────────────────────────────────────

def test_resolve_setta_finestra_su_piano_che_non_ce_lha():
    # Il caso del bug: piano ereditato dal champion senza time_window.
    out = resolve_time_window(
        "read_messages", {"account": "metnos_system", "max_results": 20},
        "controlla tutte le mie mailbox ultime 24 ore", _SCHEMA_TW)
    assert out["time_window"] == "last-24h"
    assert out["max_results"] == 20  # altri arg preservati


def test_resolve_override_finestra_ereditata_diversa():
    # Piano cachato di un'ALTRA query («di oggi») servito a «ultime 24 ore»:
    # la query attuale vince (§7.9 ri-risoluzione, mai eredita' verbatim).
    out = resolve_time_window(
        "read_messages", {"time_window": "today"},
        "posta delle ultime 24 ore", _SCHEMA_TW)
    assert out["time_window"] == "last-24h"


def test_resolve_idempotente_su_valore_gia_canonico():
    args = {"time_window": "last-24h"}
    out = resolve_time_window(
        "read_messages", args, "posta delle ultime 24 ore", _SCHEMA_TW)
    assert out is args  # nessuna copia: gia' canonico


def test_resolve_query_senza_tempo_mai_spurio():
    args = {"account": "all"}
    out = resolve_time_window(
        "read_messages", args, "controlla la posta", _SCHEMA_TW)
    assert "time_window" not in out


def test_resolve_schema_senza_time_window_noop():
    args = {"path": "/tmp"}
    out = resolve_time_window(
        "list_dirs", args, "i file di oggi", _SCHEMA_NO_TW)
    assert "time_window" not in out


def test_resolve_senza_schema_noop_conservativo():
    out = resolve_time_window(
        "read_messages", {}, "posta delle ultime 24 ore", None)
    assert "time_window" not in out


def test_resolve_verbo_mutating_mai_iniettato():
    # Mai cambiare il perimetro di un'azione mutating (delete/move/...).
    for tool in ("delete_events", "move_messages", "write_files"):
        out = resolve_time_window(
            tool, {}, "cancella gli eventi di oggi", _SCHEMA_TW)
        assert "time_window" not in out


def test_resolve_since_before_espliciti_vincono():
    args = {"since": "22-Apr-2026"}
    out = resolve_time_window(
        "read_messages", args, "posta delle ultime 24 ore", _SCHEMA_TW)
    assert "time_window" not in out


def test_resolve_input_degeneri():
    assert resolve_time_window("read_messages", {}, "", _SCHEMA_TW) == {}
    args = {"x": 1}
    assert resolve_time_window("read_messages", args, None, _SCHEMA_TW) is args
    assert resolve_time_window("", {}, "oggi", _SCHEMA_TW) == {}


# ── anno di calendario assoluto (fix bug live 3/7) ─────────────────────────
# «del 2026»/«dell'anno 2026» genera una stringa "2026-01-01/2026-12-31" che
# NESSUN consumer riconosce (email_metnos._resolve_window: unknown_preset).
# Il manifest read_messages dichiara since/before proprio per le finestre
# custom: qui si valorizzano quelli, MAI un dict dentro time_window
# (lo schema lo dichiara type=string).

@pytest.mark.parametrize("query,expected_year", [
    ("le bollette plenitude del 2026", 2026),
    ("le bollette plenitude ed enel dell'anno 2026", 2026),
    ("le fatture nell'anno 2025", 2025),
    ("invoices of 2024", 2024),
    ("mail received in 2023", 2023),
])
def test_parse_absolute_year(query, expected_year):
    assert _parse_absolute_year(query) == expected_year


@pytest.mark.parametrize("query", [
    "controlla la posta di oggi",       # nessun anno
    "le mail del 1999",                 # fuori range 2000-2099
    "ultimi 2026 messaggi",             # numero ma non un anno-di-calendario
])
def test_parse_absolute_year_noop(query):
    assert _parse_absolute_year(query) is None


def test_year_bounds_imap_exclusive_before():
    # BEFORE e' esclusivo per contratto IMAP: il bound superiore e' il 1°
    # gennaio dell'anno SUCCESSIVO, non il 31 dicembre (altrimenti i
    # messaggi del 31/12 verrebbero esclusi dalla ricerca).
    since, before = _year_bounds_imap(2026)
    assert since == "01-Jan-2026"
    assert before == "01-Jan-2027"


def test_resolve_absolute_year_sets_since_before():
    args = {"account": "all", "time_window": "2026-01-01/2026-12-31"}
    out = resolve_time_window(
        "read_messages", args,
        "cerca in tutte le mie mailbox le bollette plenitude del 2026",
        _SCHEMA_TW_SINCE_BEFORE)
    assert out["since"] == "01-Jan-2026"
    assert out["before"] == "01-Jan-2027"
    assert "time_window" not in out  # sostituito, mai lasciato in giro rotto


def test_resolve_absolute_year_noop_without_since_before_in_schema():
    # Tool che non dichiara since/before (es. find_images_indices): mai
    # emettere il dict, resterebbe inespresso -> noop di proposito.
    args = {"time_window": "2026-01-01/2026-12-31"}
    out = resolve_time_window(
        "find_images_indices", args, "foto del 2026", _SCHEMA_TW)
    assert out is args


def test_resolve_rolling_wins_over_absolute_year_when_both_present():
    # «ultimi 2 anni» e' una forma esplicita rolling (priority 0): vince
    # sempre sull'anno di calendario, anche se un anno compare altrove.
    out = resolve_time_window(
        "read_messages", {}, "le mail degli ultimi 2 anni, non del 2020",
        _SCHEMA_TW_SINCE_BEFORE)
    assert out["time_window"] == "last-2y"
    assert "since" not in out


def test_resolve_absolute_year_idempotent():
    since, before = _year_bounds_imap(2026)
    args = {"since": since, "before": before}
    out = resolve_time_window(
        "read_messages", args, "le bollette del 2026", _SCHEMA_TW_SINCE_BEFORE)
    assert out is args  # since/before espliciti gia' corretti: noop, non ri-scrive

"""Convergence test: pipeline propose-and-notify (ADR 0128 + bug live
turn 35431172, 12/5/2026).

3 cicli x 5 query (15 totali):
  Ciclo 1: propose+create+email — multi-pipeline (P=1, N=1).
  Ciclo 2: edge varianti — solo propose / propose+create / propose+email /
           multi-canale / pre-existing busy.
  Ciclo 3: controlli negativi — invio diretto, create diretto, lettura,
           undo, calendario read. NESSUNO deve triggerare il multi-pipeline.

Verifica:
  - `_query_is_propose_intent` + `_query_has_notify_continuation` detection.
  - Multi-pipeline injection in `rank_with_intent` post-detection:
    se LLM intent ritorna verb=send object=messages, dopo l'injection
    i tool calendar sono presenti e gli hijackers mail-search rimossi.
  - Prompt section calendar.j2 contiene il pattern (propose_and_notify).

Run con `python3 -m pytest runtime/tests/test_multi_pipeline_propose_create_notify.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path("/opt/myclaw/runtime")
_EXECUTORS = Path("/opt/myclaw/executors")
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXECUTORS))


# --------------------------------------------------------------------------
# Detection helpers
# --------------------------------------------------------------------------

# 15 query: (text, expected_propose, expected_notify)
_CONVERGENCE_QUERIES = [
    # Ciclo 1: propose+create+email
    ("proponi 3 orari prossima settimana mattina e mandami email con la scelta", True, True),
    ("suggeriscimi 3 mattine e poi prenotami e notifica via mail", True, True),
    ("propose 3 morning slots next week and email me my choice", True, True),
    ("proponi 2 slot mercoledi e creami evento + invia conferma", True, True),
    ("trova 3 finestre libere giovedi e fissa il primo + notifica", False, True),
    # Ciclo 2: edge varianti
    ("proponi 3 orari mattina prossima settimana", True, False),  # solo propose
    ("proponi 3 orari e prenotami quello che scelgo", True, False),  # propose+create
    ("proponi 3 orari e mandami via email le opzioni", True, True),  # propose+email
    ("proponi 3 e avvisami su telegram", True, True),  # multi-canale
    ("proponi 3 mattine quando sono libero", True, False),  # propose-only
    # Ciclo 3: controlli negativi
    ("manda email a Mario", False, False),  # send diretto
    ("fissa appuntamento mercoledi", False, False),  # create diretto
    ("leggi mail oggi", False, False),  # read diretto
    ("annulla ultimo", False, False),  # undo
    ("mostra calendario", False, False),  # read events
]


@pytest.mark.parametrize("q,exp_p,exp_n", _CONVERGENCE_QUERIES)
def test_detection_propose_and_notify(q, exp_p, exp_n):
    """Detection delle due fasi (propose_intent + notify_continuation).
    Determinismo §7.9: regex compilate, niente LLM.
    """
    from agent_runtime import (
        _query_is_propose_intent,
        _query_has_notify_continuation,
    )
    assert _query_is_propose_intent(q) is exp_p, (
        f"propose_intent atteso {exp_p} per query: {q!r}"
    )
    assert _query_has_notify_continuation(q) is exp_n, (
        f"notify_continuation atteso {exp_n} per query: {q!r}"
    )


# --------------------------------------------------------------------------
# Multi-pipeline injection in candidates (end-to-end smoke)
# --------------------------------------------------------------------------

# Fixture catalog rimossa: i test usano fake catalog o leggono il manifest
# direttamente da disco per evitare dipendenza dal cache di sessione che
# alcuni test pollutings inquinano con HOME=tmp_path (Path.home() viene
# resolved a import-time in loader.py).


def _simulate_runtime_injection(query, intent, catalog):
    """Riproduce inline il flusso di agent_runtime: rank_with_intent →
    multi-pipeline injection. Ritorna lista di executor names finale.
    """
    from prefilter import rank_with_intent
    from agent_runtime import (
        _query_is_propose_intent,
        _query_has_notify_continuation,
    )
    picked = rank_with_intent(query, catalog, intent, k=8) or []
    candidates = list(picked)
    existing = {e.name for e in candidates}
    if (_query_is_propose_intent(query)
            and _query_has_notify_continuation(query)):
        NEEDED = (
            "get_now", "find_events_empty", "create_events",
            "read_events", "get_inputs", "send_messages",
        )
        HIJACKERS = frozenset({
            "find_messages_google_workspace",
            "read_messages",
            "read_messages_google_workspace",
        })
        candidates = [e for e in candidates if e.name not in HIJACKERS]
        for need in NEEDED:
            if need in existing:
                continue
            exec_ = next((e for e in catalog if e.name == need), None)
            if exec_ is not None:
                candidates.append(exec_)
    return [e.name for e in candidates]


class _FakeExec:
    """Minimal executor stub usato per simulare il catalog senza dipendenza
    da HOME (Path.home() viene resolved a import-time in loader.py)."""
    def __init__(self, name):
        self.name = name
        self.affinity = [name]
        self.description = name
        self.args_schema = {}


def _fake_catalog():
    return [
        _FakeExec(n) for n in [
            "get_now", "find_events_empty", "create_events", "read_events",
            "delete_events", "get_inputs", "send_messages",
            "send_messages_google_workspace", "read_messages",
            "find_messages_google_workspace", "read_messages_google_workspace",
            "describe_entries", "filter_entries", "classify_entries",
            "find_files", "read_files", "get_proposals",
        ]
    ]


def _inject_multi_pipeline(query, candidates_names, catalog_names):
    """Replica della logica di multi-pipeline injection in agent_runtime.
    Riceve candidates (post rank_with_intent) e ritorna la lista finale.
    """
    from agent_runtime import (
        _query_is_propose_intent,
        _query_has_notify_continuation,
    )
    candidates = list(candidates_names)
    existing = set(candidates)
    if (_query_is_propose_intent(query)
            and _query_has_notify_continuation(query)):
        NEEDED = (
            "get_now", "find_events_empty", "create_events",
            "read_events", "get_inputs", "send_messages",
        )
        HIJACKERS = {
            "find_messages_google_workspace",
            "read_messages",
            "read_messages_google_workspace",
        }
        candidates = [c for c in candidates if c not in HIJACKERS]
        for need in NEEDED:
            if need in existing:
                continue
            if need in catalog_names:
                candidates.append(need)
    return candidates


def test_bug_live_turn_35431172_recovered():
    """Query live del bug (turn 35431172 del 12/5/2026): senza la
    multi-pipeline injection, l'intent LLM verb=send object=messages
    portava a un top-K composto solo da send/find/read_messages —
    nessun tool calendar visibile al PLANNER. Con la injection, i tool
    calendar sono presenti e i hijackers mail-search rimossi.
    """
    query = (
        "proponi per la prossima settimana 3 possibili orari per un "
        "appuntamento di una ora la mattina e mandami una email col la scelta"
    )
    # Simulazione dei candidates post rank_with_intent(verb=send, object=messages):
    initial = [
        "send_messages",
        "send_messages_google_workspace",
        "read_messages",
        "find_messages_google_workspace",
    ]
    catalog = {e.name for e in _fake_catalog()}
    names = _inject_multi_pipeline(query, initial, catalog)
    # Tool calendar OBBLIGATORI dopo injection.
    assert "find_events_empty" in names, names
    assert "get_inputs" in names, names
    assert "send_messages" in names, names
    # Hijacker mail-search RIMOSSO.
    assert "find_messages_google_workspace" not in names, names
    assert "read_messages" not in names, names


def test_negative_does_not_trigger_injection():
    """Query negative (no propose o no notify): la injection NON deve
    scattare. find_messages_google_workspace puo' restare se intent lo
    chiede legittimamente (es. «cerca email da Mario»)."""
    query = "leggi mail oggi"
    initial = ["read_messages", "find_messages_google_workspace"]
    catalog = {e.name for e in _fake_catalog()}
    names = _inject_multi_pipeline(query, initial, catalog)
    # find_events_empty NON aggiunto (no multi-pipeline).
    assert "find_events_empty" not in names, names
    # read_messages presente (l'injection negativa non rimuove nulla).
    assert "read_messages" in names, names


# --------------------------------------------------------------------------
# Prompt section presente (smoke)
# --------------------------------------------------------------------------

def test_calendar_section_has_propose_and_notify():
    p_it = (
        _RUNTIME / "prompts" / "it" / "planner" / "sections" / "calendar.j2"
    ).read_text(encoding="utf-8")
    p_en = (
        _RUNTIME / "prompts" / "en" / "planner" / "sections" / "calendar.j2"
    ).read_text(encoding="utf-8")
    assert "(propose_and_notify)" in p_it
    assert "(propose_and_notify)" in p_en
    # Verifica che la pipeline send_messages sia menzionata in entrambe
    # le varianti (a) e (b).
    assert "send_messages" in p_it
    assert "send_messages" in p_en
    # Variant detection: il prompt deve descrivere ENTRAMBE le varianti
    # (a) solo NOTIFY e (b) NOTIFY + create_events.
    assert "Variante (a)" in p_it or "Variante a" in p_it
    assert "Variante (b)" in p_it or "Variante b" in p_it
    assert "Variant (a)" in p_en
    assert "Variant (b)" in p_en


# --------------------------------------------------------------------------
# Affinity bonifica: find_messages_google_workspace + get_proposals
# --------------------------------------------------------------------------

def test_find_messages_gws_affinity_does_not_match_email_noun():
    """Bonifica F4 (C-fix): l'affinity di find_messages_google_workspace
    NON deve contenere termini generici «mail», «email», «messaggi»
    che matchano in query NON di search mail (es. «mandami email»).

    Legge il manifest direttamente dal disco (sotto HOME utente reale)
    per evitare dipendenze da catalog cache che alcuni test contigui
    inquinano con HOME=tmp_path (Path.home() resolved a module-import
    time in loader.py).
    """
    import tomllib
    manifest_path = Path(
        "/home/roberto/.local/share/metnos/executors/_imports/"
        "google-workspace/find_messages_google_workspace/manifest.toml"
    )
    assert manifest_path.exists(), f"manifest non trovato: {manifest_path}"
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)
    aff_lower = {a.lower() for a in manifest.get("affinity", [])}
    # Termini generici banditi (matchavano «mandami email»):
    BANNED = {"mail", "email", "posta", "messaggio", "messaggi",
              "messages", "emails", "lettera", "inbox", "find", "search",
              "cerca"}
    overlap = aff_lower & BANNED
    assert not overlap, (
        f"find_messages_gws.affinity contiene termini generici: {overlap}. "
        f"Devono essere termini qualificati (es. «cerca mail», «trova email»)."
    )
    # Almeno qualche termine qualified presente (sanity).
    assert any("gmail" in a or "cerca mail" in a or "search mail" in a
               for a in aff_lower)


def test_get_proposals_affinity_no_bare_propose_terms():
    """Bonifica F5 (D-fix): l'affinity di get_proposals NON deve contenere
    termini singoli generici «proposta/proposte/proposals/review/pending/
    candidati/candidates/dedupe» che matchano «proponi 3 proposte» o
    «pending appointment». Devono essere qualificati con suffisso
    discriminante (introvertiva/mnest).

    Legge il manifest direttamente da disco per indipendenza da cache.
    """
    import tomllib
    manifest_path = Path(
        "/opt/myclaw/executors/get_proposals/manifest.toml"
    )
    assert manifest_path.exists(), f"manifest non trovato: {manifest_path}"
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)

    class _Stub:
        pass
    gp = _Stub()
    gp.affinity = manifest.get("affinity", [])
    aff_lower = {a.lower() for a in gp.affinity}
    BANNED = {"proposta", "proposte", "proposals", "review", "pending",
              "candidati", "candidates", "dedupe", "consolidamento"}
    overlap = aff_lower & BANNED
    assert not overlap, (
        f"get_proposals.affinity contiene termini generici: {overlap}. "
        f"Devono essere qualificati (es. «introvertiva», «mnest»)."
    )


# --------------------------------------------------------------------------
# adaptive_rerank: `kind` come generic piping arg
# --------------------------------------------------------------------------

def test_kind_in_generic_piping_args():
    """Bug live turn 35431172 step 5: free_slot.kind matcho' get_proposals.kind
    via consumer_match. Il fix aggiunge `kind` a `_GENERIC_PIPING_ARGS`
    (campo di discriminazione, non di pipeline). §7.3 generale.
    """
    from adaptive_rerank import _GENERIC_PIPING_ARGS
    assert "kind" in _GENERIC_PIPING_ARGS


def test_consumer_match_no_longer_returns_get_proposals():
    """Post-fix, `kind` non viene piu' incluso nei `_produced_keys` di
    un'observation con entries.kind, evitando match falso con
    get_proposals.kind enum (dedupe/generalize/specialize/all).
    """
    from adaptive_rerank import _produced_keys
    obs = {
        "ok": True,
        "entries": [{
            "kind": "free_slot", "start": "...", "end": "...",
            "duration_min": 240, "calendar_id": "primary",
        }],
    }
    keys = _produced_keys(obs)
    assert "kind" not in keys, (
        f"`kind` non deve essere produced key: {keys}. "
        f"`kind` e' in _GENERIC_PIPING_ARGS post-fix."
    )
    # Verifica che gli ALTRI campi siano produced normalmente.
    assert "start" in keys
    assert "end" in keys
    assert "calendar_id" in keys

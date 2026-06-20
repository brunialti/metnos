"""Test build_routing_pool (engine/routing_pool.py, fix B3 9/6/2026).

La costruzione-pool di produzione e' estratta da dispatch.run_turn in una
funzione PURA condivisa col guard anti-regressione
`bench/routing_subset_bench.py`. Questi test esercitano la funzione su query
rappresentative (mono-azione, compound per-clausola, web-companion) e
verificano i layer che la vecchia copia semplificata del bench NON copriva:
k da env, unione pool per-clausola, universal-helpers, companions.

Test deterministici: NESSUN LLM coinvolto (l'intent e' costruito a mano,
come lo produce intent_extractor a monte).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Helpers ────────────────────────────────────────────────────────────

def _load_real_catalog():
    """Carica il catalog reale dal path canonico (verify=False, no synth).

    Stesso pattern di test_planner_routing_composition.py: pin esplicito di
    `<install_root>/executors` + loader interno `_load_dir_into_catalog`,
    per isolarsi dai monkeypatch di altri test del suite (DEFAULT_EXECUTORS_DIR,
    lifecycle filter, executor_aging DB).
    """
    from loader import Catalog, _load_dir_into_catalog
    cat = Catalog()
    _load_dir_into_catalog(Path(__file__).resolve().parents[2] / "executors",
                           cat, verify=False, is_synthesized=False)
    return list(cat.executors.values())


@pytest.fixture(scope="module")
def catalog():
    return _load_real_catalog()


def _intent(verb="", object="", keywords=None, actions=None):
    from engine.types import Intent
    return Intent(verb=verb, object=object, keywords=list(keywords or []),
                  lang="it", actions=list(actions or []))


def _build(query, intent, catalog, **kw):
    from engine.routing_pool import build_routing_pool
    return build_routing_pool(query, intent, catalog, **kw)


# ── Mono-azione ────────────────────────────────────────────────────────

def test_mono_action_find_files(catalog):
    pool = _build("cerca i file .pdf nella cartella Documenti",
                  _intent("find", "files"), catalog)
    assert isinstance(pool, list)
    assert all(isinstance(n, str) and n for n in pool)
    assert "find_files" in pool


def test_mono_action_read_messages(catalog):
    pool = _build("leggi le mail non lette di oggi",
                  _intent("read", "messages"), catalog)
    assert "read_messages" in pool


def test_universal_helpers_appended(catalog):
    """I universal-helpers presenti nel catalog entrano SEMPRE nel pool,
    anche se il prefilter per-verbo non li rankerebbe (layer che la vecchia
    copia del bench non aveva)."""
    from tool_grammar import _UNIVERSAL_HELPERS
    cat_names = {getattr(e, "name", None) for e in catalog}
    expected = set(_UNIVERSAL_HELPERS) & cat_names
    assert expected, "catalog senza alcun universal-helper? loader rotto"
    pool = set(_build("cerca i file .pdf nella cartella Documenti",
                      _intent("find", "files"), catalog))
    missing = expected - pool
    assert not missing, f"universal-helpers assenti dal pool: {sorted(missing)}"


# ── get_inputs: dialog UI, MAI iniettato come universal-helper ──────────

def test_get_inputs_non_iniettato_su_query_azione(catalog):
    """Causa-radice misroute 9/6/2026: get_inputs (dialog UI, ADR 0090) era
    in _UNIVERSAL_HELPERS → SEMPRE nel pool+grammar del Proposer → l'LLM
    wise collassava su get_inputs-only ("chiede" invece di "fare"; bench
    routing_subset 2/22 rossi, riparati in prod solo dal guard di dispatch
    con un re-propose = seconda chiamata wise). Per query d'azione il pool
    NON deve contenerlo: la proposta GREZZA deve già essere corretta."""
    pool_send = _build("invia una mail a Mario con oggetto Promemoria",
                       _intent("send", "messages"), catalog)
    assert "send_messages" in pool_send
    assert "get_inputs" not in pool_send
    pool_img = _build("cerca foto di una persona col viso in primo piano",
                      _intent("find", "images"), catalog)
    assert "find_images_indices" in pool_img
    assert "get_inputs" not in pool_img


def test_get_inputs_resta_per_object_inputs(catalog):
    """Flusso legittimo PRESERVATO: quando l'utente chiede un dialog
    (intent object=inputs), il prefilter porta get_inputs nel pool via
    _OBJECT_PRIMARY_TOOLS['inputs'] (ADR 0090) — la rimozione dagli
    universal-helpers non lo rende mai-proponibile."""
    pool = _build("chiedimi i valori di configurazione",
                  _intent("get", "inputs"), catalog)
    assert "get_inputs" in pool


# ── Compound per-clausola (intent.actions) ─────────────────────────────

def test_compound_actions_union_per_clause(catalog):
    """Query multi-azione: il pool unisce il ranking di OGNI clausola
    (find/processes + write/files) — senza unione il producer della prima
    clausola (get_processes) o il consumer della seconda (write_files)
    resterebbero fuori (bug q20/q21 del 4/6)."""
    intent = _intent("find", "processes",
                     actions=[{"verb": "find", "object": "processes"},
                              {"verb": "write", "object": "files"}])
    pool = _build("trova i processi che consumano più CPU e scrivi "
                  "un report in /tmp/report.txt", intent, catalog)
    assert "get_processes" in pool
    assert "write_files" in pool


# ── Web companion (find_urls → read_urls_*) ────────────────────────────

def test_web_companions_injected(catalog):
    """find_urls nel pool porta SEMPRE i consumer read_urls_html/pdf
    (§7.3 companion, bug ROCm 3/6) — assente nella vecchia copia bench."""
    pool = _build("cerca sul web le ultime notizie su AMD ROCm",
                  _intent("find", "urls"), catalog)
    assert "find_urls" in pool
    assert "read_urls_html" in pool
    assert "read_urls_pdf" in pool


# ── k: parametro esplicito ≡ env METNOS_ENGINE_POOL_SIZE ───────────────

def test_k_param_equivale_env(catalog, monkeypatch):
    q = "cerca i file .pdf nella cartella Documenti"
    it = _intent("find", "files")
    monkeypatch.setenv("METNOS_ENGINE_POOL_SIZE", "7")
    pool_env = _build(q, it, catalog)
    monkeypatch.delenv("METNOS_ENGINE_POOL_SIZE")
    pool_k = _build(q, it, catalog, k=7)
    assert pool_env == pool_k


# ── Intent incompleto → full catalog (contratto produzione) ────────────

def test_incomplete_intent_full_catalog(catalog):
    # get_approval (20/6/2026) e' un gate runtime-managed (consent-gate
    # inserito da dispatch + FIX 1 gate-resume), escluso dal pool del proposer
    # salvo richiesta ESPLICITA (intent con clausola (get, approval)) — vedi
    # _gate_approval_tool. Con intent vuoto resta filtrato, come _gate_store_skill.
    pool = _build("query senza intent", _intent(), catalog)
    assert pool == [getattr(e, "name", None) for e in catalog
                    if getattr(e, "name", None)
                    and getattr(e, "name", None) != "get_approval"]


# ── Purezza: il catalog NON viene mutato ───────────────────────────────

def test_catalog_not_mutated(catalog):
    before = [getattr(e, "name", None) for e in catalog]
    _build("cerca sul web le ultime notizie su AMD ROCm",
           _intent("find", "urls"), catalog)
    after = [getattr(e, "name", None) for e in catalog]
    assert before == after


# ── Anti-regressione B3: dispatch e bench usano la STESSA funzione ─────

def test_dispatch_e_bench_condividono_la_pool_build():
    """Il punto del fix B3: ne' dispatch ne' il bench devono re-implementare
    la costruzione del pool inline."""
    root = Path(__file__).resolve().parents[2]
    dispatch_src = (root / "runtime" / "engine" / "dispatch.py").read_text(
        encoding="utf-8")
    bench_src = (root / "bench" / "routing_subset_bench.py").read_text(
        encoding="utf-8")
    assert "build_routing_pool" in dispatch_src
    assert "rank_with_intent" not in dispatch_src, \
        "dispatch.run_turn re-implementa il pool inline (regressione B3)"
    assert "build_routing_pool" in bench_src
    assert "rank_with_intent" not in bench_src, \
        "bench re-implementa il pool inline (regressione B3)"


# ── Cross-object affinity phrase recall (misroute live 10/6/2026) ──────

def test_affinity_phrase_recall_account_mail(catalog):
    """«quali account mail hai?» — intent object=messages ("mail" domina su
    "account") escludeva find_credentials/read_persons a monte = RECALL miss
    (read_messages leggeva 426 email). Il tag affinity multi-parola "quali
    account" interamente coperto dalla query li forza nel pool ANCHE con
    verb/object dell'intent diversi."""
    pool = _build("quali account mail hai?", _intent("get", "messages"),
                  catalog)
    assert "find_credentials" in pool
    assert "read_persons" in pool


def test_affinity_phrase_recall_scoped(catalog):
    """SCOPED: il recall scatta SOLO su phrase-match pieno di tag multi-parola
    distintivi. Tag singola-parola coincidenti ("archivio"+"cartella" di
    move_messages) NON sporcano le query move_files; query senza phrase-match
    non recuperano nulla fuori dal gating object."""
    from prefilter import affinity_phrase_recall
    assert affinity_phrase_recall(
        "sposta vecchio.txt nella cartella archivio", catalog) == []
    assert affinity_phrase_recall(
        "leggi le mail non lette di oggi", catalog) == []


def test_affinity_phrase_recall_excludes_present(catalog):
    """exclude_names: i tool gia' nel pool non vengono duplicati."""
    from prefilter import affinity_phrase_recall
    rec = affinity_phrase_recall("quali account mail hai?", catalog,
                                 exclude_names={"find_credentials"})
    names = [getattr(e, "name", None) for e in rec]
    assert "find_credentials" not in names
    assert "read_persons" in names

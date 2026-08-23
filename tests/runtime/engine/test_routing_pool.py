"""Test build_routing_pool (engine/routing_pool.py, fix B3 9/6/2026).

La costruzione-pool di produzione e' estratta da dispatch.run_turn in una
funzione PURA condivisa col guard anti-regressione
`tests/benchmarks/routing_subset_bench.py`. Questi test esercitano la funzione su query
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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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
    _load_dir_into_catalog(Path(__file__).resolve().parents[3] / "executors",
                           cat, verify=False, is_synthesized=False)
    return list(cat.executors.values())


@pytest.fixture(scope="module")
def catalog(standard_catalog):
    return standard_catalog


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


def test_run_processes_injects_signed_program_resolver(catalog):
    """La relazione run→resolver arriva dal manifest, non dal motore."""
    launcher = next(item for item in catalog if item.name == "run_processes")
    assert launcher.planning_object_aliases == ["packages"]
    assert ["programs", "from_step"] in launcher.args_schema["requires_one_of"]
    programs = launcher.args_schema["properties"]["programs"]
    assert programs["from_entries_key"] == "resolved_id"
    assert programs["from_entries_complete"] is True
    pool = _build("avvia un programma installato sul mio dispositivo",
                  _intent("run", "processes"), catalog)
    assert "run_processes" in pool
    assert "find_packages" in pool
    assert pool.index("run_processes") < pool.index("open_sites")

    # Il nome naturale puo' far classificare l'oggetto come package invece
    # che come processo: il verbo canonico conserva comunque il launcher.
    package_pool = _build("start Notepad on my Windows device",
                          _intent("run", "packages"), catalog)
    assert package_pool[0] == "run_processes"
    assert "find_packages" in package_pool


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
    # I tool provider-suffissati (ADR 0136: _github/_google_workspace/
    # _google_photos) sono gateati fuori quando la query NON contiene il loro
    # marker (`provider_gate_names`): "query senza intent" non ha marker → escono
    # (context-binding, non scelta LLM). Come get_approval, restano fuori.
    from vocab import PROVIDER_SUFFIXES

    def _is_provider(n):
        return any(n.endswith("_" + p) for p in PROVIDER_SUFFIXES)

    pool = _build("query senza intent", _intent(), catalog)
    assert pool == [getattr(e, "name", None) for e in catalog
                    if getattr(e, "name", None)
                    and getattr(e, "name", None) != "get_approval"
                    and not _is_provider(getattr(e, "name", None))]


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
    root = Path(__file__).resolve().parents[3]
    dispatch_src = (root / "runtime" / "engine" / "dispatch.py").read_text(
        encoding="utf-8")
    bench_src = (
        root / "tests" / "benchmarks" / "routing_subset_bench.py"
    ).read_text(
        encoding="utf-8")
    assert "build_routing_pool" in dispatch_src
    assert "rank_with_intent" not in dispatch_src, \
        "dispatch.run_turn re-implementa il pool inline (regressione B3)"
    assert "build_routing_pool" in bench_src
    assert "rank_with_intent" not in bench_src, \
        "bench re-implementa il pool inline (regressione B3)"


# ── Cross-object affinity phrase recall (misroute live 10/6/2026) ──────

def test_affinity_phrase_recall_account_mail(catalog):
    """«quali account mail hai?» resta nel dominio credenziali/account.

    ``read_persons`` non apre piu' il vault mail per arricchire un profilo:
    includerlo qui ricreerebbe un'autorita' implicita e sovradimensionata.
    """
    pool = _build("quali account mail hai?", _intent("get", "messages"),
                  catalog)
    assert "find_credentials" in pool
    assert "read_persons" not in pool


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
    assert "read_persons" not in names


# ── Segregazione modalità immagini (2/7/2026, replay job A) ────────────

def test_image_tools_dropped_from_text_clause(catalog):
    """«riassumi i file readme su github» (describe|texts): describe_images /
    find_images_web NON entrano — la query non nomina le immagini."""
    it = _intent(verb="describe", object="texts",
                 actions=[{"verb": "find", "object": "files"},
                          {"verb": "read", "object": "files"},
                          {"verb": "describe", "object": "texts"}])
    pool = _build("riassumi tutti i file readme.md su github nel repo o/r",
                  it, catalog)
    bad = {n for n in pool if "_images" in n}
    assert not bad, bad


def test_image_tools_dropped_from_web_news_clause(catalog):
    """«cerca sul web notizie su python» (find|urls): find_images_web fuori."""
    it = _intent(verb="find", object="urls")
    pool = _build("cerca sul web notizie su python", it, catalog)
    assert "find_images_web" not in pool, pool


def test_image_tools_kept_for_images_clause(catalog):
    it = _intent(verb="find", object="images")
    pool = _build("cerca le foto della gita a venezia", it, catalog)
    assert any("_images" in n for n in pool), pool


def test_text_web_image_search_recruits_public_image_executor(catalog):
    it = _intent(verb="find", object="images")
    pool = _build("cerca sul web immagini del soggetto esempio", it, catalog)
    assert "find_images_web" in pool


def test_image_gate_keeps_when_text_names_photos():
    """Intent misclassificato (object=files) ma il TESTO nomina le foto →
    il detector testuale preserva i tool images (nessun falso negativo del
    GATE; test diretto: il rank per files può non portarli affatto)."""
    from types import SimpleNamespace
    from engine.routing_pool import _gate_image_modality
    pool = [SimpleNamespace(name="find_images_indices"),
            SimpleNamespace(name="read_files")]
    it = _intent(verb="describe", object="files")
    kept = _gate_image_modality(pool, "riassumi le foto della gita", it)
    assert any(getattr(e, "name", "") == "find_images_indices" for e in kept)
    # e sulla stessa query SENZA foto nel testo il tool images cade
    kept2 = _gate_image_modality(pool, "riassumi i documenti della gita", it)
    assert all(getattr(e, "name", "") != "find_images_indices" for e in kept2)

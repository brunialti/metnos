"""Search result relevance, bounded work, and BM25 collection regressions."""
from __future__ import annotations

import importlib.util
import json
import math
import urllib.parse
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "find_urls_search_resolution", ROOT / "executors/find_urls/find_urls.py")
assert SPEC and SPEC.loader
f = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f)

QUERY = "cerca se ci sono novità sullo stack amd rocm"
CANDIDATES = [
    {"url": "https://news.example/tv", "title": "Novità televisive", "snippet": "Nuovi programmi"},
    {"url": "https://rocm.docs.amd.com/en/latest/about/release-notes.html",
     "title": "ROCm release notes", "snippet": "AMD ROCm software releases", "search_engines": ["example"]},
    {"url": "https://github.com/ROCm/ROCm/releases", "title": "ROCm releases",
     "snippet": "AMD compute stack updates", "search_engines": ["example"]},
]


@pytest.fixture
def search(monkeypatch):
    monkeypatch.setenv("METNOS_FIND_URLS_RERANK", "1")
    monkeypatch.setattr(f, "_inject_current_date", lambda text: text)
    monkeypatch.setattr(f, "_msg", lambda key, **_kwargs: key)

    def backend(query, **kwargs):
        kwargs["metadata"]["backend_result_count"] = len(CANDIDATES)
        return [dict(c) for c in CANDIDATES], None

    monkeypatch.setattr(f, "_searxng_search_full", backend)

    def unexpected_fetch(*_args, **_kwargs):
        pytest.fail("ordinary search must not download or crawl result pages")

    monkeypatch.setattr(f.urllib.request, "build_opener", unexpected_fetch)
    return f


def test_original_query_empty_selection_never_reappears_as_success(search, monkeypatch):
    seen_queries = []

    def reject(query, candidates, **_kwargs):
        seen_queries.append(query)
        assert len(candidates) == 3
        return [], {"used": True, "reason": "no_relevant_candidates", "n_kept": 0}

    monkeypatch.setattr(search, "_llm_rerank_candidates", reject)
    out = search._invoke_default({"search_query": QUERY, "seed_urls": ["https://ignored.example/"]})
    assert seen_queries == [QUERY]
    assert out["entries"] == []
    assert out["ok"] is False
    assert out["error_class"] == "search_no_results"
    assert out["metadata"]["search_results_used"] == 0
    assert out["metadata"]["rerank"]["used"] is True
    assert set(out["metadata"]["timings_ms"]) == {"search", "rerank"}


@pytest.mark.parametrize("query", [QUERY, "latest compiler releases", "novedades de energía solar"])
def test_simple_search_preserves_verified_order_snippets_and_sources(search, monkeypatch, query):
    wanted = [CANDIDATES[2]["url"], CANDIDATES[1]["url"]]
    monkeypatch.setattr(search, "_llm_rerank_candidates", lambda *_a, **_k: (
        wanted, {"used": True, "scores": {wanted[0]: 0.9, wanted[1]: 0.8}}))
    out = search._invoke_default({"search_query": query})
    assert out["ok"] is True
    assert [e["url"] for e in out["entries"]] == wanted
    assert out["entries"][0]["snippet"] == CANDIDATES[2]["snippet"]
    assert out["entries"][0]["score"] == 0.9
    assert out["entries"][0]["search_engines"] == ["example"]
    assert out["entries"][0]["fetched_at"] is None
    assert out["discovery_strategy"] == "search"
    assert out["metadata"]["max_depth_used"] == 0
    assert out["metadata"]["pages_fetched"] == 0


def test_small_candidate_set_is_not_exempt_from_relevance(search, monkeypatch):
    def backend(_query, **kwargs):
        kwargs["metadata"]["backend_result_count"] = 1
        return [CANDIDATES[0]], None

    monkeypatch.setattr(search, "_searxng_search_full", backend)
    monkeypatch.setattr(search, "_llm_rerank_candidates", lambda *_a, **_k: (
        [], {"used": True, "reason": "no_relevant_candidates"}))
    out = search._invoke_default({"search_query": QUERY})
    assert out["error_class"] == "search_no_results"
    assert out["entries"] == []


def test_relevance_failure_does_not_reuse_unverified_candidates(search, monkeypatch):
    monkeypatch.setattr(search, "_llm_rerank_candidates", lambda *_a, **_k: (
        [], {"used": False, "reason": "llm_unavailable"}))
    out = search._invoke_default({"search_query": QUERY})
    assert out["entries"] == []
    assert out["error_class"] == "search_relevance_unavailable"
    assert out["error_code"] == "ERR_EXT_SVC_UNAVAILABLE"


def test_search_backend_error_never_activates_discarded_caller_seeds(search, monkeypatch):
    monkeypatch.setattr(search, "_searxng_search_full", lambda *_a, **_k: (
        [], "search_backend_unavailable"))
    out = search._invoke_default({"search_query": QUERY, "seed_urls": ["https://ignored.example/"]})
    assert out["error_class"] == "search_backend_unavailable"
    assert out["entries"] == []


def test_simple_search_honors_explicit_output_cap(search, monkeypatch):
    wanted = [CANDIDATES[1]["url"], CANDIDATES[2]["url"]]
    monkeypatch.setattr(search, "_llm_rerank_candidates", lambda *_a, **_k: (wanted, {"used": True}))
    out = search._invoke_default({"search_query": QUERY, "max_pages": 1})
    assert len(out["entries"]) == 1
    assert out["truncated"] is True
    assert out["available_total"] == 2
    assert out["truncated_intentional"] is True


def test_bm25_matches_standard_formula_with_document_length_and_idf():
    docs = ["alpha beta beta", "beta", "gamma gamma gamma gamma"]
    k1, b, avgdl = 1.5, 0.75, 8 / 3
    idf_alpha = math.log(1 + 2.5 / 1.5)
    idf_beta = math.log(1 + 1.5 / 2.5)
    alpha = idf_alpha * 2.5 / (1 + k1 * (1 - b + b * 3 / avgdl))
    beta_twice = idf_beta * 5 / (2 + k1 * (1 - b + b * 3 / avgdl))
    beta_once = idf_beta * 2.5 / (1 + k1 * (1 - b + b / avgdl))
    assert f._bm25_scores(["alpha", "beta"], docs) == pytest.approx([alpha + beta_twice, beta_once, 0])


def test_bm25_tokenizes_each_document_once(monkeypatch):
    tokenize = f._tokenize
    visited = []
    monkeypatch.setattr(f, "_tokenize", lambda text: visited.append(text) or tokenize(text))
    docs = [f"document {i} alpha beta" for i in range(200)]
    scores = f._bm25_scores(["alpha", "missing"], docs)
    assert len(scores) == len(docs)
    assert visited == docs


def test_bm25_empty_inputs_repeated_terms_and_unicode():
    assert f._bm25_scores([], ["text"]) == [0.0]
    assert f._bm25_scores(["text"], []) == []
    assert f._bm25_scores(["text"], [""]) == [0.0]
    one = f._bm25_scores(["text"], ["text", "other"])
    assert f._bm25_scores(["text", "text"], ["text", "other"]) == pytest.approx([2 * x for x in one])
    assert f._tokenize("Énergie Робот") == ["énergie", "робот"]


def test_backend_empty_results_are_valid_and_search_parameters_are_explicit(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"results": [], "unresponsive_engines": []}).encode()

    monkeypatch.setattr(f.urllib.request, "urlopen", lambda request, **_kw: requests.append(request) or Response())
    metadata = {}
    results, error = f._searxng_search_full(QUERY, metadata=metadata)
    assert results == [] and error is None
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[0].full_url).query)
    assert params["q"] == [QUERY]
    assert params["language"] == ["all"]
    assert params["categories"] == ["general"]
    assert metadata == {"backend_result_count": 0, "unresponsive_engines": []}

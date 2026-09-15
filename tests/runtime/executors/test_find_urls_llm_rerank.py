"""Test LLM re-rank di find_urls (ADR 0118)."""
from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import patch


_FIND_URLS_DIR = Path(__file__).resolve().parents[3] / "executors/find_urls"
if str(_FIND_URLS_DIR) not in sys.path:
    sys.path.insert(0, str(_FIND_URLS_DIR))

import find_urls as f  # type: ignore


_SAMPLE_CANDIDATES = [
    {"url": "https://a.example/a.pdf", "title": "Aaa", "snippet": "first"},
    {"url": "https://b.example/b.html", "title": "Bbb", "snippet": "second"},
    {"url": "https://c.example/c.pdf", "title": "Ccc", "snippet": "third"},
    {"url": "https://d.example/d", "title": "Ddd", "snippet": "fourth"},
]


def test_rerank_empty_returns_no_used():
    urls, meta = f._llm_rerank_candidates("query", [], top_k=5)
    assert urls == []
    assert meta["used"] is False
    assert meta["reason"] == "no_candidates"


def test_rerank_single_candidate_still_requires_relevance():
    with patch("llm_helpers.call_llm", return_value=('{"top": []}', {})) as model:
        urls, meta = f._llm_rerank_candidates(
            "q", [{"url": "https://x", "title": "X", "snippet": "x"}], top_k=5,
        )
    model.assert_called_once()
    assert urls == []
    assert meta["used"] is True


def test_rerank_llm_failure_has_no_approved_urls():
    """A dependency failure must not approve unverified results."""
    with patch("llm_helpers.call_llm", side_effect=RuntimeError("provider down")):
        urls, meta = f._llm_rerank_candidates(
            "q", _SAMPLE_CANDIDATES, top_k=3,
        )
    assert urls == []
    assert meta["used"] is False
    assert meta["reason"] == "llm_unavailable"
    assert "provider down" in meta["error"]


def test_rerank_invalid_json_has_no_approved_urls():
    """An invalid response is a technical failure, not an empty selection."""
    with patch("llm_helpers.call_llm",
               return_value=("not json at all", {"in_tokens": 100, "out_tokens": 5, "latency_ms": 1})):
        urls, meta = f._llm_rerank_candidates(
            "q", _SAMPLE_CANDIDATES, top_k=3,
        )
    assert urls == []
    assert meta["used"] is False
    assert meta["reason"] == "json_invalid"


def test_rerank_valid_json_reorders():
    """JSON valido → riordina secondo score."""
    fake = (
        '{"top": ['
        '{"url": "https://c.example/c.pdf", "score": 0.95},'
        '{"url": "https://a.example/a.pdf", "score": 0.7},'
        '{"url": "https://d.example/d", "score": 0.4}'
        ']}'
    )
    with patch("llm_helpers.call_llm",
               return_value=(fake, {"in_tokens": 200, "out_tokens": 50, "latency_ms": 1500})):
        urls, meta = f._llm_rerank_candidates(
            "q", _SAMPLE_CANDIDATES, top_k=3,
        )
    assert urls == [
        "https://c.example/c.pdf",
        "https://a.example/a.pdf",
        "https://d.example/d",
    ]
    assert meta["used"] is True
    assert meta["n_candidates"] == 4
    assert meta["n_kept"] == 3


def test_rerank_unknown_url_skipped():
    """URL non in candidates viene scartato."""
    fake = (
        '{"top": ['
        '{"url": "https://hallucinated.example/x", "score": 1.0},'
        '{"url": "https://b.example/b.html", "score": 0.6}'
        ']}'
    )
    with patch("llm_helpers.call_llm",
               return_value=(fake, {"in_tokens": 200, "out_tokens": 50, "latency_ms": 1500})):
        urls, meta = f._llm_rerank_candidates(
            "q", _SAMPLE_CANDIDATES, top_k=5,
        )
    assert urls == ["https://b.example/b.html"]
    assert meta["used"] is True


def test_rerank_top_empty_is_authoritative():
    """The prompt explicitly uses an empty list to reject all candidates."""
    fake = '{"top": []}'
    with patch("llm_helpers.call_llm",
               return_value=(fake, {"in_tokens": 200, "out_tokens": 5, "latency_ms": 1500})):
        urls, meta = f._llm_rerank_candidates(
            "q", _SAMPLE_CANDIDATES, top_k=3,
        )
    assert urls == []
    assert meta["used"] is True
    assert meta["reason"] == "no_relevant_candidates"
    assert meta["n_candidates"] == 4
    assert meta["n_kept"] == 0
    assert meta["latency_ms"] == 1500


def test_rerank_code_fence_stripped():
    """Output con code fence ```json...``` viene parsato."""
    fake = (
        "```json\n"
        '{"top": ['
        '{"url": "https://a.example/a.pdf", "score": 0.9}'
        ']}\n```'
    )
    with patch("llm_helpers.call_llm",
               return_value=(fake, {"in_tokens": 200, "out_tokens": 50, "latency_ms": 1500})):
        urls, meta = f._llm_rerank_candidates(
            "q", _SAMPLE_CANDIDATES, top_k=3,
        )
    assert urls == ["https://a.example/a.pdf"]
    assert meta["used"] is True


def test_rerank_passes_real_deadline_and_accepts_wide_candidate_payload():
    candidates = [{**c, "snippet": "x" * 600} for c in _SAMPLE_CANDIDATES] * 8
    with patch("llm_helpers.call_llm", return_value=('{"top": []}', {})) as model:
        f._llm_rerank_candidates("query", candidates)
    kwargs = model.call_args.kwargs
    assert kwargs["timeout_s"] == f._RERANK_TIMEOUT_S
    assert kwargs["max_query_chars"] >= len(json.dumps(model.call_args.args[0]))


def test_rerank_rejects_invalid_and_irrelevant_scores():
    fake = json.dumps({"top": [
        {"url": _SAMPLE_CANDIDATES[0]["url"], "score": 0.2},
        {"url": _SAMPLE_CANDIDATES[1]["url"], "score": float("nan")},
        {"url": _SAMPLE_CANDIDATES[2]["url"], "score": True},
        {"url": _SAMPLE_CANDIDATES[3]["url"], "score": 0.8},
    ]})
    with patch("llm_helpers.call_llm", return_value=(fake, {})):
        urls, meta = f._llm_rerank_candidates("q", _SAMPLE_CANDIDATES)
    assert urls == [_SAMPLE_CANDIDATES[3]["url"]]
    assert meta["scores"] == {_SAMPLE_CANDIDATES[3]["url"]: 0.8}

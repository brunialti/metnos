"""Regression tests: classifier failures must never invent valid labels."""
from __future__ import annotations

import json
import sys
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import classify_entries as ce


def _items():
    return [
        {"subject": "A", "body_preview": "first"},
        {"subject": "B", "body_preview": "second"},
    ]


def test_llm_exception_is_partial_and_entries_stay_unclassified(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise RuntimeError("model offline")

    monkeypatch.setattr(ce, "_classify_batch", _fail)
    result = ce.handle_classify_entries({
        "entries": _items(), "classes": ["low", "high"],
        "batch_size": 2,
    })

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "partial" not in result
    assert result["error_class"] == "classification_incomplete"
    assert result["counts"] == {"low": 0, "high": 0}
    assert result["llm_classified"] == 0
    assert result["unclassified"] == 2
    assert result["failed_entries"] == [0, 1]
    assert all("relevance" not in entry for entry in result["entries"])


def test_timeout_names_the_unavailable_model_and_action(monkeypatch):
    monkeypatch.setattr(
        ce, "_classify_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    result = ce.handle_classify_entries({
        "entries": _items(), "classes": ["low", "high"],
    })

    assert result["ok"] is False
    assert result["error_class"] == "provider_unavailable"
    assert result["error_code"] == "ERR_LLM_UNAVAILABLE"
    assert "servizio LLM" in result["error"]


def test_large_structured_input_is_packed_below_llm_boundary(monkeypatch):
    seen = []

    def classify(items, *_args, fields=None, **_kwargs):
        projected = [ce._project_entry(item, fields) for item in items]
        seen.append(len(json.dumps(projected, ensure_ascii=False)))
        return ["low"] * len(items), {
            "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
        }

    monkeypatch.setattr(ce, "_classify_batch", classify)
    entries = [
        {"subject": f"mail {index}", "body_preview": "x" * 700}
        for index in range(25)
    ]
    result = ce.handle_classify_entries({
        "entries": entries, "classes": ["low", "high"], "batch_size": 30,
    })

    assert result["ok"] is True
    assert len(seen) >= 2
    assert max(seen) <= ce.CLASSIFY_BATCH_PACK_CHARS


def test_classify_call_has_explicit_payload_and_time_budget(monkeypatch):
    captured = {}

    def call(query, prompt, **kwargs):
        captured.update(kwargs)
        return '["low", "high"]', {
            "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
        }

    monkeypatch.setattr(ce, "call_llm", call)
    labels, _meta = ce._classify_batch(
        _items(), "relevance", ["low", "high"], "criterion", "email",
        "fast", ["subject", "body_preview"],
    )

    assert labels == ["low", "high"]
    assert captured["max_query_chars"] == ce.CLASSIFY_QUERY_MAX_CHARS
    assert captured["timeout_s"] == ce.CLASSIFY_LLM_TIMEOUT_S


def test_importance_uses_operational_email_criterion(monkeypatch):
    captured = {}

    def classify(items, dimension, classes, criterion, *_args, **_kwargs):
        captured.update({
            "dimension": dimension,
            "classes": classes,
            "criterion": criterion,
        })
        return ["low"] * len(items), {
            "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
        }

    monkeypatch.setattr(ce, "_classify_batch", classify)
    result = ce.handle_classify_entries({
        "entries": _items(),
        "dimension": "importance",
        "data_kind": "email",
    })

    assert result["ok"] is True
    assert captured["classes"] == ["important", "normal", "low"]
    assert "newsletter" in captured["criterion"]
    assert "movimenti finanziari" in captured["criterion"]


def test_importance_deterministically_demotes_mailing_lists(monkeypatch):
    seen = []

    def classify(items, *_args, **_kwargs):
        seen.extend(item["subject"] for item in items)
        return ["important"] * len(items), {
            "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
        }

    monkeypatch.setattr(ce, "_classify_batch", classify)
    result = ce.handle_classify_entries({
        "entries": [
            {"subject": "LinkedIn jobs", "category_hints": ["list"]},
            {"subject": "Bank withdrawal", "category_hints": []},
        ],
        "dimension": "importance",
        "data_kind": "email",
    })

    assert result["ok"] is True
    assert seen == ["Bank withdrawal"]
    assert result["entries"][0]["importance"] == "low"
    assert result["entries"][1]["importance"] == "important"
    assert result["pre_filtered"] == 1


def test_importance_profile_does_not_leak_email_semantics_to_files(monkeypatch):
    captured = {}

    def classify(_items, _dimension, _classes, criterion, *_args, **_kwargs):
        captured["criterion"] = criterion
        return ["normal"], {
            "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
        }

    monkeypatch.setattr(ce, "_classify_batch", classify)
    result = ce.handle_classify_entries({
        "entries": [{"name": "report.pdf", "kind": "file", "size": 4}],
        "dimension": "importance",
        "classes": ["important", "normal", "low"],
        "data_kind": "file",
    })

    assert result["ok"] is True
    assert "fatture" not in captured["criterion"]
    assert "dimensione «importance»" in captured["criterion"]


def test_english_turn_uses_english_template_and_profile(monkeypatch):
    captured = {}

    def classify(_items, dimension, classes, criterion, kind, *_args,
                 lang=None, **_kwargs):
        captured["criterion"] = criterion
        captured["prompt"] = ce._build_prompt(
            dimension, classes, criterion, kind, 1, lang=lang)
        return ["normal"], {
            "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
        }

    monkeypatch.setattr(ce, "_classify_batch", classify)
    result = ce.handle_classify_entries({
        "entries": [{"from": "bank@example.test", "subject": "Alert"}],
        "dimension": "importance",
        "data_kind": "email",
        "_lang": "en",
    })

    assert result["ok"] is True
    assert "financial transactions" in captured["criterion"]
    assert "movimenti finanziari" not in captured["criterion"]
    assert "Classify 1 entries" in captured["prompt"]
    assert "Classifica 1 entries" not in captured["prompt"]


def test_unparseable_batch_does_not_fall_back_to_last_class(monkeypatch):
    monkeypatch.setattr(
        ce, "_classify_batch",
        lambda *_a, **_k: (None, {
            "in_tokens": 10, "out_tokens": 3, "latency_ms": 5,
        }),
    )
    result = ce.handle_classify_entries({
        "entries": _items(), "classes": ["low", "high"],
    })

    assert result["ok"] is False
    assert result["counts"]["high"] == 0
    assert result["coverage"] == 0
    assert all("relevance" not in entry for entry in result["entries"])


def test_successful_batches_keep_existing_contract(monkeypatch):
    monkeypatch.setattr(
        ce, "_classify_batch",
        lambda *_a, **_k: (["low", "high"], {
            "in_tokens": 10, "out_tokens": 3, "latency_ms": 5,
            "model": "stub",
        }),
    )
    result = ce.handle_classify_entries({
        "entries": _items(), "classes": ["low", "high"],
    })

    assert result["ok"] is True
    assert result["counts"] == {"low": 1, "high": 1}
    assert result["llm_classified"] == 2
    assert result["unclassified"] == 0
    assert "partial" not in result


def test_mixed_batch_failure_preserves_only_observed_labels(monkeypatch):
    calls = 0

    def classify(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["low", "high"], {
                "in_tokens": 4, "out_tokens": 2, "latency_ms": 1}
        raise RuntimeError("second batch offline")

    monkeypatch.setattr(ce, "_classify_batch", classify)
    entries = _items() + [
        {"subject": "C", "body_preview": "third"},
        {"subject": "D", "body_preview": "fourth"},
    ]
    result = ce.handle_classify_entries({
        "entries": entries, "classes": ["low", "high"], "batch_size": 2,
    })

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["status"] == "partial"
    assert result["coverage"] == 0.5
    assert result["failed_entries"] == [2, 3]
    assert result["counts"] == {"low": 1, "high": 1}
    assert "relevance" in result["entries"][0]
    assert "relevance" not in result["entries"][2]

"""Test proposal_evaluator (ADR 0122).

Mock proposte synth con shape diversi e verifica killer/score/verdict.
Determinismo §7.9 — niente LLM, niente network.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from proposal_evaluator import (
    AFFINITY_OVERLAP_THRESHOLD_EVALUATOR,
    EvaluationResult,
    SCORE_ACCEPT,
    _decidability,
    evaluate_proposal,
)


def _make_proposal(
    *,
    name="find_files_size",
    expected_name=None,
    user_query="trova file grandi",
    intent="Trova file con size > 100MB",
    final_state="synthesized",
    affinity=None,
    description="Trova file di grandi dimensioni con error_class missing/invalid.",
    code='def invoke(args):\n    if "paths" not in args: raise ValueError("error_class: missing")\n    if not args["paths"]: raise ValueError("error_class: invalid")\n    return {"ok": True, "entries": [], "truncated": False}',
    args_required=None,
    args_properties=None,
    capabilities=None,
    reverse_pattern=None,
    tests=None,
    path_hash="ab12cd34ef560000",
    path_steps=None,
    path_n_steps=2,
    path_eta_p50_ms=4000,
    path_eta_p95_ms=6000,
    path_call_count_60d=50,
    new_executor_latency_p50_ms=1500,
    ts_start=None,
):
    """Costruisce un dict proposal-like che `evaluate_proposal` puo' processare."""
    affinity = affinity if affinity is not None else ["dimensione", "size", "grandi", "files"]
    if path_steps is None:
        path_steps = ["find_files", "filter_entries"]
    if tests is None:
        tests = [{"name": "happy"}, {"name": "empty"}, {"name": "invalid"}]
    return {
        "id": f"{int(time.time())}_{name}",
        "expected_name": expected_name or name,
        "intent": intent,
        "user_query": user_query,
        "ts_start": ts_start if ts_start is not None else time.time(),
        "elapsed_s": 100.0,
        "final_state": final_state,
        "name": name,
        "abandon_reason": None,
        "path_hash": path_hash,
        "path_steps": path_steps,
        "path_n_steps": path_n_steps,
        "path_eta_p50_ms": path_eta_p50_ms,
        "path_eta_p95_ms": path_eta_p95_ms,
        "path_call_count_60d": path_call_count_60d,
        "new_executor_latency_p50_ms": new_executor_latency_p50_ms,
        "stages": [
            {
                "stage": 1, "success": True, "latency_ms": 100, "error": None,
                "output": {
                    "name": name,
                    "action": name.split("_")[0],
                    "object": name.split("_")[1] if "_" in name else "files",
                    "qualifier": "_".join(name.split("_")[2:]) or None,
                    "revertible": False,
                    "critical": False,
                    "target_kind": "host",
                },
            },
            {
                "stage": 2, "success": True, "latency_ms": 100, "error": None,
                "output": {
                    "args_required": args_required if args_required is not None else ["paths"],
                    "args_properties": args_properties if args_properties is not None else {
                        "paths": {"type": "array", "description": "lista path"},
                    },
                    "capabilities": capabilities or [],
                    "reverse_pattern": reverse_pattern,
                },
            },
            {
                "stage": 3, "success": True, "latency_ms": 100, "error": None,
                "output": {"tests": tests},
            },
            {
                "stage": 4, "success": True, "latency_ms": 100, "error": None,
                "output": {"description": description, "affinity": affinity},
            },
            {
                "stage": 5, "success": True, "latency_ms": 100, "error": None,
                "output": {"code": code},
            },
        ],
    }


def _make_catalog(
    *,
    handcrafted: dict | None = None,
    synth: dict | None = None,
):
    """Builds a minimal catalog-like with `executors` dict."""
    execs = {}
    handcrafted = handcrafted or {}
    synth = synth or {}
    for n, aff in handcrafted.items():
        execs[n] = SimpleNamespace(
            name=n, affinity=aff,
            manifest_path=Path(__file__).resolve().parents[3] / "executors" / n / "manifest.toml",
            reverse_pattern=None,
        )
    for n, payload in synth.items():
        aff = payload.get("affinity", []) if isinstance(payload, dict) else payload
        rp = payload.get("reverse_pattern") if isinstance(payload, dict) else None
        mt = payload.get("mtime", time.time() - 86400) if isinstance(payload, dict) else time.time() - 86400
        # write a fake manifest for stat()
        execs[n] = SimpleNamespace(
            name=n, affinity=aff,
            manifest_path=Path(f"/tmp/synth_{n}_manifest.toml"),
            reverse_pattern=rp,
            _mtime=mt,
        )
    return SimpleNamespace(executors=execs)


def _write_proposal(tmp_path: Path, proposal: dict) -> Path:
    p = tmp_path / f"{proposal['id']}.json"
    p.write_text(json.dumps(proposal, ensure_ascii=False, indent=2))
    return p


@pytest.fixture
def isolated_detection_lexicon(tmp_path, monkeypatch):
    import detection_lexicon as dl

    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache_data_version", None)
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setattr(dl._C, "INSTANCE_LANG", "it")
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    yield dl
    if dl._conn is not None:
        dl._conn.close()
        dl._conn = None
    dl._cache.clear()
    dl._regex_cache.clear()


def _materialize_mapping(dl, concept: str, lang: str, *, key: str,
                         form: str) -> None:
    source = dl.resource_for_language(
        concept, "en", fallback=False, ready_only=True,
    )["payload"]
    translated = {
        canonical: [
            form if canonical == key else f"{lang}_{index}_{canonical}"
        ]
        for index, canonical in enumerate(source)
    }
    dl.mark_for_translation(concept, lang, source_lang="en")
    dl.set_translated(concept, lang, translated)


def test_decidability_it_en_are_equivalent_canonical_queries(
    isolated_detection_lexicon, monkeypatch,
) -> None:
    import prefilter

    dl = isolated_detection_lexicon
    captured = []

    def rank(query, _catalog, intent, k=0):
        captured.append((query, intent, k))
        return [SimpleNamespace(name="find_files_size")]

    monkeypatch.setattr(prefilter, "rank_with_intent", rank)
    proposal = {"name": "find_files_size"}
    cases = (("it", "trova file grandi"), ("en", "find large files"))
    for lang, query in cases:
        monkeypatch.setattr(dl._C, "INSTANCE_LANG", lang)
        dl._invalidate()
        pct, info = _decidability(
            {**proposal, "user_query": query}, catalog=object(),
        )
        assert pct == 1.0
        assert info["decidability_evaluable"] is True
        assert info["decidability_queries_total"] == 1
        assert info["decidability_queries_pass"] == 1
        assert captured[-1][1] == {"verb": "find", "object": "files"}


def test_synthesized_intent_summary_is_not_admission_evidence(monkeypatch) -> None:
    import prefilter

    monkeypatch.setattr(
        prefilter, "rank_with_intent",
        lambda *_args, **_kwargs: pytest.fail("intent summary reached ranker"),
    )

    pct, info = _decidability(
        {"name": "find_files_size", "intent": "trova file grandi"},
        catalog=object(),
    )

    assert pct == 0.0
    assert info["decidability_evaluable"] is False
    assert info["decidability_status"] == "input_unavailable"


def test_decidability_uses_complete_ready_third_language(
    isolated_detection_lexicon, monkeypatch,
) -> None:
    import prefilter

    dl = isolated_detection_lexicon
    monkeypatch.setattr(dl._C, "INSTANCE_LANG", "zz")
    dl.enqueue_language("zz")
    _materialize_mapping(
        dl, "prefilter.verb_canonical", "zz", key="find", form="zzfind",
    )
    _materialize_mapping(
        dl, "vocab.action_surfaces", "zz", key="find", form="zzseek",
    )
    _materialize_mapping(
        dl, "prefilter.object_hint", "zz", key="files", form="zzfiles",
    )
    dl._invalidate()
    captured = []

    def rank(_query, _catalog, intent, k=0):
        captured.append((intent, k))
        return [SimpleNamespace(name="find_files_size")]

    monkeypatch.setattr(prefilter, "rank_with_intent", rank)
    pct, info = _decidability(
        {"name": "find_files_size", "user_query": "zzfind zzfiles"},
        catalog=object(),
    )

    assert pct == 1.0
    assert info["decidability_lexicon_ready"] is True
    assert info["decidability_status"] == "ranked"
    assert captured == [({"verb": "find", "object": "files"}, 5)]


def test_pending_language_never_produces_positive_admission_signal(
    tmp_path, isolated_detection_lexicon, monkeypatch,
) -> None:
    import prefilter

    dl = isolated_detection_lexicon
    monkeypatch.setattr(dl._C, "INSTANCE_LANG", "zz")
    dl.enqueue_language("zz")
    dl._invalidate()
    monkeypatch.setattr(
        prefilter, "rank_with_intent",
        lambda *_args, **_kwargs: pytest.fail("pending lexicon reached ranker"),
    )
    prop = _make_proposal(
        name="find_files_size",
        path_eta_p50_ms=6000,
        new_executor_latency_p50_ms=1500,
        path_call_count_60d=100,
        path_n_steps=4,
    )
    prop["pipeline_terminal"] = True
    cat = _make_catalog()
    cat.executors[prop["name"]] = SimpleNamespace(
        name=prop["name"], affinity=prop["stages"][3]["output"]["affinity"],
        manifest_path=Path("/tmp/find_files_size/manifest.toml"),
        reverse_pattern=None, capabilities=[], description="",
        lifecycle="active",
    )

    result = evaluate_proposal(
        _write_proposal(tmp_path, prop), catalog=cat, audit=False,
    )

    assert result.signals["decidability_lexicon_ready"] is False
    assert result.signals["decidability_status"] == "lexicon_unavailable"
    assert result.signals["decidability_pct"] == 0.0
    assert result.signals.get("noising_top10_pct", 0.0) == 0.0
    assert result.score >= SCORE_ACCEPT
    assert result.verdict == "gray"


# ─── KILLER 1: inflation ──────────────────────────────────────────────


def test_inflation_killer_unknown_verb(tmp_path):
    prop = _make_proposal(name="hijack_files")  # 'hijack' not in ACTIONS
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert result.verdict == "reject"
    assert "inflation" in result.killers_triggered


def test_inflation_killer_unknown_object(tmp_path):
    prop = _make_proposal(name="find_widgets")  # 'widgets' not in OBJECTS
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert result.verdict == "reject"
    assert "inflation" in result.killers_triggered


def test_inflation_killer_unknown_qualifier(tmp_path):
    prop = _make_proposal(name="find_files_unknownqual")
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert result.verdict == "reject"
    assert "inflation" in result.killers_triggered


def test_inflation_passes_with_valid_compound_name(tmp_path):
    prop = _make_proposal(name="find_files_size")
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "inflation" not in result.killers_triggered


# ─── KILLER 2: affinity overlap ───────────────────────────────────────


def test_affinity_overlap_killer_vs_handcrafted(tmp_path):
    """Synth con affinity 80% in comune con find_urls (handcrafted)."""
    prop = _make_proposal(
        name="find_files_size",
        affinity=["url", "web", "internet", "online", "search"],
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog(handcrafted={
        "find_urls": ["url", "web", "internet", "online", "search"],
    })
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "overlap" in result.killers_triggered
    assert result.signals["overlap"]["best_jaccard"] >= AFFINITY_OVERLAP_THRESHOLD_EVALUATOR


def test_affinity_overlap_threshold_below_passes(tmp_path):
    prop = _make_proposal(
        name="find_files_size",
        affinity=["a", "b", "c", "d", "e"],
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog(handcrafted={
        "find_urls": ["a", "z", "y", "x", "w"],
    })
    # Jaccard 1/9 ≈ 0.11, well below 0.4.
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "overlap" not in result.killers_triggered


# ─── KILLER 3: test pass rate ─────────────────────────────────────────


def test_test_pass_rate_killer_when_final_state_abandoned(tmp_path):
    prop = _make_proposal(final_state="abandoned")
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "defectiveness" in result.killers_triggered


def test_test_pass_rate_killer_when_no_tests(tmp_path):
    prop = _make_proposal(tests=[])
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "defectiveness" in result.killers_triggered


# ─── KILLER 4: reversibility parity ───────────────────────────────────


def test_reversibility_parity_killer(tmp_path):
    """Path conteneva move_files (reverse_pattern) ma la nuova non lo dichiara."""
    prop = _make_proposal(
        name="find_files_size",
        reverse_pattern=None,
        path_steps=["find_files", "move_files"],
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    # Manualmente metto un executor con reverse_pattern nel catalog
    cat.executors["move_files"] = SimpleNamespace(
        name="move_files", affinity=["sposta", "move"],
        manifest_path=Path(__file__).resolve().parents[3] / "executors/move_files/manifest.toml",
        reverse_pattern="swap_src_dst",
    )
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "reversibility" in result.killers_triggered


def test_reversibility_parity_passes_when_new_has_pattern(tmp_path):
    prop = _make_proposal(
        name="move_files_loc",
        reverse_pattern="swap_src_dst",
        path_steps=["find_files", "move_files"],
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    cat.executors["move_files"] = SimpleNamespace(
        name="move_files", affinity=[],
        manifest_path=Path(__file__).resolve().parents[3] / "executors/move_files/manifest.toml",
        reverse_pattern="swap_src_dst",
    )
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "reversibility" not in result.killers_triggered


# ─── KILLER 5: error_class discriminability ───────────────────────────


def test_error_class_killer_when_only_one_class(tmp_path):
    prop = _make_proposal(
        description="Trova file. Solleva missing se il path manca.",
        code="def invoke(args):\n    return {'ok': True}",
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "discriminability" in result.killers_triggered


def test_error_class_passes_with_two_distinct_classes(tmp_path):
    prop = _make_proposal(
        description='error_class="missing" o error_class="invalid".',
        code="def invoke(args): return {}",
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "discriminability" not in result.killers_triggered


# ─── KILLER 6: observation_schema_stability ───────────────────────────


def test_observation_schema_killer_for_transformative_returning_entries(tmp_path):
    # Verbo trasformativo con code che ritorna `entries`.
    prop = _make_proposal(
        name="move_files_loc",
        code='def invoke(args):\n    return {"ok": True, "entries": [{}, {}]}',
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "plugability" in result.killers_triggered


def test_observation_schema_passes_for_transformative_with_results(tmp_path):
    prop = _make_proposal(
        name="move_files_loc",
        code='def invoke(args):\n    return {"ok": True, "results": [{}], "entries": []}',
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "plugability" not in result.killers_triggered


# ─── SCORE WEIGHTED → ACCEPT ──────────────────────────────────────────


def test_accept_path_with_high_signals(tmp_path):
    """Tutti i signal positivi (eta>=2, freq>=30, decid>=0.7, ...) → ACCEPT.

    Riempio un catalog con la nuova proposta in modo che il prefilter
    deterministico la veda; in alternativa lo bypassiamo aumentando la
    decidability artificialmente non dichiarando il catalog (skip path).
    """
    prop = _make_proposal(
        name="find_files_size",
        path_eta_p50_ms=4500,  # speedup 3.0× vs 1500ms default
        new_executor_latency_p50_ms=1500,
        path_call_count_60d=100,
        affinity=["dimensione", "size", "grandi", "byte", "filesize"],
        path_n_steps=4,  # token saving = 75%
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    # Inserisco la "find_files_size" nel catalog cosi' il prefilter la trova
    cat.executors["find_files_size"] = SimpleNamespace(
        name="find_files_size",
        affinity=["dimensione", "size", "grandi", "byte", "filesize", "trova", "file"],
        manifest_path=Path("/tmp/find_files_size/manifest.toml"),
        reverse_pattern=None,
        # campi richiesti da prefilter
        capabilities=[], description=prop["stages"][3]["output"]["description"],
        lifecycle="active",
    )
    # Evaluator bypasses prefilter when import fails; ensure it's available.
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    # Senza far girare il prefilter live, il signal noising puo' essere basso.
    # Verdict piu' robusto: NO killer + score positive.
    assert result.killers_triggered == []
    assert result.signals.get("eta_speedup", 0) >= 2.0
    assert result.signals.get("call_freq_60d") == 100
    assert result.score >= 2  # almeno eta + call_freq + saving


def test_reject_path_with_low_signals(tmp_path):
    """Freq=0 su path multi-step → dal 2/7 il killer LAYER_OVERLAP scatta
    (covered by L1: non highly-requested); il verdetto resta REJECT, con
    causa esplicita invece del solo score."""
    prop = _make_proposal(
        name="find_files_size",
        path_eta_p50_ms=1000,  # speedup 0.66 < 1.2 → -1
        new_executor_latency_p50_ms=1500,
        path_call_count_60d=0,  # < soglia highly-requested → killer
        path_n_steps=1,  # token saving = 0
        affinity=["a", "b", "c", "d", "e"],  # no overlap
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "layer_overlap" in result.killers_triggered
    assert result.verdict == "reject"
    # lo score resta calcolato (audit): eta -1, freq -0.5, decid -1 → basso
    assert result.score <= -1.5


def test_gray_path(tmp_path):
    """Score in zona grigia → GRAY."""
    prop = _make_proposal(
        name="find_files_size",
        path_eta_p50_ms=2000,  # speedup 1.33 → 0
        new_executor_latency_p50_ms=1500,
        path_call_count_60d=35,  # >= 30: +1.5 e NIENTE killer layer_overlap
        path_n_steps=2,  # token saving 50% → +1
        affinity=["a", "b", "c", "d", "e"],
    )
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    # Killers vuoti, score in -2..4 → gray
    assert result.killers_triggered == []
    assert result.verdict in ("gray", "reject", "accept")


# ─── audit log ────────────────────────────────────────────────────────


def test_audit_log_appends_line(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr("proposal_evaluator._AUDIT_DIR", audit_dir)
    prop = _make_proposal()
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    evaluate_proposal(pf, catalog=cat, audit=True)
    log_file = audit_dir / "proposal_evaluator.jsonl"
    assert log_file.exists()
    lines = [ln for ln in log_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["proposal_id"] == prop["id"]
    assert rec["verdict"] in ("accept", "gray", "reject")


def test_no_audit_when_flag_false(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr("proposal_evaluator._AUDIT_DIR", audit_dir)
    prop = _make_proposal()
    pf = _write_proposal(tmp_path, prop)
    cat = _make_catalog()
    evaluate_proposal(pf, catalog=cat, audit=False)
    log_file = audit_dir / "proposal_evaluator.jsonl"
    assert not log_file.exists()


# ─── EvaluationResult dataclass ───────────────────────────────────────


def test_evaluation_result_to_dict_roundtrip():
    r = EvaluationResult(
        proposal_id="x", name="find_files_size", verdict="accept",
        score=5.0, killers_triggered=[], signals={"eta_speedup": 2.5},
        rationale="ok",
    )
    d = r.to_dict()
    assert d["proposal_id"] == "x"
    assert d["verdict"] == "accept"
    assert d["signals"]["eta_speedup"] == 2.5


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        evaluate_proposal("/tmp/nonexistent_proposal_zzz.json")


def test_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    with pytest.raises(ValueError):
        evaluate_proposal(p, audit=False)



# ─── NEW KILLERS: TRIVIALITY / CAPABILITIES / SAFETY / TESTABILITY ────


class _ExecStub:
    def __init__(self, name, args_schema=None, capabilities=None,
                 reverse_pattern=None):
        self.name = name
        self.args_schema = args_schema or {}
        self.capabilities = capabilities or []
        self.reverse_pattern = reverse_pattern


class _CatStub:
    def __init__(self, executors):
        self.executors = executors


def test_triviality_killer_when_path_len_1_args_subset(tmp_path):
    prop = _make_proposal(
        name="list_processes",
        path_steps=["get_processes"],
        args_properties={"top_n": {"type": "integer"}},
    )
    cat = _CatStub({
        "get_processes": _ExecStub(
            "get_processes",
            args_schema={"properties": {
                "top_n": {"type": "integer"},
                "sort_by": {"type": "string"},
                "include_health": {"type": "boolean"},
            }},
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "triviality" in result.killers_triggered
    assert result.signals["triviality"]["subset"] is True


def test_triviality_passes_when_path_len_above_1(tmp_path):
    prop = _make_proposal(
        name="find_things",
        path_steps=["a", "b"],
        args_properties={"new_arg": {"type": "string"}},
    )
    cat = _CatStub({
        "a": _ExecStub("a", args_schema={"properties": {"new_arg": {}}}),
        "b": _ExecStub("b", args_schema={"properties": {"x": {}}}),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "triviality" not in result.killers_triggered


def test_capabilities_killer_escalation(tmp_path):
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["read_files"],
        capabilities=[{"name": "fs:write", "hint": ["~/**"]}],
    )
    cat = _CatStub({
        "read_files": _ExecStub(
            "read_files",
            capabilities=[{"name": "fs:read", "hint": ["~/**"]}],
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "capabilities" in result.killers_triggered


def test_capabilities_passes_when_subset(tmp_path):
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["read_files"],
        capabilities=[{"name": "fs:read", "hint": ["~/**"]}],
    )
    cat = _CatStub({
        "read_files": _ExecStub(
            "read_files",
            capabilities=[{"name": "fs:read", "hint": ["~/**"]}],
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "capabilities" not in result.killers_triggered


def test_safety_killer_signatures_without_admin(tmp_path):
    prop = _make_proposal(
        name="set_signatures_blacklist",
        capabilities=[{"name": "fs:write"}],
    )
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, audit=False)
    assert "safety" in result.killers_triggered


def test_safety_passes_when_admin_capability(tmp_path):
    prop = _make_proposal(
        name="set_signatures_blacklist",
        capabilities=[{"name": "admin"}, {"name": "fs:write"}],
    )
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, audit=False)
    assert "safety" not in result.killers_triggered


def test_testability_killer_loses_dry_run(tmp_path):
    prop = _make_proposal(
        name="move_files",
        path_steps=["move_files_old"],
        args_properties={"src": {}, "dst": {}},
    )
    cat = _CatStub({
        "move_files_old": _ExecStub(
            "move_files_old",
            args_schema={"properties": {
                "src": {}, "dst": {}, "dry_run": {"type": "boolean"},
            }},
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "testability" in result.killers_triggered


def test_testability_passes_when_dry_run_preserved(tmp_path):
    prop = _make_proposal(
        name="find_files_size",  # verbo non-trasformativo
        path_steps=["find_files_old"],
        args_properties={"q": {}, "dry_run": {"type": "boolean"}},
    )
    cat = _CatStub({
        "find_files_old": _ExecStub(
            "find_files_old",
            args_schema={"properties": {
                "q": {}, "dry_run": {"type": "boolean"},
            }},
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "testability" not in result.killers_triggered


# ─── Killer 11: LAYER_OVERLAP (regola dei livelli, 2/7/2026) ──────────


def test_layer_overlap_default_bake_equal_keys(tmp_path):
    """Default-bake a PARITÀ di chiavi args (il buco di triviality, che
    esige il sottoinsieme stretto): superseded by L0."""
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["find_files"],
        path_call_count_60d=500,  # anche highly-requested: L0 vince comunque
        args_properties={
            "pattern": {"type": "string", "default": "*.py"},  # BAKED
            "base_path": {"type": "string"},
        },
    )
    cat = _CatStub({
        "find_files": _ExecStub(
            "find_files",
            args_schema={"properties": {
                "pattern": {"type": "string"},
                "base_path": {"type": "string"},
            }},
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "layer_overlap" in result.killers_triggered
    assert result.signals["layer_overlap"]["baked_defaults"] == ["pattern"]
    assert result.verdict == "reject"


def test_layer_overlap_not_triggered_same_default(tmp_path):
    """Default IDENTICO al parent = nessun bake nuovo → non triggera."""
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["find_files"],
        path_call_count_60d=500,
        args_properties={"pattern": {"type": "string", "default": "*"}},
    )
    cat = _CatStub({
        "find_files": _ExecStub(
            "find_files",
            args_schema={"properties": {
                "pattern": {"type": "string", "default": "*"},
            }},
        ),
    })
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=cat, audit=False)
    assert "layer_overlap" not in result.killers_triggered


def test_layer_overlap_multistep_low_freq(tmp_path):
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["find_files", "filter_entries"],
        path_call_count_60d=10,  # < 30 → covered by L1
    )
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=_make_catalog(), audit=False)
    assert "layer_overlap" in result.killers_triggered
    assert result.verdict == "reject"


def test_layer_overlap_multistep_freq_absent_not_judged(tmp_path):
    # Mai bloccare senza evidenza: senza path_call_count_60d non giudica.
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["find_files", "filter_entries"],
        path_call_count_60d=None,
    )
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=_make_catalog(), audit=False)
    assert "layer_overlap" not in result.killers_triggered


def test_layer_overlap_env_zero_disables(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_HIGHLY_REQUESTED_FREQ_60D", "0")
    prop = _make_proposal(
        name="find_files_size",
        path_steps=["find_files", "filter_entries"],
        path_call_count_60d=1,
    )
    pf = _write_proposal(tmp_path, prop)
    result = evaluate_proposal(pf, catalog=_make_catalog(), audit=False)
    assert "layer_overlap" not in result.killers_triggered

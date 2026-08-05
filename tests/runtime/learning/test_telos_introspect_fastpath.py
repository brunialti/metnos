from __future__ import annotations

import json
from types import SimpleNamespace

import alignment_engine
import telos_introspect
import telos_lenses
import telos_loader
import telos_proposals_store


def _telos(telos_id: str = "t.tempo") -> SimpleNamespace:
    return SimpleNamespace(
        id=telos_id,
        phrase=f"telos {telos_id}",
        notes="test",
        weight=1.0,
        activation_threshold=0.2,
    )


def _proposal(target: str = "compute_entries") -> telos_lenses.LensProposal:
    return telos_lenses.LensProposal(
        lens="scamper",
        operator="S",
        executor_target=target,
        telos_id="t.tempo",
        proposed_action="azione test",
        rationale="rationale test",
    )


def _prepare_run(monkeypatch, tmp_path, proposals) -> object:
    telos = _telos()
    monkeypatch.setattr(
        telos_introspect, "TELEMETRY_PATH",
        tmp_path / "telos_proposals.jsonl",
    )
    monkeypatch.setattr(
        telos_proposals_store, "DECISIONS_PATH",
        tmp_path / "telos_decisions.jsonl",
    )
    monkeypatch.setattr(
        telos_introspect, "_build_mnestoma_summary", lambda **_kw: "none")
    monkeypatch.setattr(
        telos_introspect, "_build_user_patterns", lambda: "none")
    monkeypatch.setattr(
        telos_introspect, "_build_executors_sample", lambda _catalog: [])
    monkeypatch.setattr(telos_loader, "current", lambda: [telos])
    monkeypatch.setattr(
        telos_lenses, "run_lens",
        lambda **_kwargs: [factory() for factory in proposals],
    )
    return telos


def _run(telos, *, evaluate_duplicates: bool = True):
    return telos_introspect.run_for_telos(
        telos,
        catalog=[SimpleNamespace(name="compute_entries")],
        llm_invoke=lambda _prompt: "[]",
        lenses=["scamper"],
        operators=("S",),
        persist=True,
        evaluate_duplicates=evaluate_duplicates,
    )


def _alignment_result():
    return SimpleNamespace(
        expected_alignment=0.7,
        per_telos=[SimpleNamespace(
            telos_id="t.tempo", fit=0.8, why="allineata")],
    )


def test_default_still_evaluates_a_duplicate_before_persist_rejects_it(
        tmp_path, monkeypatch) -> None:
    telos = _prepare_run(monkeypatch, tmp_path, [lambda: _proposal()])
    path = telos_introspect.TELEMETRY_PATH
    path.write_text(json.dumps({
        "executor_target": "compute_entries", "lens": "scamper",
    }) + "\n")
    calls = 0

    def estimate(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _alignment_result()

    monkeypatch.setattr(alignment_engine, "estimate_fit", estimate)
    results = _run(telos)

    assert calls == 1
    assert results[0]["expected_alignment"] == 0.7
    assert results[0]["persisted"] is False
    assert len(path.read_text().splitlines()) == 1


def test_nightly_fastpath_skips_judge_for_stored_append_only_pair(
        tmp_path, monkeypatch) -> None:
    telos = _prepare_run(monkeypatch, tmp_path, [lambda: _proposal()])
    path = telos_introspect.TELEMETRY_PATH
    original = json.dumps({
        "executor_target": "compute_entries", "lens": "scamper",
    }) + "\n"
    path.write_text(original)

    def unexpected_judge(*_args, **_kwargs):
        raise AssertionError("duplicate must not reach the LLM judge")

    monkeypatch.setattr(
        alignment_engine, "estimate_fit", unexpected_judge)
    results = _run(telos, evaluate_duplicates=False)

    assert results[0]["expected_alignment"] == 0.0
    assert results[0]["persisted"] is False
    assert path.read_text() == original


def test_fastpath_updates_pairs_only_after_successful_first_persist(
        tmp_path, monkeypatch) -> None:
    telos = _prepare_run(
        monkeypatch, tmp_path,
        [lambda: _proposal(), lambda: _proposal()],
    )
    calls = 0

    def estimate(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _alignment_result()

    monkeypatch.setattr(alignment_engine, "estimate_fit", estimate)
    results = _run(telos, evaluate_duplicates=False)

    assert calls == 1
    assert [row["persisted"] for row in results] == [True, False]
    stored = telos_introspect.TELEMETRY_PATH.read_text().splitlines()
    assert len(stored) == 1
    assert json.loads(stored[0])["expected_alignment"] == 0.7


def test_run_all_telos_propagates_duplicate_evaluation_policy(
        monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        telos_loader, "current", lambda: [_telos("t.tempo"), _telos("t.ordine")])

    def run_one(telos, **kwargs):
        seen.append((telos.id, kwargs["evaluate_duplicates"]))
        return []

    monkeypatch.setattr(telos_introspect, "run_for_telos", run_one)
    summary = telos_introspect.run_all_telos(evaluate_duplicates=False)

    assert seen == [("t.tempo", False), ("t.ordine", False)]
    assert summary == {
        "telos_count": 2,
        "proposals_total": 0,
        "persisted_total": 0,
        "by_telos": {"t.tempo": 0, "t.ordine": 0},
    }

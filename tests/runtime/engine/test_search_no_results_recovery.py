"""Bounded recovery and honest identity for empty web searches."""
from __future__ import annotations

import pytest

from engine.executor import (
    compute_execution_fingerprint,
    compute_framework_hash,
)
from engine.proposer import _render_recovery_signal
from engine.recovery import SimpleRecovery, classify_error, recovery_signal_for
from engine.recovery_metis import MetisRecovery
from engine.types import Framework, Intent, RunResult, StepRun, StepSpec


def _failed_search_run(*, query="ufficio pubblico Roma", candidates=10) -> RunResult:
    framework = Framework(
        steps=[StepSpec("find_urls", {"search_query": query})],
        final_message="Risultati: ${step1.entries}",
    )
    return RunResult(
        steps=[StepRun(
            step_idx=0,
            tool="find_urls",
            args={"search_query": query},
            result={
                "ok": False,
                "error_class": "search_no_results",
                "search_query": query,
                "entries": [],
                "metadata": {
                    "backend_result_count": candidates,
                    "search_results_used": 0,
                    "rerank": {
                        "used": True,
                        "reason": "no_relevant_candidates",
                        "n_candidates": candidates,
                        "n_kept": 0,
                    },
                },
            },
            ok=False,
            latency_ms=1,
        )],
        final_kind="error",
        framework_hash=compute_framework_hash(framework),
        ok_count=0,
    )


class _CaptureProposer:
    def __init__(self, result: Framework):
        self.result = result
        self.kwargs = None
        self.signal = None

    def propose(self, **kwargs):
        self.kwargs = kwargs
        self.signal = getattr(kwargs["intent"], "_recovery_signal", None)
        return self.result


def test_search_failure_is_recoverable_without_calling_it_malformed():
    run = _failed_search_run()
    assert classify_error(run) == "missing_input"
    signal = recovery_signal_for(run)
    assert signal == {
        "kind": "search_no_results",
        "failure_stage": "relevance_rejected",
        "attempted_query": "ufficio pubblico Roma",
        "candidate_count": 10,
        "accepted_count": 0,
    }


@pytest.mark.parametrize("recovery", [SimpleRecovery(), MetisRecovery()])
def test_search_recovery_keeps_find_urls_and_exposes_signal_once(recovery):
    intent = Intent(verb="find", object="urls", lang="it")
    alternative = Framework(steps=[StepSpec(
        "find_urls", {"search_query": "ente competente sede Roma documento"},
    )])
    proposer = _CaptureProposer(alternative)

    result = recovery.recover(
        failed_run=_failed_search_run(),
        query="trova l'ufficio pubblico competente a Roma",
        intent=intent,
        pool=["find_urls"],
        proposer=proposer,
        llm_call=lambda *_a, **_kw: "",
        catalog=None,
    )

    assert result is alternative
    assert proposer.kwargs["pool"] == ["find_urls"]
    # Shape exclusion would reject every same-tool query. Exact duplicates are
    # rejected later through the execution fingerprint instead.
    assert proposer.kwargs["excluded_hashes"] == set()
    assert proposer.signal["failure_stage"] == "relevance_rejected"
    assert not hasattr(intent, "_recovery_signal")


def test_recovery_prompt_requires_one_materially_different_attempt():
    intent = Intent()
    assert _render_recovery_signal(intent, "it") == ""
    intent._recovery_signal = recovery_signal_for(_failed_search_run())
    rendered = _render_recovery_signal(intent, "it")
    assert "un solo nuovo find_urls" in rendered
    assert "ufficio pubblico Roma" in rendered
    assert "risultati fuori tema" in rendered


def test_execution_fingerprint_tracks_args_but_not_final_prose():
    original = Framework(
        steps=[StepSpec("find_urls", {"search_query": "ufficio Roma"})],
        final_message="Prima formulazione",
    )
    prose_only = Framework(
        steps=[StepSpec("find_urls", {"search_query": "ufficio Roma"})],
        final_message="Testo finale diverso",
    )
    reformulated = Framework(
        steps=[StepSpec("find_urls", {
            "search_query": "ente competente sede Roma documento",
        })],
        final_message="Prima formulazione",
    )

    assert compute_execution_fingerprint(original) == compute_execution_fingerprint(
        prose_only
    )
    assert compute_execution_fingerprint(original) != compute_execution_fingerprint(
        reformulated
    )
    # The established cache/exclusion identity remains shape-based.
    assert compute_framework_hash(original) == compute_framework_hash(reformulated)

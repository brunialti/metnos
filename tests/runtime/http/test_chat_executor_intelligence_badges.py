from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE = _ROOT / "runtime" / "templates" / "chat.html"
_ROUTES = _ROOT / "runtime" / "http_routes_agent.py"


def test_chat_badges_use_catalog_metadata_without_executor_name_table() -> None:
    template = _TEMPLATE.read_text(encoding="utf-8")
    routes = _ROUTES.read_text(encoding="utf-8")
    assert "const EXECUTOR_INTELLIGENCE =" in template
    assert "function fillExecutorBadge" in template
    assert "executorBrainIcon(mode)" in template
    assert "fillExecutorBadge(b, step.tool)" in template
    assert 'getattr(ex, "intelligence", "deterministic")' in routes
    assert "AGENTIC_EXECUTORS" not in template
    assert "LLM_EXECUTORS" not in template


def test_all_three_brain_variants_are_rendered_from_one_svg_family() -> None:
    source = _TEMPLATE.read_text(encoding="utf-8")
    assert "mode === 'deterministic'" in source
    assert "mode === 'agentic'" in source
    assert "{deterministic: 'DET', llm: 'LLM', agentic: 'AGENT'}" in source
    assert "executor-brain" in source
    assert "'1.3 0 19.7 14.7'" in source
    assert "'1.3 1.3 13.4 13.4'" in source
    assert "agent-spark" in source
    assert 'width:16px;height:16px' in source
    assert 'width:23px;height:17px' in source


def test_live_and_recovered_turns_share_the_completed_turn_renderer() -> None:
    source = _TEMPLATE.read_text(encoding="utf-8")
    assert "function renderCompletedTurn(data, options)" in source
    assert "renderCompletedTurn(t, { placeholder: ph" in source
    assert "renderCompletedTurn(payload, { placeholder: ph" in source
    assert "renderCompletedTurn(data, {" in source
    assert "ph.innerHTML = '';" not in source[source.index(
        "function reattachTurnStream"):source.index("let pollTimer")]

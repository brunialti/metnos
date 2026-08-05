from __future__ import annotations

import sys
from pathlib import Path


import prompt_loader


def _render(lang: str) -> str:
    return prompt_loader.get(
        "agentic_sites_action", lang,
        goal_json='{"primitive":"click"}', state_json="{}",
        observed_json='["m1"]', history_json="[]",
        forbidden_code="unrelated_control",
    )


def test_agentic_prompt_catalog_is_language_complete():
    prompt_loader.validate_invariant()
    assert "DEVI: scegliere" in _render("it")
    assert "YOU MUST: choose" in _render("en")
    assert "DEVI: scegliere un solo ID" in prompt_loader.get(
        "agentic_sites_action_system", "it")
    assert "YOU MUST: choose one listed ID" in prompt_loader.get(
        "agentic_sites_action_system", "en")


def test_high_benefit_executor_prompts_are_localized():
    assert "DEVI: trascrivere alla lettera" in prompt_loader.get(
        "agentic_ocr_extract", "it")
    assert "YOU MUST: transcribe every readable" in prompt_loader.get(
        "agentic_ocr_extract", "en")
    it_rank = prompt_loader.get(
        "agentic_image_rerank", "it", query_json='"q"',
        candidates_json="[]")
    en_rank = prompt_loader.get(
        "agentic_image_rerank", "en", query_json='"q"',
        candidates_json="[]")
    assert "DEVI: ordinare i candidati" in it_rank
    assert "YOU MUST: rank the candidates" in en_rank
    assert "DEVI: descrivere in italiano" in prompt_loader.get(
        "vlm_describe_image", "it")
    assert "YOU MUST: describe all visible" in prompt_loader.get(
        "vlm_describe_image", "en")


def test_rejected_pipeline_prompt_is_catalogued_and_new_languages_fall_back():
    values = {
        "hard": True,
        "consec_errors": 3,
        "pipelines": "- find_files → read_files",
    }
    it_text = prompt_loader.get("rejected_pipelines", "it", **values)
    en_text = prompt_loader.get("rejected_pipelines", "en", **values)
    fr_fallback = prompt_loader.get("rejected_pipelines", "fr", **values)
    assert "L'UTENTE HA RIFIUTATO" in it_text
    assert "THE USER REJECTED" in en_text
    assert fr_fallback == en_text

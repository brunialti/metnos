from __future__ import annotations

import i18n
import describe_entries


def test_format_and_grouping_prompts_follow_request_language():
    with i18n.language_context("en"):
        formatting = describe_entries._format_directive("markdown")
        grouping = describe_entries._build_group_directive(
            "category",
            [{"category": "one"}, {"category": "one"},
             {"category": "two"}],
        )

    assert "write all user-facing text in English" in formatting
    assert "create 2 sections" in grouping


def test_new_language_fallback_keeps_target_language_instruction():
    with i18n.language_context("fr"):
        formatting = describe_entries._format_directive("plain")
        grouping = describe_entries._build_group_directive(
            "theme", [{"name": "one"}],
        )

    assert "write all user-facing text in français" in formatting
    assert "write the result in français" in grouping


def test_health_context_prompt_uses_request_language():
    with i18n.language_context("fr"):
        prompt = describe_entries.prompt_loader.get(
            "describe_health_context", i18n.current_lang(),
            health_block="CPU: ok",
        )

    assert "write the response in français" in prompt

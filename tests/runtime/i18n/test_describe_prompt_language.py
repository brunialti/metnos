from __future__ import annotations

import i18n
import describe_entries


def test_format_and_grouping_prompts_follow_instance_language(monkeypatch):
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
    formatting = describe_entries._format_directive("markdown")
    grouping = describe_entries._build_group_directive(
        "category",
        [{"category": "one"}, {"category": "one"},
         {"category": "two"}],
    )

    assert "write all user-facing text in BCP-47 language en" in formatting
    assert "create 2 sections" in grouping


def test_new_instance_language_fallback_keeps_target_instruction(monkeypatch):
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")
    formatting = describe_entries._format_directive("plain")
    grouping = describe_entries._build_group_directive(
        "theme", [{"name": "one"}],
    )

    assert "write all user-facing text in BCP-47 language fr" in formatting
    assert "write the result in BCP-47 language fr" in grouping


def test_health_context_prompt_uses_instance_language(monkeypatch):
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")
    prompt = describe_entries.prompt_loader.get(
        "describe_health_context", i18n.current_lang(),
        health_block="CPU: ok",
    )

    assert "write the response in BCP-47 language fr" in prompt

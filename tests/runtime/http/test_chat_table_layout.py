"""Regressione UI: le tabelle Markdown restano dentro la bolla chat."""

from pathlib import Path
import re


_CHAT = (Path(__file__).resolve().parents[3] / "runtime") / "templates" / "chat.html"


def test_html_tables_are_bounded_and_scrollable_inside_message_frame():
    source = _CHAT.read_text(encoding="utf-8")
    rule = re.search(
        r'\.msg\[data-html-mode="true"\]\s+table\{([^}]+)\}', source)
    assert rule is not None
    declarations = rule.group(1).replace("\n", "").replace(" ", "")
    assert "max-width:100%" in declarations
    assert "overflow-x:auto" in declarations


def test_message_flex_item_can_shrink_for_long_paths():
    source = _CHAT.read_text(encoding="utf-8")
    message_rule = re.search(r"\.msg\{([^}]+)\}", source)
    assert message_rule is not None
    declarations = message_rule.group(1).replace("\n", "").replace(" ", "")
    assert "min-width:0" in declarations
    assert "overflow-wrap:anywhere" in declarations


def test_table_response_uses_full_chat_column_without_widening_other_bubbles():
    source = _CHAT.read_text(encoding="utf-8")
    assert ".msg.bot.wide-table:not(.gallery){width:100%;max-width:none}" in source
    assert "d.classList.add('wide-table')" in source


def test_manifest_nowrap_table_cells_are_preserved_by_the_chat_skin():
    source = _CHAT.read_text(encoding="utf-8")
    assert ".msg th.cell-nowrap,.msg td.cell-nowrap" in source
    assert "white-space:nowrap" in source

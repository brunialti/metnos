"""Telegram tables stay readable on narrow clients."""
from __future__ import annotations

from channels.telegram_format import format_for_telegram


def test_wide_table_becomes_wrapping_records_without_data_loss():
    table = (
        "| subject | size | date | account | body_preview | category_hints |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| Login code | 13370 | 21 Jul 2026 | tiscali | Long preview text | ['noreply'] |\n"
        "| Digital invoice | 22423 | 21 Jul 2026 | tiscali | Invoice preview | [] |"
    )

    rendered = "\n".join(format_for_telegram(table))

    assert "<pre>" not in rendered
    assert "| ---" not in rendered
    assert "<b>subject:</b> Login code" in rendered
    assert "<b>body_preview:</b> Long preview text" in rendered
    assert "<b>category_hints:</b> ['noreply']" in rendered
    assert "<b>subject:</b> Digital invoice" in rendered


def test_narrow_table_keeps_compact_monospace_layout():
    table = (
        "| name | value |\n"
        "| --- | --- |\n"
        "| alpha | 1 |\n"
    )

    rendered = "\n".join(format_for_telegram(table))

    assert rendered.startswith("<pre>")
    assert "alpha" in rendered

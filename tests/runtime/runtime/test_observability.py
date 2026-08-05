"""Focused guarantees for the optional static observability snapshot."""

from __future__ import annotations

import observability


def test_mnestoma_edge_names_are_html_escaped() -> None:
    rendered = observability._render_mnestoma({
        "stats": {
            "total_mnests": 1,
            "active": 1,
            "proto": 0,
            "decaying": 0,
            "events": 1,
        },
        "top_active": [],
        "top_proto": [],
        "audit": [{
            "ts": "2026-07-28T00:00:00Z",
            "kind": "use",
            "delta": 0.1,
            "src_executor": '<script>alert("source")</script>',
            "dst_executor": '<img src=x onerror=alert("destination")>',
            "reason": "ordinary",
        }],
    })

    assert "<script>" not in rendered
    assert "<img " not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img src=x onerror=alert(&quot;destination&quot;)&gt;" in rendered


def test_turn_text_is_html_escaped() -> None:
    rendered = observability._render_turns([{
        "ts_start": 0,
        "final_kind": "answer",
        "steps": [],
        "user_query": "<svg onload=alert(1)>",
        "final_message": "<b>not markup</b>",
    }])

    assert "<svg " not in rendered
    assert "<b>not markup</b>" not in rendered
    assert "&lt;svg onload=alert(1)&gt;" in rendered
    assert "&lt;b&gt;not markup&lt;/b&gt;" in rendered

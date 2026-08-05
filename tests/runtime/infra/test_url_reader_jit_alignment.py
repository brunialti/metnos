"""Regression gates for content-type-aware URL reader alignment."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import pytest


from engine.executor import Executor, _align_url_reader_jit
from engine.types import Framework, StepSpec


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        (["https://example.test/report.pdf"], "read_urls_pdf"),
        (["https://example.test/REPORT.PDF?download=1"], "read_urls_pdf"),
        (["https://example.test/page.html"], "read_urls_html"),
        (["https://example.test/article"], "read_urls_html"),
        (["https://example.test/a.pdf", "https://example.test/b.html"],
         "read_urls_html"),
        (["not a URL"], "read_urls_html"),
    ],
)
def test_pdf_reader_is_kept_only_for_unambiguous_pdf_batches(
        urls: list[str], expected: str) -> None:
    assert _align_url_reader_jit("read_urls_pdf", {"urls": urls}) == expected


def test_alignment_is_narrow_and_missing_args_remain_executor_errors() -> None:
    assert _align_url_reader_jit(
        "read_urls_html", {"urls": ["https://example.test/a.pdf"]},
    ) == "read_urls_html"
    assert _align_url_reader_jit("read_urls_pdf", {}) == "read_urls_pdf"
    assert _align_url_reader_jit(
        "read_urls_pdf", {"urls": []},
    ) == "read_urls_pdf"


@dataclass
class _CatalogEntry:
    name: str
    args_schema: dict


def _catalog() -> list[_CatalogEntry]:
    schema = {
        "type": "object",
        "required": ["urls"],
        "properties": {"urls": {"type": "array"}},
    }
    return [
        _CatalogEntry("read_urls_pdf", schema),
        _CatalogEntry("read_urls_html", schema),
    ]


def test_executor_invokes_aligned_reader_without_mutating_framework() -> None:
    calls: list[tuple[str, dict]] = []

    def invoke(tool: str, args: dict) -> dict:
        calls.append((tool, dict(args)))
        return {
            "ok": True,
            "entries": [{"url": args["urls"][0], "title": "HTML"}],
        }

    framework = Framework(steps=[
        StepSpec(
            tool="read_urls_pdf",
            args={"urls": ["https://example.test/page.html"]},
        ),
        StepSpec(tool="final_answer", args={}),
    ])

    result = Executor(invoke_executor=invoke, catalog=_catalog()).run(
        framework, query="leggi questa pagina web",
    )

    assert calls == [(
        "read_urls_html",
        {"urls": ["https://example.test/page.html"]},
    )]
    assert result.steps[0].tool == "read_urls_html"
    assert result.steps[0].ok is True
    assert framework.steps[0].tool == "read_urls_pdf"


def test_executor_preserves_pdf_only_reader() -> None:
    calls: list[str] = []

    def invoke(tool: str, _args: dict) -> dict:
        calls.append(tool)
        return {"ok": True, "entries": [{"content_kind": "pdf"}]}

    framework = Framework(steps=[
        StepSpec(
            tool="read_urls_pdf",
            args={"urls": ["https://example.test/report.pdf?rev=2"]},
        ),
        StepSpec(tool="final_answer", args={}),
    ])

    result = Executor(invoke_executor=invoke, catalog=_catalog()).run(framework)

    assert calls == ["read_urls_pdf"]
    assert result.steps[0].tool == "read_urls_pdf"

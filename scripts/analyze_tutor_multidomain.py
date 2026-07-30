#!/usr/bin/env python3
"""Measure Tutor F2 retrieval on the natural examples published on metnos.com.

Every indexable published page contributes each of its quoted example
paragraphs as one query; the ground truth of an example is the page section
that contains it.  The harness is retrieval-only (no LLM composition), runs
against an isolated catalog directory, and derives queries, sections, and
expected sources entirely from the published inventory: nothing here names a
domain, a phrase, an executor, or a file.

Usage:
  python3 scripts/analyze_tutor_multidomain.py \
      --catalog-dir /tmp/metnos-public-docs-catalog \
      --report /path/report.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

_QUOTE_SPLIT = re.compile(r"[“”]")
_SPACE = re.compile(r"\s+")
_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link"})


@dataclass(frozen=True, slots=True)
class ExampleCase:
    document_ref: str
    lang: str
    section: str
    query: str


class _ExampleParser(HTMLParser):
    """Collect (section heading, example text) with the compiler's heading fold."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.examples: list[tuple[str, str]] = []
        self._heading = ""
        self._heading_tag = ""
        self._heading_buffer: list[str] = []
        self._example_depth = 0
        self._example_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            if self._example_depth:
                self._example_buffer.append(" ")
            return
        if self._example_depth:
            self._example_depth += 1
            return
        classes = {
            token
            for key, value in attrs
            if key.lower() == "class" and value
            for token in str(value).lower().split()
        }
        if "example" in classes:
            self._example_depth = 1
            self._example_buffer = []
            return
        if tag in {"h1", "h2", "h3"}:
            self._heading_tag = tag
            self._heading_buffer = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if self._example_depth:
            self._example_depth -= 1
            if not self._example_depth:
                text = _SPACE.sub(" ", "".join(self._example_buffer)).strip()
                if text:
                    self.examples.append((self._heading, text))
            return
        if self._heading_tag and tag == self._heading_tag:
            self._heading = _SPACE.sub(
                " ", "".join(self._heading_buffer)).strip()
            self._heading_tag = ""

    def handle_data(self, data: str) -> None:
        if self._example_depth:
            self._example_buffer.append(data)
        elif self._heading_tag:
            self._heading_buffer.append(data)


def _configure_isolated_catalog(directory: Path) -> None:
    import tutor.catalog as tutor_catalog

    directory.mkdir(parents=True, exist_ok=True)
    tutor_catalog.CATALOG_PATH = directory / "tutor_catalog.sqlite"
    tutor_catalog.SIGNATURE_PATH = directory / "tutor_catalog.sqlite.sig"
    tutor_catalog.BACKUP_PATH = directory / "tutor_catalog.last_good.json"
    tutor_catalog.LOCK_PATH = directory / "tutor_catalog.lock"


def _quoted_query(text: str) -> str:
    pieces = _QUOTE_SPLIT.split(text)
    if len(pieces) < 3:
        return ""
    quoted = [pieces[index].strip() for index in range(1, len(pieces) - 1, 2)]
    return max(quoted, key=len, default="")


def collect_cases() -> tuple[ExampleCase, ...]:
    from published_docs import catalog as published_documents
    from tutor.sources import REPO_ROOT as SOURCES_ROOT

    cases: list[ExampleCase] = []
    for document in published_documents():
        parser = _ExampleParser()
        parser.feed(document.path.read_text(encoding="utf-8"))
        if not parser.examples:
            continue
        reference = document.path.relative_to(SOURCES_ROOT).as_posix()
        lang = document.lang.split("-", 1)[0]
        for section, raw in parser.examples:
            query = _quoted_query(raw)
            if query and section:
                cases.append(ExampleCase(
                    document_ref=reference,
                    lang=lang,
                    section=section,
                    query=query,
                ))
    return tuple(cases)


def _hit_descriptor(hit, adjusted_score: float) -> dict:
    unit = hit.unit
    return {
        "unit_id": unit.unit_id if unit else f"card:{hit.source_id}",
        "title": unit.title if unit else hit.source_id,
        "source_ref": unit.source_ref if unit else f"card:{hit.source_id}",
        "source_kind": unit.source_kind if unit else "curated_guide",
        "lang": hit.lang,
        "cosine": round(hit.score, 4),
        "adjusted": round(adjusted_score, 4),
    }


def analyze(case: ExampleCase, *, units, knowledge_index, embedder,
            audience: str) -> dict:
    from tutor.semantic import retrieve_sources

    explain: dict = {}
    context = retrieve_sources(
        case.query,
        case.lang,
        audience,
        cards=(),
        units=units,
        knowledge_index=knowledge_index,
        embedder=embedder,
        explain=explain,
    )
    ranked = explain.get("ranked", ())
    threshold = explain.get("threshold", 0.0)
    band = explain.get("band", 0.0)

    expected_ids = {
        unit.unit_id
        for unit in units
        if unit.source_ref.split("#", 1)[0] == case.document_ref
        and unit.title == case.section
    }
    selected = tuple(context.hits) if context is not None else ()
    selected_ids = {
        hit.unit.unit_id for hit in selected if hit.unit is not None
    }
    recall = bool(expected_ids & selected_ids)

    expected_rank = 0
    expected_adjusted = 0.0
    expected_unit_id = ""
    for position, (hit, adjusted_score) in enumerate(ranked, start=1):
        if hit.unit is not None and hit.unit.unit_id in expected_ids:
            expected_rank = position
            expected_adjusted = adjusted_score
            expected_unit_id = hit.unit.unit_id
            break
    top_adjusted = ranked[0][1] if ranked else 0.0
    same_document_selected = any(
        hit.unit is not None
        and hit.unit.source_ref.split("#", 1)[0] == case.document_ref
        for hit in selected
    )

    if recall:
        cause = "ok"
    elif not ranked:
        cause = "no_candidates"
    elif not expected_rank:
        cause = "expected_not_in_candidates"
    elif context is None:
        cause = ("expected_below_threshold"
                 if expected_adjusted < threshold else "global_below_threshold")
    elif context.restricted:
        cause = "restricted"
    elif expected_adjusted < threshold:
        cause = "expected_below_threshold"
    elif expected_adjusted < top_adjusted - band:
        cause = "outside_band"
    else:
        cause = "capped_out"

    return {
        "document": case.document_ref,
        "lang": case.lang,
        "section": case.section,
        "query": case.query,
        "recall": recall,
        "cause": cause,
        "expected_rank": expected_rank,
        "expected_adjusted": round(expected_adjusted, 4),
        "expected_unit_id": expected_unit_id,
        "top_adjusted": round(top_adjusted, 4),
        "delta_to_top": round(top_adjusted - expected_adjusted, 4),
        "threshold": threshold,
        "band": band,
        "same_document_selected": same_document_selected,
        "selected": [
            _hit_descriptor(hit, dict((id(h), s) for h, s in ranked).get(
                id(hit), hit.score))
            for hit in selected
        ],
        "top5": [
            _hit_descriptor(hit, adjusted_score)
            for hit, adjusted_score in ranked[:5]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, required=True,
                        help="isolated Tutor catalog directory")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--audience", default="user")
    parser.add_argument("--lang", default="",
                        help="restrict to one base language")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    _configure_isolated_catalog(args.catalog_dir)
    cases = collect_cases()
    if args.lang:
        cases = tuple(case for case in cases if case.lang == args.lang)
    if args.list:
        for case in cases:
            print(f"{case.lang}\t{case.section}\t{case.query}")
        return 0
    if not cases:
        print("Nessun esempio pubblicato trovato", file=sys.stderr)
        return 2

    from tutor.catalog import load_knowledge_units, load_knowledge_vector_index
    from virt import get_local_embedder

    started = time.monotonic()
    units = load_knowledge_units()
    knowledge_index = load_knowledge_vector_index()
    embedder = get_local_embedder("text")
    records = []
    for position, case in enumerate(cases, start=1):
        record = analyze(
            case, units=units, knowledge_index=knowledge_index,
            embedder=embedder, audience=args.audience)
        records.append(record)
        marker = "PASS" if record["recall"] else f"MISS({record['cause']})"
        print(f"[{position:03d}/{len(cases):03d}] {marker} "
              f"{case.lang} {case.section} :: {case.query[:60]}", flush=True)

    causes = Counter(record["cause"] for record in records)
    by_lang = Counter(
        (record["lang"], record["recall"]) for record in records)
    summary = {
        "cases": len(records),
        "recall": sum(record["recall"] for record in records),
        "selected_mean": round(
            sum(len(record["selected"]) for record in records)
            / max(1, len(records)), 2),
        "causes": dict(sorted(causes.items())),
        "by_lang": {
            lang: {
                "recall": by_lang.get((lang, True), 0),
                "miss": by_lang.get((lang, False), 0),
            }
            for lang in sorted({record["lang"] for record in records})
        },
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    report = {
        "schema_version": 1,
        "audience": args.audience,
        "summary": summary,
        "records": records,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    misses = [record for record in records if not record["recall"]]
    if misses:
        print("\nMISS DETAIL (lang | section | cause | exp_adj | rank | "
              "delta | top1):")
        for record in misses:
            top1 = record["top5"][0] if record["top5"] else {}
            print(f"- {record['lang']} | {record['section']} | "
                  f"{record['cause']} | {record['expected_adjusted']} | "
                  f"#{record['expected_rank']} | {record['delta_to_top']} | "
                  f"{top1.get('title', '')} [{top1.get('source_ref', '')}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

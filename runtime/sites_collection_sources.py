# SPDX-License-Identifier: MIT
"""Local record views connected by observed, successful detail navigation.

Equal dates, amounts or row text never identify a record. Raw snapshots stay
intact; only extraction sources are partitioned into linked views.
"""
from __future__ import annotations

import hashlib
import copy
import json
from collections import Counter
from itertools import combinations

from record_identity import (conflicting_fields, conflicting_self_identifier_fields,
                             enrich_observations, retain_conflicts, valid_reference)
import re
from urllib.parse import urldefrag

from sites_url_scrub import scrub_url


def _opaque(session: str, kind: str, *parts) -> str:
    """Full digest, segmented so ordinary secret redaction preserves the ref."""
    digest = hashlib.sha256(repr((session, kind, parts)).encode()).hexdigest()
    return kind + "-" + "-".join("x" + digest[n:n + 8] for n in range(0, 64, 8))


def _context_observed(page: dict) -> bool:
    value = page.get("context")
    return isinstance(value, str) and (bool(value) or page.get("context_observed") is True)


def _snapshot_signature(page: dict):
    """Compare complete native captures only, inside one collection/session.

    A page key alone excludes binding and truncation differences. Arbitrary
    Python values are not native JSON and cannot establish an exact replica.
    """
    def native(value):
        if type(value) is dict:
            return all(type(k) is str and native(v) for k, v in value.items())
        if type(value) is list:
            return all(native(v) for v in value)
        return value is None or type(value) in (str, bool, int, float)

    if (not all(isinstance(page.get(k), str) and page[k] for k in ("key", "url", "text"))
            or not _context_observed(page) or page.get("context_observed") is False
            or not native(page)):
        return None
    try:
        return json.dumps(page, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None


def retain_snapshot(pages: list[dict], snapshot: dict) -> None:
    """Freeze an observation; preserve different payloads even at equal keys."""
    signature = _snapshot_signature(snapshot)
    if signature is None or not any(_snapshot_signature(p) == signature for p in pages):
        pages.append(copy.deepcopy(snapshot))


def _native_snapshots(pages: list[dict]):
    # Retain the original ordinal: source refs must still name the captured
    # page when an older collection already contains complete native replicas.
    seen, result = set(), []
    for ordinal, page in enumerate(pages):
        signature = _snapshot_signature(page)
        if signature is None or signature not in seen:
            result.append((ordinal, page))
        if signature is not None:
            seen.add(signature)
    return result


def _row_span(page: dict, binding):
    row = binding.get("text") if isinstance(binding, dict) else binding
    if not isinstance(row, str) or not row.strip():
        return None
    # The page text is what the browser renders: style may change the case of
    # a row's own text (real runs fw9/fw25a, 22/10/2026: no row found exactly,
    # every row found without case). Without case only when no exact match
    # exists: elsewhere the same words in another case are other text (real
    # run en25c: an occurrence counted without case named another place).
    # Line and cell breaks are only spacing.
    pattern = r"(?<!\S)" + r"\s+".join(map(re.escape, row.split())) + r"(?!\S)"
    matches = (list(re.finditer(pattern, page.get("text", "")))
               or list(re.finditer(pattern, page.get("text", ""), re.IGNORECASE)))
    if isinstance(binding, dict):
        occurrence, count = binding.get("occurrence"), binding.get("count")
        if (type(occurrence) is not int or type(count) is not int
                or count != len(matches) or not 0 <= occurrence < count):
            return None
    elif len(matches) == 1:
        occurrence = 0
    else:
        return None
    match = matches[occurrence]
    return match.start(), match.end(), match.group(0)


def _segments(text: str) -> list[str]:
    """A row's fields as the browser lays them out: its lines, and the cells
    of a table line, where an empty cell keeps its place."""
    out = []
    for line in text.split("\n"):
        if "\t" in line:
            out.extend(" ".join(cell.split()) for cell in line.split("\t"))
        elif line.strip():
            out.append(" ".join(line.split()))
    return out


def list_rows(page: dict) -> list[tuple[int, int, list[str]]]:
    """The rows the collector bound on a page (a repeated container with one
    action each): their span in the page text and their fields. Overlapping
    spans are not one row each and are left out."""
    found = []
    for binding in (page.get("records") or {}).values():
        span = _row_span(page, binding)
        if span is not None and all(span[:2] != other[:2] for other in found):
            found.append(span)
    kept = [span for span in found if not any(
        other is not span and span[0] < other[1] and other[0] < span[1] for other in found)]
    return sorted((start, end, _segments(text)) for start, end, text in kept)


def _unique_resource_row(page: dict, resource: str):
    # Native href plus an observed successful edge, not URL/content equality.
    if (not isinstance(resource, str) or not resource.startswith(("https://", "http://"))
            or resource == page.get("url")):
        return None
    bindings = [b for b in (page.get("records") or {}).values()
                if isinstance(b, dict) and b.get("resource") == resource]
    return _row_span(page, bindings[0]) if len(bindings) == 1 else None


def record_views(pages: list[dict], edges: list[dict], session_id: str) -> list[dict]:
    native = _native_snapshots(pages)
    pages = [page for _ordinal, page in native]
    counts = Counter(p.get("key") for p in pages if isinstance(p.get("key"), str))
    by_key = {p.get("key"): p for p in pages
              if isinstance(p.get("key"), str) and counts[p["key"]] == 1}
    spans: dict[str, list[tuple]] = {}
    successful = []

    def add_span(parent, child, span):
        start, end, row = span
        item = (start, end, child["key"], row)
        bucket = spans.setdefault(parent["key"], [])
        if item not in bucket:
            bucket.append(item)

    for edge in edges:
        parent, child = by_key.get(edge.get("parent")), by_key.get(edge.get("detail"))
        if not parent or not child or parent is child:
            continue
        if not parent.get("url") or not child.get("url"):
            continue
        known = _context_observed(parent) and _context_observed(child)
        if known and parent["context"] != child["context"]:
            continue
        same_url = urldefrag(parent["url"])[0] == urldefrag(child["url"])[0]
        if same_url and not known:
            continue
        binding = (parent.get("records") or {}).get(edge.get("record"))
        span = _row_span(parent, binding)
        if span is None:
            continue
        add_span(parent, child, span)
        # Only a specific, unique native resource can extend this successful
        # relationship to another snapshot. Buttons/SPA stay edge-only.
        if (known and isinstance(binding, dict)
                and binding.get("resource") == child["url"]
                and _unique_resource_row(parent, child["url"]) == span):
            successful.append((parent, child))
    destinations = {}
    for parent, child in successful:
        destinations.setdefault((child["url"], parent["context"]), set()).add(child["key"])
    for parent, child in successful:
        if len(destinations[(child["url"], parent["context"])]) != 1:
            continue
        for other in by_key.values():
            if (other is child or not _context_observed(other)
                    or other["context"] != parent["context"]):
                continue
            span = _unique_resource_row(other, child["url"])
            if span is not None:
                add_span(other, child, span)
    # Nested or ambiguous spans cannot safely be removed from a source.
    for key, items in spans.items():
        spans[key] = [item for item in items if not any(
            other != item and item[0] < other[1] and other[0] < item[1]
            for other in items)]
    groups: dict[str, set[str]] = {}
    for parent_key, items in spans.items():
        for start, end, child_key, _row in items:
            groups.setdefault(child_key, {child_key}).add(f"{parent_key}:{start}:{end}")

    def source(page, ordinal, text, partition, child_key=None, rank=0, rows=()):
        ref = _opaque(session_id, "source", ordinal, page.get("key"), partition)
        result = {**page, "text": text, "_source_snapshot_ref": ref}
        if rows:
            # Private: the page's bound rows inside this text; every source of
            # one page carries the same list reference.
            result["_source_list"] = {"ref": _opaque(session_id, "list", ordinal, page.get("key")),
                                      "rows": [{"segments": segments, "start": start, "end": end}
                                               for start, end, segments in rows]}
        if child_key is not None:
            group = _opaque(session_id, "view", child_key)
            result["_source_record_view"] = {"group": group, "source": ref,
                "rank": rank, "source_count": len(groups[child_key])}
            # Scope proves only this local relationship, never global uniqueness.
            result["_source_identity_scope"] = {"version": 1,
                "entity": _opaque(session_id, "entity", child_key),
                "namespace": _opaque(session_id, "relation", child_key)}
        return result

    result = []
    for ordinal, page in native:
        key = page.get("key")
        items = sorted(spans.get(key, []))
        rows = list_rows(page) if isinstance(page.get("text"), str) else []
        # A detail containing another linked collection is not proven 1:1.
        if key in groups and items:
            result.append(source(page, ordinal, page["text"], "whole", rows=rows))
            continue
        pieces, tail = [], 0
        for start, end, _child, _row in items:
            pieces.append((tail, start)); tail = end
        pieces.append((tail, len(page["text"])))
        joined = "".join(page["text"][a:b] for a, b in pieces)
        residual, lead = joined.strip(), len(joined) - len(joined.lstrip())

        def kept(start, end):
            # A row inside one kept piece, at its place in the residual text.
            offset = -lead
            for a, b in pieces:
                if a <= start and end <= b:
                    return start - a + offset, end - a + offset
                offset += b - a
            return None
        if residual:
            residual_rows = [(*place, segments) for start, end, segments in rows
                             if (place := kept(start, end)) is not None]
            result.append(source(page, ordinal, residual, "residual",
                                 key if key in groups else None, 1, rows=residual_rows))
        for start, end, child_key, row in items:
            result.append(source(page, ordinal, row, (start, end), child_key,
                                 rows=[(0, len(row), _segments(row))]))
    return result


def merge_extracted_views(records: list[dict]) -> tuple[list[dict], int]:
    """Merge complete 1:1 extractions of broker-associated views only.

    Missing, failed, capped or multi-record views leave their group intact.
    A relationship cannot override contradictory qualified self-identifiers.
    Otherwise prefer detail values, preserving every observation and conflict.
    """
    groups: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        meta = record.get("_source_record_view")
        if (isinstance(meta, dict) and valid_reference(meta.get("group"))
                and valid_reference(meta.get("source"))
                and type(meta.get("rank")) is int and meta["rank"] in (0, 1)
                and type(meta.get("source_count")) is int
                and meta["source_count"] > 1):
            groups.setdefault(meta["group"], []).append(index)
    replacements, removed = {}, set()
    for indexes in groups.values():
        views = [records[i]["_source_record_view"] for i in indexes]
        if (any(v.get("single") is not True for v in views)
                or len({v.get("source") for v in views}) != len(views)
                or any(v.get("source_count") != len(views) for v in views)
                or sum(v.get("rank") == 1 for v in views) != 1):
            continue
        conflicts = set().union(*(conflicting_fields(records[i], records[j])
                                  for i, j in combinations(indexes, 2)))
        if conflicting_self_identifier_fields([records[i] for i in indexes]):
            for index in indexes:
                replacements[index] = retain_conflicts(records[index], conflicts)
            continue
        detail = next(n for n, i in enumerate(indexes)
                      if records[i]["_source_record_view"]["rank"] == 1)
        replacements[indexes[0]] = enrich_observations(
            [records[i] for i in indexes], preferred=detail)
        if conflicts:
            replacements[indexes[0]] = retain_conflicts(replacements[indexes[0]], conflicts)
        removed.update(indexes[1:])
    out = []
    for index, record in enumerate(records):
        if index not in removed:
            item = dict(replacements.get(index, record))
            item.pop("_source_record_view", None)
            out.append(item)
    return out, len(removed)


def page_identity(url: str, context: str, text: str) -> str:
    # Equal text does not prove the same page, nor does the URL alone: the
    # selected account or category is part of what a page shows.
    return hashlib.sha256("\0".join(
        (scrub_url(url or ""), context or "", text or "")).encode("utf-8")).hexdigest()


def read_pages(collected: list[dict], edges: list[dict], session_id: str,
               current: str | None = None) -> list[dict]:
    """The pages a collection hands to its reader: one source per linked view
    or residual, with private provenance and the bound rows of each source.
    `current` is the identity of the page the browser shows now."""
    sources = record_views(collected, edges or [], session_id)
    return [{"url": scrub_url(str(item.get("url") or "")),
             "title": str(item.get("title") or ""), "text": str(item["text"]),
             **({"_source_record_view": item["_source_record_view"]}
                if item.get("_source_record_view") else {}),
             **({key: item[key] for key in
                 ("_source_snapshot_ref", "_source_identity_scope", "_source_list") if key in item}),
             **({"_source_context_label": item["context_label"],
                 "_source_partition": item["partition_ref"]}
                if isinstance(item.get("context_label"), str) and item["context_label"]
                and isinstance(item.get("partition_ref"), str) else {}),
             "current": page_identity(
                 str(item.get("url") or ""), str(item.get("context") or ""),
                 str(item["text"])) == current}
            for item in sources]

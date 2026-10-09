# SPDX-License-Identifier: MIT
"""Local record views connected by observed, successful detail navigation.

Equal dates, amounts or row text never identify a record. Raw snapshots stay
intact; only extraction sources are partitioned into linked views.
"""
from __future__ import annotations

import hashlib
import re


def record_views(pages: list[dict], edges: list[dict], session_id: str) -> list[dict]:
    by_key = {p.get("key"): p for p in pages if p.get("key")}
    spans: dict[str, list[tuple]] = {}
    for edge in edges:
        parent, child = by_key.get(edge.get("parent")), by_key.get(edge.get("detail"))
        if not parent or not child or parent is child:
            continue
        # An unchanged SPA URL does not establish identity between rows.
        if not parent.get("url") or parent.get("url") == child.get("url"):
            continue
        row = (parent.get("records") or {}).get(edge.get("record"))
        if not isinstance(row, str) or not row.strip():
            continue
        pattern = r"(?<!\S)" + r"\s+".join(map(re.escape, row.split())) + r"(?!\S)"
        matches = list(re.finditer(pattern, parent["text"]))
        if len(matches) != 1:
            continue
        match = matches[0]
        item = (match.start(), match.end(), child["key"], row)
        bucket = spans.setdefault(parent["key"], [])
        if item not in bucket:
            bucket.append(item)
    # Nested or ambiguous spans cannot safely be removed from a source.
    for key, items in spans.items():
        spans[key] = [item for item in items if not any(
            other != item and item[0] < other[1] and other[0] < item[1]
            for other in items)]
    groups: dict[str, set[str]] = {}
    for parent_key, items in spans.items():
        for start, end, child_key, _row in items:
            groups.setdefault(child_key, {child_key}).add(f"{parent_key}:{start}:{end}")

    def metadata(child_key, source, rank):
        group = hashlib.sha256((session_id + "\0" + child_key).encode()).hexdigest()
        return {"group": group, "source": source, "rank": rank,
                "source_count": len(groups[child_key])}

    result = []
    for page in pages:
        key = page.get("key")
        items = sorted(spans.get(key, []))
        # A detail containing another linked collection is not proven 1:1.
        if key in groups and items:
            result.append(dict(page))
            continue
        text, tail = [], 0
        for start, end, _child, _row in items:
            text.append(page["text"][tail:start]); tail = end
        text.append(page["text"][tail:])
        residual = {**page, "text": "".join(text).strip()}
        if key in groups:
            residual["_source_record_view"] = metadata(key, key, 1)
        if residual["text"]:
            result.append(residual)
        for start, end, child_key, row in items:
            result.append({**page, "text": row,
                "_source_record_view": metadata(child_key, f"{key}:{start}:{end}", 0)})
    return result


def merge_extracted_views(records: list[dict]) -> tuple[list[dict], int]:
    """Merge complete 1:1 extractions of broker-associated views only.

    Missing, failed, capped or multi-record views leave their group intact.
    Detail fields win; nonempty fields unique to a list survive.
    """
    groups: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        meta = record.get("_source_record_view")
        if (isinstance(meta, dict) and isinstance(meta.get("group"), str)
                and isinstance(meta.get("source"), str)
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
        merged = {}
        for index in sorted(indexes, key=lambda i: records[i]["_source_record_view"]["rank"]):
            for field, value in records[index].items():
                if field != "_source_record_view" and (field not in merged or value not in (None, "", [])):
                    merged[field] = value
        replacements[indexes[0]] = merged
        removed.update(indexes[1:])
    out = []
    for index, record in enumerate(records):
        if index not in removed:
            item = dict(replacements.get(index, record))
            item.pop("_source_record_view", None)
            out.append(item)
    return out, len(removed)

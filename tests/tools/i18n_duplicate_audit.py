#!/usr/bin/env python3
"""Deterministic i18n duplicate and placeholder audit.

The audit is read-only.  Exact and near duplicate texts are reported for
human review; objective placeholder or source/bundle drift is reported
separately so CI can fail on those findings without forcing alias removal.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = ROOT / "install/data/i18n_seed.sqlite"
DEFAULT_LIVE = Path(os.environ.get(
    "METNOS_USER_DATA", Path.home() / ".local/share/metnos"
)) / "i18n.sqlite"
DEFAULT_BUNDLE = ROOT / "runtime/device_shim/messages_i18n.json"
DEFAULT_ALLOWLIST = ROOT / "internal/design/i18n_duplicate_allowlist.json"
_PLACEHOLDER = re.compile(
    r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}|"
    r"\$\{([A-Za-z_][\w:.-]*)\}|"
    r"\{([A-Za-z_][\w.-]*)\}|"
    r"%\(([A-Za-z_][\w.-]*)\)[#0+\- ]?(?:\d+|\*)?(?:\.\d+)?[a-zA-Z]"
)
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _db_rows(path: Path) -> list[dict[str, str]]:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT key, lang, text FROM i18n "
            "WHERE text IS NOT NULL AND trim(text)<>'' ORDER BY lang, key"
        ).fetchall()
    finally:
        conn.close()
    return [{"key": key, "lang": lang, "text": text} for key, lang, text in rows]


def _bundle_rows(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"key": key, "lang": lang, "text": text}
        for lang in sorted(data)
        for key, text in sorted(data[lang].items())
    ]


def _norm(text: str, *, strip_punctuation: bool = False) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    if strip_punctuation:
        value = _PUNCT.sub(" ", value)
    return _SPACE.sub(" ", value).strip()


def _placeholders(text: str) -> tuple[str, ...]:
    values = []
    for match in _PLACEHOLDER.finditer(text):
        values.append(next(group for group in match.groups() if group is not None))
    return tuple(sorted(values))


def _groups(rows: list[dict[str, str]], *, punctuation: bool = False):
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        groups[(row["lang"], _norm(row["text"], strip_punctuation=punctuation))].append(row["key"])
    return [
        {"lang": lang, "normalized": text, "keys": sorted(keys)}
        for (lang, text), keys in sorted(groups.items())
        if text and len(set(keys)) > 1
    ]


def _near_duplicates(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_lang: dict[str, list[tuple[dict[str, str], str]]] = defaultdict(list)
    for row in rows:
        normalized = _norm(row["text"], strip_punctuation=True)
        by_lang[row["lang"]].append((row, normalized))
    found = []
    for lang, items in sorted(by_lang.items()):
        buckets: dict[tuple[int, str], list[tuple[dict[str, str], str]]] = defaultdict(list)
        for row, normalized in items:
            if len(normalized) >= 16:
                buckets[(len(normalized) // 20, normalized[:3])].append((row, normalized))
        for bucket, candidates in sorted(buckets.items()):
            neighboring = []
            for other_bucket, values in buckets.items():
                if abs(other_bucket[0] - bucket[0]) <= 1 and other_bucket[1] == bucket[1]:
                    neighboring.extend(values)
            for index, (left, left_norm) in enumerate(candidates):
                for right, right_norm in neighboring:
                    if left["key"] >= right["key"]:
                        continue
                    if abs(len(left_norm) - len(right_norm)) > max(len(left_norm), len(right_norm)) * 0.15:
                        continue
                    score = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
                    if score >= 0.94 and left_norm != right_norm:
                        found.append({
                            "lang": lang, "score": round(score, 4),
                            "keys": sorted((left["key"], right["key"])),
                        })
    unique = {(item["lang"], tuple(item["keys"])): item for item in found}
    return sorted(unique.values(), key=lambda item: (-item["score"], item["lang"], item["keys"]))


def _cross_language_identical(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[_norm(row["text"])].append((row["lang"], row["key"]))
    return [
        {"normalized": text, "entries": [
            {"lang": lang, "key": key} for lang, key in sorted(entries)
        ]}
        for text, entries in sorted(grouped.items())
        if text and len({lang for lang, _ in entries}) > 1
    ]


def audit(seed: Path, live: Path, bundle: Path, allowlist: Path = DEFAULT_ALLOWLIST) -> dict[str, Any]:
    seed_rows = _db_rows(seed)
    live_rows = _db_rows(live)
    bundle_rows = _bundle_rows(bundle)
    by_key_lang = {(row["key"], row["lang"]): row for row in seed_rows}
    placeholder_mismatch = []
    for row in live_rows:
        source = by_key_lang.get((row["key"], row["lang"]))
        if source and _placeholders(source["text"]) != _placeholders(row["text"]):
            placeholder_mismatch.append({
                "key": row["key"], "lang": row["lang"],
                "seed": _placeholders(source["text"]),
                "live": _placeholders(row["text"]),
            })
    seed_map = {(row["key"], row["lang"]): row["text"] for row in seed_rows}
    live_map = {(row["key"], row["lang"]): row["text"] for row in live_rows}
    bundle_map = {(row["key"], row["lang"]): row["text"] for row in bundle_rows}
    allowed = {
        (entry["lang"], tuple(sorted(entry["keys"])))
        for entry in json.loads(allowlist.read_text(encoding="utf-8"))
    } if allowlist.is_file() else set()
    exact = _groups(live_rows)
    allowed_exact = [
        item for item in exact if (item["lang"], tuple(item["keys"])) in allowed
    ]
    unresolved_exact = [
        item for item in exact if (item["lang"], tuple(item["keys"])) not in allowed
    ]
    seed_keys = set(seed_map)
    live_keys = set(live_map)
    bundle_keys = set(bundle_map)
    bundle_expected = {
        key for key in live_keys
        if key[0].startswith(("ERR_", "WARN_", "MSG_"))
    }
    bundle_drift = [
        {"key": key, "lang": lang}
        for (key, lang), text in sorted(bundle_map.items())
        if (key, lang) in live_map and live_map[(key, lang)] != text
    ]
    return {
        "schema_version": 1,
        "sources": {
            "seed": str(seed), "live": str(live), "bundle": str(bundle),
            "counts": {"seed": len(seed_rows), "live": len(live_rows), "bundle": len(bundle_rows)},
        },
        "findings": {
            "exact_same_language": unresolved_exact,
            "allowed_exact_aliases": allowed_exact,
            "exact_ignoring_punctuation": _groups(live_rows, punctuation=True),
            "near_same_language": _near_duplicates(live_rows),
            "cross_language_identical": _cross_language_identical(live_rows),
            "placeholder_mismatch": placeholder_mismatch,
            "bundle_drift": bundle_drift,
            "seed_live_missing": [
                {"key": key, "lang": lang}
                for key, lang in sorted(seed_keys - live_keys)
            ],
            "seed_live_extra": [
                {"key": key, "lang": lang}
                for key, lang in sorted(live_keys - seed_keys)
            ],
            "bundle_missing": [
                {"key": key, "lang": lang}
                for key, lang in sorted(bundle_expected - bundle_keys)
            ],
            "bundle_extra": [
                {"key": key, "lang": lang}
                for key, lang in sorted(bundle_keys - bundle_expected)
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(args.seed, args.live, args.bundle, args.allowlist)
    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    findings = report["findings"]
    blocking = (findings["placeholder_mismatch"] or findings["bundle_drift"]
                or findings["seed_live_missing"] or findings["bundle_missing"])
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())

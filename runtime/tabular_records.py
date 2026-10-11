# SPDX-License-Identifier: MIT
"""Pure record-carrier declarations shared by routing and table compilation.

Column sources name record fields. Until that projection has run, neither a
natural field name such as ``value`` nor dict insertion order may turn the
record into a positional matrix. This boundary preserves malformed/mixed
payloads too; the writing backend owns validation and its error receipt.
"""
from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit, urlunsplit


# Producer limits remain separate from the writer's own physical limits.
# A count alone does not declare whether collection was complete.
SOURCE_METADATA_FIELDS = ("partial", "collection_partial", "truncated", "used",
                          "available_total", "truncated_what", "cap_field",
                          "cap_value", "truncations", "partial_reason")

_SOURCE_KEYS = frozenset({"producer", "scope", "source_ref", "partial",
    "collection_partial", "truncated", "truncated_what", "used",
    "available_total", "cap_field", "cap_value", "reason_code", "provenance"})
_SOURCE_FLAGS = frozenset({"partial", "collection_partial", "truncated"})
_SOURCE_COUNTS = frozenset({"used", "available_total"})
_SOURCE_NAMES = frozenset({"producer", "scope", "source_ref", "truncated_what",
                           "cap_field", "reason_code"})
_SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_SOURCE_MAX_ITEMS = 512
_SOURCE_MAX_BYTES = 131072


def _source_url(value: object) -> str:
    """Keep only observed http(s) origin/path, never credentials or query."""
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("invalid_source_metadata")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid_source_metadata")
        host = parsed.hostname.lower()
        if any(c.isspace() for c in host) or "\\" in host:
            raise ValueError("invalid_source_metadata")
        if ":" in host:
            host = "[" + host + "]"
        if parsed.port is not None:
            host += ":" + str(parsed.port)
        return urlunsplit((parsed.scheme.lower(), host, parsed.path, "", ""))
    except (ValueError, UnicodeError):
        raise ValueError("invalid_source_metadata") from None


def validate_source_metadata(value: object) -> list[dict]:
    """Validate the runtime-owned envelope, not fields inside entry data.

    Producer/scope are transport references, never entity identity. Unknown
    fields and free prose fail explicitly; arbitrary page/model payloads are
    not accepted as a declaration of source completeness. The caller owns
    authentication of the producer (runtime-resolved contract arguments).
    """
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _SOURCE_MAX_ITEMS:
        raise ValueError("invalid_source_metadata")
    out = []
    for item in value:
        if (not isinstance(item, dict) or not {"producer", "scope"}.issubset(item)
                or any(key not in _SOURCE_KEYS for key in item)):
            raise ValueError("invalid_source_metadata")
        clean = {}
        for key, field in item.items():
            if key in _SOURCE_NAMES:
                if not isinstance(field, str) or _SOURCE_NAME.fullmatch(field) is None:
                    raise ValueError("invalid_source_metadata")
            elif key in _SOURCE_FLAGS:
                if not isinstance(field, bool):
                    raise ValueError("invalid_source_metadata")
            elif key in _SOURCE_COUNTS:
                if field is None and key == "available_total":
                    pass
                elif isinstance(field, bool) or not isinstance(field, int) or not 0 <= field <= 2**63 - 1:
                    raise ValueError("invalid_source_metadata")
            elif key == "cap_value":
                if (isinstance(field, bool) or not isinstance(field, (int, float))
                        or not 0 <= field <= 2**63 - 1 or not math.isfinite(field)):
                    raise ValueError("invalid_source_metadata")
            elif key == "provenance":
                if not isinstance(field, dict) or set(field) != {"url"}:
                    raise ValueError("invalid_source_metadata")
                field = {"url": _source_url(field["url"])}
            clean[key] = field
        out.append(clean)
    if len(json.dumps(out, ensure_ascii=False, sort_keys=True).encode("utf-8")) > _SOURCE_MAX_BYTES:
        raise ValueError("invalid_source_metadata")
    return out


def merge_source_metadata(*sources: object) -> list[dict]:
    """Union exact normalized transport repeats; never merge by scope/value."""
    out, seen = [], set()
    for source in sources:
        for item in validate_source_metadata(source):
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                out.append(item)
                seen.add(key)
    return validate_source_metadata(out)


def has_explicit_column_sources(args: dict) -> bool:
    """Recognize a declared source, leaving its validity to projection.

    An invalid source is still a declaration: it must fail validation rather
    than regain a heuristic or positional interpretation of the records.
    """
    specs = args.get("column_specs")
    return (isinstance(specs, list)
            and any(isinstance(spec, dict) and "source" in spec for spec in specs))


def preserve_explicit_record_source(args: dict) -> dict:
    """Keep record-valued ``values`` available for the explicit projection.

    Mixed payloads are moved intact. A competing explicit ``entries`` input
    is never overwritten; both inputs reach the backend's ambiguity check.
    The caller's mapping and record objects are not mutated or serialized.
    """
    values = args.get("values")
    if (not has_explicit_column_sources(args)
            or not isinstance(values, list)
            or not any(isinstance(row, dict) for row in values)
            or args.get("entries")):
        return args
    out = dict(args)
    out["entries"] = values
    out.pop("values", None)
    return out

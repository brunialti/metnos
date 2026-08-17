"""Small, strict JSON helpers for the isolated candidate."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any


MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_JSON_STRING_CHARS = 1_000_000
MAX_JSON_INTEGER_DIGITS = 4_300


class StrictJsonError(ValueError):
    """A bounded JSON document is malformed or ambiguous."""


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate key: {key}")
        result[key] = value
    return result


def strict_json_loads(raw: bytes, *, max_bytes: int = 1_000_000) -> Any:
    if type(raw) is not bytes:
        raise TypeError("raw must be exact bytes")
    if len(raw) > max_bytes:
        raise StrictJsonError("document exceeds offline byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError("document is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_float=lambda token: _finite_float(token),
            parse_int=lambda token: _bounded_int(token),
            parse_constant=lambda token: (_ for _ in ()).throw(
                StrictJsonError(f"non-finite number: {token}")
            ),
        )
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, RecursionError, OverflowError, ValueError, UnicodeError) as exc:
        raise StrictJsonError("invalid JSON") from exc
    _assert_json_tree(value)
    return value


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise StrictJsonError("floating-point overflow")
    return value


def _bounded_int(token: str) -> int:
    digits = len(token) - int(token.startswith("-"))
    if digits > MAX_JSON_INTEGER_DIGITS:
        raise StrictJsonError("integer digit overflow")
    return int(token)


def _assert_json_tree(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise StrictJsonError("JSON node limit exceeded")
        if depth > MAX_JSON_DEPTH:
            raise StrictJsonError("JSON depth limit exceeded")
        if current is None or type(current) in (bool, int):
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise StrictJsonError("non-finite number")
            continue
        if type(current) is str:
            if len(current) > MAX_JSON_STRING_CHARS:
                raise StrictJsonError("JSON string limit exceeded")
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise StrictJsonError("invalid Unicode scalar") from exc
            continue
        if type(current) is list:
            stack.extend((item, depth + 1) for item in reversed(current))
            continue
        if type(current) is dict:
            for key, item in reversed(tuple(current.items())):
                if type(key) is not str:
                    raise StrictJsonError("non-string JSON key")
                try:
                    key.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise StrictJsonError("invalid Unicode key") from exc
                stack.append((item, depth + 1))
            continue
        raise StrictJsonError("non-JSON value")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _ir_key_order(value: Any) -> Any:
    if type(value) is list:
        return [_ir_key_order(item) for item in value]
    if type(value) is dict:
        keys = sorted(value, key=lambda key: (key != "kind", key))
        return {key: _ir_key_order(value[key]) for key in keys}
    return value


def canonical_ir_json_bytes(value: Any) -> bytes:
    """Serialize JSON canonically while presenting every ``kind`` key first.

    Key order is not part of IR validity.  This presentation helper exists so
    prompts, fixtures and diagnostics can consistently follow the model-facing
    instruction without pretending JSON Schema can enforce object order.
    """
    _assert_json_tree(value)
    return json.dumps(
        _ir_key_order(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=False,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_file(path: Path, *, max_bytes: int = 8_000_000) -> Any:
    raw = path.read_bytes()
    return strict_json_loads(raw, max_bytes=max_bytes)

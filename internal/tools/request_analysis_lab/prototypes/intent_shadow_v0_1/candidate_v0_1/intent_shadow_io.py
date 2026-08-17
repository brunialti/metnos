"""Strict JSON and canonical hashing primitives.

The parser implements the four hardening families already required by the
canonical oracle:

* D-01: duplicate keys are rejected;
* D-02: NaN, infinities and finite-parser overflow are rejected recursively;
* D-03: callers can require exact JSON types and closed objects;
* D-04: callers compare authority collections as exact unique sets.

Resource limits are offline guardrails, not semantic classifications and not
GPU protocol choices.  Crossing one always raises ``StrictJsonError`` and can
never become ``unrepresentable``.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable

from intent_shadow_types import FrozenArray, FrozenJson, FrozenObject


@dataclass(frozen=True, slots=True)
class TechnicalLimits:
    max_bytes: int = 8 * 1024 * 1024
    max_depth: int = 64
    max_nodes: int = 100_000
    max_string_chars: int = 1_000_000
    max_integer_digits: int = 4_300

    def __post_init__(self) -> None:
        for name in (
            "max_bytes",
            "max_depth",
            "max_nodes",
            "max_string_chars",
            "max_integer_digits",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise TypeError(f"{name} must be a positive exact integer")


DEFAULT_OFFLINE_LIMITS = TechnicalLimits()


class StrictJsonError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


class _NonFinite:
    __slots__ = ("lexeme",)

    def __init__(self, lexeme: str) -> None:
        self.lexeme = lexeme


class _OversizedInteger:
    __slots__ = ("digit_count",)

    def __init__(self, digit_count: int) -> None:
        self.digit_count = digit_count


def _duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError("JSON_DUPLICATE_KEY", "$", repr(key))
        result[key] = value
    return result


def _nonfinite_constant(value: str) -> _NonFinite:
    return _NonFinite(value)


def _finite_float(value: str) -> float | _NonFinite:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else _NonFinite(value)


def _bounded_integer(value: str, max_digits: int) -> int | _OversizedInteger:
    digit_count = len(value) - int(value.startswith("-"))
    if digit_count > max_digits:
        return _OversizedInteger(digit_count)
    return int(value)


def json_path(parent: str, key: str | int) -> str:
    if type(key) is int:
        return f"{parent}[{key}]"
    encoded = json.dumps(key, ensure_ascii=False, allow_nan=False)
    return f"{parent}[{encoded}]"


def _check_tree(value: Any, limits: TechnicalLimits) -> None:
    stack: list[tuple[Any, str, int]] = [(value, "$", 0)]
    nodes = 0
    while stack:
        current, path, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise StrictJsonError("JSON_NODE_LIMIT", path, str(limits.max_nodes))
        if depth > limits.max_depth:
            raise StrictJsonError("JSON_DEPTH_LIMIT", path, str(limits.max_depth))
        if isinstance(current, _NonFinite):
            raise StrictJsonError("JSON_NONFINITE_NUMBER", path, current.lexeme)
        if isinstance(current, _OversizedInteger):
            raise StrictJsonError(
                "JSON_INTEGER_DIGIT_LIMIT",
                path,
                f"digits={current.digit_count},limit={limits.max_integer_digits}",
            )
        if current is None or type(current) in (bool, int):
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise StrictJsonError("JSON_NONFINITE_NUMBER", path, repr(current))
            continue
        if type(current) is str:
            if len(current) > limits.max_string_chars:
                raise StrictJsonError(
                    "JSON_STRING_LIMIT", path, str(limits.max_string_chars)
                )
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise StrictJsonError(
                    "JSON_UNICODE_SCALAR", path, "surrogate code point"
                ) from None
            continue
        if type(current) is list:
            for index in range(len(current) - 1, -1, -1):
                stack.append((current[index], json_path(path, index), depth + 1))
            continue
        if type(current) is dict:
            for key, item in reversed(tuple(current.items())):
                if type(key) is not str:
                    raise StrictJsonError("JSON_KEY_TYPE", path, type(key).__name__)
                try:
                    key.encode("utf-8", errors="strict")
                except UnicodeEncodeError:
                    raise StrictJsonError(
                        "JSON_UNICODE_SCALAR", path, "surrogate object key"
                    ) from None
                stack.append((item, json_path(path, key), depth + 1))
            continue
        raise StrictJsonError("JSON_VALUE_TYPE", path, type(current).__name__)


def strict_json_loads(
    raw: bytes | str,
    *,
    limits: TechnicalLimits = DEFAULT_OFFLINE_LIMITS,
) -> Any:
    if type(raw) is bytes:
        if len(raw) > limits.max_bytes:
            raise StrictJsonError("JSON_BYTE_LIMIT", "$", str(limits.max_bytes))
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise StrictJsonError("JSON_UTF8", "$", str(exc.start)) from None
    elif type(raw) is str:
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise StrictJsonError(
                "JSON_UNICODE_SCALAR", "$", "surrogate input text"
            ) from None
        if len(encoded) > limits.max_bytes:
            raise StrictJsonError("JSON_BYTE_LIMIT", "$", str(limits.max_bytes))
        text = raw
    else:
        raise TypeError("raw JSON must be exact bytes or string")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_object,
            parse_constant=_nonfinite_constant,
            parse_float=_finite_float,
            parse_int=lambda value: _bounded_integer(value, limits.max_integer_digits),
        )
    except StrictJsonError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictJsonError(
            "JSON_SYNTAX", "$", f"line={exc.lineno},column={exc.colno}"
        ) from None
    except RecursionError:
        raise StrictJsonError("JSON_RECURSION", "$", "decoder recursion limit") from None
    except (OverflowError, ValueError, UnicodeError) as exc:
        raise StrictJsonError(
            "JSON_DECODER_FAILURE", "$", type(exc).__name__
        ) from None
    except Exception as exc:
        raise StrictJsonError(
            "JSON_DECODER_INTERNAL_FAILURE", "$", type(exc).__name__
        ) from None
    try:
        _check_tree(value, limits)
    except StrictJsonError:
        raise
    except Exception as exc:
        raise StrictJsonError(
            "JSON_TREE_INTERNAL_FAILURE", "$", type(exc).__name__
        ) from None
    return value


def strict_json_file(
    path: Path,
    *,
    limits: TechnicalLimits = DEFAULT_OFFLINE_LIMITS,
    expected_sha256: str | None = None,
) -> Any:
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    raw = path.read_bytes()
    observed = sha256(raw).hexdigest()
    if expected_sha256 is not None and observed != expected_sha256:
        raise StrictJsonError("FILE_SHA256", "$", path.as_posix())
    return strict_json_loads(raw, limits=limits)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def freeze_json(value: Any) -> FrozenJson:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if type(value) is list:
        return FrozenArray(tuple(freeze_json(item) for item in value))
    if type(value) is dict:
        return FrozenObject(tuple((key, freeze_json(item)) for key, item in value.items()))
    raise TypeError(f"cannot freeze non-JSON value {type(value).__name__}")


def thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, FrozenArray):
        return [thaw_json(item) for item in value.items]
    if isinstance(value, FrozenObject):
        return {key: thaw_json(item) for key, item in value.items}
    return value


def require_exact_type(value: Any, expected: type, path: str) -> None:
    if type(value) is not expected:
        raise StrictJsonError(
            "JSON_EXACT_TYPE", path, f"expected={expected.__name__},actual={type(value).__name__}"
        )


def require_exact_keys(
    value: Any,
    required: Iterable[str],
    optional: Iterable[str],
    path: str,
) -> None:
    require_exact_type(value, dict, path)
    required_set = set(required)
    optional_set = set(optional)
    actual = set(value)
    if actual != required_set | (actual & optional_set) or not required_set <= actual:
        missing = sorted(required_set - actual)
        extra = sorted(actual - required_set - optional_set)
        raise StrictJsonError(
            "JSON_CLOSED_OBJECT", path, f"missing={missing!r},extra={extra!r}"
        )


def require_exact_unique_set(
    values: Any,
    expected: set[str] | frozenset[str],
    path: str,
) -> None:
    require_exact_type(values, list, path)
    if any(type(item) is not str for item in values):
        raise StrictJsonError("JSON_SET_ITEM_TYPE", path, "items must be strings")
    if len(values) != len(set(values)):
        raise StrictJsonError("JSON_SET_DUPLICATE", path, "duplicate item")
    if set(values) != set(expected) or len(values) != len(expected):
        raise StrictJsonError("JSON_SET_MISMATCH", path, "authority set differs")

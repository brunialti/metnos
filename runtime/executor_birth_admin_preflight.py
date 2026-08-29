#!/usr/bin/python3
"""Root-only, standard-library startup preflight for executor Birth V1.

The installed copy is invoked with ``python3 -I -S``.  Consequently this file
must remain self contained: importing a Metnos module here would make the
component being authenticated part of its own trust path.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import PurePosixPath
from typing import NamedTuple


SUPPORTED_SYSTEMD_VERSIONS = ("255.4-1ubuntu8.17",)

EXIT_MISSING = 20
EXIT_INVALID = 21
EXIT_HEAD_MISMATCH = 22
EXIT_PLATFORM = 23
EXIT_RECOVERY = 24

CODE_MISSING = "birth_ownership_preflight_missing"
CODE_INVALID = "birth_ownership_preflight_invalid"
CODE_HEAD_MISMATCH = "birth_ownership_preflight_head_mismatch"
CODE_PLATFORM = "birth_ownership_platform_unsupported"
CODE_RECOVERY = "birth_ownership_recovery_required"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENTRY_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_UNIT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,191}\.(?:service|timer|target)\Z"
)
_OBSERVED_UNIT_RE = re.compile(
    r"(?:-|[A-Za-z0-9](?:[A-Za-z0-9_.:@-]|\\x[0-9A-Fa-f]{2}){0,246})\."
    r"(?:service|socket|target|device|mount|automount|swap|timer|path|slice|scope)\Z"
)
_INTEGER_RE = re.compile(r"0|[1-9][0-9]*\Z")
_DURATION_COMPONENT_RE = re.compile(
    r"(0|[1-9][0-9]*)(?:\.([0-9]{1,6}))?(us|ms|s|min|h|d|w)\Z"
)
_PROPERTY_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")

REPEATABLE_PROPERTIES = frozenset({
    "ExecStartPre", "ExecStartPreEx", "ExecStart", "ExecStartEx", "ExecStop",
    "ExecStopEx", "TimersMonotonic", "TimersCalendar",
})
_DURATION_FACTORS = {
    "us": 1, "ms": 1_000, "s": 1_000_000, "min": 60_000_000,
    "h": 3_600_000_000, "d": 86_400_000_000, "w": 604_800_000_000,
}


class PreflightError(RuntimeError):
    """One stable public denial class; detail is never written to stderr."""

    def __init__(self, code: str, exit_status: int, detail: str = "") -> None:
        self.code = code
        self.exit_status = exit_status
        self.detail = detail
        super().__init__(detail or code)


def _invalid(detail: str) -> PreflightError:
    return PreflightError(CODE_INVALID, EXIT_INVALID, detail)


class CliCommandV1(NamedTuple):
    command: str
    entry_id: str | None


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid("non-canonical value") from exc


def _json_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise _invalid("duplicate JSON key")
        result[key] = value
    return result


def _reject_number(_: str) -> object:
    raise _invalid("non-integer JSON number")


def _parse_integer(raw: str) -> int:
    if len(raw) > 64:
        raise _invalid("JSON integer bound")
    try:
        return int(raw)
    except ValueError as exc:
        raise _invalid("JSON integer") from exc


def _require_json_depth_v1(value: object) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        # One maximum manifest has 20,000 file objects and about 100,000
        # value nodes.  Keep a closed margin for its top-level metadata.
        if depth > 64 or nodes > 120_000:
            raise _invalid("JSON structural bound")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def decode_canonical_json_v1(encoded: bytes, maximum: int) -> object:
    """Decode one size-bounded, duplicate-free canonical ASCII document."""
    if (
        type(encoded) is not bytes or type(maximum) is not int
        or maximum <= 0 or len(encoded) > maximum
    ):
        raise _invalid("JSON size")
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=_json_pairs,
            parse_int=_parse_integer,
            parse_float=_reject_number, parse_constant=_reject_number,
        )
    except PreflightError:
        raise
    except (
        UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError,
    ) as exc:
        raise _invalid("JSON encoding") from exc
    _require_json_depth_v1(value)
    if _canonical_json(value) != encoded:
        raise _invalid("JSON canonicality")
    return value


def _digest(domain: bytes, payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + payload).hexdigest()


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise _invalid(field)
    return value


def validate_digest_v1(value: object) -> str:
    return _require_digest(value, "digest")


def validate_entry_id_v1(value: object) -> str:
    if not isinstance(value, str) or _ENTRY_ID_RE.fullmatch(value) is None:
        raise _invalid("entry_id")
    return value


def validate_unit_name_v1(value: object) -> str:
    if (
        not isinstance(value, str) or _UNIT_RE.fullmatch(value) is None
        or len(value.encode("utf-8")) > 192
    ):
        raise _invalid("unit name")
    return value


def validate_absolute_path_v1(value: object) -> str:
    if (
        not isinstance(value, str) or not value or "\0" in value
        or "\\" in value or unicodedata.normalize("NFC", value) != value
    ):
        raise _invalid("absolute path")
    try:
        encoded = value.encode("utf-8")
        path = PurePosixPath(value)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _invalid("absolute path") from exc
    if (
        len(encoded) > 4096 or not path.is_absolute()
        or not value.startswith("/") or value.startswith("//")
        or str(path) != value or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise _invalid("absolute path")
    return value


def validate_relative_path_v1(value: object) -> str:
    if (
        not isinstance(value, str) or not value or "\0" in value
        or "\\" in value or unicodedata.normalize("NFC", value) != value
    ):
        raise _invalid("relative path")
    try:
        encoded = value.encode("utf-8")
        path = PurePosixPath(value)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _invalid("relative path") from exc
    if (
        len(encoded) > 4096 or path.is_absolute() or value == "."
        or str(path) != value
        or len(path.parts) > 32 or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _invalid("relative path")
    return value


def require_linux_before_io_v1() -> None:
    """First productive guard; reading ``sys.platform`` performs no I/O."""
    if sys.platform != "linux":
        raise PreflightError(CODE_PLATFORM, EXIT_PLATFORM, "platform")


def parse_cli_v1(argv: list[str]) -> CliCommandV1:
    """Parse only the three byte-for-byte command forms from the contract."""
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise _invalid("CLI arguments")
    if argv == ["check-all"]:
        return CliCommandV1("check-all", None)
    if (
        len(argv) == 3 and argv[0] in {"check", "launch"}
        and argv[1] == "--entry-id"
    ):
        return CliCommandV1(argv[0], validate_entry_id_v1(argv[2]))
    raise _invalid("CLI arguments")


def _validate_property_request_v1(properties: object) -> tuple[str, ...]:
    if (
        type(properties) is not tuple or not properties
        or any(
            not isinstance(item, str) or _PROPERTY_RE.fullmatch(item) is None
            for item in properties
        )
    ):
        raise _invalid("systemctl property request")
    if (
        tuple(sorted(properties)) != properties
        or len(properties) != len(set(properties))
    ):
        raise _invalid("systemctl property request")
    return properties


def parse_systemctl_show_v1(
    stdout: bytes, requested_properties: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """Parse the closed byte protocol used for every ``systemctl show`` call.

    Absence is retained as absence.  The caller, which knows the unit class,
    must enforce mandatory properties and the paired empty-Exec exception.
    """
    if (
        type(stdout) is not bytes or len(stdout) > 4 * 1024 * 1024
        or not stdout.endswith(b"\n") or stdout.endswith(b"\n\n")
        or b"\r" in stdout or b"\0" in stdout
    ):
        raise _invalid("systemctl output framing")
    _validate_property_request_v1(requested_properties)
    allowed = frozenset(requested_properties)
    result: dict[str, list[str]] = {}
    lines = stdout[:-1].split(b"\n")
    if len(lines) > 4096 or any(len(line) > 64 * 1024 for line in lines):
        raise _invalid("systemctl output bounds")
    for raw in lines:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _invalid("systemctl UTF-8") from exc
        name, separator, value = line.partition("=")
        if separator != "=" or name not in allowed:
            raise _invalid("systemctl property line")
        values = result.setdefault(name, [])
        if values and name not in REPEATABLE_PROPERTIES:
            raise _invalid("duplicate systemctl property")
        values.append(value)
    return {name: tuple(values) for name, values in result.items()}


def systemctl_show_argv_v1(
    systemctl_executable: str, unit_name: str | None,
    properties: tuple[str, ...],
) -> tuple[str, ...]:
    _validate_property_request_v1(properties)
    executable = validate_absolute_path_v1(systemctl_executable)
    if executable == "/":
        raise _invalid("systemctl executable")
    argv = (
        executable, "--no-pager", "--plain", "--all", "show",
        "--property=" + ",".join(properties),
    )
    if unit_name is None:
        if properties != ("Version",):
            raise _invalid("manager property request")
        return argv
    if (
        type(unit_name) is not str or _OBSERVED_UNIT_RE.fullmatch(unit_name) is None
        or len(unit_name.encode("utf-8")) > 255
    ):
        raise _invalid("unit name")
    return argv + ("--", unit_name)


def parse_systemd_manager_version_v1(stdout: bytes) -> str:
    parsed = parse_systemctl_show_v1(stdout, ("Version",))
    if set(parsed) != {"Version"} or len(parsed["Version"]) != 1:
        raise _invalid("manager Version")
    version = parsed["Version"][0]
    if version not in SUPPORTED_SYSTEMD_VERSIONS:
        raise _invalid("unsupported manager Version")
    return version


def tokenize_systemd_words_v1(value: str) -> tuple[str, ...]:
    """Decode the bounded C-quoted word form emitted by systemd 255."""
    if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value:
        raise _invalid("systemd word list")
    words: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    active = False
    escapes = {"\\": "\\", '"': '"', "'": "'", "s": " ", "t": "\t"}
    while index < len(value):
        char = value[index]
        if quote is None and char == " ":
            if not active:
                raise _invalid("systemd word spacing")
            words.append("".join(current))
            current = []
            active = False
            index += 1
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            else:
                current.append(char)
            active = True
            index += 1
            continue
        if char == "\\":
            index += 1
            if index >= len(value):
                raise _invalid("systemd escape")
            escaped = value[index]
            if escaped in escapes:
                current.append(escapes[escaped])
            elif escaped == "x" and index + 2 < len(value):
                raw = value[index + 1:index + 3]
                if re.fullmatch(r"[0-9A-Fa-f]{2}", raw) is None:
                    raise _invalid("systemd hex escape")
                codepoint = int(raw, 16)
                if codepoint == 0 or codepoint > 0x7f:
                    raise _invalid("systemd NUL escape")
                current.append(chr(codepoint))
                index += 2
            elif escaped in "01234567" and index + 2 < len(value):
                raw = value[index:index + 3]
                if re.fullmatch(r"[0-7]{3}", raw) is None:
                    raise _invalid("systemd octal escape")
                codepoint = int(raw, 8)
                if codepoint == 0 or codepoint > 0x7f:
                    raise _invalid("systemd NUL escape")
                current.append(chr(codepoint))
                index += 2
            else:
                raise _invalid("unknown systemd escape")
            active = True
            index += 1
            continue
        current.append(char)
        active = True
        index += 1
    if quote is not None:
        raise _invalid("unterminated systemd quote")
    if value and not active:
        raise _invalid("systemd word spacing")
    if active:
        words.append("".join(current))
    if any(not word or "\0" in word for word in words):
        raise _invalid("empty systemd word")
    return tuple(words)


def normalize_systemd_duration_usec_v1(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid("duration")
    total = 0
    for component in value.split(" "):
        match = _DURATION_COMPONENT_RE.fullmatch(component)
        if match is None:
            raise _invalid("duration component")
        whole, fraction, suffix = match.groups()
        factor = _DURATION_FACTORS[suffix]
        total += int(whole) * factor
        if fraction is not None:
            numerator = int(fraction.ljust(6, "0")) * factor
            if numerator % 1_000_000:
                raise _invalid("fractional duration precision")
            total += numerator // 1_000_000
        if total > (1 << 63) - 1:
            raise _invalid("duration overflow")
    return str(total)


_EXEC_RE = re.compile(
    r"\{ path=(?P<path>[^ ;]+) ; argv\[\]=(?P<argv>.*?) ; "
    r"(?P<flag_name>flags|ignore_errors)=(?P<flags>[^ ;]*) ; "
    r"start_time=(?P<start>.*?) ; stop_time=(?P<stop>.*?) ; "
    r"pid=(?P<pid>[^ ;]+) ; code=(?P<code>[^ ;]+) ; "
    r"status=(?P<status>[^ }]+) \}\Z"
)


def parse_systemd_exec_v1(value: str, *, extended: bool) -> dict[str, object]:
    match = _EXEC_RE.fullmatch(value)
    if match is None:
        raise _invalid("Exec structure")
    flag_name = match.group("flag_name")
    if flag_name != ("flags" if extended else "ignore_errors"):
        raise _invalid("Exec flag field")
    path = validate_absolute_path_v1(match.group("path"))
    if path == "/":
        raise _invalid("Exec path")
    argv = tokenize_systemd_words_v1(match.group("argv"))
    if not path.startswith("/") or not argv or argv[0] != path:
        raise _invalid("Exec path/argv")
    if any(
        re.fullmatch(r"\[[A-Za-z0-9:.,+_~/\- ]{1,128}\]", match.group(name))
        is None
        for name in ("start", "stop")
    ):
        raise _invalid("Exec dynamic time")
    if _INTEGER_RE.fullmatch(match.group("pid")) is None:
        raise _invalid("Exec dynamic pid")
    code = match.group("code")
    status = match.group("status")
    if code not in {"(null)", "exited", "killed", "dumped"}:
        raise _invalid("Exec dynamic code")
    if (
        (code == "(null)" and re.fullmatch(r"[0-9]+/[0-9]+", status) is None)
        or (code != "(null)" and _INTEGER_RE.fullmatch(status) is None)
    ):
        raise _invalid("Exec dynamic status")
    raw_flags = match.group("flags")
    if extended:
        flags = () if raw_flags == "" else (raw_flags,)
        if raw_flags not in {"", "no-setuid"}:
            raise _invalid("Exec flags")
    else:
        if raw_flags not in {"yes", "no"}:
            raise _invalid("Exec ignore_errors")
        flags = ("ignore-failure",) if raw_flags == "yes" else ()
    return {
        "path": path, "argv": argv, "flags": flags,
        "_dynamic": (
            match.group("start"), match.group("stop"), match.group("pid"),
            code, status,
        ),
    }


_TIMER_RE = re.compile(
    r"\{ (?P<name>OnBootUSec|OnActiveUSec|OnUnitActiveUSec|OnCalendar)="
    r"(?P<value>.+?) ; next_elapse=(?P<next>.+?) \}\Z"
)


def parse_systemd_timer_v1(value: str) -> tuple[str, str]:
    match = _TIMER_RE.fullmatch(value)
    if match is None:
        raise _invalid("timer structure")
    dynamic = match.group("next")
    if (
        len(dynamic) > 256
        or re.fullmatch(r"(?:\[n/a\]|[A-Za-z0-9:.,+_~/\- ]+)", dynamic) is None
    ):
        raise _invalid("timer dynamic value")
    return match.group("name") + "=" + match.group("value"), dynamic


def parse_systemd_timer_properties_v1(
    monotonic: tuple[str, ...], calendar: tuple[str, ...],
) -> dict[str, str]:
    """Normalize the static timer bases and discard only next_elapse."""
    result: dict[str, str] = {}
    for value in monotonic:
        base, _dynamic = parse_systemd_timer_v1(value)
        name, separator, duration = base.partition("=")
        if separator != "=" or name not in {
            "OnBootUSec", "OnActiveUSec", "OnUnitActiveUSec"
        } or name in result:
            raise _invalid("TimersMonotonic base")
        result[name] = normalize_systemd_duration_usec_v1(duration)
    for value in calendar:
        base, _dynamic = parse_systemd_timer_v1(value)
        name, separator, expression = base.partition("=")
        if (
            separator != "=" or name != "OnCalendar" or name in result
            or not expression or expression != expression.strip()
            or "\0" in expression or "\n" in expression or "\r" in expression
            or len(expression) > 256
            or re.fullmatch(r"[A-Za-z0-9*,:.+_~/\- ]+", expression) is None
        ):
            raise _invalid("TimersCalendar base")
        result[name] = expression
    return result


def validate_exec_property_pair_v1(
    historical: tuple[str, ...], extended: tuple[str, ...],
    expected_flags: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    if len(historical) != len(extended):
        raise _invalid("Exec pair cardinality")
    result: list[dict[str, object]] = []
    for old_value, ex_value in zip(historical, extended):
        old = parse_systemd_exec_v1(old_value, extended=False)
        new = parse_systemd_exec_v1(ex_value, extended=True)
        if (
            old["path"] != new["path"] or old["argv"] != new["argv"]
            or old["_dynamic"] != new["_dynamic"]
        ):
            raise _invalid("Exec pair mismatch")
        if old["flags"] or new["flags"] != expected_flags:
            raise _invalid("Exec static flags")
        result.append({
            "path": new["path"], "argv": new["argv"], "flags": new["flags"],
        })
    return tuple(result)

"""Compact portable oracles for the autonomous RM-0008 preflight."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import executor_birth_admin_preflight as preflight


def _invalid(callable_, *args, **kwargs) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        callable_(*args, **kwargs)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.exit_status == preflight.EXIT_INVALID
    return failure.value


def test_script_loads_with_isolated_standard_library_only() -> None:
    script = str(Path(preflight.__file__).resolve())
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            f"import runpy; runpy.run_path({script!r}, run_name='preflight_probe')",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace",
    )
    assert completed.stdout == b""
    assert completed.stderr == b""


def test_cli_accepts_only_three_exact_forms() -> None:
    assert preflight.parse_cli_v1(["check-all"]) == ("check-all", None)
    assert preflight.parse_cli_v1(
        ["check", "--entry-id", "service-http"],
    ) == ("check", "service-http")
    assert preflight.parse_cli_v1(
        ["launch", "--entry-id", "entry-installer"],
    ) == ("launch", "entry-installer")
    for argv in (
        [], ["--help"], ["check-all", "extra"],
        ["check", "--entry", "service-http"],
        ["check", "--entry-id", "service-http", "--entry-id", "x"],
        ["launch", "service-http"], ["CHECK-ALL"],
    ):
        _invalid(preflight.parse_cli_v1, argv)


def test_platform_guard_is_a_stable_early_denial(monkeypatch) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    with pytest.raises(preflight.PreflightError) as failure:
        preflight.require_linux_before_io_v1()
    assert (failure.value.code, failure.value.exit_status) == (
        preflight.CODE_PLATFORM, preflight.EXIT_PLATFORM,
    )


def test_canonical_json_rejects_duplicate_noncanonical_and_noninteger() -> None:
    encoded = b'{"a":1,"text":"caf\\u00e9"}'
    assert preflight.decode_canonical_json_v1(encoded, len(encoded)) == {
        "a": 1, "text": "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
    }
    for mutant in (
        b'{"a":1,"a":1}', b'{"a": 1}', b'{"a":1.0}',
        b'{"a":NaN}', b'{"text":"caf\xc3\xa9"}',
    ):
        _invalid(preflight.decode_canonical_json_v1, mutant, 1024)
    _invalid(preflight.decode_canonical_json_v1, encoded, len(encoded) - 1)
    _invalid(preflight.decode_canonical_json_v1, encoded, True)
    huge_integer = b'{"a":' + b"9" * 5000 + b"}"
    _invalid(
        preflight.decode_canonical_json_v1, huge_integer, len(huge_integer),
    )
    too_deep = b"[" * 1100 + b"0" + b"]" * 1100
    _invalid(preflight.decode_canonical_json_v1, too_deep, len(too_deep))


def test_canonical_json_accepts_manifest_maximum_node_shape() -> None:
    digest = "sha256:" + "a" * 64
    value = {
        "files": [
            {"content_hash": digest, "path": f"f/{index}", "role": "runtime_code", "size": 0}
            for index in range(20_000)
        ],
        "schema_version": 1,
    }
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    assert preflight.decode_canonical_json_v1(encoded, 16 * 1024 * 1024) == value


def test_closed_identifier_and_path_grammars() -> None:
    digest = "sha256:" + "a" * 64
    assert preflight.validate_digest_v1(digest) == digest
    assert preflight.validate_entry_id_v1("service-http") == "service-http"
    exact_unit = "a" * 184 + ".service"
    assert len(exact_unit.encode()) == 192
    assert preflight.validate_unit_name_v1(exact_unit) == exact_unit
    assert preflight.validate_absolute_path_v1("/usr/bin/systemctl") == (
        "/usr/bin/systemctl"
    )
    assert preflight.validate_absolute_path_v1("/") == "/"
    assert preflight.validate_relative_path_v1("pkg/main.py") == "pkg/main.py"
    for value in ("SHA256:" + "a" * 64, "sha256:" + "g" * 64, None):
        _invalid(preflight.validate_digest_v1, value)
    for value in ("-bad", "Bad", "a" * 65):
        _invalid(preflight.validate_entry_id_v1, value)
    for value in ("a" * 185 + ".service", "name.socket", "../a.service"):
        _invalid(preflight.validate_unit_name_v1, value)
    for value in (
        "//", "//usr/bin/x", "/usr/../bin/x", "/usr//bin/x",
        "/tmp/a\\b", "/tmp/cafe\N{COMBINING ACUTE ACCENT}", "usr/bin/x",
    ):
        _invalid(preflight.validate_absolute_path_v1, value)
    for value in (
        ".", "/pkg/main.py", "../main.py", "pkg//main.py",
        "pkg\\main.py", "cafe\N{COMBINING ACUTE ACCENT}.py",
    ):
        _invalid(preflight.validate_relative_path_v1, value)


def test_systemctl_argv_is_closed_and_dash_safe() -> None:
    assert preflight.systemctl_show_argv_v1(
        "/usr/bin/systemctl", None, ("Version",),
    ) == (
        "/usr/bin/systemctl", "--no-pager", "--plain", "--all", "show",
        "--property=Version",
    )
    assert preflight.systemctl_show_argv_v1(
        "/usr/bin/systemctl", "-.slice", ("Id", "LoadState"),
    )[-2:] == ("--", "-.slice")
    for unit in (
        "home.mount", "systemd-journald.socket", "system.slice",
        "dev-sda.device", "init.scope",
    ):
        assert preflight.systemctl_show_argv_v1(
            "/usr/bin/systemctl", unit, ("Id", "LoadState"),
        )[-1] == unit
    _invalid(
        preflight.systemctl_show_argv_v1,
        "usr/bin/systemctl", None, ("Version",),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/", None, ("Version",),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", "a.service", ("LoadState", "Id"),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", r"bad\q.socket", ("Id", "LoadState"),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", "a.service", ("Id", 1),
    )


def test_systemctl_show_parser_preserves_only_allowed_repetition() -> None:
    properties = ("LoadState", "TimersMonotonic")
    parsed = preflight.parse_systemctl_show_v1(
        b"LoadState=loaded\n"
        b"TimersMonotonic={ OnUnitActiveUSec=1d ; next_elapse=2w }\n"
        b"TimersMonotonic={ OnBootUSec=15min ; next_elapse=15min }\n",
        properties,
    )
    assert parsed["LoadState"] == ("loaded",)
    assert len(parsed["TimersMonotonic"]) == 2
    _invalid(
        preflight.parse_systemctl_show_v1,
        b"LoadState=loaded\nLoadState=loaded\n", properties,
    )
    for output in (
        b"Unknown=x\n", b"LoadState=loaded", b"LoadState=loaded\r\n",
        b"LoadState=loaded\n\n", b"LoadState=\xff\n",
    ):
        _invalid(preflight.parse_systemctl_show_v1, output, properties)


def test_manager_version_is_exact() -> None:
    assert preflight.parse_systemd_manager_version_v1(
        b"Version=255.4-1ubuntu8.17\n",
    ) == "255.4-1ubuntu8.17"
    _invalid(
        preflight.parse_systemd_manager_version_v1,
        b"Version=255.4-1ubuntu8.18\n",
    )


def test_systemd_word_tokenizer_is_canonical_and_bounded() -> None:
    assert preflight.tokenize_systemd_words_v1(
        'one "two\\swords" three\\x2dfour',
    ) == ("one", "two words", "three-four")
    for value in (
        " one", "one ", "one  two", '"unterminated', "bad\\q",
        "bad\\x00", "bad\\xc3",
    ):
        _invalid(preflight.tokenize_systemd_words_v1, value)


@pytest.mark.parametrize(("raw", "expected"), [
    ("100ms", "100000"), ("90s", "90000000"),
    ("1min 30s", "90000000"), ("1.5s", "1500000"),
])
def test_duration_normalization_uses_integer_microseconds(
    raw: str, expected: str,
) -> None:
    assert preflight.normalize_systemd_duration_usec_v1(raw) == expected


@pytest.mark.parametrize("raw", [
    "1.0000001s", "0.1us", "1s  2s", "1 s", "-1s", "infinity",
])
def test_duration_normalization_rejects_ambiguous_values(raw: str) -> None:
    _invalid(preflight.normalize_systemd_duration_usec_v1, raw)


def _exec_value(*, extended: bool, flags: str, argv: str = "/bin/x arg") -> str:
    field = "flags" if extended else "ignore_errors"
    return (
        f"{{ path=/bin/x ; argv[]={argv} ; {field}={flags} ; "
        "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; "
        "status=0/0 }"
    )


def test_exec_pair_preserves_privileged_prefix_and_argv() -> None:
    historical = (_exec_value(extended=False, flags="no"),)
    privileged = (_exec_value(extended=True, flags="no-setuid"),)
    result = preflight.validate_exec_property_pair_v1(
        historical, privileged, ("no-setuid",),
    )
    assert result[0] == {
        "path": "/bin/x", "argv": ("/bin/x", "arg"),
        "flags": ("no-setuid",),
    }
    assert preflight.validate_exec_property_pair_v1(
        historical, (_exec_value(extended=True, flags=""),), (),
    )[0]["flags"] == ()
    for extended in (
        (_exec_value(extended=True, flags="privileged"),),
        (_exec_value(extended=True, flags="ambient"),),
        (_exec_value(extended=True, flags="no-setuid,privileged"),),
    ):
        _invalid(
            preflight.validate_exec_property_pair_v1,
            historical, extended, ("no-setuid",),
        )
    _invalid(
        preflight.validate_exec_property_pair_v1,
        historical,
        (_exec_value(extended=True, flags="no-setuid", argv="/bin/x other"),),
        ("no-setuid",),
    )


def test_exec_pair_accepts_completed_process_and_rejects_mixed_state() -> None:
    historical = _exec_value(extended=False, flags="no").replace(
        "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0",
        "stop_time=[Fri 2026-08-28 03:38:07 CEST] ; pid=2766513 ; "
        "code=exited ; status=0",
    )
    extended = _exec_value(extended=True, flags="").replace(
        "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0",
        "stop_time=[Fri 2026-08-28 03:38:07 CEST] ; pid=2766513 ; "
        "code=exited ; status=0",
    )
    assert preflight.validate_exec_property_pair_v1(
        (historical,), (extended,), (),
    )[0]["path"] == "/bin/x"
    _invalid(
        preflight.validate_exec_property_pair_v1,
        (historical,), (extended.replace("status=0", "status=0/0"),), (),
    )
    _invalid(
        preflight.validate_exec_property_pair_v1,
        (historical,), (extended.replace("pid=2766513", "pid=2766514"),), (),
    )
    _invalid(
        preflight.parse_systemd_exec_v1,
        historical.replace(
            "start_time=[n/a]", "start_time=[n/a] ; injected=[x]",
        ),
        extended=False,
    )


def test_timer_parser_matches_real_repeated_systemd_255_shape() -> None:
    observed = preflight.parse_systemd_timer_properties_v1(
        (
            "{ OnUnitActiveUSec=1d ; next_elapse=2w 16min 42.105129s }",
            "{ OnBootUSec=15min ; next_elapse=15min }",
        ),
        ("{ OnCalendar=*-*-* 06,18:00:00 ; next_elapse=[n/a] }",),
    )
    assert observed == {
        "OnUnitActiveUSec": "86400000000",
        "OnBootUSec": "900000000",
        "OnCalendar": "*-*-* 06,18:00:00",
    }
    _invalid(
        preflight.parse_systemd_timer_properties_v1,
        (
            "{ OnBootUSec=15min ; next_elapse=15min }",
            "{ OnBootUSec=15min ; next_elapse=15min }",
        ), (),
    )
    _invalid(
        preflight.parse_systemd_timer_properties_v1,
        ("{ OnBootUSec=15min ; next_elapse=[n/a] ; injected=yes }",), (),
    )

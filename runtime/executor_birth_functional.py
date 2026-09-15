# SPDX-License-Identifier: AGPL-3.0-only
"""Declarative Synth tests, executed only by the core's hermetic Birth runner.

This is a pre-publication test, not an admission receipt. Producers supply
source bytes and JSON cases; they cannot supply a command, environment,
capability, backend, callback or path to host data.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from executor_birth_runner import (
    FixtureOp, FixtureOpKind, LinuxSandboxRegistry, RunnerStatus,
    WindowsSandboxRegistry, begin_birth_deadline, run_birth_phase,
)
from executor_birth_template_table_v1 import template_v1
from test_runner import check_expect


MAX_SOURCE_BYTES = 1024 * 1024
MAX_CASE_BYTES = 128 * 1024
MAX_SUPPORT_FILE_BYTES = 2 * 1024 * 1024
MIN_CASES = 3
MAX_CASES = 6
# Only ordinary source helpers and the released public message seed. Never
# a live database, home, keystore, arbitrary dependency or runtime directory.
FUNCTIONAL_SUPPORT_FILES_V1 = (
    "executor_helpers.py", "messages.py", "i18n.py", "config.py",
    "logging_setup.py", "worker_policy.py", "hashutil.py",
)
_HARNESS_NAME = "_metnos_functional_v1.py"
_INPUT_NAME = "_metnos_functional_input.json"


def _decode_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def finite(_value):
        raise ValueError("nonfinite JSON value")

    return json.loads(value, object_pairs_hook=unique, parse_constant=finite)


def validate_functional_cases(cases: object) -> None:
    """One closed, nonempty case shape shared by rendering and execution."""
    if not isinstance(cases, list) or not MIN_CASES <= len(cases) <= MAX_CASES:
        raise ValueError("synth_tests_count_invalid")
    names: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"name", "input", "expect"}:
            raise ValueError("synth_test_fields_invalid")
        name = case["name"]
        if (not isinstance(name, str) or not name.strip() or len(name) > 80
                or "\x00" in name or name in names):
            raise ValueError("synth_test_name_invalid")
        names.add(name)
        if not isinstance(case["input"], dict):
            raise ValueError("synth_test_input_invalid")
        if not isinstance(case["expect"], dict) or not case["expect"]:
            raise ValueError("synth_test_expect_empty")
    try:
        encoded = json.dumps(cases, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("synth_test_json_invalid") from exc
    if len(encoded) > MAX_CASE_BYTES:
        raise ValueError("synth_tests_size_invalid")


@dataclass(frozen=True, slots=True)
class SynthTestData:
    source_name: str
    source_bytes: bytes
    cases_json: bytes

    def __post_init__(self) -> None:
        if (not isinstance(self.source_name, str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,119}\.py", self.source_name)):
            raise ValueError("synth_test_source_name_invalid")
        if (not isinstance(self.source_bytes, bytes)
                or not 0 < len(self.source_bytes) <= MAX_SOURCE_BYTES):
            raise ValueError("synth_test_source_size_invalid")
        if (not isinstance(self.cases_json, bytes)
                or not 0 < len(self.cases_json) <= MAX_CASE_BYTES):
            raise ValueError("synth_tests_size_invalid")
        self.cases()

    @classmethod
    def from_cases(cls, source_name: str, source_bytes: bytes, cases: object):
        validate_functional_cases(cases)
        return cls(source_name, source_bytes, json.dumps(
            cases, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))

    def cases(self) -> list[dict]:
        try:
            cases = _decode_json(self.cases_json)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise ValueError("synth_test_json_invalid") from exc
        validate_functional_cases(cases)
        return cases


@dataclass(frozen=True, slots=True)
class SynthTestReport:
    tests: tuple[dict, ...] = ()
    error_code: str | None = None

    @property
    def passed_count(self) -> int:
        return sum(case.get("passed") is True for case in self.tests)

    @property
    def all_passed(self) -> bool:
        return (self.error_code is None and MIN_CASES <= len(self.tests) <= MAX_CASES
                and self.passed_count == len(self.tests))

    @property
    def summary(self) -> str:
        if self.error_code:
            return self.error_code
        result = f"{self.passed_count}/{len(self.tests)} passed"
        failed = [f"{case['name']}: {case.get('reason', 'failed')}"
                  for case in self.tests if case.get("passed") is not True]
        return result + ("; " + "; ".join(failed[:3]) if failed else "")


def _support_files() -> dict[str, bytes]:
    runtime = Path(__file__).resolve().parent
    sources = {"runtime/" + name: runtime / name
               for name in FUNCTIONAL_SUPPORT_FILES_V1}
    sources["install/data/i18n_seed.sqlite"] = (
        runtime.parent / "install/data/i18n_seed.sqlite"
    )
    result = {}
    for label, source in sources.items():
        with source.open("rb") as stream:
            payload = stream.read(MAX_SUPPORT_FILE_BYTES + 1)
        if not payload or len(payload) > MAX_SUPPORT_FILE_BYTES:
            raise ValueError("synth_test_support_invalid")
        result[label] = payload
    return result


def _run_synth_tests(
    data: SynthTestData, *, linux_registry: LinuxSandboxRegistry | None,
    windows_registry: WindowsSandboxRegistry | None,
) -> SynthTestReport:
    """Private core seam; the producer facade never accepts these registries."""
    if type(data) is not SynthTestData:
        raise ValueError("synth_test_data_invalid")
    cases = data.cases()
    if sys.platform == "win32":
        command = (_HARNESS_NAME, data.source_name)
    elif sys.platform.startswith("linux") and isinstance(linux_registry, LinuxSandboxRegistry):
        # Use the registered program, not this process's venv or PATH.
        command = (str(linux_registry.interpreter_path), "-I",
                   "candidate/" + _HARNESS_NAME, data.source_name)
    else:
        return SynthTestReport(error_code="test_environment_unavailable")
    try:
        files = _support_files()
    except (OSError, ValueError):
        return SynthTestReport(error_code="test_environment_unavailable")
    files[data.source_name] = data.source_bytes
    files[_HARNESS_NAME] = template_v1("runner.functional_stdin").encode("utf-8")
    candidate_id = "sha256:" + hashlib.sha256(
        data.source_name.encode() + b"\0" + data.source_bytes + data.cases_json
    ).hexdigest()
    # One overall budget, never a fresh 30 seconds for each case.
    deadline = begin_birth_deadline()
    reports = []
    for case in cases:
        files[_INPUT_NAME] = json.dumps(
            case["input"], ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        result = run_birth_phase(
            command, candidate_files=files, candidate_id=candidate_id,
            linux_registry=linux_registry, windows_registry=windows_registry,
            deadline=deadline,
            fixture_ops=(
                FixtureOp(FixtureOpKind.MKDIR, "fixture"),
                FixtureOp(FixtureOpKind.WRITE_BYTES, "fixture/input.txt", b"birth fixture\n"),
                FixtureOp(FixtureOpKind.WRITE_BYTES, "fixture/empty.txt", b""),
                FixtureOp(FixtureOpKind.MKDIR, "fixture/output"),
            ),
        )
        attestation = result.attestation
        isolated = (attestation.sandboxed and attestation.network_unshared
                    and attestation.tree_empty and attestation.termination_attested)
        if sys.platform.startswith("linux"):
            isolated = (isolated and attestation.pid_unshared and attestation.user_unshared
                        and attestation.ipc_unshared and attestation.uts_unshared
                        and attestation.cgroup_v2)
        reason = result.error_code or "synth_test_process_failed"
        passed = False
        failures = []
        if (result.status is RunnerStatus.PASSED and result.error_code is None
                and result.returncode == 0 and isolated):
            try:
                actual = _decode_json(result.stdout)
                if not isinstance(actual, dict):
                    raise ValueError("not an object")
                failures = check_expect(actual, case["expect"])
                if "ok" in case["expect"] and (
                    type(case["expect"]["ok"]) is not bool
                    or actual.get("ok") is not case["expect"]["ok"]
                ):
                    failures.append("ok must be the expected boolean")
                passed = not failures
                reason = "ok" if passed else "synth_test_expectation_failed"
            except (ValueError, TypeError, AttributeError, IndexError, RecursionError):
                reason = "synth_test_output_invalid"
        reports.append({
            **case, "passed": passed, "reason": reason,
            "returncode": result.returncode,
            # Diagnostic data only, never interpreted as a verdict or receipt.
            "failures": [str(item)[:300] for item in failures[:3]],
            "stdout": result.stdout[:2000], "stderr": result.stderr[:1000],
        })
        if result.status is RunnerStatus.UNAVAILABLE or not isolated:
            return SynthTestReport(tuple(reports), "test_environment_unavailable")
    return SynthTestReport(tuple(reports))

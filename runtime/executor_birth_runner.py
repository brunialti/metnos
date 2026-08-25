# SPDX-License-Identifier: AGPL-3.0-only
"""Hermetic execution foundation for RM-0008 birth tests.

This module is deliberately independent from :mod:`test_runner`.  A birth
test either runs with the complete v1 isolation contract or returns a typed
``test_environment_unavailable`` result; it never falls back to host
execution.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from executor_birth_runner_windows_v1 import (
    WindowsBirthHelperError,
    invoke_helper as invoke_windows_birth_helper,
)

from bounded_subprocess import (
    SubprocessOutputLimitExceeded,
    SubprocessTerminationError,
    run_bounded_subprocess,
)


PHASE_TIMEOUT_S = 10.0
TOTAL_TIMEOUT_S = 30.0
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
STDOUT_LIMIT_BYTES = 1024 * 1024
STDERR_LIMIT_BYTES = 1024 * 1024
MAX_PROCESSES = 32
TERMINATION_DRAIN_S = 2.0

# Constructed from zero.  No value is copied from ``os.environ``.
SANDBOX_ENV: Mapping[str, str] = {
    "HOME": "/work",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TMPDIR": "/tmp",
    "TZ": "UTC",
}

_CGROUP_DELEGATE = Path("/sys/fs/cgroup/metnos-birth")


class FixtureOpKind(str, Enum):
    MKDIR = "mkdir"
    WRITE_BYTES = "write_bytes"
    SEED_JSON = "seed_json"


class RunnerStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "test_environment_unavailable"


@dataclass(frozen=True, slots=True)
class FixtureOp:
    kind: FixtureOpKind
    path: str
    payload: bytes | object | None = None


@dataclass(frozen=True, slots=True)
class RunnerPolicy:
    phase_timeout_s: float = PHASE_TIMEOUT_S
    total_timeout_s: float = TOTAL_TIMEOUT_S
    memory_limit_bytes: int = MEMORY_LIMIT_BYTES
    stdout_limit_bytes: int = STDOUT_LIMIT_BYTES
    stderr_limit_bytes: int = STDERR_LIMIT_BYTES
    max_processes: int = MAX_PROCESSES
    termination_drain_s: float = TERMINATION_DRAIN_S


V1_POLICY = RunnerPolicy()


@dataclass(frozen=True, slots=True)
class WindowsSandboxRegistry:
    helper_path: Path
    helper_binary_hash: str
    config_path: Path
    config_hash: str
    runtime_binary_hash: str


@dataclass(frozen=True, slots=True)
class BirthDeadline:
    """Core-owned wall-clock budget shared by every phase of one birth."""

    started_at: float
    expires_at: float

    @classmethod
    def begin(cls) -> "BirthDeadline":
        started = time.monotonic()
        return cls(started, started + TOTAL_TIMEOUT_S)

    def phase_budget(self) -> float:
        return min(PHASE_TIMEOUT_S, max(0.0, self.expires_at - time.monotonic()))


def begin_birth_deadline() -> BirthDeadline:
    return BirthDeadline.begin()


@dataclass(frozen=True, slots=True)
class ProcessAttestation:
    backend: str
    sandboxed: bool
    network_unshared: bool
    pid_unshared: bool
    user_unshared: bool
    ipc_unshared: bool
    uts_unshared: bool
    cgroup_v2: bool
    cgroup_path: str | None
    tree_empty: bool
    termination_attested: bool


@dataclass(frozen=True, slots=True)
class RunnerResult:
    status: RunnerStatus
    error_code: str | None
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_s: float
    attestation: ProcessAttestation


class RunnerInputError(ValueError):
    """A request violates the closed v1 runner contract."""


def _empty_attestation(backend: str) -> ProcessAttestation:
    return ProcessAttestation(
        backend=backend,
        sandboxed=False,
        network_unshared=False,
        pid_unshared=False,
        user_unshared=False,
        ipc_unshared=False,
        uts_unshared=False,
        cgroup_v2=False,
        cgroup_path=None,
        tree_empty=False,
        termination_attested=False,
    )


def _relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RunnerInputError("fixture_path_invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RunnerInputError("fixture_path_invalid")
    return path


def validate_fixture_ops(ops: Sequence[FixtureOp]) -> tuple[FixtureOp, ...]:
    if isinstance(ops, (str, bytes)) or not isinstance(ops, Sequence):
        raise RunnerInputError("fixture_ops_invalid")
    checked: list[FixtureOp] = []
    occupied: set[str] = set()
    for op in ops:
        if not isinstance(op, FixtureOp) or not isinstance(op.kind, FixtureOpKind):
            raise RunnerInputError("fixture_op_invalid")
        relative = _relative_path(op.path).as_posix()
        if relative in occupied:
            raise RunnerInputError("fixture_path_duplicate")
        occupied.add(relative)
        if op.kind is FixtureOpKind.MKDIR:
            if op.payload is not None:
                raise RunnerInputError("fixture_mkdir_payload")
        elif op.kind is FixtureOpKind.WRITE_BYTES:
            if not isinstance(op.payload, bytes):
                raise RunnerInputError("fixture_bytes_payload")
        elif op.kind is FixtureOpKind.SEED_JSON:
            try:
                json.dumps(op.payload, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise RunnerInputError("fixture_json_payload") from exc
        checked.append(FixtureOp(op.kind, relative, op.payload))
    return tuple(checked)


def materialize_fixture(root: Path, ops: Sequence[FixtureOp]) -> None:
    """Apply the closed fixture language below an already-private directory."""
    checked = validate_fixture_ops(ops)
    root = root.resolve(strict=True)
    for op in checked:
        parts = PurePosixPath(op.path).parts
        destination = root.joinpath(*parts)
        cursor = root
        for component in parts[:-1]:
            cursor = cursor / component
            try:
                mode = cursor.lstat().st_mode
            except FileNotFoundError as exc:
                raise RunnerInputError("fixture_parent_missing") from exc
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise RunnerInputError("fixture_parent_invalid")
        # Parents created by a prior mkdir remain required.  This prevents a
        # write operation from silently expanding the fixture language.
        if op.kind is FixtureOpKind.MKDIR:
            destination.mkdir(mode=0o700, parents=False, exist_ok=False)
            continue
        if op.kind is FixtureOpKind.WRITE_BYTES:
            payload = op.payload
            assert isinstance(payload, bytes)
        else:
            payload = (
                json.dumps(
                    op.payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
            ).encode("utf-8")
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                destination.unlink()
            except OSError:
                pass
            raise


def materialize_candidate_files(root: Path, files: Mapping[str, bytes]) -> None:
    if not isinstance(files, Mapping) or not files:
        raise RunnerInputError("candidate_files_invalid")
    folded: set[str] = set()
    checked: list[tuple[PurePosixPath, bytes]] = []
    for name, payload in files.items():
        path = _relative_path(name)
        key = path.as_posix().casefold()
        if key in folded or not isinstance(payload, bytes):
            raise RunnerInputError("candidate_files_invalid")
        folded.add(key); checked.append((path, payload))
    for path, payload in sorted(checked, key=lambda item: (len(item[0].parts), item[0].as_posix())):
        destination = root.joinpath(*path.parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())


def _command(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
        raise RunnerInputError("command_invalid")
    value = tuple(command)
    if not value or any(not isinstance(item, str) or not item or "\x00" in item for item in value):
        raise RunnerInputError("command_invalid")
    return value


def _bwrap_command(bwrap: str, work: Path, command: tuple[str, ...]) -> tuple[str, ...]:
    args = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-uts",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", str(work), "/work",
        "--chdir", "/work",
        "--clearenv",
    ]
    for key, value in SANDBOX_ENV.items():
        args.extend(("--setenv", key, value))
    for host_path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(host_path).exists():
            args.extend(("--ro-bind", host_path, host_path))
    args.append("--")
    args.extend(command)
    return tuple(args)


def _read_setup_handshake(path: Path) -> tuple[bool, int | None]:
    """Read the launcher-owned bwrap status channel, never candidate output."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False, None
    if not isinstance(value, dict) or set(value) != {"child_started", "exit_code"}:
        return False, None
    started = value["child_started"] is True
    exit_code = value["exit_code"]
    if not started:
        return False, None
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        return False, None
    return True, exit_code


def _cgroup_v2_delegate() -> tuple[Path | None, str | None]:
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        return None, "cgroup_v2_unavailable"
    delegate = _CGROUP_DELEGATE
    if not delegate.is_dir():
        return None, "cgroup_delegate_missing"
    required = ("cgroup.procs", "cgroup.events", "memory.max", "pids.max")
    if any(not (delegate / name).exists() for name in required):
        return None, "cgroup_delegate_incomplete"
    if not os.access(delegate, os.W_OK):
        return None, "cgroup_delegate_not_writable"
    return delegate, None


def _write_control(path: Path, value: str) -> None:
    with path.open("w", encoding="ascii") as handle:
        handle.write(value)
        handle.flush()


def _tree_empty(scope: Path) -> bool:
    try:
        events = (scope / "cgroup.events").read_text(encoding="ascii")
        populated = next(
            line.split()[1] for line in events.splitlines()
            if line.startswith("populated ")
        )
        procs = (scope / "cgroup.procs").read_text(encoding="ascii").strip()
        return populated == "0" and not procs
    except (OSError, StopIteration, IndexError):
        return False


def _unavailable(code: str, backend: str, started: float) -> RunnerResult:
    return RunnerResult(
        RunnerStatus.UNAVAILABLE,
        code,
        None,
        "",
        "",
        max(0.0, time.monotonic() - started),
        _empty_attestation(backend),
    )


def run_birth_phase(
    command: Sequence[str],
    *,
    fixture_ops: Sequence[FixtureOp] = (),
    phase: str = "candidate",
    deadline: BirthDeadline | None = None,
    candidate_id: str | None = None,
    windows_registry: WindowsSandboxRegistry | None = None,
    candidate_files: Mapping[str, bytes] | None = None,
) -> RunnerResult:
    """Run one birth-test phase under the complete fixed v1 policy."""
    started = time.monotonic()
    argv = _command(command)
    checked_ops = validate_fixture_ops(fixture_ops)
    birth_deadline = deadline or begin_birth_deadline()
    if not isinstance(birth_deadline, BirthDeadline):
        raise RunnerInputError("deadline_invalid")
    # A supplied deadline may only be the fixed v1 budget.  This prevents a
    # caller from constructing a longer-lived lookalike.
    duration = birth_deadline.expires_at - birth_deadline.started_at
    if duration < 0 or duration > TOTAL_TIMEOUT_S + 1e-6:
        raise RunnerInputError("deadline_invalid")
    budget = birth_deadline.phase_budget()
    if budget <= 0:
        return _unavailable("total_timeout", "deadline", started)
    if phase not in {"candidate", "reference", "equivalence"}:
        raise RunnerInputError("phase_invalid")
    if os.name == "nt":
        if (not isinstance(candidate_id, str) or not isinstance(windows_registry, WindowsSandboxRegistry)
                or not isinstance(candidate_files, Mapping)):
            return _unavailable("windows_sandbox_registry_unavailable", "windows-appcontainer-job-v1", started)
        request_id = "sha256:" + __import__("hashlib").sha256(uuid.uuid4().bytes).hexdigest()
        with tempfile.TemporaryDirectory(prefix="metnos-birth-runner-") as temporary:
            private_root = Path(temporary).resolve()
            try:
                candidate_root = private_root / "candidate"
                work_root = private_root / "work"
                candidate_root.mkdir(mode=0o700)
                work_root.mkdir(mode=0o700)
                materialize_candidate_files(candidate_root, candidate_files)
                materialize_fixture(work_root, checked_ops)
                result = invoke_windows_birth_helper(
                    windows_registry.helper_path,
                    trusted_hashes=frozenset({windows_registry.helper_binary_hash}),
                    config=windows_registry.config_path,
                    expected_config_hash=windows_registry.config_hash,
                    request_id=request_id, candidate_id=candidate_id, phase=phase,
                    private_root=private_root, entrypoint=argv[0], arguments=argv[1:],
                    timeout_s=budget + TERMINATION_DRAIN_S,
                    expected_runtime_hash=windows_registry.runtime_binary_hash,
                )
            except (WindowsBirthHelperError, OSError, RunnerInputError) as exc:
                code = exc.code if isinstance(exc, WindowsBirthHelperError) else (
                    str(exc) if isinstance(exc, RunnerInputError) else "windows_helper_unavailable"
                )
                return _unavailable(code, "windows-appcontainer-job-v1", started)
        available = result.status != RunnerStatus.UNAVAILABLE.value
        attestation = ProcessAttestation(
            backend="windows-appcontainer-job-v1", sandboxed=available,
            network_unshared=available, pid_unshared=False, user_unshared=available,
            ipc_unshared=False, uts_unshared=False, cgroup_v2=False, cgroup_path=None,
            tree_empty=result.attestation["tree_empty"] is True,
            termination_attested=result.attestation["termination_attested"] is True,
        )
        return RunnerResult(RunnerStatus(result.status), result.error_code,
                            result.exit_code, result.stdout.decode("utf-8", "replace"),
                            result.stderr.decode("utf-8", "replace"),
                            result.elapsed_ms / 1000.0, attestation)
    if not sys.platform.startswith("linux"):
        return _unavailable("platform_backend_unavailable", sys.platform, started)
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return _unavailable("bwrap_unavailable", "linux-bwrap-cgroup-v2", started)
    delegate, error = _cgroup_v2_delegate()
    if delegate is None:
        return _unavailable(error or "cgroup_delegate_unavailable", "linux-bwrap-cgroup-v2", started)

    with tempfile.TemporaryDirectory(prefix="metnos-birth-runner-") as temporary:
        work = Path(temporary) / "work"
        handshake = Path(temporary) / "bwrap-status.json"
        work.mkdir(mode=0o700)
        materialize_fixture(work, checked_ops)
        scope = delegate / f"phase-{uuid.uuid4().hex}"
        try:
            scope.mkdir(mode=0o700)
            _write_control(scope / "memory.max", str(MEMORY_LIMIT_BYTES))
            _write_control(scope / "pids.max", str(MAX_PROCESSES))
        except OSError:
            try:
                scope.rmdir()
            except OSError:
                pass
            return _unavailable("cgroup_scope_unavailable", "linux-bwrap-cgroup-v2", started)

        # The launcher joins the delegated cgroup before exec.  It contains no
        # candidate-controlled shell and passes only the already-validated argv.
        launcher = """
import json, os, subprocess, sys
scope, status, *args = sys.argv[1:]
open(scope + '/cgroup.procs', 'w').write(str(os.getpid()))
r, w = os.pipe()
args = [str(w) if item == '{STATUS_FD}' else item for item in args]
p = subprocess.Popen(args, pass_fds=(w,))
os.close(w)
started = False
exit_code = None
with os.fdopen(r) as stream:
    for line in stream:
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict) and isinstance(event.get('child-pid'), int):
            started = True
            with open(status, 'w') as out:
                json.dump({'child_started': True, 'exit_code': None}, out,
                          separators=(',', ':'))
        if isinstance(event, dict) and isinstance(event.get('exit-code'), int):
            exit_code = event['exit-code']
rc = p.wait()
result = {'child_started': started,
          'exit_code': exit_code if exit_code is not None else rc}
temporary = status + '.complete'
with open(temporary, 'x') as out:
    json.dump(result, out, separators=(',', ':'))
os.replace(temporary, status)
sys.exit(0 if started else 125)
"""
        wrapped = _bwrap_command(bwrap, work, argv)
        # The placeholder is replaced in the launcher with a private pipe passed only to
        # bwrap.  Its JSON event is emitted after namespaces and mounts exist.
        wrapped = (wrapped[0], "--json-status-fd", "{STATUS_FD}", *wrapped[1:])
        host_command = (
            sys.executable, "-I", "-c", launcher, str(scope), str(handshake),
            *wrapped,
        )
        returncode: int | None = None
        stdout = ""
        stderr = ""
        error_code: str | None = None
        try:
            execution_budget = birth_deadline.phase_budget()
            if execution_budget <= 0:
                raise subprocess.TimeoutExpired(host_command, 0.0)
            completed = run_bounded_subprocess(
                host_command,
                input_text="",
                timeout_s=execution_budget,
                env={},
                stdout_limit_bytes=STDOUT_LIMIT_BYTES,
                stderr_limit_bytes=STDERR_LIMIT_BYTES,
            )
            setup_ok, candidate_returncode = _read_setup_handshake(handshake)
            returncode = candidate_returncode
            stdout = completed.stdout
            stderr = completed.stderr
            if not setup_ok:
                error_code = "sandbox_setup_unattested"
            elif returncode != 0:
                error_code = "candidate_process_failed"
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.output or "")
            stderr = str(exc.stderr or "")
            error_code = "phase_timeout"
        except SubprocessOutputLimitExceeded as exc:
            stdout, stderr = exc.stdout, exc.stderr
            error_code = f"{exc.stream}_limit_exceeded"
        except SubprocessTerminationError:
            error_code = "process_termination_unattested"
        except (OSError, ValueError):
            error_code = "test_environment_unavailable"

        deadline = time.monotonic() + TERMINATION_DRAIN_S
        empty = _tree_empty(scope)
        if not empty:
            try:
                _write_control(scope / "cgroup.kill", "1")
            except OSError:
                pass
            while not empty and time.monotonic() < deadline:
                time.sleep(0.01)
                empty = _tree_empty(scope)
        try:
            scope.rmdir()
        except OSError:
            empty = False

        setup_attested = _read_setup_handshake(handshake)[0]
        attestation = ProcessAttestation(
            backend="linux-bwrap-cgroup-v2",
            sandboxed=setup_attested,
            network_unshared=setup_attested,
            pid_unshared=setup_attested,
            user_unshared=setup_attested,
            ipc_unshared=setup_attested,
            uts_unshared=setup_attested,
            cgroup_v2=setup_attested,
            cgroup_path=str(scope),
            tree_empty=empty,
            termination_attested=empty,
        )
        if not empty:
            error_code = "process_termination_unattested"
        status = RunnerStatus.PASSED if error_code is None else RunnerStatus.FAILED
        if error_code in {"test_environment_unavailable", "sandbox_setup_unattested"}:
            status = RunnerStatus.UNAVAILABLE
        return RunnerResult(
            status,
            error_code,
            returncode,
            stdout,
            stderr,
            time.monotonic() - started,
            attestation,
        )


__all__ = [
    "BirthDeadline",
    "FixtureOp",
    "FixtureOpKind",
    "ProcessAttestation",
    "RunnerInputError",
    "RunnerPolicy",
    "RunnerResult",
    "RunnerStatus",
    "SANDBOX_ENV",
    "V1_POLICY",
    "begin_birth_deadline",
    "materialize_fixture",
    "materialize_candidate_files",
    "run_birth_phase",
    "validate_fixture_ops",
]

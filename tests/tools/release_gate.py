#!/usr/bin/env python3
"""Internal Metnos release lifecycle verifier.

This tool never updates the running installation.  It materializes a baseline
and a candidate in a temporary directory, then verifies:

    fresh candidate -> baseline -> candidate upgrade -> baseline rollback

Each runtime uses isolated config/data/state/workspace directories and a private
HTTP port.  The machine-readable report is deliberately transport-neutral so
its contracts can later be promoted into a public administrator feature.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

try:  # POSIX advisory lock.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows.
    _fcntl = None

try:  # Windows byte-range lock.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX.
    _msvcrt = None


SCHEMA_VERSION = 1
PROFILE = "portable-isolated"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8770/agent/health"
DEFAULT_TURN_QUERY = "che ore sono?"
CREDENTIAL_PROBE_DOMAIN = "release-gate.invalid"
REQUIRED_RELEASE_PATHS = (
    "requirements.txt",
    "docs",
    "runtime/metnos_http_server.py",
    "runtime/agent_runtime.py",
    "runtime/loader.py",
    "runtime/published_docs.py",
    "runtime/sign.py",
    "scripts/compile_tutor_catalog.py",
    "tutor/sources.toml",
    "install/data/i18n_seed.sqlite",
    "executors",
)
RUNTIME_ENV = {
    "METNOS_ENGINE": "metis",
    "METNOS_PROPOSER_GRAMMAR": "1",
    "METNOS_PROPOSER_VERB_FILTER": "1",
    "METNOS_PROPOSER_FAST_CONFIDENCE": "0.70",
    "METNOS_PREFILTER_RULES": "1",
    "METNOS_INTENT_CLASSIFIER": "1",
    "METNOS_LANG": "it",
}


class GateFailure(RuntimeError):
    """Stable failure carrying a machine-readable code."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclasses.dataclass(frozen=True)
class ReleaseSource:
    spec: str
    ref: str = ""

    @property
    def public_label(self) -> str:
        if self.spec == "current":
            return "current-public-export"
        if self.spec.startswith(("https://", "http://", "git@")):
            host = self.spec.split("//", 1)[-1].split("/", 1)[0]
            return f"git:{host}:{self.ref or 'default'}"
        return f"directory:{Path(self.spec).name}"


@dataclasses.dataclass(frozen=True)
class PersistentLayout:
    root: Path
    home: Path
    config: Path
    data: Path
    state: Path
    workspace: Path

    @classmethod
    def create(cls, root: Path) -> "PersistentLayout":
        layout = cls(
            root=root,
            home=root / "home",
            config=root / "config",
            data=root / "data",
            state=root / "state",
            workspace=root / "workspace",
        )
        for path in dataclasses.astuple(layout)[1:]:
            Path(path).mkdir(parents=True, exist_ok=True)
        return layout


@dataclasses.dataclass
class StepResult:
    name: str
    ok: bool
    duration_s: float
    details: dict[str, Any] = dataclasses.field(default_factory=dict)
    error_code: str = ""
    error: str = ""


@dataclasses.dataclass
class GateReport:
    gate_id: str
    profile: str
    baseline: str
    candidate: str
    started_at: str
    dependency_mode: str = "isolated-venv"
    host: dict[str, str] = dataclasses.field(default_factory=dict)
    finished_at: str = ""
    ok: bool = False
    first_error_code: str = ""
    first_error: str = ""
    steps: list[StepResult] = dataclasses.field(default_factory=list)
    artifacts: dict[str, str] = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        return out


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tail(path: Path, limit: int = 30) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
         timeout_s: float = 600, log_path: Path | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        result = subprocess.run(
            command, cwd=str(cwd), env=merged, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure(
            "command_timeout", f"command timed out after {timeout_s:.0f}s",
            details={"program": command[0]},
        ) from exc
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        raise GateFailure(
            "command_failed", f"command failed with exit {result.returncode}",
            details={
                "program": command[0],
                "exit_code": result.returncode,
                "log": log_path.name if log_path else "",
            },
        )
    return result


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise GateFailure("source_missing", "release source directory does not exist")
    shutil.copytree(
        source, destination, symlinks=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"),
    )


def materialize_source(source: ReleaseSource, destination: Path, *, repo_root: Path,
                       log_dir: Path) -> dict[str, Any]:
    """Materialize a release without exposing local absolute paths in reports."""
    if destination.exists():
        shutil.rmtree(destination)
    if source.spec == "current":
        exporter = repo_root / "scripts" / "export-public.sh"
        if not exporter.is_file():
            raise GateFailure("exporter_missing", "public exporter is unavailable")
        _run(
            ["bash", str(exporter), str(destination)], cwd=repo_root,
            timeout_s=300, log_path=log_dir / "export-candidate.log",
        )
    else:
        local = Path(source.spec).expanduser()
        if local.exists():
            _copy_tree(local.resolve(), destination)
        elif source.spec.startswith(("https://", "http://", "git@")):
            command = ["git", "clone", "--depth", "1"]
            if source.ref:
                command += ["--branch", source.ref]
            command += [source.spec, str(destination)]
            _run(
                command, cwd=repo_root, timeout_s=300,
                log_path=log_dir / "clone-baseline.log",
            )
            shutil.rmtree(destination / ".git", ignore_errors=True)
        else:
            raise GateFailure("source_unsupported", "release source is not a directory or git URL")
    identity = validate_release_tree(destination)
    return {"label": source.public_label, **identity}


def validate_release_tree(root: Path) -> dict[str, Any]:
    """Validate package structure and reject links escaping the release root."""
    root = root.resolve()
    missing = [relative for relative in REQUIRED_RELEASE_PATHS if not (root / relative).exists()]
    if missing:
        raise GateFailure(
            "release_incomplete", "release tree is missing required paths",
            details={"missing": missing},
        )
    file_count = 0
    total_bytes = 0
    identity = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise GateFailure(
                    "unsafe_symlink", "release contains a symlink escaping its root",
                    details={"path": relative},
                ) from exc
            identity.update(f"L\0{relative}\0{target}\n".encode())
            file_count += 1
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            raise GateFailure(
                "unsupported_file_type", "release contains a special file",
                details={"path": relative},
            )
        digest = _sha256_file(path)
        size = path.stat().st_size
        identity.update(f"F\0{relative}\0{size}\0{digest}\n".encode())
        file_count += 1
        total_bytes += size
    return {
        "tree_sha256": identity.hexdigest(),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _requirements_digest(tree: Path) -> str:
    return _sha256_file(tree / "requirements.txt")


def _venv_python(venv: Path) -> Path:
    """Return the interpreter path using the host virtualenv convention."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _venv_root(python: Path) -> Path:
    parent = python.parent
    if parent.name.casefold() in {"bin", "scripts"}:
        return parent.parent
    return Path(sys.prefix)


def ensure_venv(tree: Path, venv_root: Path, *, skip_dependencies: bool,
                log_dir: Path) -> Path:
    if skip_dependencies:
        return Path(sys.executable)
    digest = _requirements_digest(tree)[:16]
    venv = venv_root / digest
    python = _venv_python(venv)
    if python.is_file():
        return python
    venv.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [sys.executable, "-m", "venv", str(venv)], cwd=tree,
        timeout_s=120, log_path=log_dir / f"venv-{digest}.log",
    )
    last_error: GateFailure | None = None
    for attempt in range(1, 5):
        try:
            _run(
                [str(python), "-m", "pip", "install", "--no-cache-dir", "--timeout", "90",
                 "--retries", "5", "-r", str(tree / "requirements.txt")],
                cwd=tree, timeout_s=1800,
                log_path=log_dir / f"pip-{digest}-attempt-{attempt}.log",
            )
            return python
        except GateFailure as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(4)
    raise GateFailure(
        "dependencies_failed", "dependency installation failed after four attempts",
        details=(last_error.details if last_error else {}),
    )


def _runtime_env(tree: Path, layout: PersistentLayout, python: Path,
                 *, port: int | None = None) -> dict[str, str]:
    env = {
        "HOME": str(layout.home),
        "PYTHONPATH": os.pathsep.join((str(tree), str(tree / "runtime"))),
        "PATH": os.pathsep.join((str(python.parent), os.defpath)),
        "METNOS_INSTALL_ROOT": str(tree),
        "METNOS_USER_CONFIG": str(layout.config),
        "METNOS_USER_DATA": str(layout.data),
        "METNOS_USER_STATE": str(layout.state),
        "METNOS_VENV": str(_venv_root(python)),
        "METNOS_WORKSPACE": str(layout.workspace),
        **RUNTIME_ENV,
    }
    if port is not None:
        env["METNOS_HTTP_PORT"] = str(port)
    return env


def _seed_runtime(tree: Path, layout: PersistentLayout) -> None:
    seed = tree / "install" / "data" / "i18n_seed.sqlite"
    target = layout.data / "i18n.sqlite"
    if not target.exists():
        shutil.copy2(seed, target)


def _active_manifest_names(tree: Path) -> set[str]:
    names: set[str] = set()
    os_name = {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(
        platform.system().lower(), platform.system().lower(),
    )
    for manifest_path in sorted((tree / "executors").glob("*/manifest.toml")):
        try:
            with manifest_path.open("rb") as handle:
                manifest = tomllib.load(handle)
        except Exception as exc:
            raise GateFailure(
                "manifest_parse_failed", "executor manifest cannot be parsed",
                details={"executor": manifest_path.parent.name},
            ) from exc
        if str(manifest.get("lifecycle", "active")) != "active":
            continue
        platforms = manifest.get("platforms") or ["linux"]
        if os_name not in platforms:
            continue
        name = manifest.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


def prepare_runtime(tree: Path, layout: PersistentLayout, python: Path,
                    *, log_dir: Path, label: str) -> dict[str, Any]:
    _seed_runtime(tree, layout)
    env = _runtime_env(tree, layout, python)
    _run(
        [str(python), str(tree / "runtime" / "sign.py"), "sign-all"],
        cwd=tree, env=env, timeout_s=300,
        log_path=log_dir / f"{label}-sign-all.log",
    )
    script = (
        "import json,loader; c=loader.load_catalog(verify=True); "
        "print('METNOS_CATALOG_JSON='+json.dumps({"
        "'names':sorted(c.executors),'rejected':c.rejected},separators=(',',':')))"
    )
    result = _run(
        [str(python), "-c", script], cwd=tree, env=env, timeout_s=120,
        log_path=log_dir / f"{label}-catalog.log",
    )
    marker = "METNOS_CATALOG_JSON="
    lines = [line for line in (result.stdout or "").splitlines() if line.startswith(marker)]
    if not lines:
        raise GateFailure("catalog_probe_invalid", "catalog probe returned no structured result")
    payload = json.loads(lines[-1][len(marker):])
    expected = _active_manifest_names(tree)
    loaded = set(payload.get("names") or [])
    missing = sorted(expected - loaded)
    if missing:
        raise GateFailure(
            "catalog_incomplete", "active platform-compatible executors are missing",
            details={
                "expected": len(expected), "loaded": len(loaded),
                "missing": missing[:20], "rejected_count": len(payload.get("rejected") or []),
            },
        )
    return {
        "expected": len(expected),
        "loaded": len(loaded),
        "rejected_count": len(payload.get("rejected") or []),
    }


def _free_port() -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
    except PermissionError as exc:
        raise GateFailure(
            "network_sandbox_blocked",
            "the execution environment forbids opening an isolated HTTP port",
        ) from exc


def _json_request(url: str, *, body: dict | None = None,
                  timeout_s: float = 10) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw[:300]}
        return int(exc.code), payload


def probe_health(url: str, *, timeout_s: float = 3) -> dict[str, Any]:
    try:
        status, payload = _json_request(url, timeout_s=timeout_s)
    except Exception as exc:
        return {"reachable": False, "ok": False, "error_type": type(exc).__name__}
    return {
        "reachable": True,
        "ok": status == 200 and payload.get("ok") is True,
        "status": status,
    }


def _wait_health(url: str, *, process: subprocess.Popen, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"reachable": False, "ok": False}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise GateFailure(
                "server_exited", "isolated server exited before becoming healthy",
                details={"exit_code": process.returncode},
            )
        last = probe_health(url, timeout_s=2)
        if last.get("ok"):
            return last
        time.sleep(0.5)
    raise GateFailure(
        "health_timeout", "isolated server did not become healthy in time",
        details=last,
    )


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def verify_runtime(tree: Path, layout: PersistentLayout, python: Path,
                   *, label: str, query: str, log_dir: Path,
                   ready_timeout_s: float, turn_timeout_s: float) -> dict[str, Any]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(_runtime_env(tree, layout, python, port=port))
    log_path = log_dir / f"{label}-server.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(python), "-m", "runtime.metnos_http_server", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=str(tree), env=env, stdout=log_handle, stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        health = _wait_health(
            f"{url}/agent/health", process=process, timeout_s=ready_timeout_s,
        )
        try:
            status, turn = _json_request(
                f"{url}/agent/turn",
                body={"query": query, "user_id": "host"},
                timeout_s=turn_timeout_s,
            )
        except Exception as exc:
            raise GateFailure(
                "turn_transport_failed", "real turn request failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        final_kind = str(turn.get("final_kind") or "")
        final_text = str(turn.get("final_message") or turn.get("final_text") or "")
        if status != 200 or final_kind != "answer" or not final_text.strip():
            raise GateFailure(
                "turn_not_answer", "real turn did not produce a non-empty answer",
                details={
                    "status": status, "final_kind": final_kind,
                    "text_length": len(final_text), "turn_id": str(turn.get("turn_id") or ""),
                },
            )
        return {
            "health": health,
            "turn_status": status,
            "final_kind": final_kind,
            "text_length": len(final_text),
            "turn_id": str(turn.get("turn_id") or ""),
        }
    except GateFailure as exc:
        exc.details.setdefault("server_log_tail", _tail(log_path, 12))
        raise
    finally:
        _stop_process(process)
        log_handle.close()


def write_preservation_probes(layout: PersistentLayout) -> dict[str, str]:
    """Write opaque probes; only their hashes are returned to the report."""
    probes: dict[str, str] = {}
    for name in ("config", "data", "state", "workspace"):
        directory = Path(getattr(layout, name)) / "release_gate"
        directory.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        path = directory / "preservation.probe"
        path.write_text(value, encoding="ascii")
        probes[name] = _sha256_text(value)
    return probes


def verify_preservation_probes(layout: PersistentLayout,
                               expected: dict[str, str]) -> dict[str, str]:
    actual: dict[str, str] = {}
    for name, digest in expected.items():
        path = Path(getattr(layout, name)) / "release_gate" / "preservation.probe"
        if not path.is_file():
            raise GateFailure(
                "persistent_probe_missing", "persistent data probe disappeared",
                details={"domain": name},
            )
        actual[name] = _sha256_text(path.read_text(encoding="ascii"))
        if actual[name] != digest:
            raise GateFailure(
                "persistent_probe_changed", "persistent data probe changed",
                details={"domain": name},
            )
    return actual


def write_credential_probe(tree: Path, layout: PersistentLayout, python: Path,
                           *, log_dir: Path) -> dict[str, str]:
    """Create a synthetic encrypted credential through the release API."""
    script = (
        "import json,secrets,credentials; "
        f"d={CREDENTIAL_PROBE_DOMAIN!r}; "
        "p={'login_url':'https://release-gate.invalid/login',"
        "'username':'release-gate','password':secrets.token_urlsafe(32)}; "
        "credentials.store(d,p); "
        "print('METNOS_CREDENTIAL_JSON='+json.dumps({"
        "'domain':d,'fingerprint':credentials.fingerprint(d)},separators=(',',':')))"
    )
    result = _run(
        [str(python), "-c", script], cwd=tree,
        env=_runtime_env(tree, layout, python), timeout_s=30,
        log_path=log_dir / "credential-probe-create.log",
    )
    return _credential_probe_payload(result.stdout or "")


def verify_credential_probe(tree: Path, layout: PersistentLayout, python: Path,
                            expected: dict[str, str], *, label: str,
                            log_dir: Path) -> dict[str, str]:
    """Decrypt the synthetic credential using the selected release."""
    script = (
        "import json,os,credentials; "
        f"d={CREDENTIAL_PROBE_DOMAIN!r}; p=credentials.load(d); "
        "assert isinstance(p,dict) and p.get('username')=='release-gate'; "
        "f=credentials.fingerprint(d); "
        "assert f==os.environ['METNOS_GATE_CREDENTIAL_FINGERPRINT']; "
        "print('METNOS_CREDENTIAL_JSON='+json.dumps({"
        "'domain':d,'fingerprint':f},separators=(',',':')))"
    )
    env = _runtime_env(tree, layout, python)
    env["METNOS_GATE_CREDENTIAL_FINGERPRINT"] = expected["fingerprint"]
    result = _run(
        [str(python), "-c", script], cwd=tree, env=env, timeout_s=30,
        log_path=log_dir / f"credential-probe-{label}.log",
    )
    actual = _credential_probe_payload(result.stdout or "")
    if actual != expected:
        raise GateFailure(
            "credential_probe_changed", "synthetic credential metadata changed",
        )
    return actual


def _credential_probe_payload(output: str) -> dict[str, str]:
    marker = "METNOS_CREDENTIAL_JSON="
    lines = [line for line in output.splitlines() if line.startswith(marker)]
    if not lines:
        raise GateFailure(
            "credential_probe_invalid", "credential probe returned no structured result",
        )
    payload = json.loads(lines[-1][len(marker):])
    domain = payload.get("domain")
    fingerprint = payload.get("fingerprint")
    if domain != CREDENTIAL_PROBE_DOMAIN or not isinstance(fingerprint, str):
        raise GateFailure(
            "credential_probe_invalid", "credential probe returned invalid metadata",
        )
    return {"domain": domain, "fingerprint": fingerprint}


def _worktree_snapshot(repo_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"], cwd=str(repo_root),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        return {"available": False}
    entries = [item for item in result.stdout.split(b"\0") if item]
    return {
        "available": True,
        "digest": hashlib.sha256(result.stdout).hexdigest(),
        "entry_count": len(entries),
    }


def _production_snapshot(repo_root: Path, health_url: str) -> dict[str, Any]:
    return {
        "health": probe_health(health_url, timeout_s=3),
        "worktree": _worktree_snapshot(repo_root),
    }


def _production_unchanged(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_health = before.get("health") or {}
    after_health = after.get("health") or {}
    if before_health.get("ok") and not after_health.get("ok"):
        raise GateFailure(
            "production_health_regressed", "production was healthy before the gate and is not healthy after",
            details={"before": before_health, "after": after_health},
        )
    before_tree = before.get("worktree") or {}
    after_tree = after.get("worktree") or {}
    if before_tree.get("available") and before_tree.get("digest") != after_tree.get("digest"):
        raise GateFailure(
            "production_worktree_changed", "production worktree changed during the gate",
            details={
                "before_entries": before_tree.get("entry_count"),
                "after_entries": after_tree.get("entry_count"),
            },
        )
    return {"health_preserved": True, "worktree_preserved": True}


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _write_markdown(path: Path, report: GateReport) -> None:
    lines = [
        "# Metnos release lifecycle verification",
        "",
        f"- Gate: `{report.gate_id}`",
        f"- Profile: `{report.profile}`",
        f"- Result: `{'PASS' if report.ok else 'FAIL'}`",
        f"- Baseline: `{report.baseline}`",
        f"- Candidate: `{report.candidate}`",
        f"- Started: `{report.started_at}`",
        f"- Finished: `{report.finished_at}`",
        "",
        "| Step | Result | Seconds | Error |",
        "|---|---:|---:|---|",
    ]
    for step in report.steps:
        error = step.error_code or ""
        lines.append(
            f"| {step.name} | {'PASS' if step.ok else 'FAIL'} | {step.duration_s:.3f} | {error} |"
        )
    if report.first_error_code:
        lines += ["", f"First failure: `{report.first_error_code}` - {report.first_error}"]
    _write_text_atomic(path, "\n".join(lines) + "\n")


def _remove_work_root(work_root: Path) -> dict[str, bool]:
    try:
        shutil.rmtree(work_root)
    except OSError as exc:
        raise GateFailure(
            "cleanup_failed", "isolated work directory could not be removed",
            details={"error_type": type(exc).__name__},
        ) from exc
    if work_root.exists():
        raise GateFailure(
            "cleanup_failed", "isolated work directory still exists after cleanup",
        )
    return {"removed": True}


class ReleaseGate:
    def __init__(self, *, repo_root: Path, baseline: ReleaseSource,
                 candidate: ReleaseSource, work_root: Path, report_path: Path,
                 keep: bool, skip_dependencies: bool, health_url: str,
                 query: str, ready_timeout_s: float, turn_timeout_s: float):
        self.repo_root = repo_root
        self.baseline = baseline
        self.candidate = candidate
        self.work_root = work_root
        self.report_path = report_path
        self.keep = keep
        self.skip_dependencies = skip_dependencies
        self.health_url = health_url
        self.query = query
        self.ready_timeout_s = ready_timeout_s
        self.turn_timeout_s = turn_timeout_s
        self.logs = work_root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.report = GateReport(
            gate_id=work_root.name,
            profile=PROFILE,
            baseline=baseline.public_label,
            candidate=candidate.public_label,
            started_at=_utc_now(),
            dependency_mode=("current-python" if skip_dependencies else "isolated-venv"),
            host={
                "system": platform.system().lower(),
                "release": platform.release(),
                "machine": platform.machine().lower(),
                "python": platform.python_version(),
            },
        )
        self._failed = False

    def step(self, name: str, function: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            details = function() or {}
        except GateFailure as exc:
            result = StepResult(
                name=name, ok=False, duration_s=time.monotonic() - started,
                details=exc.details, error_code=exc.code, error=str(exc),
            )
            self.report.steps.append(result)
            if not self.report.first_error_code:
                self.report.first_error_code = exc.code
                self.report.first_error = str(exc)
            self._failed = True
            raise
        except Exception as exc:
            result = StepResult(
                name=name, ok=False, duration_s=time.monotonic() - started,
                error_code="unexpected_error", error=f"{type(exc).__name__}: {exc}",
            )
            self.report.steps.append(result)
            if not self.report.first_error_code:
                self.report.first_error_code = result.error_code
                self.report.first_error = result.error
            self._failed = True
            raise GateFailure(result.error_code, result.error) from exc
        self.report.steps.append(StepResult(
            name=name, ok=True, duration_s=time.monotonic() - started, details=details,
        ))
        return details

    def run(self) -> GateReport:
        production_before: dict[str, Any] = {}
        production_after: dict[str, Any] = {}
        caught: Exception | None = None
        try:
            production_before = self.step(
                "production_before",
                lambda: _production_snapshot(self.repo_root, self.health_url),
            )
            baseline_tree = self.work_root / "releases" / "baseline"
            candidate_tree = self.work_root / "releases" / "candidate"
            self.step(
                "materialize_baseline",
                lambda: materialize_source(
                    self.baseline, baseline_tree, repo_root=self.repo_root, log_dir=self.logs,
                ),
            )
            self.step(
                "materialize_candidate",
                lambda: materialize_source(
                    self.candidate, candidate_tree, repo_root=self.repo_root, log_dir=self.logs,
                ),
            )
            venv_root = self.work_root / "venvs"
            baseline_python = Path(self.step(
                "dependencies_baseline",
                lambda: {"python": str(ensure_venv(
                    baseline_tree, venv_root,
                    skip_dependencies=self.skip_dependencies, log_dir=self.logs,
                ))},
            )["python"])
            candidate_python = Path(self.step(
                "dependencies_candidate",
                lambda: {"python": str(ensure_venv(
                    candidate_tree, venv_root,
                    skip_dependencies=self.skip_dependencies, log_dir=self.logs,
                ))},
            )["python"])

            fresh = PersistentLayout.create(self.work_root / "persistent" / "fresh")
            self.step(
                "fresh_prepare",
                lambda: prepare_runtime(
                    candidate_tree, fresh, candidate_python,
                    log_dir=self.logs, label="fresh-candidate",
                ),
            )
            self.step(
                "fresh_runtime",
                lambda: verify_runtime(
                    candidate_tree, fresh, candidate_python,
                    label="fresh-candidate", query=self.query, log_dir=self.logs,
                    ready_timeout_s=self.ready_timeout_s,
                    turn_timeout_s=self.turn_timeout_s,
                ),
            )

            transition = PersistentLayout.create(self.work_root / "persistent" / "transition")
            self.step(
                "baseline_prepare",
                lambda: prepare_runtime(
                    baseline_tree, transition, baseline_python,
                    log_dir=self.logs, label="transition-baseline",
                ),
            )
            self.step(
                "baseline_runtime",
                lambda: verify_runtime(
                    baseline_tree, transition, baseline_python,
                    label="transition-baseline", query=self.query, log_dir=self.logs,
                    ready_timeout_s=self.ready_timeout_s,
                    turn_timeout_s=self.turn_timeout_s,
                ),
            )
            probes = self.step(
                "persistent_probes_create",
                lambda: write_preservation_probes(transition),
            )
            credential_probe = self.step(
                "credential_probe_create",
                lambda: write_credential_probe(
                    baseline_tree, transition, baseline_python, log_dir=self.logs,
                ),
            )
            self.step(
                "candidate_prepare_on_baseline_data",
                lambda: prepare_runtime(
                    candidate_tree, transition, candidate_python,
                    log_dir=self.logs, label="transition-candidate",
                ),
            )
            self.step(
                "candidate_preserves_data",
                lambda: verify_preservation_probes(transition, probes),
            )
            self.step(
                "candidate_reads_credential",
                lambda: verify_credential_probe(
                    candidate_tree, transition, candidate_python, credential_probe,
                    label="candidate", log_dir=self.logs,
                ),
            )
            self.step(
                "candidate_runtime_on_baseline_data",
                lambda: verify_runtime(
                    candidate_tree, transition, candidate_python,
                    label="transition-candidate", query=self.query, log_dir=self.logs,
                    ready_timeout_s=self.ready_timeout_s,
                    turn_timeout_s=self.turn_timeout_s,
                ),
            )
            self.step(
                "rollback_preserves_data",
                lambda: verify_preservation_probes(transition, probes),
            )
            self.step(
                "rollback_reads_credential",
                lambda: verify_credential_probe(
                    baseline_tree, transition, baseline_python, credential_probe,
                    label="rollback", log_dir=self.logs,
                ),
            )
            self.step(
                "rollback_runtime",
                lambda: verify_runtime(
                    baseline_tree, transition, baseline_python,
                    label="rollback-baseline", query=self.query, log_dir=self.logs,
                    ready_timeout_s=self.ready_timeout_s,
                    turn_timeout_s=self.turn_timeout_s,
                ),
            )
            self.step(
                "rollback_final_data",
                lambda: verify_preservation_probes(transition, probes),
            )
        except Exception as exc:
            caught = exc
        finally:
            try:
                production_after = self.step(
                    "production_after",
                    lambda: _production_snapshot(self.repo_root, self.health_url),
                )
                if production_before:
                    self.step(
                        "production_unchanged",
                        lambda: _production_unchanged(production_before, production_after),
                    )
            except Exception as exc:
                if caught is None:
                    caught = exc
            if not self.keep and caught is None:
                try:
                    self.step("cleanup", lambda: _remove_work_root(self.work_root))
                except Exception as exc:
                    caught = exc
            self.report.finished_at = _utc_now()
            self.report.ok = caught is None and not self._failed
            self.report.artifacts = {
                "json": self.report_path.name,
                "markdown": self.report_path.with_suffix(".md").name,
            }
            if self.keep or caught is not None:
                self.report.artifacts["retained_work_root"] = str(self.work_root)
            _write_json_atomic(self.report_path, self.report.as_dict())
            _write_markdown(self.report_path.with_suffix(".md"), self.report)
        return self.report


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            elif _msvcrt is not None:
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
            else:  # Defensive: every supported host has one implementation.
                raise GateFailure(
                    "lock_unsupported", "the host provides no supported file-lock primitive",
                )
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise GateFailure("gate_already_running", "another release gate is already running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            elif _msvcrt is not None:
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify fresh install, upgrade compatibility, and rollback in isolation.",
    )
    parser.add_argument("--baseline", required=True,
                        help="previous public checkout directory or git URL")
    parser.add_argument("--baseline-ref", default="",
                        help="branch/tag used only when --baseline is a git URL")
    parser.add_argument("--candidate", default="current",
                        help="candidate directory/git URL, or 'current' for public export")
    parser.add_argument("--candidate-ref", default="")
    parser.add_argument("--work-root", type=Path,
                        help="explicit isolated working directory")
    parser.add_argument("--report", type=Path,
                        help="JSON report path (default: host temporary directory/<gate-id>.json)")
    parser.add_argument("--keep", action="store_true",
                        help="keep isolated trees and logs after the run")
    parser.add_argument("--skip-dependencies", action="store_true",
                        help="developer-only shortcut: use current Python environment")
    parser.add_argument("--production-health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--turn-query", default=DEFAULT_TURN_QUERY)
    parser.add_argument("--ready-timeout", type=float, default=45.0)
    parser.add_argument("--turn-timeout", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = _repo_root()
    if args.work_root:
        work_root = args.work_root.resolve()
        work_root.mkdir(parents=True, exist_ok=False)
    else:
        work_root = Path(tempfile.mkdtemp(prefix="metnos-release-gate-"))
    temp_root = Path(tempfile.gettempdir()).resolve()
    report_path = (args.report or temp_root / f"{work_root.name}.json").resolve()
    try:
        report_path.relative_to(work_root)
    except ValueError:
        pass
    else:
        print("release_gate: report path must be outside the isolated work root", file=sys.stderr)
        return 2
    gate = ReleaseGate(
        repo_root=repo_root,
        baseline=ReleaseSource(args.baseline, args.baseline_ref),
        candidate=ReleaseSource(args.candidate, args.candidate_ref),
        work_root=work_root,
        report_path=report_path,
        keep=args.keep,
        skip_dependencies=args.skip_dependencies,
        health_url=args.production_health_url,
        query=args.turn_query,
        ready_timeout_s=args.ready_timeout,
        turn_timeout_s=args.turn_timeout,
    )
    try:
        with _exclusive_lock(temp_root / "metnos-release-gate.lock"):
            report = gate.run()
    except GateFailure as exc:
        print(f"release_gate: FAIL [{exc.code}] {exc}", file=sys.stderr)
        return 2
    print(f"release_gate: {'PASS' if report.ok else 'FAIL'}")
    print(f"report: {report_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

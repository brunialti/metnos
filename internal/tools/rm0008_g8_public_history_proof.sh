#!/bin/bash
# Candidate-reader proof against live public artifacts, never F5 activation.
# The administrative runner captures diagnostics privately. Only bounded
# public identities, counts and error codes leave this process.
set -euo pipefail
test "${2:-}" = public-history
/opt/metnos/.venv/bin/python -I - "$1" <<'PY'
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import time

# This internal proof pins the assigned worktree, not a product lookup path.
source = Path("/opt/metnos/.claude/worktrees/rm0009-development")
modules = (
    "executor_birth_context_v1.py", "executor_birth_prepared_set.py",
    "executor_birth_prepared_root.py",
)

def source_hashes():
    return {
        name: hashlib.sha256((source / "runtime" / name).read_bytes()).hexdigest()
        for name in modules
    }

def main_pid():
    return int(subprocess.check_output(
        ["/usr/bin/systemctl", "show", "metnos-http.service", "-p", "MainPID", "--value"],
        timeout=5, text=True,
    ).strip())

pid = main_pid()
account = pwd.getpwnam("metnos")
if pid <= 1 or Path(f"/proc/{pid}").stat().st_uid != account.pw_uid:
    raise RuntimeError("http_process_owner_unavailable")
working_directory = Path(os.readlink(f"/proc/{pid}/cwd"))
with open(f"/proc/{pid}/cmdline", "rb") as stream:
    command = stream.read(16385).split(b"\0")
if (sum(map(len, command)) > 16384 or b"-m" not in command
        or command[command.index(b"-m") + 1] != b"runtime.metnos_http_server"):
    raise RuntimeError("http_python_module_selection_invalid")
with open(f"/proc/{pid}/environ", "rb") as stream:
    raw = stream.read(65537)
if len(raw) > 65536:
    raise RuntimeError("environment_size_limit")
allowed = {
    b"HOME", b"METNOS_USER_DATA", b"METNOS_USER_STATE", b"METNOS_USER_CONFIG",
    b"METNOS_INSTALL_ROOT", b"METNOS_HOME", b"METNOS_WORKSPACE", b"METNOS_LANG",
}
selected = {}
for entry in raw.split(b"\0"):
    key, separator, value = entry.partition(b"=")
    if separator and key in allowed:
        selected[key.decode()] = value.decode()
del raw
service_home = Path(selected.get("HOME") or account.pw_dir)
for variable, suffix in (
    ("METNOS_USER_DATA", ".local/share/metnos"),
    ("METNOS_USER_STATE", ".local/state/metnos"),
    ("METNOS_USER_CONFIG", ".config/metnos"),
):
    location = Path(selected.get(variable) or service_home / suffix)
    if not location.is_absolute() or not location.is_dir():
        raise RuntimeError("selected_root_unavailable")
    os.environ[variable] = str(location)
for variable in ("METNOS_INSTALL_ROOT", "METNOS_HOME", "METNOS_WORKSPACE", "METNOS_LANG"):
    if selected.get(variable):
        os.environ[variable] = selected[variable]
if main_pid() != pid:
    raise RuntimeError("http_process_changed")
candidate_payloads = {
    name: (source / "runtime" / name).read_bytes() for name in modules
}
before = {name: hashlib.sha256(raw).hexdigest() for name, raw in candidate_payloads.items()}
output = os.open(str(Path(sys.argv[1]) / "result.json"),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
os.setgroups([])
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
os.chdir(source)
sys.dont_write_bytecode = True
denied = {"private_read": 0, "metadata_repair": 0, "write_or_execution": 0}
denied_events = []

def read_only_audit(event, args):
    """A regression guard, not an authorization boundary for untrusted code."""
    if event == "open":
        path, _mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            denied["write_or_execution"] += 1
            denied_events.append({"event": event, "name": Path(os.fsdecode(path)).name})
            raise PermissionError("proof_write_denied")
        if isinstance(path, (str, bytes)):
            text = os.fsdecode(path)
            name = Path(text).name
            if ("private" in Path(text).parts or name == "keystore.json"
                    or name.endswith(".key") or "-private-" in name):
                denied["private_read"] += 1
                raise PermissionError("proof_private_read_denied")
    elif event == "os.mkdir":
        # config.ensure_dirs may confirm existing directories at import.
        # A new directory is never allowed, even inside selected user roots.
        if args[2] == -1 and Path(args[0]).is_dir():
            return
        denied["write_or_execution"] += 1
        denied_events.append({"event": event, "name": Path(args[0]).name})
        raise PermissionError("proof_directory_creation_denied")
    elif event == "os.chmod":
        # Suppress config's optional import-time permission repair as well.
        denied["metadata_repair"] += 1
        raise PermissionError("proof_metadata_repair_denied")
    elif event in {
        "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.chown",
        "os.truncate", "os.utime", "subprocess.Popen", "os.exec", "os.system",
        "socket.connect", "socket.bind",
    }:
        denied["write_or_execution"] += 1
        denied_events.append({"event": event})
        raise PermissionError("proof_mutation_or_execution_denied")

sys.addaudithook(read_only_audit)
started = time.monotonic()
result = {
    "scope": "candidate_public_readers_with_installed_dependencies_not_deployment_or_f5",
    "http_pid": pid, "reader_uid": os.geteuid(), "reader_gid": os.getegid(),
    "source_hashes": before, "contexts": [],
    "configured_install_root": selected.get("METNOS_INSTALL_ROOT"),
    "configured_legacy_root": selected.get("METNOS_HOME"),
    "http_working_directory": str(working_directory),
}

def deadline(_signum, _frame):
    raise TimeoutError("public_history_proof_deadline")

signal.signal(signal.SIGALRM, deadline)
signal.alarm(180)
try:
    # The native launcher deliberately strips root overrides. Its signed
    # Python target selects the installation through its working directory.
    dependency_root = working_directory
    override = selected.get("METNOS_INSTALL_ROOT") or selected.get("METNOS_HOME")
    if override and Path(override) != dependency_root:
        raise RuntimeError("http_root_selection_ambiguous")
    release_parent = Path("/var/lib/metnos/executor-birth/releases-v1")
    if (not dependency_root.is_absolute() or dependency_root.parent != release_parent
            or len(dependency_root.name) != 20 or not dependency_root.name.isdecimal()):
        raise RuntimeError("installed_dependency_root_not_explicit")
    for path in (dependency_root, *dependency_root.parents):
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or info.st_mode & 0o022):
            raise RuntimeError("installed_dependency_root_untrusted")
    sys.path[:0] = [str(dependency_root / "runtime"), str(dependency_root)]
    result["installed_dependency_root"] = str(dependency_root)
    # Keep the installed, signed policy unchanged. Only the three candidate
    # readers are overlaid in this process; no on-disk file is replaced.
    for filename in modules:
        name = filename.removesuffix(".py")
        spec = importlib.util.spec_from_file_location(name, source / "runtime" / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(candidate_payloads[filename], str(source / "runtime" / filename),
                     "exec", dont_inherit=True), module.__dict__)
    from executor_birth_ownership_chain import (
        VerifiedOwnershipChain, inspect_ownership_chain_state_v1,
    )
    from executor_birth_prepared_root import load_historical_context_verifiers_v1

    import contract_boundary_guard

    result["installed_policy_source_review"] = (
        contract_boundary_guard.BIRTH_CLOSED_SOURCE_REVIEW_SHA256
    )

    chain = inspect_ownership_chain_state_v1()
    if type(chain) is not VerifiedOwnershipChain or not chain.context_transitions:
        raise RuntimeError("required_context_transition_missing")
    if (chain.required_distribution is None
            or Path(chain.required_distribution.installation_root) != dependency_root):
        raise RuntimeError("http_release_differs_from_required_distribution")
    result.update(
        required_head_id=chain.required_head.head_id,
        release_sequence=chain.required_head.release_sequence,
        authenticated_transition_count=len(chain.context_transitions),
    )
    selectors = dict.fromkeys(
        transition.prepared_admission_context_id
        for transition in (chain.context_transitions[0], chain.context_transitions[-1])
    )
    for selector in selectors:
        evidence = load_historical_context_verifiers_v1(selector)
        if evidence.required_head_id != chain.required_head.head_id:
            raise RuntimeError("proof_frontier_changed")
        public = evidence.public_set
        result["contexts"].append({
            "context_id": selector, "set_id": public.set_id,
            "context_epoch": public.material.pin.context_epoch,
            "set_json_sha256": public.set_json_sha256,
            "context_material_sha256": public.material.material_sha256,
            "author_verifiers": len(public.author_verifier_keys),
            "admission_verifiers": len(public.admission_verifier_keys),
            "producer_namespaces": len(public.producers),
        })
    if source_hashes() != before:
        raise RuntimeError("candidate_source_changed")
    critical = (
        "contract_boundary_guard", "executor_birth_distribution_manifest",
        "executor_birth_ownership_chain", "executor_birth_ownership_authorities",
        "executor_birth_secure_fs",
    )
    origins = {name: sys.modules[name].__file__ for name in critical}
    if any(Path(path).parent != dependency_root / "runtime" for path in origins.values()):
        raise RuntimeError("undeclared_dependency_origin")
    candidate_names = {name.removesuffix(".py") for name in modules}
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).is_relative_to(source) and name not in candidate_names:
            raise RuntimeError("undeclared_candidate_import")
    result["critical_module_origins"] = origins
    result["candidate_module_origins"] = {
        name: sys.modules[name].__file__ for name in sorted(candidate_names)
    }
    if denied["private_read"] or denied["write_or_execution"]:
        raise RuntimeError("proof_forbidden_access_attempted")
    result["status"] = "verified_selected_public_contexts"
except Exception as exc:
    errors = []
    while exc is not None and len(errors) < 8:
        item = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
        if type(exc) is RuntimeError:
            item["detail"] = str(exc)[:128]
        if type(exc).__name__ in {"OwnershipChainError", "DistributionManifestError"}:
            item["detail"] = str(getattr(exc, "detail", ""))[:256]
        errors.append(item)
        exc = exc.__cause__ or getattr(exc, "_internal_cause", None)
    result.update(status="not_verified", errors=errors)
finally:
    signal.alarm(0)
    result.update(elapsed_seconds=round(time.monotonic() - started, 3),
                  denied=denied, denied_events=denied_events[:8])
    payload = json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
    if len(payload) > 8192:
        raise RuntimeError("output_size_limit")
    os.write(output, payload)
    os.close(output)
raise SystemExit(0 if result["status"] == "verified_selected_public_contexts" else 1)
PY

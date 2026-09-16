#!/bin/bash
# Candidate-reader proof against live public artifacts, never F5 activation.
# The administrative runner captures diagnostics privately. Only bounded
# public identities, counts and error codes leave this process.
set -euo pipefail
case "${2:-}" in public-history|initial-public-history|initial-policy-binding|producer-policy|producer-declarations|producer-binding|publication-binding|producer-history|contract-history|contract-history-v1|contract-inventory|contract-residual|historical-inventory) ;; *) exit 64 ;; esac
/opt/metnos/.venv/bin/python -I - "$1" "$2" <<'PY'
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
if sys.argv[2] in {"producer-declarations", "producer-binding", "publication-binding", "historical-inventory"}:
    # This candidate helper was not present in the installed release. Measure
    # it explicitly; do not mistake the older source-availability probe for
    # an execution of the new exact-file owner interface.
    modules = ("executor_birth_distribution_manifest.py",) + modules
if sys.argv[2] in {"producer-binding", "publication-binding", "historical-inventory"}:
    modules = ("executor_birth_receipts.py",) + modules
if sys.argv[2] == "historical-inventory":
    modules += ("executor_birth_producer_context.py",)
if sys.argv[2] in {"producer-history", "contract-history", "contract-history-v1", "producer-binding", "publication-binding", "historical-inventory"}:
    modules += ("executor_birth_producer_store.py",)
if sys.argv[2] in {"contract-history", "contract-history-v1", "contract-inventory", "producer-binding", "publication-binding", "historical-inventory"}:
    modules += ("contract_store.py",)
if sys.argv[2] in {"publication-binding", "historical-inventory"}:
    modules += ("executor_birth_operational.py",)
if sys.argv[2] == "historical-inventory":
    modules += ("executor_birth_reattestation.py", "executor_birth_history.py")

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
archived_public_sources = []
if sys.argv[2] == "historical-inventory":
    # Git supplies untrusted public bytes, never historical authority. Read
    # only these exact source paths as the checkout owner, before the service
    # reader's stricter no-subprocess audit is installed. No checkout/export,
    # filter, hook, network, private file or production write is involved.
    owner = source.stat()
    if owner.st_uid == 0:
        raise RuntimeError("public_source_reader_requires_nonroot_owner")
    git = ["/usr/bin/git", "--no-optional-locks", "--no-replace-objects",
           "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(source)]
    git_env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    git_deadline = time.monotonic() + 30

    def read_git(*args):
        remaining = git_deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("public_source_read_deadline")
        return subprocess.check_output(
            [*git, *args], timeout=min(5, remaining), env=git_env,
            user=owner.st_uid, group=owner.st_gid, extra_groups=[],
        )

    for path in ("runtime/executor_birth_intent.py", "runtime/executor_birth_bootstrap.py"):
        commits = read_git("log", "--all", "--diff-filter=AM", "--max-count=128", "--format=%H", "--", path).splitlines()
        seen = set()
        for commit in commits:
            if len(commit) != 40 or any(char not in b"0123456789abcdef" for char in commit):
                raise RuntimeError("public_source_object_invalid")
            object_name = commit.decode("ascii") + ":" + path
            length = int(read_git("cat-file", "-s", object_name))
            if length > 1024 * 1024:
                raise RuntimeError("public_source_byte_limit")
            encoded = read_git("cat-file", "blob", object_name)
            if len(encoded) != length:
                raise RuntimeError("public_source_changed")
            digest = hashlib.sha256(encoded).hexdigest()
            if digest not in seen:
                seen.add(digest)
                archived_public_sources.append((path, encoded))
            if sum(len(item[1]) for item in archived_public_sources) > 32 * 1024 * 1024:
                raise RuntimeError("public_source_total_byte_limit")
output = os.open(str(Path(sys.argv[1]) / "result.json"),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
os.setgroups([])
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
os.chdir(source)
sys.dont_write_bytecode = True
denied = {"private_read": 0, "metadata_repair": 0, "log_initialization": 0, "write_or_execution": 0}
denied_events = []

def read_only_audit(event, args):
    """A regression guard, not an authorization boundary for untrusted code."""
    if event == "sqlite3.connect":
        # SQLite's C-level WAL bookkeeping is not covered by Python's open
        # event. Permit only the reviewed, logically read-only connection.
        allowed_database = (Path(os.environ["METNOS_USER_STATE"]) / "birth"
                            / "producer_receipts.sqlite")
        if (sys.argv[2] not in {"producer-history", "contract-history", "contract-history-v1", "producer-binding", "publication-binding", "historical-inventory"}
                or args[0] != allowed_database.as_uri() + "?mode=ro&cache=private"):
            denied["write_or_execution"] += 1
            denied_events.append({"event": event})
            raise PermissionError("proof_sqlite_connection_denied")
    elif event == "open":
        path, _mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            if (isinstance(path, (str, bytes))
                    and Path(os.fsdecode(path)) == Path(os.environ["METNOS_USER_STATE"]) / "metnos.log"):
                # sign's cold import tries to initialize the ordinary logger.
                # Keep the write denied and report it separately, like config's
                # optional permission repair; never create a production log.
                denied["log_initialization"] += 1
                raise PermissionError("proof_log_initialization_denied")
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
    "mode": sys.argv[2],
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
    # Keep the installed, signed policy unchanged. Only the declared candidate
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
    if sys.argv[2] == "initial-public-history":
        selectors = (chain.context_transitions[0].previous_admission_context_id,)
    if sys.argv[2] == "initial-policy-binding":
        from executor_birth_prepared_root import open_prepared_root_session_v1
        from executor_birth_prepared_set import (
            AUTHORITY_SETS_BASENAME_V1, _read_integrity_document_v1,
        )
        from executor_birth_context_v1 import (
            CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1,
        )

        evidence = load_historical_context_verifiers_v1(
            chain.context_transitions[0].previous_admission_context_id,
        )
        if evidence.required_head_id != chain.required_head.head_id:
            raise RuntimeError("proof_frontier_changed")
        public = evidence.public_set
        with open_prepared_root_session_v1() as session:
            with session.global_lock(exclusive=False, create=False):
                encoded_material = _read_integrity_document_v1(session, (
                    AUTHORITY_SETS_BASENAME_V1, public.set_id,
                    CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1,
                ))
        if hashlib.sha256(encoded_material).hexdigest() != public.material.material_sha256:
            raise RuntimeError("initial_material_binding_changed")
        material = json.loads(encoded_material)
        records = [
            {"component": name, **item}
            for name, component in material["components"].items()
            for item in component["files"]
        ]
        result["initial_policy_binding"] = {
            "context_id": public.material.pin.admission_context_id,
            "material_sha256": public.material.material_sha256,
            "material_file_entries": len(records),
            "source_labels": sorted({item["label"] for item in records}),
            "producer_policy_records": [
                item for item in records
                if Path(item["label"]).name == "executor_birth_producer_table_v1.py"
            ],
            "qualification": "exact_initial_material_inventory_not_policy_substitution",
        }
        selectors = ()
    if sys.argv[2] == "producer-declarations":
        from executor_birth_prepared_root import load_historical_producer_declarations_v1

        evidence = load_historical_producer_declarations_v1(
            chain.context_transitions[-1].prepared_admission_context_id,
        )
        if evidence.context.required_head_id != chain.required_head.head_id:
            raise RuntimeError("proof_frontier_changed")
        result["producer_declarations"] = {
            "context_id": evidence.context.public_set.material.pin.admission_context_id,
            "closed_build_id": evidence.closed_build_id,
            "source_path": evidence.source_path,
            "source_hash": evidence.source_hash,
            "producer_namespaces": len(evidence.authors),
            "executor_origins": dict(evidence.executor_origins),
            "qualification": "historical_declarations_not_qualifying_admission",
        }
        selectors = ()
    if sys.argv[2] in {"producer-binding", "publication-binding"}:
        import base64
        from contract_store import read_historical_birth_evidence_v1
        from executor_birth_prepared_root import load_historical_producer_declarations_v1
        from executor_birth_producer_store import (
            read_producer_history_v1, verify_historical_producer_binding_v1,
        )
        from manifest_inventory import ContractId, ManifestOrigin

        history = read_producer_history_v1()
        targets = {item.prepared_admission_context_id for item in chain.context_transitions}
        candidates = []
        ordinary_hints = 0
        # Unsigned wire fields choose a deterministic sample, not its truth.
        # The join then authenticates the exact bytes, key and request itself.
        for row in history.receipts:
            if row.terminal_envelope is None:
                continue
            terminal = json.loads(row.terminal_envelope)
            embedded = terminal.get("admission_receipt")
            if embedded is None:
                continue
            encoded_admission = base64.b64decode(embedded, validate=True)
            hint = json.loads(encoded_admission)
            if hint["kind"] != "admission":
                continue
            ordinary_hints += 1
            if hint["admission_context_id"] in targets:
                candidates.append((row, encoded_admission, hint))
        if not candidates:
            raise RuntimeError("no_ordinary_target_context_sample")
        row, encoded_admission, hint = max(candidates, key=lambda item: item[0].row_id)
        issuance = [item for item in history.issuances if item.request_id == hint["birth_request_id"]]
        if len(issuance) != 1:
            raise RuntimeError("ordinary_sample_issuance_ambiguous")
        declarations = load_historical_producer_declarations_v1(hint["admission_context_id"])
        if declarations.context.required_head_id != chain.required_head.head_id:
            raise RuntimeError("proof_frontier_changed")
        binding = verify_historical_producer_binding_v1(
            encoded_admission, receipt_row=row, issuance_row=issuance[0],
            declarations=declarations,
        )
        origin, relative = binding.admission.contract_id.split(":", 1)
        durable = read_historical_birth_evidence_v1(
            ContractId(ManifestOrigin(origin), relative), binding.admission.generation_id,
            admission_context_id=binding.admission.admission_context_id,
        )
        if durable.receipt_bytes != encoded_admission:
            raise RuntimeError("ordinary_sample_durable_receipt_differs")
        result["producer_binding"] = {
            "unsigned_ordinary_terminal_hints": ordinary_hints,
            "unsigned_target_context_hints": len(candidates),
            "selected_receipt_row_id": row.row_id,
            "selected_issuance_row_id": issuance[0].row_id,
            "selection_rule": "largest_row_id_with_embedded_ordinary_target_context_hint",
            "admission_receipt_id": binding.admission.receipt_id,
            "producer_receipt_id": binding.producer.receipt_id,
            "context_id": binding.admission.admission_context_id,
            "namespace": binding.namespace,
            "signed_act_time": binding.admission.issued_at,
            "producer_expires_at": binding.producer.expires_at,
            "durable_receipt_byte_equal": True,
            "qualification": "signed_producer_binding_sample_not_terminal_or_admission_qualification",
        }
        if sys.argv[2] == "publication-binding":
            from executor_birth_operational import verify_historical_publication_v1

            joined = verify_historical_publication_v1(
                evidence=durable, receipt_row=row, issuance_row=issuance[0],
                declarations=declarations,
            )
            result["publication_binding"] = {
                "generation_id": joined.terminal.publication.current_generation_id,
                "outcome": joined.terminal.report.outcome.value,
                "revision_class": joined.terminal.report.revision_class.value,
                "approved_lifecycle": joined.producer_binding.admission.approved_lifecycle.value,
                "embedded_receipt_present": True,
                "original_source_journal_reread": False,
                "qualification": "authenticated_publication_sample_not_complete_inventory_or_f5_count",
            }
        selectors = ()
    if sys.argv[2] == "producer-history":
        from executor_birth_producer_store import read_producer_history_v1

        history = read_producer_history_v1()
        states = ("available", "in_progress", "committed", "rejected")
        result["producer_history"] = {
            "source_path": str(history.source_path),
            "schema_version": history.schema_version,
            "receipt_rows": len(history.receipts),
            "issuance_rows": len(history.issuances),
            "state_counts": {state: sum(row.state == state for row in history.receipts)
                             for state in states},
            "unclassified_rows": sum(row.state not in states for row in history.receipts),
            "field_bytes": history.field_bytes,
            "terminal_rows": sum(row.terminal_envelope is not None for row in history.receipts),
            "qualification": "complete_logical_snapshot_only_no_signature_or_global_frontier_proof",
        }
        selectors = ()
    if sys.argv[2] == "historical-inventory":
        from collections import Counter
        from contract_store import read_historical_birth_inventory_v1
        from executor_birth_history import reconcile_historical_birth_v1
        from executor_birth_prepared_root import load_historical_producer_declarations_for_contexts_v1
        from executor_birth_producer_store import read_producer_history_v1

        producer_history = read_producer_history_v1()
        inventory = read_historical_birth_inventory_v1()
        # These are selectors only. The join subsequently verifies each
        # receipt, including its physical locator and independent reread.
        wanted_contexts = set()
        for contract in inventory.contracts:
            for located in contract.receipts:
                document = json.loads(located.encoded)
                wanted_contexts.add(document["admission_context_id"])
        known_contexts = {item.prepared_admission_context_id for item in chain.context_transitions}
        declarations = load_historical_producer_declarations_for_contexts_v1(
            tuple(sorted(wanted_contexts & known_contexts)), include_reattestation=True,
            public_sources=tuple(archived_public_sources),
        )
        if any(item.context.required_head_id != chain.required_head.head_id for item in declarations):
            raise RuntimeError("proof_frontier_changed")
        joined = reconcile_historical_birth_v1(inventory, producer_history, declarations)
        unchanged = (read_historical_birth_inventory_v1() == inventory
                     and read_producer_history_v1() == producer_history)
        result["historical_inventory"] = {
            "contracts": len(inventory.contracts), "physical_receipts": joined.physical_receipts,
            "producer_rows": joined.producer_rows, "issuance_rows": joined.issuance_rows,
            "contexts_observed": len(wanted_contexts), "contexts_with_declarations": len(declarations),
            "contexts_with_reattestation_scope": sum(item.reattestation_scope is not None for item in declarations),
            "untrusted_archived_public_artifacts": len(archived_public_sources),
            "untrusted_source_reader_uid": owner.st_uid,
            "scope_limitations": dict(Counter(item.reattestation_scope_error for item in declarations
                                               if item.reattestation_scope_error)),
            "authenticated_acts": len(joined.acts),
            "act_classifications": dict(Counter(item.classification for item in joined.acts)),
            "technical_candidates": len(joined.technical_acts),
            "technical_candidate_issuers": joined.technical_issuers,
            "issues": dict(Counter(item.code for item in joined.issues)),
            "issue_examples": [dict(source=item.source, identity=item.identity, code=item.code)
                               for item in joined.issues[:5]],
            "inventories_unchanged_on_reread": unchanged,
            "original_source_journal_reread": False,
            "qualification": "observed_complete_inventory_with_explicit_gaps_not_certification_frontier_or_f5",
        }
        if not unchanged:
            raise RuntimeError("observed_inventories_changed")
        selectors = ()
    if sys.argv[2] == "contract-inventory":
        from contract_store import read_historical_birth_inventory_v1

        inventory = read_historical_birth_inventory_v1()
        result["contract_inventory"] = {
            "source_path": str(inventory.source_path),
            "contracts": len(inventory.contracts),
            "contracts_without_receipts": sum(not item.receipts for item in inventory.contracts),
            "unbound_empty_namespaces": len(inventory.unbound_empty_namespaces),
            "generation_entries": sum(len(item.generation_ids) for item in inventory.contracts),
            "retirement_entries": sum(len(item.retirement_ids) for item in inventory.contracts),
            "receipt_entries_v1": sum(receipt.admission_context_id is None
                                      for item in inventory.contracts for receipt in item.receipts),
            "receipt_entries_v2": sum(receipt.admission_context_id is not None
                                      for item in inventory.contracts for receipt in item.receipts),
            "entry_count": inventory.entry_count,
            "field_bytes": inventory.field_bytes,
            "qualification": "complete_raw_namespace_not_authentication_or_global_frontier_proof",
            "certification_ready": "not_determined_unbound_namespaces_require_reconciliation",
        }
        selectors = ()
    if sys.argv[2] == "contract-residual":
        import config
        from contract_bootstrap import STORE_RELATIVE

        # Diagnostic of one explicitly named failed namespace, not a fallback
        # inventory and not an inference of its absent contract identity.
        # Exact diagnostic test locator from run-m7roerbp. This script is
        # digest-pinned by the administrative runner; no product exception or
        # inferred contract mapping is introduced for this stored artifact.
        target = "4e2feabf613fef06a63bddbf37396733f427cbeae5366aa75df4816d7f27611d"
        root = Path(config.PATH_USER_STATE) / STORE_RELATIVE
        directory = root / target

        def metadata(path):
            info = path.lstat()
            return {"name": str(path.relative_to(directory)), "mode": oct(stat.S_IMODE(info.st_mode)),
                    "uid": info.st_uid, "gid": info.st_gid, "size": info.st_size,
                    "device": info.st_dev, "inode": info.st_ino, "links": info.st_nlink,
                    "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                    "kind": "directory" if stat.S_ISDIR(info.st_mode) else
                    "regular" if stat.S_ISREG(info.st_mode) else "unsupported"}

        residual_before = metadata(directory)
        if residual_before["kind"] != "directory" or directory.resolve(strict=True) != directory:
            raise RuntimeError("residual_directory_invalid")
        entries = []
        with os.scandir(directory) as children:
            for child in children:
                if len(entries) >= 32:
                    raise RuntimeError("residual_entry_limit")
                item = metadata(directory / child.name)
                if item["kind"] == "directory":
                    names = []
                    with os.scandir(directory / child.name) as grandchildren:
                        for grandchild in grandchildren:
                            if len(names) >= 32:
                                raise RuntimeError("residual_entry_limit")
                            names.append(grandchild.name)
                    item["children"] = sorted(names)
                entries.append(item)
        if metadata(directory) != residual_before:
            raise RuntimeError("residual_changed")
        result["contract_residual"] = {
            "source_path": str(directory), "directory": residual_before,
            "entries": sorted(entries, key=lambda item: item["name"]),
            "qualification": "exact_metadata_only_no_identity_authentication_or_cleanup",
        }
        selectors = ()
    if sys.argv[2] in {"contract-history", "contract-history-v1"}:
        import base64
        from contract_store import read_historical_birth_evidence_v1
        from executor_birth_producer_store import read_producer_history_v1
        from manifest_inventory import ContractId, ManifestOrigin

        history = read_producer_history_v1()
        # This is a new exact durable-read traversal, not a repeat of the raw
        # row census or a signature/eligibility verifier. Unsigned locators
        # select two samples and cannot authorize publication or certification.
        located = []
        for row in history.receipts:
            if row.state != "committed" or row.terminal_envelope is None:
                continue
            value = json.loads(row.terminal_envelope)
            if isinstance(value.get("publication"), dict) and value.get("admission_receipt"):
                located.append(value)
        if not located:
            raise RuntimeError("history_sample_unavailable")
        samples = [located[0]] if len(located) == 1 else [located[0], located[-1]]
        if sys.argv[2] == "contract-history-v1":
            # Explicit V1 for the first sample of this fresh snapshot. No
            # cross-run identity claim and no failed-V2-to-V1 fallback.
            samples = [located[0]]
        result["contract_history"] = []
        sample_failures = 0
        for sample in samples:
            publication = sample["publication"]
            raw_origin, relative = publication["contract_id"].split(":", 1)
            context = sample["report"]["admission_context_id"]
            if not isinstance(context, str) or not context:
                raise RuntimeError("history_sample_context_not_v2")
            try:
                evidence = read_historical_birth_evidence_v1(
                    ContractId(ManifestOrigin(raw_origin), relative),
                    publication["current_generation_id"],
                    admission_context_id=None if sys.argv[2] == "contract-history-v1" else context,
                )
            except Exception as exc:
                item = {"status": "not_read", "code": getattr(exc, "code", None)}
                cause = exc.__cause__
                if isinstance(cause, FileNotFoundError) and cause.filename:
                    try:
                        item["missing_state_relative_path"] = str(
                            Path(cause.filename).relative_to(os.environ["METNOS_USER_STATE"])
                        )
                    except ValueError:
                        item["missing_state_relative_path"] = "outside_selected_state"
                result["contract_history"].append(item)
                sample_failures += 1
                continue
            embedded = base64.b64decode(sample["admission_receipt"], validate=True)
            if evidence.receipt_bytes != embedded:
                raise RuntimeError("historical_embedded_receipt_differs_from_durable")
            result["contract_history"].append({
                "receipt_bytes": len(evidence.receipt_bytes),
                "manifest_bytes": len(evidence.manifest_bytes),
                "signature_bytes": len(evidence.signature_bytes),
                "language_state_bytes": len(evidence.language_state_bytes),
                "binding_bytes": len(evidence.binding_bytes),
                "embedded_equals_durable": True,
                "generation_content_address_matches": True,
                "selected_layout": "v1" if sys.argv[2] == "contract-history-v1" else "v2",
                "qualification": "exact_samples_not_authentication_or_complete_inventory",
            })
        if sample_failures:
            raise RuntimeError("historical_samples_incomplete_no_replacement_samples")
        selectors = ()
    if sys.argv[2] == "producer-policy":
        from executor_birth_distribution_manifest import file_content_hash

        # This changes the next design decision: can the historical author
        # policy be recovered from an already retained, exact public source?
        policy_path = "runtime/executor_birth_producer_table_v1.py"
        with (dependency_root / policy_path).open("rb") as stream:
            policy_source = stream.read(1024 * 1024 + 1)
        if len(policy_source) > 1024 * 1024:
            raise RuntimeError("policy_source_size_limit")
        current_hash = file_content_hash(policy_path, policy_source)
        variants = {}
        for record in chain.authenticated_records:
            matches = [item for item in record.files if item.path == policy_path]
            if len(matches) != 1:
                raise RuntimeError("historical_policy_inventory_incomplete")
            item = matches[0]
            variants.setdefault(item.content_hash, []).append(record.release_sequence)
            if item.content_hash == current_hash and item.size != len(policy_source):
                raise RuntimeError("historical_policy_size_mismatch")
        result["producer_policy"] = {
            "current_public_source_hash": current_hash,
            "authenticated_release_variants": variants,
            "exact_copy_available_for_all": set(variants) == {current_hash},
            "qualification": "source_availability_only_no_admission_count",
        }
        selectors = ()
    for selector in selectors:
        evidence = load_historical_context_verifiers_v1(selector)
        if evidence.required_head_id != chain.required_head.head_id:
            raise RuntimeError("proof_frontier_changed")
        public = evidence.public_set
        result["contexts"].append({
            "context_id": selector, "set_id": public.set_id,
            "binding_kind": evidence.binding_kind,
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
    candidate_names = {name.removesuffix(".py") for name in modules}
    if any(
        Path(path).parent != (source if name in candidate_names else dependency_root) / "runtime"
        for name, path in origins.items()
    ):
        raise RuntimeError("undeclared_dependency_origin")
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
    result["status"] = {
        "producer-history": "observed_complete_producer_history",
        "contract-history": "observed_exact_durable_history_samples",
        "contract-history-v1": "observed_exact_original_layout_sample",
        "contract-inventory": "observed_complete_raw_contract_inventory",
        "contract-residual": "observed_exact_unbound_namespace_metadata",
        "producer-policy": "observed_authenticated_policy_sources",
        "public-history": "verified_selected_public_contexts",
        "initial-public-history": "verified_initial_public_context_not_historical_producer_policy",
        "initial-policy-binding": "observed_authenticated_initial_material_source_inventory",
        "producer-declarations": "verified_selected_historical_producer_declarations",
        "producer-binding": "verified_ordinary_producer_and_durable_receipt_binding_sample",
        "publication-binding": "verified_ordinary_historical_publication_sample",
        "historical-inventory": "reconciled_observed_inventory_with_explicit_evidence_limits",
    }[sys.argv[2]]
except Exception as exc:
    errors = []
    while exc is not None and len(errors) < 8:
        item = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
        if type(exc) is RuntimeError:
            item["detail"] = str(exc)[:128]
        if type(exc).__name__ in {"OwnershipChainError", "DistributionManifestError", "ContractStoreError"}:
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
raise SystemExit(0 if result["status"] != "not_verified" else 1)
PY

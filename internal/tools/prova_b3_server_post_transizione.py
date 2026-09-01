#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Prove a full server turn from an isolated post-transition release copy.

The outer process prepares a disposable source tree and a private filesystem
namespace.  The inner process then uses the productive authority provisioner,
source receiver and signed-release builder against their fixed paths.  It
publishes a new prepared authority set and a complete signed ownership chain
inside the namespace before the existing C1 probe starts the full HTTP server.

No production path is writable.  The namespace root and every fixed ownership
ancestor are private tmpfs objects, while only the caller-owned scratch tree is
bound read-write.  The copied authority inputs and generated private material
are destroyed with that scratch tree when the probe exits.

Usage:
    python3 internal/tools/prova_b3_server_post_transizione.py \
        --repository DIR --pre-transition-repository DIR --llama-server FILE
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


EXIT_OK = 0
EXIT_SELF = 1
EXIT_PREPARATION = 2
EXIT_TURN = 3

FIXED_OWNERSHIP_ROOT = Path("/var/lib/metnos/executor-birth")
FIXED_RELEASE_ROOT = FIXED_OWNERSHIP_ROOT / "releases-v1" / f"{1:020d}"
SERVICE_USER = "metnos"
SERVICE_UID = 1001
SERVICE_GID = 1001


def _repository_imports(repository: Path) -> None:
    for directory in (repository, repository / "runtime", repository / "install"):
        value = str(directory)
        if value not in sys.path:
            sys.path.insert(0, value)


def _copy_regular(source: Path, destination: Path, mode: int | None = None) -> None:
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"non-regular input: {source}")
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode if mode is not None else (info.st_mode & 0o777))


def _copy_reviewed_source(
    repository: Path, destination: Path, llama_server: Path,
) -> None:
    _repository_imports(repository)
    import executor_birth_distribution_release as release

    destination.mkdir(mode=0o755)
    selected: set[str] = set()
    for base in sorted(release._SOURCE_ROOTS_V1):
        source_root = repository / base
        for source in sorted(source_root.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(repository).as_posix()
            if release._projected_source_path_v1(relative) is None:
                continue
            _copy_regular(
                source,
                destination.joinpath(*relative.split("/")),
                0o755 if source.lstat().st_mode & 0o111 else 0o644,
            )
            selected.add(relative)
    for relative in (
        release.BOUNDARY_INVENTORY_SOURCE_PATH_V1,
        release.DEPENDENCY_SOURCE_PATH_V1,
    ):
        _copy_regular(
            repository.joinpath(*relative.split("/")),
            destination.joinpath(*relative.split("/")),
            0o644,
        )
        selected.add(relative)
    _copy_regular(
        llama_server,
        destination.joinpath(*release.LLAMA_SOURCE_PATH_V1.split("/")),
        0o755,
    )
    selected.add(release.LLAMA_SOURCE_PATH_V1)
    if not selected:
        raise RuntimeError("reviewed source selection is empty")
    for directory in sorted(
        (item for item in destination.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts), reverse=True,
    ):
        directory.chmod(0o755)


def _copy_authority_inputs(source_birth: Path, target_config: Path) -> None:
    """Copy only stable initial inputs into disposable storage."""
    target_birth = target_config / "birth"
    target_birth.mkdir(mode=0o755, parents=True)
    source = source_birth / "operator-input-v1"
    if not source.is_dir() or source.is_symlink():
        raise RuntimeError("missing initial authority operator input")
    for item in source.rglob("*"):
        if item.is_symlink() or not (item.is_dir() or item.is_file()):
            raise RuntimeError(f"unsupported authority input: {item}")
    destination = target_birth / "operator-input-v1"
    shutil.copytree(source, destination, copy_function=shutil.copyfile)
    for original in source.rglob("*"):
        copied = destination / original.relative_to(source)
        copied.chmod(original.lstat().st_mode & 0o777)
    destination.chmod(source.lstat().st_mode & 0o777)

    source_keys = source_birth.parent / "keys"
    target_keys = target_config / "keys"
    target_keys.mkdir(mode=0o700)
    for name in ("author_priv.bin", "author_pub.bin", "synt_pub.bin"):
        _copy_regular(source_keys / name, target_keys / name, 0o600)


def _write_namespace_identity(root: Path) -> None:
    (root / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/sh\n"
        f"{SERVICE_USER}:x:{SERVICE_UID}:{SERVICE_GID}:"
        "Metnos service:/srv/metnos:/proof/nologin\n",
        encoding="ascii",
    )
    (root / "group").write_text(
        "root:x:0:\n"
        f"{SERVICE_USER}:x:{SERVICE_GID}:\n",
        encoding="ascii",
    )
    for name in ("passwd", "group"):
        (root / name).chmod(0o644)
    nologin = Path("/usr/sbin/nologin")
    if not nologin.is_file():
        nologin = Path("/usr/bin/false")
    _copy_regular(nologin, root / "nologin", 0o755)


def _namespace_command(
    *, repository: Path, scratch: Path, arguments: list[str],
    from_release: bool = False, baseline_repository: Path | None = None,
    from_baseline: bool = False,
) -> list[str]:
    python_path = "/work:/work/runtime:/work/install"
    install_root = "/work"
    if from_release:
        python_path = (
            f"{FIXED_RELEASE_ROOT}:{FIXED_RELEASE_ROOT}/runtime:"
            f"{FIXED_RELEASE_ROOT}/install:/work"
        )
        install_root = FIXED_RELEASE_ROOT.as_posix()
    elif from_baseline:
        python_path = "/baseline:/baseline/runtime:/baseline/install:/work"
        install_root = "/baseline"
    options = [
        "bwrap", "--unshare-user", "--uid", "0", "--gid", "0",
        "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/etc", "/etc",
        "--dir", "/opt", "--dir", "/opt/metnos",
        "--ro-bind", "/opt/metnos/.venv", "/opt/metnos/.venv",
        "--dir", "/var", "--dir", "/var/lib", "--dir", "/var/lib/metnos",
        "--dir", FIXED_OWNERSHIP_ROOT.as_posix(),
        "--bind", (scratch / "ownership").as_posix(),
        FIXED_OWNERSHIP_ROOT.as_posix(),
        "--dir", "/work", "--ro-bind", repository.as_posix(), "/work",
        "--ro-bind", (scratch / "source/runtime").as_posix(), "/work/runtime",
        "--dir", "/proof", "--bind", scratch.as_posix(), "/proof",
        "--tmpfs", "/run", "--tmpfs", "/tmp", "--dir", "/root",
        "--bind", (scratch / "passwd").as_posix(), "/etc/passwd",
        "--bind", (scratch / "group").as_posix(), "/etc/group",
        "--setenv", "HOME", "/root",
        "--setenv", "PATH", "/opt/metnos/.venv/bin:/usr/bin:/bin",
        "--setenv", "PYTHONPATH", python_path,
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONUNBUFFERED", "1",
        "--setenv", "METNOS_INSTALL_ROOT", install_root,
        "--setenv", "METNOS_USER_CONFIG", "/proof/user/cfg",
        "--setenv", "METNOS_USER_STATE", "/proof/user/state",
        "--setenv", "METNOS_USER_DATA", "/proof/user/data",
        "--setenv", "METNOS_WORKSPACE", "/proof/user/workspace",
    ]
    if baseline_repository is not None:
        options.extend([
            "--dir", "/baseline",
            "--bind", baseline_repository.as_posix(), "/baseline",
        ])
    return options + arguments


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(
        b"metnos.rm0008.copy-proof/v1\0" + label.encode("ascii")
    ).hexdigest()


def _publication_store_shape(state_root: Path) -> dict[str, object]:
    """Return structural diagnostics without reading contract payloads."""
    version_root = state_root / "contract-publications" / "v1"
    if not version_root.is_dir():
        return {"present": False}
    directories = tuple(
        item for item in version_root.iterdir() if item.is_dir()
    )
    bindings = tuple(item / "binding.json" for item in directories)
    return {
        "present": True,
        "directories": len(directories),
        "bindings": sum(item.is_file() for item in bindings),
        "current": sum((item / "current").is_file() for item in directories),
        "generation_roots": sum(
            (item / "generations").is_dir() for item in directories
        ),
        "active_marker": (
            state_root / "contract-publications.ACTIVE"
        ).is_file(),
    }


def _single_key_id(registry: object) -> str:
    entries = tuple(registry.keys.values())
    if len(entries) != 1:
        raise RuntimeError("ownership registry cardinality")
    return entries[0].key_id


def _converge_contracts_inside() -> int:
    """Create and receipt the complete store through the open predecessor."""
    baseline = Path("/baseline")
    _repository_imports(baseline)
    from admin.i18n_migrate_manifests import activate_prepared_contract_store
    from birth_authority_provisioner import (
        ensure_executor_birth_authorities_prepared,
    )
    from birth_ownership_authority_provisioner import (
        provision_root_ownership_authorities_v1,
    )
    from executor_birth_bootstrap import prepare_initial_installer_catalog_v1
    from executor_birth_cutover import prepare_current_receipt_proof
    from executor_birth_ownership_chain import OwnershipChainStore
    from executor_birth_ownership_coordinator import (
        _current_reattestation_port_v1,
    )
    from executor_birth_reattestation import reattest_current_generation
    import manifest_inventory

    ensure_executor_birth_authorities_prepared()
    provision_root_ownership_authorities_v1()
    OwnershipChainStore.initialize()
    complete_inventory = manifest_inventory.inventory_authoring_manifests()
    selected = tuple(
        ref for ref in complete_inventory.manifests
        if ref.contract_id.value == "builtin:get_preferences/manifest.toml"
    )
    if complete_inventory.problems or len(selected) != 1:
        raise RuntimeError("representative contract is unavailable")
    representative_inventory = manifest_inventory.ManifestInventory(
        selected, (),
    )
    manifest_inventory.inventory_authoring_manifests = (
        lambda *_args, **_kwargs: representative_inventory
    )
    report = prepare_initial_installer_catalog_v1(
        prove_quiescent=lambda: True,
    )
    activate_prepared_contract_store(
        report, quiescence_guard=lambda: True,
    )
    port = _current_reattestation_port_v1()
    receipt_report = prepare_current_receipt_proof(
        prove_quiescent=lambda: True,
        enumerate_current=port.enumerate_current,
        read_receipt=port.read,
        reattest_via_birth=lambda item: reattest_current_generation(item).receipt,
        verify_receipt=port.verify_receipt,
    )
    proof_path = Path("/proof/current-receipt-proof-v1.json")
    proof_path.write_text(json.dumps({
        "identities": [list(item) for item in receipt_report.proof.identities],
        "receipt_hashes": [
            [*identity, digest]
            for identity, digest in receipt_report.proof.receipt_hashes.items()
        ],
    }, sort_keys=True, separators=(",", ":")), encoding="ascii")
    proof_path.chmod(0o600)
    print(json.dumps({
        "contracts": report["contracts"],
        "receipts": len(receipt_report.proof.identities),
    }, sort_keys=True))
    return EXIT_OK


def _prepare_inside() -> int:
    repository = Path("/work")
    _repository_imports(repository)
    from birth_authority_provisioner import (
        ensure_executor_birth_authorities_prepared,
    )
    from birth_ownership_authority_provisioner import (
        provision_root_ownership_authorities_v1,
    )
    from executor_birth_bootstrap import verify_initial_installer_store_v1
    from executor_birth_distribution_release import (
        build_and_install_received_source_v1,
    )
    from executor_birth_source_receiver import _receive_source_v1

    ensure_executor_birth_authorities_prepared()
    provision_root_ownership_authorities_v1()
    initial_catalog = verify_initial_installer_store_v1(
        prove_quiescent=lambda: True,
    )
    source_id = _receive_source_v1("/proof/source", SERVICE_USER)
    distribution = build_and_install_received_source_v1(source_id)
    handoff = {
        "encoded": base64.b64encode(distribution.encoded).decode("ascii"),
        "signature": base64.b64encode(distribution.signature).decode("ascii"),
        "source_id": source_id,
    }
    path = Path("/proof/release-handoff-v1.json")
    path.write_text(
        json.dumps(handoff, sort_keys=True, separators=(",", ":")),
        encoding="ascii",
    )
    path.chmod(0o600)
    print(json.dumps({
        "closed_build_id": distribution.identity.closed_build_id,
        "contracts": initial_catalog["contracts"],
        "release_root": FIXED_RELEASE_ROOT.as_posix(),
        "source_id": source_id,
    }, sort_keys=True))
    return EXIT_OK


def _cross_inside() -> int:
    release_root = FIXED_RELEASE_ROOT
    _repository_imports(release_root)
    from birth_authority_provisioner import (
        _prepare_transition_receipt_material_locked_v2,
        _publish_prepared_authority_set_v2,
    )
    from executor_birth_context_transition import issue_context_transition_v1
    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1,
        verify_current_installation_distribution_v1,
    )
    from executor_birth_ownership_authorities import (
        load_root_ownership_authorities_v1,
    )
    from executor_birth_ownership_chain import (
        OwnershipChainStore, inspect_ownership_chain_state_v1,
        issue_ownership_head,
    )
    from executor_birth_ownership_coordinator import (
        _deployment_lock_v1, _reserve_transition_edge_locked_v2,
    )
    from executor_birth_ownership_cutover import (
        install_ownership_cutover_certificate,
        issue_ownership_cutover_certificate,
    )
    from executor_birth_prepared_root import load_required_context_runtime_v1

    handoff = json.loads(
        Path("/proof/release-handoff-v1.json").read_text(encoding="ascii")
    )
    if not isinstance(handoff, dict) or set(handoff) != {
        "encoded", "signature", "source_id",
    }:
        raise RuntimeError("release handoff schema")
    try:
        encoded = base64.b64decode(handoff["encoded"], validate=True)
        signature = base64.b64decode(handoff["signature"], validate=True)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("release handoff encoding") from exc
    source_id = handoff["source_id"]
    record = authenticate_distribution_record_v1(encoded, signature)
    distribution = verify_current_installation_distribution_v1(
        record.encoded, record.signature,
    )
    proof_document = json.loads(Path(
        "/proof/current-receipt-proof-v1.json"
    ).read_text(encoding="ascii"))
    identities = tuple(tuple(item) for item in proof_document["identities"])
    receipt_hashes = {
        tuple(item[:2]): item[2]
        for item in proof_document["receipt_hashes"]
    }
    proof = CurrentReceiptProof(identities, receipt_hashes)

    with _deployment_lock_v1() as session:
        claim = _reserve_transition_edge_locked_v2(
            session, distribution=distribution, source_id=source_id,
        )
        preparation = _prepare_transition_receipt_material_locked_v2(
            session, distribution,
        )
    previous = preparation.previous_context
    target = preparation.prepared_authority_set
    transition_encoded, transition = issue_context_transition_v1(
        request_id=claim.request_id,
        closed_build_id=distribution.identity.closed_build_id,
        previous_cutover_id=None,
        previous_set_id=previous.set_id,
        previous_admission_context_id=previous.prepared_admission_context_id,
        previous_context_epoch=previous.prepared_context_epoch,
        set_id=target.target_set_id,
        prepared_admission_context_id=target.target_admission_context_id,
        prepared_context_epoch=target.target_context_epoch,
        context_material_sha256=target.target_context_material_sha256,
        set_json_sha256=target.target_set_json_sha256,
        current_inventory=proof.inventory,
    )
    _publish_prepared_authority_set_v2(target)

    authorities = load_root_ownership_authorities_v1()
    dominant_receipt = _digest("dominant-startup")
    cutover_encoded, cutover_signature = issue_ownership_cutover_certificate(
        proof=proof, previous_cutover_id=None, request_id=claim.request_id,
        signing_key_id=_single_key_id(authorities.public.cutover),
        maintenance_evidence_hash=_digest("maintenance"),
        boundary_inventory_hash=distribution.identity.boundary_inventory_hash,
        boundary_guard_version=distribution.identity.boundary_guard_version,
        closed_build_id=distribution.identity.closed_build_id,
        context_transition_id=transition.transition_id,
        dominant_startup_receipt=dominant_receipt,
        private_key=authorities.cutover_private,
    )
    store = OwnershipChainStore.initialize()
    store.append_authenticated_build(distribution)
    store.append_context_transition(
        transition_encoded, expected_proof=proof,
    )
    cutover = store.append_cutover(cutover_encoded, cutover_signature)
    head_encoded, head_signature = issue_ownership_head(
        release_sequence=1, cutover_id=cutover.cutover_id,
        closed_build_id=distribution.identity.closed_build_id,
        previous_head_id=None,
        signing_key_id=_single_key_id(authorities.public.head),
        private_key=authorities.head_private,
    )
    head = store.append_head(head_encoded, head_signature)
    install_ownership_cutover_certificate(
        FIXED_OWNERSHIP_ROOT, cutover_encoded, cutover_signature,
        registry=authorities.public.cutover, expected_proof=proof,
        expected_context_transition_id=transition.transition_id,
        expected_dominant_startup_receipt=dominant_receipt,
    )
    store.update_required_head(
        head_encoded, head_signature, expected_head_id=None,
    )
    chain = inspect_ownership_chain_state_v1()
    required = load_required_context_runtime_v1()
    if (
        chain.required_head.head_id != head.head_id
        or required.required_head_id != head.head_id
        or required.selection.transition_id != transition.transition_id
        or required.selection.set_id != target.target_set_id
    ):
        raise RuntimeError("post-transition readback mismatch")
    print(json.dumps({
        "closed_build_id": distribution.identity.closed_build_id,
        "contracts": len(proof.identities),
        "head_id": head.head_id,
        "release_root": FIXED_RELEASE_ROOT.as_posix(),
        "source_id": source_id,
        "target_set_id": target.target_set_id,
        "transition_id": transition.transition_id,
    }, sort_keys=True))
    return EXIT_OK


def _run_outer(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve(strict=True)
    baseline_repository = Path(
        args.pre_transition_repository
    ).resolve(strict=True)
    llama_server = Path(args.llama_server).resolve(strict=True)
    source_birth = Path(args.authority_inputs).resolve(strict=True)
    if repository == Path("/opt/metnos"):
        print("REFUSED: the repository must be a non-production worktree", file=sys.stderr)
        return EXIT_SELF
    if baseline_repository == repository or not (
        baseline_repository / "runtime/executor_birth_legacy_gate.py"
    ).is_file():
        print("REFUSED: invalid pre-transition repository", file=sys.stderr)
        return EXIT_SELF
    gate_source = (
        baseline_repository / "runtime/executor_birth_legacy_gate.py"
    ).read_text(encoding="utf-8")
    if "    return False\n" not in gate_source or "    return True\n" in gate_source:
        print("REFUSED: predecessor policy bit is not open", file=sys.stderr)
        return EXIT_SELF
    if not (repository / "runtime/metnos_http_server.py").is_file():
        print("REFUSED: incomplete repository", file=sys.stderr)
        return EXIT_SELF
    if not llama_server.is_file() or llama_server.is_symlink():
        print("REFUSED: llama-server must be one regular file", file=sys.stderr)
        return EXIT_SELF
    if shutil.which("bwrap") is None:
        print("REFUSED: bubblewrap is unavailable", file=sys.stderr)
        return EXIT_SELF

    with tempfile.TemporaryDirectory(prefix="metnos-rm0008-b3-") as temporary:
        scratch = Path(temporary)
        scratch.chmod(0o755)
        (scratch / "ownership").mkdir(mode=0o755)
        for name in ("cfg", "state", "data"):
            (scratch / "user" / name).mkdir(mode=0o755, parents=True)
        _copy_authority_inputs(
            source_birth, scratch / "user/cfg",
        )
        _copy_reviewed_source(repository, scratch / "source", llama_server)
        _copy_reviewed_source(
            repository, scratch / "baseline", llama_server,
        )
        _copy_regular(
            baseline_repository / "runtime/executor_birth_legacy_gate.py",
            scratch / "baseline/runtime/executor_birth_legacy_gate.py",
            0o644,
        )
        _write_namespace_identity(scratch)

        converge = _namespace_command(
            repository=repository, scratch=scratch,
            baseline_repository=scratch / "baseline", from_baseline=True,
            arguments=[
                "/opt/metnos/.venv/bin/python",
                "/work/internal/tools/prova_b3_server_post_transizione.py",
                "--converge-inside",
            ],
        )
        converged = subprocess.run(
            converge, text=True, capture_output=True,
            timeout=args.prepare_timeout,
        )
        if converged.returncode != 0:
            print(converged.stdout, end="")
            print(converged.stderr, end="", file=sys.stderr)
            print("B3 CONTRACT CONVERGENCE FAILED", file=sys.stderr)
            return EXIT_PREPARATION
        print("== BASELINE CONTRACT CONVERGENCE ==")
        print(converged.stdout.strip())

        prepare = _namespace_command(
            repository=repository, scratch=scratch,
            arguments=[
                "/opt/metnos/.venv/bin/python",
                "/work/internal/tools/prova_b3_server_post_transizione.py",
                "--prepare-inside",
            ],
        )
        prepared = subprocess.run(
            prepare, text=True, capture_output=True, timeout=args.prepare_timeout,
        )
        if prepared.returncode != 0:
            print(prepared.stdout, end="")
            print(prepared.stderr, end="", file=sys.stderr)
            print("B3 PREPARATION FAILED", file=sys.stderr)
            return EXIT_PREPARATION
        print("== SIGNED RELEASE COPY ==")
        print(prepared.stdout.strip())

        cross = _namespace_command(
            repository=repository, scratch=scratch, from_release=True,
            arguments=[
                "/opt/metnos/.venv/bin/python",
                "/work/internal/tools/prova_b3_server_post_transizione.py",
                "--cross-inside",
            ],
        )
        crossed = subprocess.run(
            cross, text=True, capture_output=True, timeout=args.prepare_timeout,
        )
        if crossed.returncode != 0:
            print(crossed.stdout, end="")
            print(crossed.stderr, end="", file=sys.stderr)
            print("B3 TRANSITION FAILED", file=sys.stderr)
            return EXIT_PREPARATION
        print("== VERIFIED POST-TRANSITION COPY ==")
        print(crossed.stdout.strip())
        print("== CONTRACT STORE BEFORE SERVER ==")
        print(json.dumps(
            _publication_store_shape(scratch / "user/state"),
            sort_keys=True,
        ))

        turn = _namespace_command(
            repository=repository, scratch=scratch, from_release=True,
            arguments=[
                "/opt/metnos/.venv/bin/python",
                "/work/internal/tools/prova_c1_server_turno.py",
                "--replica", FIXED_RELEASE_ROOT.as_posix(),
                "--radici", "/proof/user",
                "--interprete", "/opt/metnos/.venv/bin/python",
                "--query", args.query,
                "--attesa-avvio", str(args.start_timeout),
                "--outer-sandbox",
            ],
        )
        completed = subprocess.run(
            turn, text=True, capture_output=True, timeout=args.turn_timeout,
        )
        print(completed.stdout, end="")
        print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            print("== CONTRACT STORE AFTER SERVER ==", file=sys.stderr)
            print(json.dumps(
                _publication_store_shape(scratch / "user/state"),
                sort_keys=True,
            ), file=sys.stderr)
            server_output = scratch / "user/server.out"
            if server_output.is_file():
                print("== SERVER OUTPUT TAIL ==", file=sys.stderr)
                print(
                    "\n".join(
                        server_output.read_text(
                            encoding="utf-8", errors="replace",
                        ).splitlines()[-80:]
                    ),
                    file=sys.stderr,
                )
            print("B3 TURN FAILED", file=sys.stderr)
            return EXIT_TURN
    print("B3 GREEN: the verified post-transition copy served a real turn.")
    return EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--converge-inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prepare-inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cross-inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repository")
    parser.add_argument("--pre-transition-repository")
    parser.add_argument("--llama-server")
    parser.add_argument(
        "--authority-inputs",
        help="Birth directory supplying only author-root and operator inputs",
    )
    parser.add_argument("--query", default="elenca le mie preferenze")
    parser.add_argument("--prepare-timeout", type=float, default=600.0)
    parser.add_argument("--start-timeout", type=float, default=180.0)
    parser.add_argument("--turn-timeout", type=float, default=600.0)
    result = parser.parse_args(argv)
    if not (
        result.converge_inside or result.prepare_inside or result.cross_inside
    ) and (
        result.repository is None
        or result.pre_transition_repository is None
        or result.llama_server is None
        or result.authority_inputs is None
    ):
        parser.error(
            "--repository, --pre-transition-repository, --llama-server, "
            "and --authority-inputs are required"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.converge_inside:
        return _converge_contracts_inside()
    if args.prepare_inside:
        return _prepare_inside()
    if args.cross_inside:
        return _cross_inside()
    try:
        return _run_outer(args)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"B3 PROBE FAILED: {exc}", file=sys.stderr)
        return EXIT_SELF


if __name__ == "__main__":
    raise SystemExit(main())

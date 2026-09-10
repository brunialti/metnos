#!/usr/bin/python3.12
"""Audit, then cross, the Release 3 ownership transition.

Two modes. `audit` reads and compares only: it changes nothing, stops nothing
and can be run as often as wanted. `complete` performs the real crossing under
the product's own locks, which stops and restarts the services.

Everything it trusts is pinned to what the build actually produced and is
re-measured here: the installed release, its administrative helper, the signed
build evidence and the release sequence. Unlike Release 2 this carries no
out-of-band repair: the live administrative helper is now an installed signed
one, so the crossing runs the product path and nothing else.

The pins are empty until the rebuild fills them: see `require_pinned`.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import traceback

RELEASE = Path(
    "/var/lib/metnos/executor-birth/releases-v1/00000000000000000003")
# The identities of the release the rebuild of 10/9 produced, printed by the
# builder and re-measured on the installed bytes. They are not those of the
# first attempt at sequence 3: that one built a release no reader could cross,
# and it was withdrawn rather than corrected.
SOURCE_ID = "sha256:93c296e97f0eece76286b067f2d11e46fb36204bb051f23769493d3253bf7948"
BUILD_ID = "sha256:28fb5158f4bd62b84fbd154bf1b1de4adfd432cee7b6297f2605332cd3ea1402"
DESCRIPTOR_SHA = "20d1fbd13847cb0ee29ebe81ef8aec419daa548cb5acb94c4c1a53e084e3e0db"
HELPER_SHA = "9478914610631aa1a25164b425ac7463c53a0a951e31db41253960e0ef531c39"
EXPECTED_SEQUENCE = 3
EVIDENCE = Path("/var/lib/metnos-admin/rm0008-release3-evidence-20260910")
EVIDENCE_SHA = {
    "distribution.json":
        "eca5c8c55c87ca2be3e3de8057e437733000c9e529db9dc626295d2083382f0f",
    "distribution.sig":
        "218240b36c095a492ce09c47f8b08c7969c2602f80d69a258ed933f981507804",
}
MAX_EVIDENCE_BYTES = 16_000_000


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise RuntimeError(detail)


def require_pinned() -> None:
    """Refuse by name, not by digest mismatch, while the pins are still empty.

    The build prints every value this needs. Filling them in is the act of
    accepting that exact release for the crossing, so the tool says which one
    is missing instead of failing later on an unrelated comparison.
    """
    missing = [name for name, value in (
        ("SOURCE_ID", SOURCE_ID), ("BUILD_ID", BUILD_ID),
        ("DESCRIPTOR_SHA", DESCRIPTOR_SHA), ("HELPER_SHA", HELPER_SHA),
        ("EVIDENCE_SHA", EVIDENCE_SHA or None)) if not value]
    require(not missing, "pins not updated after the rebuild: " + ", ".join(missing))


def _stamp(info: os.stat_result) -> tuple:
    return tuple(getattr(info, field) for field in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def read_evidence(name: str) -> bytes:
    """Read one build artefact, proving it did not move while being read."""
    for parent in (EVIDENCE, *EVIDENCE.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
                and not info.st_mode & 0o7022, "unsafe evidence parent")
    require(stat.S_IMODE(EVIDENCE.lstat().st_mode) == 0o700,
            "evidence directory mode")
    path = EVIDENCE / name
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode)
                and (before.st_uid, before.st_gid, before.st_nlink,
                     stat.S_IMODE(before.st_mode)) == (0, 0, 1, 0o400)
                and 0 < before.st_size <= MAX_EVIDENCE_BYTES, "unsafe evidence")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(MAX_EVIDENCE_BYTES + 1)
        require(_stamp(before) == _stamp(os.fstat(descriptor))
                == _stamp(path.lstat())
                and len(content) == before.st_size
                and hashlib.sha256(content).hexdigest() == EVIDENCE_SHA[name],
                "evidence changed")
        return content
    finally:
        os.close(descriptor)


def bootstrap():
    require(hasattr(os, "geteuid") and os.geteuid() == 0, "root required")
    require(len(sys.argv) == 2 and sys.argv[1] in {"audit", "complete"},
            "usage: rm0008_complete_release3.py audit|complete")
    descriptor_path = RELEASE / "deployment/executor-birth-deployment-v1.json"
    require(hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
            == DESCRIPTOR_SHA, "release descriptor changed")
    require(hashlib.sha256((RELEASE / "deployment/admin/preflight.py")
                           .read_bytes()).hexdigest() == HELPER_SHA,
            "release helper changed")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(RELEASE), str(RELEASE / "runtime")]
    from executor_birth_account_identity import (
        metnos_xdg_layout_v1, resolve_posix_account_v1,
    )
    account = resolve_posix_account_v1("metnos")
    require((account.uid, account.gid, account.home)
            == (995, 985, "/var/lib/metnos-service"), "service identity changed")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                       "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
                       "METNOS_INSTALL_ROOT": str(RELEASE),
                       **metnos_xdg_layout_v1(account).environment()})

    # Reuse the exact build evidence, authenticated and checked against the
    # installed bytes through the normal product API: nothing is rebuilt and
    # no authority is created here.
    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1,
        verify_installed_distribution_record_v1,
    )
    distribution = verify_installed_distribution_record_v1(
        authenticate_distribution_record_v1(
            read_evidence("distribution.json"),
            read_evidence("distribution.sig")),
    )
    require(distribution.release_sequence == EXPECTED_SEQUENCE
            and distribution.identity.closed_build_id == BUILD_ID,
            "candidate identity changed")
    print("EXACT_SIGNED_SUCCESSOR_VERIFIED", BUILD_ID, flush=True)
    return distribution


def audit(distribution):
    from executor_birth_distribution_assembler import (
        decode_predecessor_descriptor_v1,
    )
    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1,
        capture_current_deployment_descriptor_v1,
        capture_previous_release_artifacts_v1,
    )
    from executor_birth_prepared_root import load_previous_context_runtime_v1
    from executor_birth_service_catalog import (
        load_previous_service_catalog_v1, load_service_catalog_v1,
    )
    from install.birth_authority_provisioner import (
        _observe_previous_retirement_v2,
    )
    from stack_reconcile import StackReconciler, Systemctl

    current = authenticate_distribution_record_v1(
        distribution.encoded, distribution.signature)
    _, descriptor = capture_current_deployment_descriptor_v1(distribution)
    # The whole reason release 3 exists: the administrative gate must run the
    # operating system interpreter, not the product's managed one.
    print("ADMINISTRATIVE_PYTHON", descriptor.python_executable, flush=True)
    require(descriptor.python_executable == os.path.realpath("/usr/bin/python3"),
            "administrative interpreter is not the operating system one")

    previous = load_previous_context_runtime_v1(current)
    old_distribution = previous.selection.distribution
    old = authenticate_distribution_record_v1(
        old_distribution.encoded, old_distribution.signature)
    artifacts = capture_previous_release_artifacts_v1(current, old)
    old_catalog = load_previous_service_catalog_v1(artifacts)
    new_catalog = load_service_catalog_v1(current)
    old_units, new_units = dict(old_catalog.unit_fragments), dict(
        new_catalog.unit_fragments)
    require(old_units.keys() == new_units.keys(), "unit names changed")
    for name, content in old_units.items():
        path = Path(descriptor.system_unit_root) / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode)
                and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
                     info.st_nlink) == (0, 0, 0o644, 1),
                "unit metadata changed: " + name)
        require(path.read_bytes() in (content, new_units[name]),
                "unit content changed: " + name)
    predecessor = decode_predecessor_descriptor_v1(
        Path("/var/lib/metnos/executor-birth/predecessor-v1.json").read_bytes())
    retirement = _observe_previous_retirement_v2(
        new_catalog, old_catalog, predecessor, {
            "system": Path(descriptor.system_unit_root),
            "user": Path("/home/roberto/.config/systemd/user"),
            "repository": Path(predecessor.installation_root),
        })
    idle = StackReconciler(
        systemctl=Systemctl(service_user="roberto"),
        default_write_report=False).require_quiescent()
    require(idle.get("ok") is True, "stack busy or unobservable")
    print("SUCCESSOR_AUDIT_OK", "units", len(old_units),
          "retirement", retirement, "previous_head", previous.required_head_id,
          "quiescence", idle["source"], flush=True)
    return descriptor


def complete(distribution, descriptor):
    from install.executor_birth_transition import (
        _complete_closed_v1, _handoff_frame_v1,
    )
    return _complete_closed_v1(
        expected_source_id=SOURCE_ID,
        expected_service_user="metnos",
        expected_legacy_service_user="roberto",
        expected_legacy_installation_root="/opt/metnos",
        expected_service_state_root=(
            Path(descriptor.service_home) / ".local/state/metnos"),
        frame=_handoff_frame_v1(
            source_id=SOURCE_ID, encoded=distribution.encoded,
            signature=distribution.signature),
    )


if __name__ == "__main__":
    try:
        require_pinned()
        candidate = bootstrap()
        current_descriptor = audit(candidate)
        if sys.argv[1] == "complete":
            print("CUTOVER_OK",
                  json.dumps(complete(candidate, current_descriptor),
                             sort_keys=True), flush=True)
    except Exception as error:
        print("RELEASE3_REFUSED", type(error).__name__,
              getattr(error, "code", ""),
              str(getattr(error, "detail", "") or error)[:350], flush=True)
        for frame in traceback.extract_tb(error.__traceback__)[-10:]:
            print("FRAME", Path(frame.filename).name, frame.lineno,
                  frame.name, flush=True)
        raise SystemExit(78)

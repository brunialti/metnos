#!/usr/bin/python3.12
"""Build and install Release 3 from the reviewed export. No head change.

Root-only, one argument-free run. It stops no service, publishes no head and
crosses nothing: it receives the reviewed source, builds the signed successor
and installs it beside the current one, exactly as Release 2 was built before
its crossing was abandoned. The chain moves only in the separate completion
step that follows.

Everything it trusts is pinned and re-measured here: the installed
administrative helper, the exported tree, the reviewed source root and the
release sequence. A single divergence refuses the build instead of assembling
an unreviewed release.

This is the second reviewed export for sequence 3. The first one built a
release that no reader could cross: three readers on the crossing path did not
know the forward exit from the abandoned Release 2, so they demanded a
completed predecessor that no longer exists. The crossing runs the code of the
installed release, not of the repository, so the correction only arrives by
rebuilding. The pending claim of the first attempt must be withdrawn first:
while it stands the builder refuses every other source.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys
import traceback

SOURCE = Path("/tmp/metnos-release3-export-2")
SOURCE_CENSUS = "10b8ef54f5c404ca0f7a40eb2d939a1e05509eaa24a3826638520ac7d4977109"
SOURCE_FILES = 1747
REVIEW = "sha256:9180f62dcf4ebc73f2f5f498a42e674e2cbacde684813dfe39b8b1825dd5b9ec"
LIVE = Path("/usr/libexec/metnos/executor-birth-v1/preflight.py")
LIVE_SHA = "35b3dc13800799b5dd2278600f9968b78ad19f4a8d7b1c8a64e7d80ca0c15d90"
EXPECTED_SEQUENCE = 3
EVIDENCE = Path("/var/lib/metnos-admin/rm0008-release3-evidence-20260910")


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise RuntimeError(detail)


def source_census() -> tuple[int, str]:
    """Re-measure the exported tree; a changed byte refuses the build.

    The receiver accepts only 0644/0755 files and a single link each, and says
    so as a bare refusal. Checking it here first names the file, which turns a
    staging slip into a one-line fix instead of a search.
    """
    for path in sorted(SOURCE.rglob("*")):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), f"symlink in the source: {path}")
        if stat.S_ISDIR(info.st_mode):
            require(stat.S_IMODE(info.st_mode) == 0o755,
                    f"directory mode {stat.S_IMODE(info.st_mode):o}: {path}")
        else:
            require(stat.S_IMODE(info.st_mode) in {0o644, 0o755}
                    and info.st_nlink == 1,
                    f"file mode {stat.S_IMODE(info.st_mode):o} "
                    f"links {info.st_nlink}: {path}")
    files = sorted(path for path in SOURCE.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(SOURCE).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return len(files), digest.hexdigest()


def save_evidence(distribution) -> None:
    for parent in (EVIDENCE.parent, *EVIDENCE.parent.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
                and not info.st_mode & 0o7022, "unsafe evidence parent")
    if not os.path.lexists(EVIDENCE):
        EVIDENCE.mkdir(mode=0o700)
    info = EVIDENCE.lstat()
    require(stat.S_ISDIR(info.st_mode) and
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
            "unsafe evidence directory")
    require(not {path.name for path in EVIDENCE.iterdir()}
            - {"distribution.json", "distribution.sig"}, "unexpected evidence")
    for name, content in (("distribution.json", distribution.encoded),
                          ("distribution.sig", distribution.signature)):
        path = EVIDENCE / name
        if os.path.lexists(path):
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and
                    (info.st_uid, info.st_gid, info.st_nlink,
                     stat.S_IMODE(info.st_mode)) == (0, 0, 1, 0o400)
                    and path.read_bytes() == content, "evidence changed")
        else:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                | os.O_CLOEXEC, 0o400)
            try:
                offset = 0
                while offset < len(content):
                    written = os.write(descriptor, content[offset:])
                    require(written > 0, "short evidence write")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        print("SAVED_EVIDENCE_SHA256", name,
              hashlib.sha256(content).hexdigest(), flush=True)
    for parent in (EVIDENCE, EVIDENCE.parent):
        descriptor = os.open(
            parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def main() -> int:
    require(len(sys.argv) == 1, "zero arguments required")
    require(hasattr(os, "geteuid") and os.geteuid() == 0, "root required")
    require(hashlib.sha256(LIVE.read_bytes()).hexdigest() == LIVE_SHA,
            "live helper changed; no build attempted")
    count, census = source_census()
    require((count, census) == (SOURCE_FILES, SOURCE_CENSUS),
            f"exported tree changed: {count} files, {census}")
    print("SOURCE_CENSUS_OK", count, census, flush=True)

    # Importing the reviewed source must not add bytecode to it: the census
    # above is the proof that the tree is the reviewed one, and a .pyc written
    # during the build would falsify it for every later reader.
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(SOURCE), str(SOURCE / "runtime")]
    from executor_birth_account_identity import (
        metnos_xdg_layout_v1, resolve_posix_account_v1,
    )
    account = resolve_posix_account_v1("metnos")
    require((account.uid, account.gid, account.home)
            == (995, 985, "/var/lib/metnos-service"), "service identity changed")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                       "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
                       "METNOS_INSTALL_ROOT": str(SOURCE),
                       **metnos_xdg_layout_v1(account).environment()})

    import contract_boundary_guard as guard
    inventory = guard.load_inventory(SOURCE / guard.DEFAULT_INVENTORY)
    findings = guard.birth_closed_findings(guard.discover(SOURCE), inventory)
    require(not findings and guard.BIRTH_CLOSED_SOURCE_REVIEW_SHA256 == REVIEW
            and guard.closed_python_source_review_finding(SOURCE) is None,
            "reviewed candidate changed")
    print("REVIEWED_CANDIDATE_OK", REVIEW, flush=True)

    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1,
    )
    from executor_birth_prepared_root import load_previous_context_runtime_v1
    from install.executor_birth_distribution_release import (
        build_and_install_received_source_v1,
    )
    from install.executor_birth_source_receiver import _receive_source_v1

    source_id = _receive_source_v1(str(SOURCE), account.name)
    print("RECEIVED_SOURCE", source_id, flush=True)
    distribution = build_and_install_received_source_v1(source_id)
    print("SIGNED_SUCCESSOR_BUILT", distribution.release_sequence,
          distribution.identity.closed_build_id,
          distribution.installation_root, flush=True)
    require(distribution.release_sequence == EXPECTED_SEQUENCE,
            f"unexpected release sequence {distribution.release_sequence}; "
            "no activation attempted")

    record = authenticate_distribution_record_v1(
        distribution.encoded, distribution.signature)
    previous = load_previous_context_runtime_v1(record)
    require(isinstance(previous.required_head_id, str)
            and previous.required_head_id.startswith("sha256:"),
            "previous context has no required head")
    print("REQUIRED_HEAD", previous.required_head_id, flush=True)

    require(hashlib.sha256(LIVE.read_bytes()).hexdigest() == LIVE_SHA,
            "live helper changed during build")
    save_evidence(distribution)
    descriptor = (Path(distribution.installation_root)
                  / "deployment/executor-birth-deployment-v1.json")
    print("SUCCESSOR_DESCRIPTOR_SHA256",
          hashlib.sha256(descriptor.read_bytes()).hexdigest(), flush=True)
    print("BUILD_ONLY_OK; NO HEAD CHANGE OR SERVICE STOP", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("RELEASE3_BUILD_REFUSED", type(error).__name__,
              getattr(error, "code", ""),
              str(getattr(error, "detail", "") or error)[:500], flush=True)
        for frame in traceback.extract_tb(error.__traceback__)[-10:]:
            print("FRAME", Path(frame.filename).name, frame.lineno,
                  frame.name, flush=True)
        raise SystemExit(78)

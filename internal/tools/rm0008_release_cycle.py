#!/usr/bin/python3.12
"""One release cycle in one command: `prepare`, then `apply`.

Why this exists. Every change to the birth code used to need seven manual steps
with six digests copied by hand between them: realign the reviewed roots,
regenerate and seal the export, stage it, retype the census into the builder,
withdraw the previous claim, retype the build's identities into the crossing
tool, cross. Three cycles were lost to that ritual in one day and none of them
to a real defect. A digest a person retypes is not a review, it is a chance to
be wrong. The review that matters is of the source tree, and it is expressed
once, by running this.

Two stages, split by the privilege each actually needs.

  prepare            any user. Realigns the reviewed roots, regenerates the
                     signed Python inventory, rebuilds and seals the export,
                     stages it with the modes the receiver accepts, measures it
                     and writes a handoff beside it. Touches only the worktree
                     and the staging directory.

  apply [--cross]    root. Re-measures the staged tree, receives it, withdraws
                     a pending claim that names a superseded source, builds and
                     installs the signed successor, and audits it. With
                     --cross it then performs the crossing, which stops and
                     restarts the services, then admits changed executors as
                     the service user through the installed deploy CLI.
                     The audit runs a read-only preview using the explicit
                     previous-context reader, without selecting N+1 or
                     invoking its live bootstrap (review C15). After the verified
                     cutover the plan runs first and admission follows only
                     if it succeeds; a refused plan ships the release and
                     admits nothing. A later admission/activation refusal
                     does not undo the crossing.

Nothing in the handoff is authority. The staged tree is measured again in
`apply`, the product's own reviewed-root gate still runs inside the build, and
the distribution is signed by the product's key. The handoff only lets this
tool refuse a tree that changed between the two stages.

The build must import the candidate's code and the crossing must import the
newly installed release's code. Both cannot live in one interpreter, so the
crossing runs as a child of this same file: the identities reach it as
arguments the build produced, never as constants a person maintains.
"""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
import dataclasses
import fcntl
import hashlib
import builtins
import json
import re
import os
import secrets
from pathlib import Path
import pwd
import stat
import subprocess
import sys


def _preparer() -> pwd.struct_passwd:
    """The account that prepares a release, seen from either stage.

    `prepare` runs as the developer and `apply` as root through `sudo`, and
    the two must agree on one directory without either of them guessing a
    name. `SUDO_UID` is the account sudo was invoked from; outside sudo the
    caller is the account itself.
    """
    value = os.environ.get("SUDO_UID", "")
    if os.geteuid() == 0 and value.isdigit():
        return pwd.getpwuid(int(value))
    return pwd.getpwuid(os.getuid())


WORKTREE = Path(__file__).resolve().parents[2]
# Not /tmp: a world-writable directory puts every local account in the trust
# set of a command that runs as root.  The developer's own state directory
# has no such visitors, and `apply` proves it before reading anything.
PREPARER = _preparer()
CYCLE_DIR = Path(PREPARER.pw_dir) / ".local/state/metnos-release-cycle"
STAGING = CYCLE_DIR / "export"
HANDOFF = CYCLE_DIR / "handoff.json"
EVIDENCE_ROOT = Path("/var/lib/metnos-admin")
CANDIDATE_ROOT = Path("/var/lib/metnos-admin")
ROOT = Path("/var/lib/metnos/executor-birth")
COORD = ROOT / "coordinator-v1"
BIRTH = Path("/var/lib/metnos-service/.config/metnos/birth")
LIVE_HELPER = Path("/usr/libexec/metnos/executor-birth-v1/preflight.py")
WITHDRAWN_ROOT = Path("/var/lib/metnos-admin/rm0008-withdrawn-claims")
OWNERS = {(0, 0)}
# The coordinator is root's; the rehearsal reads a copy the developer owns.
ROOT_OWNED = True
# The birth root belongs to the service account; its parents to root.
BIRTH_OWNERS = {(0, 0), (995, 985)}
JOURNAL_PREFIX = ".birth-provisioning-v2.txn."
SUPERSEDED_JOURNAL_PREFIX = ".birth-provisioning-v2.superseded."
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def require(condition: object, detail: str) -> None:
    if not condition:
        raise RuntimeError(detail)


def say(*parts: object) -> None:
    print(*parts, flush=True)


# --------------------------------------------------------------------------
# measurement shared by both stages
# --------------------------------------------------------------------------

def census(tree: Path) -> tuple[int, str]:
    """Measure the staged tree exactly as the receiver will read it.

    The receiver accepts only 0644/0755 files with a single link each and no
    symlinks, and says so as a bare refusal. Checking it here names the file,
    which turns a staging slip into a one-line fix instead of a search.
    """
    for path in sorted(tree.rglob("*")):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), f"symlink in the source: {path}")
        if stat.S_ISDIR(info.st_mode):
            require(stat.S_IMODE(info.st_mode) == 0o755,
                    f"directory mode {stat.S_IMODE(info.st_mode):o}: {path}")
            continue
        require(path.suffix not in {".pyc", ".pyo"}, f"bytecode staged: {path}")
        require(stat.S_IMODE(info.st_mode) in {0o644, 0o755} and info.st_nlink == 1,
                f"file mode {stat.S_IMODE(info.st_mode):o} "
                f"links {info.st_nlink}: {path}")
    files = sorted(path for path in tree.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(tree).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return len(files), digest.hexdigest()


def stamp(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_parent(path: Path, owners: set | None = None) -> int:
    for item in (path, *path.parents):
        info = item.lstat()
        require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o7022
                and (info.st_uid, info.st_gid) in (owners or OWNERS),
                "unsafe parent")
    handle = os.open(path, READ | os.O_DIRECTORY)
    try:
        require(stamp(os.fstat(handle)) == stamp(path.lstat()), "parent changed")
        return handle
    except BaseException:
        os.close(handle)
        raise


def snapshot(path: Path, owners: set | None = None) -> dict:
    """Hash exact inode metadata and content; never expose confidential bytes."""
    rows, total = [], 0
    accepted_owners = owners or OWNERS
    parent = open_parent(path.parent, accepted_owners)
    device = os.fstat(parent).st_dev

    def visit(container: int, name: str, relative: str) -> None:
        nonlocal total
        handle = os.open(name, READ, dir_fd=container)
        try:
            before = os.fstat(handle)
            require((before.st_uid, before.st_gid) in accepted_owners
                    and before.st_dev == device and not before.st_mode & 0o7022
                    and not os.listxattr(handle), "unsafe member metadata")
            row = [relative, *stamp(before)[:-1]]
            if stat.S_ISREG(before.st_mode):
                total += before.st_size
                require(before.st_nlink == 1 and before.st_size <= 16000000
                        and total <= 96000000, "file bound or hardlink")
                digest = hashlib.sha256()
                remaining = before.st_size
                while remaining:
                    block = os.read(handle, min(65536, remaining))
                    require(block, "short read")
                    remaining -= len(block)
                    digest.update(block)
                require(not os.read(handle, 1), "file grew")
                row.append(digest.hexdigest())
                rows.append(row)
            else:
                require(stat.S_ISDIR(before.st_mode), "unexpected member type")
                names = sorted(os.listdir(handle), key=os.fsencode)
                rows.append(row)
                require(len(rows) + len(names) <= 6000, "tree bound")
                for child in names:
                    visit(handle, child,
                          child if relative == "." else relative + "/" + child)
                require(names == sorted(os.listdir(handle), key=os.fsencode),
                        "inventory changed")
            require(stamp(before) == stamp(os.fstat(handle))
                    == stamp(os.stat(name, dir_fd=container, follow_symlinks=False)),
                    "member changed")
        finally:
            os.close(handle)

    try:
        visit(parent, path.name, ".")
        payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
        return {"sha256": hashlib.sha256(payload.encode("ascii")).hexdigest(),
                "objects": len(rows), "bytes": total}
    finally:
        os.close(parent)


# --------------------------------------------------------------------------
# stage 1: prepare
# --------------------------------------------------------------------------

def run_in_worktree(*command: str) -> None:
    environment = {**os.environ, "METNOS_VENV": "/opt/metnos/.venv"}
    result = subprocess.run(command, cwd=WORKTREE, env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", "replace")[-4000:])
        raise RuntimeError("step failed: " + " ".join(command))


# The copy/module check is advisory: its one probe never gets to hold prepare.
_MODULE_MAP_TIMEOUT_S = 10
_MODULE_MAP_PROBE = """\
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("generator", sys.argv[1])
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
root = Path.cwd().resolve()
print(json.dumps({name: path.resolve().relative_to(root).as_posix()
                  for name, (_spec, path) in generator._all_specs().items()}))
"""


def builtin_module_map(python: str) -> dict[str, str] | None:
    """Each builtin's own module, exactly as the generator pairs them."""
    try:
        result = subprocess.run(
            [python, "-c", _MODULE_MAP_PROBE, "scripts/generate_builtin_executor_contracts.py"],
            cwd=WORKTREE, env={**os.environ, "METNOS_VENV": "/opt/metnos/.venv"},
            stdin=subprocess.DEVNULL, capture_output=True, timeout=_MODULE_MAP_TIMEOUT_S)
        modules = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        modules = None
    return modules if isinstance(modules, dict) else None


def builtin_copy_drift(tree: Path, modules: dict[str, str]) -> list[str]:
    """Builtins whose copy in ``tree`` differs from their own module."""
    drift = []
    for name, module in sorted(modules.items()):
        copy = tree / "runtime/builtin_executor_contracts" / name / "implementation.py.src"
        try:
            same = copy.read_bytes() == (tree / module).read_bytes()
        except OSError:
            same = False
        if not same:
            drift.append(name)
    return drift


def prepare() -> int:
    require(os.geteuid() != 0, "prepare runs as the developer, never as root")
    python = "/opt/metnos/.venv/bin/python"
    say("== builtin contracts ==")
    # The admitted copy of each builtin is derived from the reviewed code
    # before any pin or export; the release publishes it later through Birth.
    run_in_worktree(python, "scripts/generate_builtin_executor_contracts.py")
    say("== reviewed roots ==")
    run_in_worktree(python, "internal/tools/rm0008_repin_source_roots.py")
    private, public = reviewed_roots()
    say("   private", private[0], private[1], "public", public[0], public[1])

    say("== signed Python inventory ==")
    run_in_worktree(python,
                    "tests/portable/rm0008_2a_acceptance/"
                    "generate_production_inventory_v1.py", "--write")

    say("== export ==")
    scratch = Path("/tmp/metnos-release-cycle-raw")
    run_in_worktree("bash", "scripts/export-public.sh", str(scratch))
    # The loader refuses a builtin whose copy differs from its module: say so
    # now, in seconds, instead of after a crossing.  A warning, never a stop.
    modules = builtin_module_map(python)
    drift = None if modules is None else builtin_copy_drift(scratch, modules)
    if drift is None:
        say("   WARNING builtin module map unavailable: copies not compared")
    elif drift:
        say("   WARNING builtin copy differs from its module:", ", ".join(drift))
    run_in_worktree(python, "-I", "-S",
                    str(scratch / "scripts/check_contract_boundary_policy.py"))
    run_in_worktree(python, "internal/tools/rm0008_public_source_review.py",
                    "public-fs-pin", str(scratch), public[1], str(public[0]),
                    private[1])

    say("== staging ==")
    # 0700, and the preparer's own: what `apply` will later read as root must
    # not be a directory other accounts can write.
    CYCLE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CYCLE_DIR.chmod(0o700)
    stage(scratch, STAGING)
    files, digest = census(STAGING)
    HANDOFF.write_text(json.dumps({
        "staging": str(STAGING), "files": files, "census": digest,
        "reviewed_root": public[1],
    }, indent=2, sort_keys=True) + "\n")
    HANDOFF.chmod(0o644)
    say("   files", files)
    say("   census", digest)
    say("PREPARE_OK", STAGING)
    return 0


def reviewed_roots() -> tuple[tuple[int, str], tuple[int, str]]:
    """Read the roots the publisher owns; this tool never chooses them."""
    text = (WORKTREE / "scripts/publish-public.sh").read_text()
    values = {}
    for line in text.splitlines():
        for name in ("PRIVATE_SOURCE_REVIEW_SHA256", "PRIVATE_SOURCE_REVIEW_COUNT",
                     "PUBLIC_SOURCE_REVIEW_SHA256", "PUBLIC_SOURCE_REVIEW_COUNT"):
            if line.startswith(name + "="):
                values[name] = line.split("=", 1)[1].strip().strip('"')
    require(len(values) == 4, "reviewed roots not found in publish-public.sh")
    return ((int(values["PRIVATE_SOURCE_REVIEW_COUNT"]),
             values["PRIVATE_SOURCE_REVIEW_SHA256"]),
            (int(values["PUBLIC_SOURCE_REVIEW_COUNT"]),
             values["PUBLIC_SOURCE_REVIEW_SHA256"]))


def stage(source: Path, destination: Path) -> None:
    """Copy the sealed export into the shape the receiver accepts."""
    subprocess.run(["rm", "-rf", str(destination)], check=True)
    subprocess.run(["cp", "-a", str(source), str(destination)], check=True)
    for path in sorted(destination.rglob("*"), reverse=True):
        if path.is_dir() and path.name == "__pycache__":
            subprocess.run(["rm", "-rf", str(path)], check=True)
    for path in destination.rglob("*"):
        if path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()
    for path in destination.rglob("*"):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            path.chmod(0o755)
        elif stat.S_ISREG(info.st_mode):
            path.chmod(0o755 if info.st_mode & 0o100 else 0o644)
    destination.chmod(0o755)


# --------------------------------------------------------------------------
# stage 2: apply
# --------------------------------------------------------------------------

def acquire_locks(stack: ExitStack) -> None:
    for path, owner, modes in (
        (ROOT / "ownership-deployment-v1.lock", (0, 0), {0o600}),
        (Path("/run/metnos-executor-birth-v1/startup-v1.lock"), (0, 0), {0o600}),
        (BIRTH / "provisioning-v1.lock", (995, 985), {0o600, 0o644}),
    ):
        handle = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        stack.callback(os.close, handle)
        info = os.fstat(handle)
        require(stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid) == owner
                and stat.S_IMODE(info.st_mode) in modes and info.st_nlink == 1,
                "unsafe lock")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(stamp(info) == stamp(path.lstat()), "lock rebound")


def preserved_paths() -> tuple[Path, ...]:
    """Everything a withdrawal must leave exactly as it found it, read now."""
    chain = ROOT / "chain-v1"
    members = tuple(sorted(
        path for path in chain.iterdir()
        if not path.name.startswith(".")
    ))
    return (
        *members,
        ROOT / "preflight-attestations-v1",
        ROOT / "startup-prerequisites-v1",
        *sorted((COORD / "transactions-v2").iterdir()),
        COORD / "abandoned-crossings-v2",
    )


def pending_claims() -> tuple[tuple[Path, dict], ...]:
    """A claim with no transaction of its own is a reservation nobody opened."""
    opened = {path.name for path in (COORD / "transactions-v2").iterdir()}
    found = []
    for path in sorted((COORD / "successor-claims-v1").iterdir()):
        value = json.loads(path.read_bytes())
        if value["request_id"] not in opened:
            found.append((path, value))
    return tuple(found)


def startup_fingerprint() -> tuple:
    """What a service start would see; a refusal is an observation, not a stop."""
    module = load_live_helper()
    try:
        materials, _entry = module._attest_service_startup_v1("service-http")
    except Exception as error:  # noqa: BLE001 - the refusal is the observation
        return ("refused", type(error).__name__,
                str(getattr(error, "code", "") or error)[:80])
    facts = materials.distribution.facts
    return ("attested", facts.release_sequence, materials.transaction.head_id)


def load_live_helper():
    payload = LIVE_HELPER.read_bytes()
    module = type(sys)("rm0008_cycle_startup_probe")
    sys.modules[module.__name__] = module
    exec(compile(payload, str(LIVE_HELPER), "exec"), module.__dict__)
    return module


def rename_no_replace(source: Path, target: Path, owners: set | None = None) -> None:
    left = open_parent(source.parent, owners)
    right = open_parent(target.parent, owners)
    try:
        require(os.fstat(left).st_dev == os.fstat(right).st_dev,
                "cross-device archive")
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(left, os.fsencode(source.name), right,
                  os.fsencode(target.name), 1) != 0:
            raise OSError(ctypes.get_errno(), "no-replace rename refused")
        os.fsync(left)
        os.fsync(right)
    finally:
        os.close(right)
        os.close(left)


def withdraw_superseded_claim(source_id: str) -> str | None:
    """Retire the reservation of an attempt this cycle replaces; move nothing else.

    Two objects move, the claim last: while the claim stands the builder refuses
    every other source, so an interruption leaves a refusal rather than a
    half-open chain. Nothing is deleted; both are preserved under an archive.

    Every identity is read from the chain in this run. The claim must be a
    reservation nobody opened, it must name a source other than the one being
    built, and the release directory it reserved must be the one it built.
    """
    claims = pending_claims()
    if not claims:
        return None
    require(len(claims) == 1, "more than one pending claim")
    path, claim = claims[0]
    if claim["source_id"] == source_id:
        return None
    release = ROOT / "releases-v1" / f"{claim['release_sequence']:020d}"
    # Named by the attempt, not by the head it would have succeeded. The head
    # does not move while attempts fail, so naming the archive after it made
    # the second withdrawal collide with the first.
    archive = WITHDRAWN_ROOT / claim["request_id"][7:]
    parked = archive / "unselected-release"
    # Stopped between the two moves (review O-04): the release already sits in
    # THIS attempt's archive while the claim still stands. That state is
    # resumed, not refused - but only when the parked release is exactly the
    # one this claim reserved. An archive is named by its own attempt, so
    # another attempt's archive is never this path, and a release missing
    # from both places is still a refusal.
    resumed = (not os.path.lexists(release)
               and parked.is_dir() and not parked.is_symlink())
    reserved = parked if resumed else release
    require(reserved.is_dir(), "the pending claim reserved no release directory")
    descriptor = json.loads(
        (reserved / "deployment/executor-birth-deployment-v1.json").read_bytes())
    require(descriptor.get("release_sequence") == claim["release_sequence"],
            "the reserved release is not the one the claim names")

    before = {str(item): snapshot(item) for item in preserved_paths()}
    first = startup_fingerprint()
    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = archive.lstat()
    require((info.st_uid, info.st_gid) in OWNERS
            and stat.S_IMODE(info.st_mode) == 0o700, "unsafe archive")
    claim_slot = archive / "successor-claim.json"
    pairs = (((path, claim_slot),) if resumed
             else ((release, parked), (path, claim_slot)))
    if resumed:
        say("RESUMING_WITHDRAWAL", claim["source_id"])
    for origin, target in pairs:
        require(not os.path.lexists(target), "archive slot already taken")
    for origin, target in pairs:
        rename_no_replace(origin, target)
    after = {str(item): snapshot(item) for item in preserved_paths()}
    require(after == before, "preserved history changed during the withdrawal")
    require(startup_fingerprint() == first, "service startup selection moved")
    say("WITHDREW_CLAIM", claim["source_id"], "->", str(archive))
    # The request, not the source: it is what binds this attempt to the
    # provisioning journal it opened before the coordinator recorded anything.
    return claim["request_id"]


def withdraw_superseded_prepared(source_id: str) -> str | None:
    """Archive an unselected PREPARED attempt, with its claim moved last.

    No selected record, receipt, authority set or producer decision is reset.
    The canonical reader must prove sequence zero and the running predecessor
    must still attest. A new source is required; replaying the failed source
    is not recovery. Each interrupted prefix resumes from its exact archive.
    The caller holds deployment, startup and provisioning locks.
    """
    from executor_birth_ownership_coordinator import (
        _decode_record_v2, _resolve_ownership_coordinator_at_v2,
        _successor_claim_basename_v1, OwnershipCoordinatorStateV1,
    )
    from install.birth_authority_provisioner import decode_transaction_header_v2

    graph = _resolve_ownership_coordinator_at_v2(COORD, root_owned=ROOT_OWNED)
    candidate = None
    if graph.transactions:
        last = graph.transactions[-1]
        if last.latest.state is OwnershipCoordinatorStateV1.PREPARED:
            candidate = last.claim
    resumed = [claim for claim in graph.pending_claims if (
        WITHDRAWN_ROOT / claim.request_id[7:] / "prepared-transaction").exists()]
    require(len(resumed) <= 1 and not (resumed and candidate),
            "multiple prepared withdrawals")
    candidate = candidate or (resumed[0] if resumed else None)
    if candidate is None or candidate.source_id == source_id:
        return None
    claim = candidate
    require(claim.previous_head_id is not None, "initial release is not withdrawable")
    archive = WITHDRAWN_ROOT / claim.request_id[7:]
    transaction = COORD / "transactions-v2" / claim.request_id

    def location(origin, target):
        live, saved = os.path.lexists(origin), os.path.lexists(target)
        require(live != saved, "duplicate or missing prepared withdrawal object")
        return target if saved else origin

    record_path = location(transaction, archive / "prepared-transaction")
    snapshot(record_path)
    require({item.name for item in record_path.iterdir()} == {"record-000-v2.json"},
            "prepared attempt advanced")
    record = _decode_record_v2((record_path / "record-000-v2.json").read_bytes())
    require(record.state is OwnershipCoordinatorStateV1.PREPARED
            and record.sequence == 0 and record.current_proof is None
            and record.head_id is None and record.certificate_payload_hash is None
            and (record.request_id, record.source_id, record.closed_build_id,
                 record.successor_claim_id, record.previous_head_id,
                 record.release_sequence) == (
                     claim.request_id, claim.source_id, claim.closed_build_id,
                     claim.claim_id, claim.previous_head_id, claim.release_sequence),
            "prepared attempt does not bind the claim")
    journal = BIRTH / (JOURNAL_PREFIX + record.provisioning_transaction_id)
    release = ROOT / "releases-v1" / f"{claim.release_sequence:020d}"
    claim_path = COORD / "successor-claims-v1" / _successor_claim_basename_v1(
        claim.release_sequence, claim.previous_head_id)
    pairs = ((transaction, archive / "prepared-transaction"),
             (journal, archive / "unselected-birth-journal"),
             (release, archive / "unselected-release"),
             (claim_path, archive / "successor-claim.json"))
    paths = [location(*pair) for pair in pairs]
    moved = [path == pair[1] for path, pair in zip(paths, pairs)]
    require(moved == sorted(moved, reverse=True), "non-monotone prepared withdrawal")
    header = decode_transaction_header_v2((paths[1] / "transaction-v2.json").read_bytes())
    require((header.request_id, header.closed_build_id, header.transaction_id,
             header.distribution_payload_hash, header.distribution_signature_hash) == (
                record.request_id, record.closed_build_id, record.provisioning_transaction_id,
                record.distribution_payload_hash, record.distribution_signature_hash),
            "prepared journal identity changed")
    descriptor = json.loads((paths[2] / "deployment/executor-birth-deployment-v1.json").read_bytes())
    require(descriptor.get("release_sequence") == claim.release_sequence
            and descriptor.get("descriptor_id") == record.deployment_descriptor_id,
            "prepared release identity changed")
    require(json.loads(paths[3].read_bytes()) == claim.as_value(),
            "prepared claim identity changed")
    first = startup_fingerprint()
    require(first == ("attested", claim.release_sequence - 1, claim.previous_head_id),
            "prepared predecessor is not the selected release")
    preserved = tuple(item for item in preserved_paths() if item != transaction)
    before = {str(item): snapshot(item) for item in preserved}
    pins = [snapshot(path, BIRTH_OWNERS if index == 1 else OWNERS)
            for index, path in enumerate(paths)]
    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = archive.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) in OWNERS
            and stat.S_IMODE(info.st_mode) == 0o700, "unsafe prepared archive")
    require({item.name for item in archive.iterdir()} == {
        pair[1].name for pair, saved in zip(pairs, moved) if saved},
        "prepared archive inventory changed")

    def unchanged():
        require({str(item): snapshot(item) for item in preserved} == before,
                "selected history changed during prepared withdrawal")
        require(startup_fingerprint() == first, "prepared startup selection moved")

    for index, (origin, target) in enumerate(pairs):
        owners = BIRTH_OWNERS if index == 1 else OWNERS
        if not moved[index]:
            unchanged()
            require(snapshot(origin, owners) == pins[index], "prepared object changed")
            rename_no_replace(origin, target, owners)
        require(snapshot(target, owners) == pins[index], "prepared archive changed")
        # Also flush a rename completed just before an interrupted fsync.
        for parent in (origin.parent, target.parent):
            fd = open_parent(parent, owners)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    unchanged()
    say("WITHDREW_UNSELECTED_PREPARED", claim.request_id, "->", str(archive))
    return claim.request_id


def withdraw_unclaimed_release(source_id: str) -> str | None:
    """Park the release in the slot the builder fills next; move nothing else.

    A crossing refused before the coordinator records anything (Release 26,
    12/9: the audit refused after the build) leaves its release installed and
    no claim naming it. The builder numbers the next release from the chain,
    not from the directories, so it targets that same name and the no-replace
    publication refuses it for ever.

    The slot is the builder's own: its reader of the coordinator and its edge,
    taken under the same locks, so no second numbering rule exists. Any other
    directory is not what blocks the build and stays where it is. The slot is
    parked only when no claim stands or names it, it carries its own number in
    its descriptor and the service is positively attested to start another
    release. Nothing is deleted: one no-replace rename moves it under an
    archive named by its number and the census of the whole tree, which is
    measured again once it has moved.
    """
    from install.executor_birth_distribution_release import (
        _next_release_edge_v1, _resolve_ownership_coordinator_at_v2,
    )
    graph = _resolve_ownership_coordinator_at_v2(COORD, root_owned=ROOT_OWNED)
    # A standing claim names its own slot, and the builder resumes it.
    if graph.pending_claims:
        return None
    sequence = _next_release_edge_v1(graph, source_id).sequence
    release = ROOT / "releases-v1" / f"{sequence:020d}"
    if (not os.path.lexists(release)
            or sequence in {claim.release_sequence for claim in graph.claims}):
        return None
    require(stat.S_ISDIR(release.lstat().st_mode),
            "the unclaimed release is not a directory")
    # The census refuses links anywhere below, the descriptor included.
    measured = census(release)
    descriptor = release / "deployment/executor-birth-deployment-v1.json"
    require(stat.S_ISREG(descriptor.lstat().st_mode),
            "the unclaimed release has no descriptor")
    require(json.loads(descriptor.read_bytes()).get("release_sequence") == sequence,
            "the unclaimed release is not the one its name says")
    first = startup_fingerprint()
    # Only a positive attestation says which release starts; a refusal, even
    # a stable one, is a refusal here rather than a licence to move.
    require(first[0] == "attested" and first[1] != sequence,
            "the unclaimed release may be the one the service starts")

    before = {str(item): snapshot(item) for item in preserved_paths()}
    # The descriptor binds units and preflight, not every published file:
    # two builds in one slot can share it, so the whole census names the
    # archive.
    archive = WITHDRAWN_ROOT / f"unclaimed-{release.name}-{measured[1][:16]}"
    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = archive.lstat()
    require((folder.st_uid, folder.st_gid) in OWNERS
            and stat.S_IMODE(folder.st_mode) == 0o700, "unsafe archive")
    parked = archive / "unselected-release"
    require(not os.path.lexists(parked), "archive slot already taken")
    rename_no_replace(release, parked)
    require(census(parked) == measured, "the parked release measures differently")
    after = {str(item): snapshot(item) for item in preserved_paths()}
    require(after == before, "preserved history changed during the withdrawal")
    require(startup_fingerprint() == first, "service startup selection moved")
    say("WITHDREW_UNCLAIMED_RELEASE", sequence, "->", str(archive))
    return str(archive)


def withdrawn_requests() -> set[str]:
    """Attempts this machine has already withdrawn, read from disk.

    The link between a withdrawn claim and its journal used to live only in
    memory. An interruption between the two moves - the claim archived, the
    journal not yet retired - left the next run treating that journal as
    foreign and refusing for ever, while the proof was sitting on disk the
    whole time: the archive holds the claim itself. The resume reads it.
    """
    found: set[str] = set()
    if not WITHDRAWN_ROOT.is_dir():
        return found
    for archive in sorted(WITHDRAWN_ROOT.iterdir()):
        claim = archive / "successor-claim.json"
        if not claim.is_file():
            continue
        request = json.loads(claim.read_bytes()).get("request_id")
        if isinstance(request, str) and request:
            found.add(request)
    return found


def retire_orphan_journals(withdrawn_request: str | None) -> tuple[str, ...]:
    """Retire the journal of the attempt this run withdrew; refuse any other.

    A failed attempt leaves three things: a claim, an installed release and a
    provisioning journal, opened before the coordinator records anything.
    Withdrawing the first two and leaving the third makes the next crossing
    adopt a journal written for another release and refuse - twice now.

    The absence of a transaction says the coordinator never recorded that
    request. It does not say whose the residue is, so on its own it is not a
    licence to move it: only the attempt this run has just withdrawn is
    retired, matched on the exact request. A journal the coordinator knows -
    abandoned crossings included - is never touched, and anything else stops
    the run instead of being guessed. The header is read with the canonical
    decoder, so a malformed one is a refusal, not an orphan. Nothing is
    deleted: a retired journal is renamed under a prefix that says so.
    """
    from install.birth_authority_provisioner import decode_transaction_header_v2

    known = {name[7:] if name.startswith("sha256:") else name
             for name in os.listdir(COORD / "transactions-v2")}
    # The attempts this run withdrew, plus the ones any earlier run did: an
    # interruption between the two moves must not turn a journal we ourselves
    # orphaned into a residue nobody may touch.
    retirable = withdrawn_requests()
    if withdrawn_request:
        retirable.add(withdrawn_request)
    retired = []
    for name in sorted(os.listdir(BIRTH)):
        if not name.startswith(JOURNAL_PREFIX):
            continue
        request = decode_transaction_header_v2(
            (BIRTH / name / "transaction-v2.json").read_bytes()).request_id
        if request.startswith("sha256:") and request[7:] in known:
            continue
        require(request in retirable,
                f"open journal of an attempt no withdrawal accounts for: {name}")
        target = BIRTH / (SUPERSEDED_JOURNAL_PREFIX + name[len(JOURNAL_PREFIX):])
        require(not os.path.lexists(target), "superseded journal slot already taken")
        rename_no_replace(BIRTH / name, target, BIRTH_OWNERS)
        retired.append(name)
    return tuple(retired)


def unique_sibling(path: Path, tag: str) -> Path:
    """A free name beside `path`; nothing is ever replaced or deleted."""
    for attempt in range(1, 100):
        candidate = path.with_name(f"{path.name}.{tag}-{attempt:02d}")
        if not os.path.lexists(candidate):
            return candidate
    raise RuntimeError(f"too many {tag} objects beside {path}")


def publish_evidence(distribution, directory: Path) -> None:
    """Write the evidence pair whole, verify one already there, resume a torn one.

    Presence is not proof. The pair is two separate writes: an attempt
    interrupted between them leaves the directory holding one file of two.
    Skipping on presence alone handed the next step half an evidence set;
    refusing on it, which was the first correction, only turned a torn write
    into a permanently stuck retry - the evidence is derived from the build
    just made, so there is nothing to lose and no reason to stop.

    So: an identical pair is reused, a torn one is moved aside under a name
    that says what it is - never deleted - and written again, and a pair that
    contradicts this build still stops the run, because one build cannot have
    two contents.

    Torn is not only a missing name. A write interrupted mid-file leaves both
    names present and one of them short, which the first correction read as a
    contradiction and refused for ever: two consecutive retries stayed stuck
    on the same directory. Shorter than expected is the signature of an
    interrupted write; the same length with different bytes, or longer, is a
    contradiction and stays one.
    """
    expected = {"distribution.json": distribution.encoded,
                "distribution.sig": distribution.signature}
    if directory.exists():
        present = sorted(item.name for item in directory.iterdir())
        content = {name: (directory / name).read_bytes()
                   for name in present if name in expected}
        if present == sorted(expected):
            if content == expected:
                return
            # Nomi tutti presenti: e' una scrittura interrotta se OGNI file e'
            # quello atteso oppure piu' corto di esso. Stessa lunghezza con
            # byte diversi, o piu' lungo, resta una contraddizione.
            for name, seen in sorted(content.items()):
                require(seen == expected[name]
                        or len(seen) < len(expected[name]),
                        f"release evidence is not this build: {name}")
        aside = unique_sibling(directory, "torn")
        rename_no_replace(directory, aside)
        say("SET_ASIDE_TORN_EVIDENCE", str(aside), present)
    directory.mkdir(mode=0o700)
    for name in sorted(expected):
        handle = os.open(directory / name,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            os.write(handle, expected[name])
            os.fsync(handle)
        finally:
            os.close(handle)
    # The two files are durable; the directory entries naming them are not
    # until the directory itself is synchronised.
    handle = os.open(directory, READ | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def adopt_candidate(staging: Path, files: int, expected: str) -> Path:
    """Copy the measured tree where only root can write, and measure it there.

    The staged tree is written by the developer and read by this process as
    root. Measuring it and then importing from it leaves a window in which the
    bytes checked and the bytes executed need not be the same, and the
    directory holding them is not root's. So the tree is copied into a
    root-owned directory first, the copy is re-measured, and everything after
    this point - the boundary guard, the source receiver, the interpreter -
    reads the copy. A copy left by an interrupted run is reused only if it
    still measures the same.

    This does not make an untrusted preparer safe: whoever writes the staging
    tree decides which code a later crossing will run. It makes the decision
    final at this line instead of open until the last import.
    """
    trusted = CANDIDATE_ROOT / ("rm0008-cycle-candidate-" + expected[:16])
    if trusted.exists():
        # A copy left by an earlier run is reused only if it still measures
        # the same. One that does not is set aside, never deleted and never
        # kept in the reusable place: it used to stay there and block that
        # identity for good, so a transient change during preparation made
        # every later run fail on a copy nobody could replace.
        os.close(open_parent(trusted))
        if census(trusted) != (files, expected):
            rename_no_replace(trusted, unique_sibling(trusted, "rejected"))
            say("SET_ASIDE_REJECTED_CANDIDATE", str(trusted))
    if not trusted.exists():
        incoming = CANDIDATE_ROOT / ".rm0008-cycle-candidate-incoming"
        subprocess.run(["rm", "-rf", str(incoming)], check=True)
        subprocess.run(["cp", "-a", str(staging), str(incoming)], check=True)
        subprocess.run(["chown", "-R", "root:root", str(incoming)], check=True)
        for path in incoming.rglob("*"):
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                path.chmod(0o755)
            elif stat.S_ISREG(info.st_mode):
                path.chmod(0o755 if info.st_mode & 0o100 else 0o644)
        incoming.chmod(0o755)
        # Measured BEFORE it becomes the reusable identity: a copy that does
        # not match never occupies the path it would block.
        seen = census(incoming)
        if seen != (files, expected):
            # Refused, and kept: nothing this tool touches is deleted. The next
            # preparation empties the incoming slot, so a refused copy left
            # there was erased by the run after (review, O-03 precision).
            aside = unique_sibling(trusted, "rejected")
            os.rename(incoming, aside)
            say("SET_ASIDE_REJECTED_CANDIDATE", str(aside))
        require(seen == (files, expected),
                f"the root-owned copy does not measure the same: {seen}")
        os.rename(incoming, trusted)
    os.close(open_parent(trusted))
    seen_files, seen_digest = census(trusted)
    require((seen_files, seen_digest) == (files, expected),
            f"the root-owned candidate does not measure the same: "
            f"{seen_files} {seen_digest}")
    return trusted


def apply_cycle(cross: bool) -> int:
    require(os.geteuid() == 0, "apply requires root")
    handoff = json.loads(HANDOFF.read_bytes())
    staging = Path(handoff["staging"])
    files, digest = census(staging)
    require((files, digest) == (handoff["files"], handoff["census"]),
            f"the staged tree changed since prepare: {files} {digest}")
    say("STAGED_TREE_OK", files, digest)

    staging = adopt_candidate(staging, files, digest)
    say("CANDIDATE_ADOPTED", str(staging))

    live_before = hashlib.sha256(LIVE_HELPER.read_bytes()).hexdigest()
    sys.dont_write_bytecode = True
    os.environ.update(PYTHONDONTWRITEBYTECODE="1")
    sys.path[:0] = [str(staging), str(staging / "runtime")]
    import contract_boundary_guard as guard
    from executor_birth_account_identity import (
        metnos_xdg_layout_v1, resolve_posix_account_v1,
    )
    inventory = guard.load_inventory(staging / guard.DEFAULT_INVENTORY)
    require(not guard.birth_closed_findings(guard.discover(staging), inventory)
            and guard.BIRTH_CLOSED_SOURCE_REVIEW_SHA256 == handoff["reviewed_root"]
            and guard.closed_python_source_review_finding(staging) is None,
            "reviewed candidate changed")
    say("REVIEWED_CANDIDATE_OK", handoff["reviewed_root"])

    account = resolve_posix_account_v1("metnos")
    require((account.uid, account.gid, account.home)
            == (995, 985, "/var/lib/metnos-service"), "service identity changed")
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                       "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
                       "METNOS_INSTALL_ROOT": str(staging),
                       **metnos_xdg_layout_v1(account).environment()})

    from install.executor_birth_source_receiver import _receive_source_v1
    source_id = _receive_source_v1(str(staging), account.name)
    say("RECEIVED_SOURCE", source_id)

    with ExitStack() as locks:
        acquire_locks(locks)
        withdraw_superseded_prepared(source_id)
        withdrawn = withdraw_superseded_claim(source_id)
        withdraw_unclaimed_release(source_id)
        for journal in retire_orphan_journals(withdrawn):
            say("RETIRED_JOURNAL", journal)

    from install.executor_birth_distribution_release import (
        build_and_install_received_source_v1,
    )
    distribution = build_and_install_received_source_v1(source_id)
    say("SIGNED_SUCCESSOR_BUILT", distribution.release_sequence,
        distribution.identity.closed_build_id, distribution.installation_root)
    require(hashlib.sha256(LIVE_HELPER.read_bytes()).hexdigest() == live_before,
            "live helper changed during the build")

    evidence = EVIDENCE_ROOT / (
        "rm0008-cycle-evidence-" + distribution.identity.closed_build_id[7:23])
    publish_evidence(distribution, evidence)
    say("BUILD_OK", str(evidence))

    child = [sys.executable, str(Path(__file__).resolve()), "_cross",
             distribution.installation_root, source_id, str(evidence)]
    if run_child(child + ["audit"]) != 0:
        return 78
    if not cross:
        say("APPLY_OK; PREVIEW_COMPLETE; NO HEAD CHANGE OR SERVICE STOP")
        return 0
    return _run_cutover_child(child + ["complete"])


def run_child(command: list[str]) -> int:
    result = subprocess.run(command, stdin=subprocess.DEVNULL)
    return result.returncode


RELEASE_EDITS_PLAN_TIMEOUT_S = 120
RELEASE_EDITS_DEPLOY_TIMEOUT_S = 600
SERVICE_RESTART_POLICY = Path("/etc/polkit-1/rules.d/49-metnos-services.rules")
RELEASE_EDITS_ENTRY = "service-stack-watchdog"
# Birth runs its tests inside the reconciler and needs a delegated cgroup
# (<unit>.service/metnos-birth-host) that a plain child of this root process
# lacks. The step therefore runs in one transient system unit; the controller
# uses fixed tools and a minimal environment of its own.
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
ENV_TOOL = "/usr/bin/env"
RELEASE_UNIT_STOP_S = 5
CONTROLLER_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                          "LANG": "C", "LC_ALL": "C"}
CUTOVER_TIMEOUT_S = 1200
CUTOVER_UNIT_PREFIX = "metnos-rm0008-cutover-"


def _run_cutover_child(command: list[str]) -> int:
    """The administrator and its unprivileged Birth checks share one delegate.

    A plain root subprocess has no service cgroup. Admission's user-only unit
    cannot perform the administrative crossing either. Give this crossing its
    own transient root service; after authenticating the descriptor it hands
    only that service's delegated subtree to the signed service identity.
    """
    unit = CUTOVER_UNIT_PREFIX + secrets.token_hex(8) + ".service"
    properties = (
        "Type=exec", "User=0", "Group=0", "Delegate=yes",
        "DelegateSubgroup=metnos-birth-host", "UMask=0027",
        "KillMode=control-group", f"TimeoutStopSec={RELEASE_UNIT_STOP_S}",
        f"RuntimeMaxSec={CUTOVER_TIMEOUT_S}", "MemoryAccounting=yes",
        "TasksAccounting=yes", "NoNewPrivileges=yes",
    )
    require(all("%" not in part for part in command), "cutover launch value unsafe")
    argv = [SYSTEMD_RUN, "--wait", "--pipe", "--collect", "--quiet",
            "--expand-environment=no", f"--unit={unit}"]
    for property_value in properties:
        argv += ["-p", property_value]
    argv += ["--", ENV_TOOL, "-i", *(
        f"{name}={value}" for name, value in CONTROLLER_ENVIRONMENT.items()),
        *command]
    try:
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, check=False, close_fds=True,
            env=CONTROLLER_ENVIRONMENT,
            timeout=CUTOVER_TIMEOUT_S + 2 * RELEASE_UNIT_STOP_S)
        return result.returncode
    except BaseException:
        stopped = _stop_release_unit(unit)
        say("CUTOVER_UNIT_STOP", unit, "confirmed" if stopped else "unconfirmed")
        raise


def _delegate_cutover_checks(descriptor) -> None:
    """Transfer only our systemd-created delegate, never a global cgroup.

    systemd initially owns this root service's cgroup. The coordinator drops
    to the signed account for ordinary property checks. That account needs
    the same limited delegation systemd gives a User= service, including its
    occupied subgroup so the runner can return processes there for cleanup.
    """
    import executor_birth_runner as runner
    from executor_birth_account_identity import resolve_posix_account_snapshot_v1

    account = resolve_posix_account_snapshot_v1(descriptor.service_user)
    require((account.record.uid, account.record.gid, account.record.home,
             account.record.shell, account.supplementary_gids) == (
                descriptor.service_uid, descriptor.service_gid,
                descriptor.service_home, descriptor.service_shell,
                descriptor.service_supplementary_gids)
            and descriptor.service_uid > 0 and descriptor.service_gid > 0,
            "cutover service account changed")
    current = runner._current_unified_cgroup()
    require(current is not None and current.name == runner._CGROUP_HOST_SUBGROUP
            and re.fullmatch(CUTOVER_UNIT_PREFIX + r"[0-9a-f]{16}\.service",
                             current.parent.name), "cutover delegate missing")
    delegate = runner._CGROUP_V2_MOUNT.joinpath(*current.parent.parts[1:])
    with ExitStack() as handles:
        parent_fd = os.open(delegate, READ | os.O_DIRECTORY)
        handles.callback(os.close, parent_fd)
        require(os.getxattr(parent_fd, "user.delegate") == b"1",
                "cutover cgroup is not delegated by systemd")
        subgroup_fd = os.open(current.name, READ | os.O_DIRECTORY, dir_fd=parent_fd)
        handles.callback(os.close, subgroup_fd)
        descriptors = [parent_fd, subgroup_fd]
        for directory_fd in (parent_fd, subgroup_fd):
            for name in ("cgroup.procs", "cgroup.threads", "cgroup.subtree_control"):
                fd = os.open(name, READ, dir_fd=directory_fd)
                handles.callback(os.close, fd)
                descriptors.append(fd)
        require(all(os.fstat(fd).st_uid == 0 for fd in descriptors),
                "cutover cgroup ownership changed")
        for fd in descriptors:
            os.fchown(fd, descriptor.service_uid, descriptor.service_gid)
    # Verify the real runner's precondition under the same temporary identity
    # used by the coordinator. Fail before any service is stopped.
    from install.birth_authority_provisioner import _service_owned_birth_identity_v2
    with _service_owned_birth_identity_v2(descriptor):
        observed, error = runner._cgroup_v2_delegate()
        require(observed == delegate and error is None,
                "cutover native runner unavailable: " + str(error))
        _probe_cutover_runner()
    say("CUTOVER_NATIVE_PROBE_OK")


def _probe_cutover_runner() -> None:
    """Exercise the native isolation before stopping services, without admission.

    This disposable backend measurement is diagnostic only. It neither
    supplies nor replaces the staged context's authenticated registry.
    """
    from executor_birth_sandbox_registry_v1 import (
        measure_sandbox_backend_v1, decode_sandbox_registry_v1,
    )
    from executor_birth_runner import run_birth_phase, RunnerStatus
    backend = decode_sandbox_registry_v1(measure_sandbox_backend_v1())
    require(backend is not None, "cutover native backend unavailable")
    result = run_birth_phase(
        (str(backend.interpreter_path), "-I", "-c", "pass"), linux_registry=backend)
    require(result.status is RunnerStatus.PASSED,
            "cutover native runner unavailable: " + str(result.error_code))


def _service_restart_granted(service_user: str) -> bool:
    """Read an existing rule; never install permissions as part of a release."""
    from services_registry import render_polkit_rule

    expected = render_polkit_rule(service_user).encode("utf-8")
    try:
        with os.fdopen(os.open(SERVICE_RESTART_POLICY, READ), "rb") as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or before.st_mode & 0o022 or before.st_nlink != 1):
                return False
            actual = handle.read(len(expected) + 1)
            return (actual == expected
                    and stamp(before) == stamp(os.fstat(handle.fileno()))
                    and stamp(before) == stamp(SERVICE_RESTART_POLICY.lstat()))
    except OSError:
        return False


def _release_unit_command(unit, account, working_directory, environment,
                          command, limit, *, read_only=False):
    """systemd-run argv for one transient delegated unit; values stay literal."""
    record = account.record
    properties = (
        "Type=exec", f"User={record.uid}", f"Group={record.gid}",
        "SupplementaryGroups=" + " ".join(
            str(gid) for gid in account.supplementary_gids),
        "Delegate=yes", "DelegateSubgroup=metnos-birth-host", "UMask=0027",
        "KillMode=control-group", f"TimeoutStopSec={RELEASE_UNIT_STOP_S}",
        # Same bound as the controller's: no extra run time if it is gone.
        # TimeoutStopSec above is the separate budget for stopping.
        f"RuntimeMaxSec={limit}",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=CAP_SETGID CAP_SETPCAP CAP_SETUID",
        "MemoryAccounting=yes", "TasksAccounting=yes",
        f"WorkingDirectory={working_directory}",
    )
    if read_only:
        # Both plan variants may create owned temporary snapshots, never
        # change the installation, user state, receipts, claims or services.
        properties += ("ProtectSystem=strict", "ProtectHome=read-only",
                       "PrivateTmp=yes", "ReadWritePaths=/tmp /var/tmp")
    service = [ENV_TOOL, "-i",
               *(f"{name}={value}" for name, value in environment.items()),
               *command]
    # systemd resolves %-specifiers in unit settings; signed values carry none.
    require(all("%" not in part for part in (*properties, *service)),
            "release launch value unsafe")
    argv = [SYSTEMD_RUN, "--wait", "--pipe", "--collect", "--quiet",
            "--expand-environment=no", f"--unit={unit}"]
    for item in properties:
        argv += ["-p", item]
    return argv + ["--", *service]


def _stop_release_unit(unit) -> bool:
    """Stop only this execution's unit; True only when systemd confirms it gone."""
    try:
        subprocess.run([SYSTEMCTL, "stop", unit], stdin=subprocess.DEVNULL,
                       capture_output=True, check=False,
                       env=CONTROLLER_ENVIRONMENT, close_fds=True,
                       timeout=2 * RELEASE_UNIT_STOP_S)
        shown = subprocess.run(
            [SYSTEMCTL, "show", "--property=LoadState,ActiveState", unit],
            stdin=subprocess.DEVNULL, capture_output=True, check=False,
            env=CONTROLLER_ENVIRONMENT, close_fds=True,
            timeout=RELEASE_UNIT_STOP_S)
    except (OSError, subprocess.SubprocessError):
        return False
    state = dict(line.split("=", 1) for line in
                 shown.stdout.decode("ascii", "replace").splitlines() if "=" in line)
    return shown.returncode == 0 and (
        state.get("LoadState") == "not-found"
        or state.get("ActiveState") in {"inactive", "failed"})


def _release_edits_child(distribution, descriptor, catalog, *, plan_only: bool,
                         preview: bool = False):
    """Use only signed launch data; the reconciler runs as the service account
    in one delegated transient unit, never as a child of this root process."""
    from executor_birth_account_identity import resolve_posix_account_snapshot_v1

    account = resolve_posix_account_snapshot_v1(descriptor.service_user)
    record = account.record
    require((record.name, record.uid, record.gid, record.home, record.shell,
             account.supplementary_gids) == (
                descriptor.service_user, descriptor.service_uid,
                descriptor.service_gid, descriptor.service_home,
                descriptor.service_shell, descriptor.service_supplementary_gids),
            "release service account changed")
    require(record.uid != 0, "release service cannot run as root")
    require(descriptor.installation_root == distribution.installation_root,
            "release descriptor root mismatch")
    entries = [item for item in catalog.catalog.entries
               if item.entry_id == RELEASE_EDITS_ENTRY]
    require(len(entries) == 1, "release deploy entry is not unique")
    entry = entries[0]
    require(entry.scope == "system" and entry.execution_kind == "python_module"
            and entry.python_module == "stack_reconcile"
            and entry.target_executable and entry.target_working_directory,
            "release deploy entry is not the system reconciler")
    env = {"HOME": record.home, "USER": record.name, "LOGNAME": record.name,
           "SHELL": record.shell,
           "METNOS_INSTALL_ROOT": distribution.installation_root}
    for item in entry.target_environment:
        require(item.name not in env, "duplicate release environment field")
        env[item.name] = item.value
    command = [entry.target_executable, "-E", "-s", "-B", "-m",
               entry.python_module, "deploy", "--changed-only",
               "--plan" if plan_only else "--sign"]
    if preview:
        require(plan_only, "preview cannot admit")
        command += ["--preview"]
    limit = (RELEASE_EDITS_PLAN_TIMEOUT_S if plan_only
             else RELEASE_EDITS_DEPLOY_TIMEOUT_S)
    unit = (f"metnos-release-edits-{'plan' if plan_only else 'sign'}-"
            f"{secrets.token_hex(8)}.service")
    argv = _release_unit_command(unit, account, entry.target_working_directory,
                                 env, command, limit, read_only=plan_only)
    # The evidence directory is root-private. Pass the already authenticated
    # PUBLIC distribution pair through the existing pipe, not argv and not a
    # new readable copy of that directory. The child re-verifies the pair.
    channel = ({"input": json.dumps({
        "distribution": distribution.encoded.decode("utf-8"),
        "signature": distribution.signature.hex(),
    }).encode("utf-8")} if preview else {"stdin": subprocess.DEVNULL})
    try:
        return subprocess.run(argv, **channel, capture_output=True,
                              check=False, env=CONTROLLER_ENVIRONMENT,
                              close_fds=True, timeout=limit)
    except subprocess.TimeoutExpired as expired:
        # The client ending is not the unit ending: stop and confirm it.
        expired.release_unit = unit
        expired.release_unit_stopped = _stop_release_unit(unit)
        raise
    except BaseException:
        # Nobody downstream reports an interruption: record it here, with
        # only the generated unit name and whether its stop was confirmed.
        stopped = _stop_release_unit(unit)
        say("RELEASE_UNIT_STOP", json.dumps(
            {"cause": "interrupted", "unit": unit,
             "stop": "confirmed" if stopped else "unconfirmed"}, sort_keys=True))
        raise


_TRACE_FRAME_RE = re.compile(rb'^[ \t]+File "([^"\n]{1,512})", line ([0-9]{1,7})', re.M)
_TRACE_TYPE_RE = re.compile(rb"^([A-Za-z_][A-Za-z0-9_]{0,80})(?::|$)")
# The only exception types ever named: Python's own, derived from the
# interpreter rather than listed. Any other line - a message, a note, a
# project class - reports nothing, so no text can pass as a type.
_BUILTIN_EXCEPTION_NAMES = frozenset(
    name for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException))
_RELEASE_FRAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}")
RELEASE_FRAMES_KEPT = 6


def _child_streams(stdout, stderr, installation_root: str, *,
                   release_files: frozenset = frozenset()) -> dict:
    """Bounded, non-sensitive facts about the child's output (review C15).

    Never the text: a few stderr lines can carry secrets, arguments or
    environment values. Lengths and hashes identify the streams. A frame is
    reported only for a path of the authenticated release inventory
    (``release_files``) and a type only when it is a Python builtin
    exception, so a line of the message can pass as neither.
    """
    streams = {}
    for name, data in (("stdout", stdout), ("stderr", stderr)):
        data = data if isinstance(data, bytes) else b""
        streams[name + "_bytes"] = len(data)
        streams[name + "_sha256"] = hashlib.sha256(data).hexdigest()
    err = stderr if isinstance(stderr, bytes) else b""
    prefix = os.fsencode(installation_root.rstrip("/") + "/")
    frames = []
    for path, line in _TRACE_FRAME_RE.findall(err):
        if not path.startswith(prefix):
            continue
        try:
            relative = path[len(prefix):].decode("ascii")
        except UnicodeDecodeError:
            continue
        if (_RELEASE_FRAME_RE.fullmatch(relative)
                and ".." not in relative.split("/")
                and relative in release_files):
            frames.append(f"{relative}:{int(line)}")
    streams["release_frames"] = frames[-RELEASE_FRAMES_KEPT:]
    exception_type = None
    if b"Traceback (most recent call last):" in err:
        for raw in reversed(err.splitlines()):
            match = _TRACE_TYPE_RE.match(raw)
            if match and match.group(1).decode("ascii") in _BUILTIN_EXCEPTION_NAMES:
                exception_type = match.group(1).decode("ascii")
                break
    streams["exception_type"] = exception_type
    return streams


def _release_files(distribution) -> frozenset:
    """Relative paths of the authenticated release inventory, in memory."""
    return frozenset(
        item.path for item in getattr(distribution, "files", ())
        if isinstance(getattr(item, "path", None), str))


def _run_release_preview(distribution, descriptor, catalog) -> None:
    """One informational read as the service user, before any cutover.

    The authenticated audit already succeeded. Preview failures are reported,
    not a substitute for (or a weakening of) the authoritative cutover checks.
    """
    summary = {"cutover_completed": False, "admission_attempted": False,
               "closed_build_id": distribution.identity.closed_build_id,
               "release_sequence": distribution.release_sequence}
    try:
        result = _release_edits_child(
            distribution, descriptor, catalog, plan_only=True,
            preview=True)
        summary["child_streams"] = _child_streams(
            result.stdout, result.stderr, distribution.installation_root,
            release_files=_release_files(distribution))
        report = json.loads(result.stdout)
        require(isinstance(report, dict) and isinstance(report.get("plan"), list),
                "preview report unavailable")
        summary.update(status="evaluated", returncode=result.returncode, result=report)
    except Exception as exc:
        from executor_birth_operational import birth_failure_diagnostic

        summary.update(status="not_evaluated", diagnostic=dataclasses.asdict(
            birth_failure_diagnostic(exc, "preview")))
    say("RELEASE_EDITS_PREVIEW", json.dumps(summary, sort_keys=True))


def _run_release_edits(distribution, descriptor, catalog, *, plan_only: bool) -> int:
    """Report the CLI's structured result without inventing atomic rollback."""
    summary = {
        "closed_build_id": distribution.identity.closed_build_id,
        "installation_root": distribution.installation_root,
        "release_sequence": distribution.release_sequence,
        "phase": "plan" if plan_only else "admit_and_activate",
        "timeout_s": (RELEASE_EDITS_PLAN_TIMEOUT_S if plan_only
                      else RELEASE_EDITS_DEPLOY_TIMEOUT_S),
        # Both children run only after CUTOVER_OK (review C15).
        "cutover_completed": True,
        "admission_attempted": False,
    }
    error_code = "release_edits_unavailable"
    try:
        require(os.geteuid() == 0, "release edits wrapper requires root")
        # Being installed is not being selected. This existing read-only
        # service proof rechecks the live selection before either child: the
        # successor's CLI can verify only the release it belongs to.
        materials, _entry = load_live_helper()._attest_service_startup_v1(
            RELEASE_EDITS_ENTRY,
        )
        facts = materials.distribution.facts
        error_code = "release_selection_changed"
        require((facts.closed_build_id, facts.installation_root,
                 facts.release_sequence) == (
                    distribution.identity.closed_build_id,
                    distribution.installation_root,
                    distribution.release_sequence), error_code)
        summary["head_id"] = materials.transaction.head_id
        if not plan_only:
            error_code = "service_restart_not_granted"
            require(_service_restart_granted(descriptor.service_user), error_code)
        error_code = "release_edits_child_failed"
        # Conservative on timeout or launch failure: do not infer that earlier
        # Birth publications rolled back, or clear I-001's activation record.
        summary["admission_attempted"] = not plan_only
        result = _release_edits_child(
            distribution, descriptor, catalog, plan_only=plan_only,
        )
        summary["returncode"] = result.returncode
        summary["child_streams"] = _child_streams(
            result.stdout, result.stderr, distribution.installation_root,
            release_files=_release_files(distribution))
        error_code = "release_edits_output_invalid"
        payload = json.loads(result.stdout)
        require(isinstance(payload, dict) and type(payload.get("ok")) is bool,
                error_code)
        summary["result"] = payload
        if result.returncode != 0 or payload["ok"] is not True:
            error_code = "release_edits_child_failed"
            if isinstance(payload.get("error_code"), str):
                summary["child_error_code"] = payload["error_code"]
            raise RuntimeError(error_code)
        rows = payload.get("plan" if plan_only else "signed")
        allowed = {"unchanged", "not_installed",
                   "changed" if plan_only else "store_verified"}
        require(isinstance(rows, list) and all(
            isinstance(row, dict) and isinstance(row.get("name"), str)
            and bool(row["name"]) and row.get("outcome") in allowed
            for row in rows), error_code)
        if not plan_only:
            require(type(payload.get("restarted")) is bool, error_code)
            published = [row for row in rows if row["outcome"] == "store_verified"]
            require(all(
                isinstance(row.get(key), str) and bool(row[key])
                for row in published
                for key in ("request_id", "candidate_id", "current_generation_id")
            ), error_code)
            if payload["restarted"]:
                ready = payload.get("readiness")
                activated = payload.get("activated")
                require(isinstance(ready, dict) and ready.get("ok") is True
                        and isinstance(activated, list) and all(
                            isinstance(row, dict)
                            and isinstance(row.get("name"), str)
                            and isinstance(row.get("current_generation_id"), str)
                            for row in activated), error_code)
                # A generation id hashes the payloads, not the origin: a core
                # and a builtin of the same name must each prove activation.
                def activation(row):
                    return (row.get("origin") or "core", row["name"],
                            row["current_generation_id"])
                require({activation(row) for row in published}
                        <= {activation(row) for row in activated},
                        error_code)
            else:
                require(not published, error_code)
        say("RELEASE_EDITS_PLAN" if plan_only else "RELEASE_EDITS_ADMITTED",
            json.dumps(summary, sort_keys=True))
        return 0
    except subprocess.TimeoutExpired as expired:
        error_code = "release_edits_timeout"
        summary["unit_stop"] = ("confirmed" if getattr(
            expired, "release_unit_stopped", False) is True else "unconfirmed")
        unit = getattr(expired, "release_unit", None)
        if isinstance(unit, str):
            summary["release_unit"] = unit
        summary["child_streams"] = _child_streams(
            expired.stdout, expired.stderr, distribution.installation_root,
            release_files=_release_files(distribution))
    except Exception as exc:
        summary["failure_type"] = type(exc).__name__
    summary["error_code"] = error_code
    summary["effects"] = (
        "unknown_check_pending_activation" if summary["admission_attempted"]
        else "no_admissions"
    )
    say("RELEASE_EDITS_PLAN_REFUSED" if plan_only else "RELEASE_EDITS_REFUSED",
        error_code, json.dumps(summary, sort_keys=True))
    return 78


# --------------------------------------------------------------------------
# stage 3: the crossing, in its own interpreter over the installed release
# --------------------------------------------------------------------------

def cross(release_root: str, source_id: str, evidence: str, mode: str) -> int:
    require(os.geteuid() == 0, "the crossing requires root")
    release = Path(release_root)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(release), str(release / "runtime")]
    from executor_birth_account_identity import (
        metnos_xdg_layout_v1, resolve_posix_account_v1,
    )
    account = resolve_posix_account_v1("metnos")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                       "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
                       "METNOS_INSTALL_ROOT": str(release),
                       **metnos_xdg_layout_v1(account).environment()})

    from executor_birth_distribution_manifest import (
        authenticate_distribution_record_v1,
        capture_current_deployment_descriptor_v1,
        verify_installed_distribution_record_v1,
    )
    directory = Path(evidence)
    distribution = verify_installed_distribution_record_v1(
        authenticate_distribution_record_v1(
            (directory / "distribution.json").read_bytes(),
            (directory / "distribution.sig").read_bytes()),
    )
    require(str(release) == distribution.installation_root,
            "crossing release does not match authenticated distribution")
    say("EXACT_SIGNED_SUCCESSOR_VERIFIED", distribution.identity.closed_build_id)
    current = authenticate_distribution_record_v1(
        distribution.encoded, distribution.signature)
    _, descriptor = capture_current_deployment_descriptor_v1(distribution)
    say("ADMINISTRATIVE_PYTHON", descriptor.python_executable)
    require(descriptor.python_executable == os.path.realpath("/usr/bin/python3"),
            "administrative interpreter is not the operating system one")

    from executor_birth_prepared_root import load_previous_context_runtime_v1
    from executor_birth_service_catalog import (
        load_previous_service_catalog_v1, load_service_catalog_v1,
    )
    from executor_birth_distribution_manifest import (
        capture_previous_release_artifacts_v1,
    )
    from stack_reconcile import StackReconciler, Systemctl

    previous = load_previous_context_runtime_v1(current)
    old_record = previous.selection.distribution
    old = authenticate_distribution_record_v1(
        old_record.encoded, old_record.signature)
    old_catalog = load_previous_service_catalog_v1(
        capture_previous_release_artifacts_v1(current, old))
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
    idle = StackReconciler(systemctl=Systemctl(service_user="roberto"),
                           default_write_report=False).require_quiescent()
    require(idle.get("ok") is True, "stack busy or unobservable")
    say("SUCCESSOR_AUDIT_OK", "units", len(old_units),
        "previous_head", previous.required_head_id, "quiescence", idle["source"])
    if mode == "audit":
        _run_release_preview(distribution, descriptor, new_catalog)
        return 0

    _delegate_cutover_checks(descriptor)

    from install.executor_birth_transition import (
        _complete_closed_v1, _handoff_frame_v1,
    )
    result = _complete_closed_v1(
        expected_source_id=source_id,
        expected_service_user="metnos",
        expected_legacy_service_user="roberto",
        expected_legacy_installation_root="/opt/metnos",
        expected_service_state_root=(
            Path(descriptor.service_home) / ".local/state/metnos"),
        frame=_handoff_frame_v1(source_id=source_id,
                                encoded=distribution.encoded,
                                signature=distribution.signature),
    )
    say("CUTOVER_OK", json.dumps(result, sort_keys=True))
    # The plan gates admission: a refused plan ships the release and admits
    # nothing in this attempt.
    if _run_release_edits(distribution, descriptor, new_catalog, plan_only=True) != 0:
        return 78
    return _run_release_edits(distribution, descriptor, new_catalog, plan_only=False)


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["_cross"]:
        require(len(arguments) == 5 and arguments[4] in {"audit", "complete"},
                "internal crossing arguments")
        return cross(*arguments[1:])
    if arguments == ["prepare"]:
        return prepare()
    if arguments[:1] == ["apply"] and set(arguments[1:]) <= {"--cross"}:
        return apply_cycle("--cross" in arguments)
    raise RuntimeError("usage: rm0008_release_cycle.py prepare | apply [--cross]")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print("RELEASE_CYCLE_REFUSED", type(error).__name__,
              getattr(error, "code", ""),
              str(getattr(error, "detail", "") or error)[:300], flush=True)
        import traceback
        for frame in traceback.extract_tb(error.__traceback__)[-8:]:
            print("FRAME", Path(frame.filename).name, frame.lineno, frame.name,
                  flush=True)
        raise SystemExit(78)

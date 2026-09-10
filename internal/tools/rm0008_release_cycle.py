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
                     restarts the services.

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
import fcntl
import hashlib
import json
import os
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


def snapshot(path: Path) -> dict:
    """Hash exact inode metadata and content; never expose confidential bytes."""
    rows, total = [], 0
    parent = open_parent(path.parent)
    device = os.fstat(parent).st_dev

    def visit(container: int, name: str, relative: str) -> None:
        nonlocal total
        handle = os.open(name, READ, dir_fd=container)
        try:
            before = os.fstat(handle)
            require((before.st_uid, before.st_gid) in OWNERS
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


def prepare() -> int:
    require(os.geteuid() != 0, "prepare runs as the developer, never as root")
    python = "/opt/metnos/.venv/bin/python"
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
    require(release.is_dir(), "the pending claim reserved no release directory")
    descriptor = json.loads(
        (release / "deployment/executor-birth-deployment-v1.json").read_bytes())
    require(descriptor.get("release_sequence") == claim["release_sequence"],
            "the reserved release is not the one the claim names")

    before = {str(item): snapshot(item) for item in preserved_paths()}
    first = startup_fingerprint()
    # Named by the attempt, not by the head it would have succeeded. The head
    # does not move while attempts fail, so naming the archive after it made
    # the second withdrawal collide with the first.
    archive = WITHDRAWN_ROOT / claim["request_id"][7:]
    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = archive.lstat()
    require((info.st_uid, info.st_gid) in OWNERS
            and stat.S_IMODE(info.st_mode) == 0o700, "unsafe archive")
    pairs = ((release, archive / "unselected-release"),
             (path, archive / "successor-claim.json"))
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
    retired = []
    for name in sorted(os.listdir(BIRTH)):
        if not name.startswith(JOURNAL_PREFIX):
            continue
        request = decode_transaction_header_v2(
            (BIRTH / name / "transaction-v2.json").read_bytes()).request_id
        if request.startswith("sha256:") and request[7:] in known:
            continue
        require(withdrawn_request is not None and request == withdrawn_request,
                f"open journal of an attempt this run did not withdraw: {name}")
        target = BIRTH / (SUPERSEDED_JOURNAL_PREFIX + name[len(JOURNAL_PREFIX):])
        require(not os.path.lexists(target), "superseded journal slot already taken")
        rename_no_replace(BIRTH / name, target, BIRTH_OWNERS)
        retired.append(name)
    return tuple(retired)


def publish_evidence(distribution, directory: Path) -> None:
    """Write the evidence pair whole, verify one already there, resume a torn one.

    Presence is not proof. The pair is two separate writes: an attempt
    interrupted between them leaves the directory holding one file of two.
    Skipping on presence alone handed the next step half an evidence set;
    refusing on it, which was the first correction, only turned a torn write
    into a permanently stuck retry - the evidence is derived from the build
    just made, so there is nothing to lose and no reason to stop.

    So: a complete pair is read back and compared byte for byte, and a
    complete pair that differs still stops the run, because the same build
    cannot have two contents. A torn one is moved aside under a name that says
    what it is - never deleted - and written again.
    """
    expected = {"distribution.json": distribution.encoded,
                "distribution.sig": distribution.signature}
    if directory.exists():
        present = sorted(item.name for item in directory.iterdir())
        if present == sorted(expected):
            for name in sorted(expected):
                require((directory / name).read_bytes() == expected[name],
                        f"release evidence is not this build: {name}")
            return
        for attempt in range(1, 100):
            torn = directory.with_name(f"{directory.name}.torn-{attempt:02d}")
            if not os.path.lexists(torn):
                break
        else:
            raise RuntimeError("too many torn evidence sets to set aside")
        rename_no_replace(directory, torn)
        say("SET_ASIDE_TORN_EVIDENCE", str(torn), present)
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
        withdrawn = withdraw_superseded_claim(source_id)
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
        say("APPLY_OK; NO HEAD CHANGE OR SERVICE STOP")
        return 0
    return run_child(child + ["complete"])


def run_child(command: list[str]) -> int:
    result = subprocess.run(command, stdin=subprocess.DEVNULL)
    return result.returncode


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
        return 0

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
    return 0


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

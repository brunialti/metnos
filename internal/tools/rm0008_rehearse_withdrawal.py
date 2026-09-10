#!/usr/bin/env python3
"""Rehearse the release cycle's withdrawal on a faithful copy of the live chain.

Reads the live chain, never writes to it, needs no root and stops nothing. It
copies the objects the withdrawal touches into a scratch directory and runs the
real code of `rm0008_release_cycle.py` against the copy: once to prove the
withdrawal moves exactly the two objects of a superseded attempt and leaves the
rest byte-identical, then once per refusal worth having.

Two things are relaxed for the copy, both stated out loud: the owner set (a
copy belongs to whoever made it) and the walk that refuses world-writable
parents, bounded to the scratch root because /tmp is world-writable by design.
Everything below the scratch root is checked exactly as in production.

Usage: rm0008_rehearse_withdrawal.py <scratch-directory>
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

TOOL = Path(__file__).with_name("rm0008_release_cycle.py")
LIVE = Path("/var/lib/metnos/executor-birth")
LOCK_NAME = ".required-head-v1.lock"


def copy_chain(destination: Path) -> None:
    """Copy what the withdrawal reads or moves; the private lock is stood in for."""
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "releases-v1").mkdir(parents=True)
    (destination / "coordinator-v1").mkdir()
    (destination / "chain-v1").mkdir()
    for name in sorted(path.name for path in (LIVE / "releases-v1").iterdir()):
        shutil.copytree(LIVE / "releases-v1" / name,
                        destination / "releases-v1" / name)
    for name in ("successor-claims-v1", "transactions-v2", "abandoned-crossings-v2"):
        shutil.copytree(LIVE / "coordinator-v1" / name,
                        destination / "coordinator-v1" / name)
    for name in ("preflight-attestations-v1", "startup-prerequisites-v1"):
        shutil.copytree(LIVE / name, destination / name)
    for path in sorted((LIVE / "chain-v1").iterdir()):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            shutil.copytree(path, destination / "chain-v1" / path.name)
        else:
            shutil.copy2(path, destination / "chain-v1" / path.name)
    lock = destination / "chain-v1" / LOCK_NAME
    lock.write_bytes(b"x")
    lock.chmod(0o600)


def bind(work: Path):
    spec = importlib.util.spec_from_file_location("rm0008_cycle_rehearsal", TOOL)
    cycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cycle)
    cycle.ROOT, cycle.COORD = work, work / "coordinator-v1"
    cycle.WITHDRAWN_ROOT = work / "withdrawn"
    cycle.OWNERS = {(os.getuid(), os.getgid())}

    def open_parent(path: Path) -> int:
        item = path
        while True:
            info = item.lstat()
            cycle.require(stat.S_ISDIR(info.st_mode)
                          and (info.st_uid, info.st_gid) in cycle.OWNERS,
                          "unsafe parent")
            if item == work:
                break
            item = item.parent
        handle = os.open(path, cycle.READ | os.O_DIRECTORY)
        cycle.require(cycle.stamp(os.fstat(handle)) == cycle.stamp(path.lstat()),
                      "parent changed")
        return handle

    cycle.open_parent = open_parent
    # The live attestation is read-only and answers the same thing throughout;
    # the rehearsal only needs it to be stable, which is the property asserted.
    cycle.startup_fingerprint = lambda: ("rehearsal", 0)
    return cycle


def pending(cycle) -> dict:
    claims = cycle.pending_claims()
    if len(claims) != 1:
        raise SystemExit(f"REHEARSAL FAILED: {len(claims)} pending claims on the copy")
    return claims[0][1]


def expect_refusal(label: str, action) -> None:
    try:
        action()
    except Exception as error:  # noqa: BLE001 - the refusal is the result
        print(f"  REFUSES {label}: {error}")
        return
    raise SystemExit(f"REHEARSAL FAILED: did not refuse {label}")


def rehearse(source: Path, work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(source, work, symlinks=True)
    cycle = bind(work)
    claim = pending(cycle)
    before = {str(path): cycle.snapshot(path) for path in cycle.preserved_paths()}
    releases = {path.name for path in (cycle.ROOT / "releases-v1").iterdir()}

    print("1. the same source is not a withdrawal")
    cycle.require(cycle.withdraw_superseded_claim(claim["source_id"]) is None,
                  "withdrew the claim of the source being built")
    cycle.require({path.name for path in (cycle.ROOT / "releases-v1").iterdir()}
                  == releases, "a release moved")
    print("  nothing moved")

    print("2. a superseded source withdraws exactly two objects")
    withdrawn = cycle.withdraw_superseded_claim("sha256:" + "0" * 64)
    cycle.require(withdrawn == claim["source_id"], "withdrew the wrong claim")
    reserved = f"{claim['release_sequence']:020d}"
    cycle.require(
        {path.name for path in (cycle.ROOT / "releases-v1").iterdir()}
        == releases - {reserved}, "the reserved release is still installed")
    cycle.require(not cycle.pending_claims(), "the claim is still pending")
    archive = cycle.WITHDRAWN_ROOT / claim["request_id"][7:]
    cycle.require(sorted(path.name for path in archive.iterdir())
                  == ["successor-claim.json", "unselected-release"],
                  "the archive is incomplete")
    after = {str(path): cycle.snapshot(path) for path in cycle.preserved_paths()}
    cycle.require(after == before, "preserved history changed")
    print(f"  two objects moved, {len(after)} preserved objects byte-identical")

    print("3. nothing left to withdraw is not an error")
    cycle.require(cycle.withdraw_superseded_claim("sha256:" + "0" * 64) is None,
                  "withdrew twice")
    print("  a second run is a no-op")

    print("4. a second failed attempt over the same head withdraws too")
    # The head does not move while attempts fail, so two attempts share their
    # previous_head_id. Only the attempt's own identity separates them.
    shutil.copytree(archive / "unselected-release",
                    cycle.ROOT / "releases-v1" / reserved)
    successor = dict(claim, request_id="sha256:" + "a" * 64,
                     source_id="sha256:" + "b" * 64)
    (cycle.COORD / "successor-claims-v1"
     / (claim["previous_head_id"][7:] + ".json")).write_bytes(
        json.dumps(successor, separators=(",", ":"), sort_keys=True).encode())
    cycle.require(
        cycle.withdraw_superseded_claim("sha256:" + "0" * 64)
        == successor["source_id"], "the second attempt was not withdrawn")
    cycle.require(len(list(cycle.WITHDRAWN_ROOT.iterdir())) == 2,
                  "the two attempts share one archive")
    print("  two attempts, two archives, nothing overwritten")


def rehearse_refusals(source: Path, work: Path) -> None:
    def fresh():
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(source, work, symlinks=True)
        return bind(work)

    other = "sha256:" + "0" * 64
    cycle = fresh()
    claim = pending(cycle)
    duplicate = (cycle.COORD / "successor-claims-v1"
                 / ("f" * 64 + ".json"))
    duplicate.write_bytes(json.dumps(
        {**json.loads(duplicate.parent.joinpath(
            claim["previous_head_id"][7:] + ".json").read_bytes()),
         "request_id": "sha256:" + "e" * 64}).encode())
    expect_refusal("more than one pending claim",
                   lambda: cycle.withdraw_superseded_claim(other))

    cycle = fresh()
    claim = pending(cycle)
    reserved = (cycle.ROOT / "releases-v1"
                / f"{claim['release_sequence']:020d}")
    shutil.rmtree(reserved)
    expect_refusal("a claim with no release directory",
                   lambda: cycle.withdraw_superseded_claim(other))

    cycle = fresh()
    claim = pending(cycle)
    archive = cycle.WITHDRAWN_ROOT / claim["request_id"][7:]
    archive.mkdir(mode=0o700, parents=True)
    (archive / "unselected-release").mkdir()
    expect_refusal("an archive slot already taken",
                   lambda: cycle.withdraw_superseded_claim(other))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: rm0008_rehearse_withdrawal.py <scratch-directory>")
    scratch = Path(sys.argv[1])
    scratch.mkdir(parents=True, exist_ok=True)
    pristine, work = scratch / "pristine", scratch / "work"
    print("copying the live chain objects (read-only)")
    copy_chain(pristine)
    rehearse(pristine, work)
    print("5. what it must refuse")
    rehearse_refusals(pristine, work)
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(pristine, ignore_errors=True)
    print("REHEARSAL_OK")


if __name__ == "__main__":
    main()

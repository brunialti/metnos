#!/usr/bin/env python3
"""Rehearse the Release 3 withdrawal on a faithful copy of the live chain.

Reads the live chain, never writes to it, needs no root and stops nothing. It
copies the objects the withdrawal touches into a scratch directory and runs the
real code of `rm0008_withdraw_release3.py` against the copy: once to prove the
withdrawal moves exactly two objects and leaves the rest byte-identical, then
once per refusal we care about.

Only two things are relaxed for the copy, both stated out loud: the owner set
(a copy belongs to whoever made it) and the walk that refuses world-writable
parents, which is bounded to the scratch root because /tmp is world-writable by
design. Everything below the scratch root is checked exactly as in production.

Usage: rm0008_rehearse_release3_withdrawal.py <scratch-directory>
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

TOOL = Path(__file__).with_name("rm0008_withdraw_release3.py")
LIVE = Path("/var/lib/metnos/executor-birth")
LOCK_NAME = ".required-head-v1.lock"


def copy_chain(destination: Path) -> None:
    """Copy only what the withdrawal reads or moves; the lock is stood in for.

    The live lock is root-private and holds no history: the copy carries a
    stand-in with the same name and mode so the inventory check still runs.
    """
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "releases-v1").mkdir(parents=True)
    (destination / "coordinator-v1").mkdir()
    (destination / "chain-v1").mkdir()
    shutil.copytree(LIVE / "releases-v1" / "00000000000000000003",
                    destination / "releases-v1" / "00000000000000000003")
    for name in ("successor-claims-v1", "transactions-v2", "abandoned-crossings-v2"):
        shutil.copytree(LIVE / "coordinator-v1" / name,
                        destination / "coordinator-v1" / name)
    for name in ("preflight-attestations-v1", "startup-prerequisites-v1"):
        shutil.copytree(LIVE / name, destination / name)
    for name in ("builds-v1", "context-transitions-v1", "cutovers-v1", "heads-v1"):
        shutil.copytree(LIVE / "chain-v1" / name, destination / "chain-v1" / name)
    shutil.copy2(LIVE / "chain-v1" / "required-head-v1.bin",
                 destination / "chain-v1" / "required-head-v1.bin")
    lock = destination / "chain-v1" / LOCK_NAME
    lock.write_bytes(b"x")
    lock.chmod(0o600)


def bind(work: Path):
    """Load the withdrawal tool and point it at the copy."""
    spec = importlib.util.spec_from_file_location("rm0008_withdrawal", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.ROOT, tool.COORD = work, work / "coordinator-v1"
    tool.ARCHIVE = work / "archive" / "withdrawn"
    tool.OWNERS = {(os.getuid(), os.getgid())}
    tool.PAIRS = (
        (tool.ROOT / "releases-v1" / f"{tool.SEQUENCE:020d}",
         tool.ARCHIVE / "unselected-release-3"),
        (tool.COORD / "successor-claims-v1" / (tool.HEAD[7:] + ".json"),
         tool.ARCHIVE / "successor-claim.json"),
    )
    tool.PRESERVED = (
        *(tool.ROOT / "chain-v1" / name for name in tool.CHAIN_MEMBERS),
        tool.ROOT / "preflight-attestations-v1",
        tool.ROOT / "startup-prerequisites-v1",
        tool.COORD / "transactions-v2" / tool.RELEASE_1,
        tool.COORD / "transactions-v2" / tool.RELEASE_2,
        tool.COORD / "abandoned-crossings-v2",
        tool.COORD / "successor-claims-v1" / "initial.json",
        tool.COORD / "successor-claims-v1" / (tool.CLAIM_2[7:] + ".json"),
    )

    def open_parent(path: Path) -> int:
        item = path
        while True:
            info = item.lstat()
            tool.require(stat.S_ISDIR(info.st_mode)
                         and (info.st_uid, info.st_gid) in tool.OWNERS,
                         "unsafe parent")
            if item == work:
                break
            item = item.parent
        handle = os.open(path, tool.READ | os.O_DIRECTORY)
        tool.require(tool.stamp(os.fstat(handle)) == tool.stamp(path.lstat()),
                     "parent changed")
        return handle

    tool.open_parent = open_parent
    # The real birth directory is service-private and is never touched by the
    # withdrawal, so the copy carries a stand-in holding the one journal the
    # abandoned crossing keeps: that inventory is the rule worth rehearsing.
    tool.BIRTH = work / "birth"
    tool.BIRTH.mkdir(exist_ok=True)
    record = json.loads((tool.COORD / "transactions-v2" / tool.RELEASE_2
                         / "record-000-v2.json").read_bytes())
    (tool.BIRTH / (tool.JOURNAL_PREFIX
                   + record["provisioning_transaction_id"])).write_bytes(b"")
    tool.PINS = {str(path): tool.snapshot(path)
                 for path in (*[pair[0] for pair in tool.PAIRS], *tool.PRESERVED)}
    tool.ARCHIVE.mkdir(mode=0o700, parents=True)
    return tool


def moving_pins(tool):
    return tuple(tool.PINS[str(source)] for source, _target in tool.PAIRS)


def expect_refusal(label: str, action) -> None:
    try:
        action()
    except Exception as error:  # noqa: BLE001 - the refusal is the result
        print(f"  REFUSES {label}: {error}")
        return
    raise SystemExit(f"REHEARSAL FAILED: did not refuse {label}")


def rehearse_withdrawal(source: Path, work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(source, work, symlinks=True)
    tool = bind(work)
    before = dict(tool.PINS)
    tool.coordinator_state()
    require = tool.require
    require(tool.topology(tool.PAIRS) == 0, "copy is not the original attempt")
    tool.move_fixed_prefix(tool.PAIRS, moving_pins(tool), tool.coordinator_state)
    require(tool.topology(tool.PAIRS) == 2, "withdrawal incomplete")
    require(not any((tool.ROOT / "releases-v1").iterdir()), "release still installed")
    require(sorted(path.name for path in tool.ARCHIVE.iterdir())
            == ["successor-claim.json", "unselected-release-3"], "archive incomplete")
    after = {str(path): tool.snapshot(path) for path in tool.PRESERVED}
    changed = sorted(name for name in after if after[name] != before[name])
    require(not changed, f"preserved history changed: {changed}")
    print(f"  two objects moved, {len(after)} preserved objects byte-identical")
    tool.move_fixed_prefix(tool.PAIRS, moving_pins(tool), tool.coordinator_state)
    require(tool.topology(tool.PAIRS) == 2, "replay moved something")
    print("  replaying the withdrawal changes nothing")


def rehearse_interruption(source: Path, work: Path) -> None:
    """A withdrawal stopped between the two moves resumes, door still shut."""
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(source, work, symlinks=True)
    tool = bind(work)
    tool.rename_no_replace(*tool.PAIRS[0])
    tool.require(tool.topology(tool.PAIRS) == 1, "interruption did not take")
    tool.require(tool.PAIRS[1][0].exists(),
                 "the claim must still stand: the builder must keep refusing")
    print("  release moved, claim still in place: the builder still refuses")
    tool.move_fixed_prefix(tool.PAIRS, moving_pins(tool), tool.coordinator_state)
    tool.require(tool.topology(tool.PAIRS) == 2, "resume incomplete")
    print("  resumed and completed")


def rehearse_refusals(source: Path, work: Path) -> None:
    def fresh():
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(source, work, symlinks=True)
        return bind(work)

    tool = fresh()
    (tool.COORD / "transactions-v2" / ("sha256:" + "0" * 64)).mkdir()
    expect_refusal("a transaction opened for release 3", tool.coordinator_state)

    tool = fresh()
    claim = tool.PAIRS[1][0]
    value = json.loads(claim.read_bytes())
    value["release_sequence"] = 4
    claim.write_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
    expect_refusal("a claim that is not the refused attempt", tool.coordinator_state)

    tool = fresh()
    (tool.ROOT / "chain-v1" / "heads-v1" / "00000000000000000003-x.json").write_bytes(b"{}")
    expect_refusal("a newly published head", tool.coordinator_state)

    tool = fresh()
    victim = tool.COORD / "abandoned-crossings-v2" / (tool.RELEASE_2[7:] + ".json")

    def tampering_guard():
        tool.coordinator_state()
        if not victim.read_bytes().startswith(b" "):
            victim.write_bytes(b" " + victim.read_bytes())

    expect_refusal("history moving during the withdrawal",
                   lambda: tool.move_fixed_prefix(tool.PAIRS, moving_pins(tool),
                                                  tampering_guard))

    tool = fresh()
    (tool.ARCHIVE / "unselected-release-3").mkdir()
    expect_refusal("an archive slot already taken",
                   lambda: tool.move_fixed_prefix(tool.PAIRS, moving_pins(tool),
                                                  tool.coordinator_state))

    # The abandoned crossing's own journal must be there and must be the only
    # one: a second journal would belong to release 3 and would mean the
    # crossing moved further than the two objects this tool knows about.
    tool = fresh()
    tool.journal_guard()
    print("  ACCEPTS the journal the abandoned crossing keeps")
    (tool.BIRTH / (tool.JOURNAL_PREFIX + "0" * 32)).write_bytes(b"")
    expect_refusal("a second birth journal", tool.journal_guard)

    tool = fresh()
    for stale in list(tool.BIRTH.iterdir()):
        stale.unlink()
    expect_refusal("the abandoned crossing's journal gone", tool.journal_guard)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: rm0008_rehearse_release3_withdrawal.py <scratch-directory>")
    scratch = Path(sys.argv[1])
    scratch.mkdir(parents=True, exist_ok=True)
    pristine, work = scratch / "pristine", scratch / "work"
    print("copying the live chain objects (read-only)")
    copy_chain(pristine)
    print("1. the withdrawal itself")
    rehearse_withdrawal(pristine, work)
    print("2. an interrupted withdrawal")
    rehearse_interruption(pristine, work)
    print("3. what it must refuse")
    rehearse_refusals(pristine, work)
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(pristine, ignore_errors=True)
    print("REHEARSAL_OK")


if __name__ == "__main__":
    main()

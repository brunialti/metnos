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
from types import SimpleNamespace

TOOL = Path(__file__).with_name("rm0008_release_cycle.py")
WORKTREE = Path(__file__).resolve().parents[2]
LIVE = Path("/var/lib/metnos/executor-birth")
LOCK_NAME = ".required-head-v1.lock"
sys.path[:0] = [str(WORKTREE), str(WORKTREE / "runtime")]


def seed_pending_attempt(cycle) -> dict:
    """Put one un-opened attempt on the copy, the way a failed run leaves it.

    This used to read the residue of a real failed crossing off the live
    chain. Once the crossing finally succeeded there was no residue left and
    the rehearsal stopped running at all: a proof that only works while the
    defect is present proves nothing. The attempt is now built on the copy,
    from the shape the chain already carries - a claim whose request the
    coordinator never opened, and the release directory it reserved.
    """
    claims = cycle.pending_claims()
    if claims:
        return claims[0][1]
    directory = cycle.COORD / "successor-claims-v1"
    # The newest claim by release, not by file name: the very first one is
    # called `initial` and has no predecessor head, which is exactly the field
    # a superseded attempt must carry.
    template = max((json.loads(path.read_bytes()) for path in directory.iterdir()),
                   key=lambda value: value["release_sequence"])
    installed = sorted((cycle.ROOT / "releases-v1").iterdir())[-1]
    sequence = int(installed.name) + 1
    reserved = cycle.ROOT / "releases-v1" / f"{sequence:020d}"
    shutil.copytree(installed, reserved)
    descriptor = reserved / "deployment/executor-birth-deployment-v1.json"
    value = json.loads(descriptor.read_bytes())
    value["release_sequence"] = sequence
    descriptor.write_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
    # Distinct from the second attempt scenario 4 adds over the same head:
    # two attempts must never share an archive, and identical fixtures would
    # hide exactly that.
    claim = {**template, "request_id": "sha256:" + "1" * 64,
             "source_id": "sha256:" + "2" * 64, "release_sequence": sequence}
    (directory / ("3" * 64 + ".json")).write_bytes(
        json.dumps(claim, separators=(",", ":"), sort_keys=True).encode())
    return claim


def write_journal(cycle, suffix: str, request: str) -> Path:
    """Write a journal header with the product's own encoder.

    The cycle reads these with `decode_transaction_header_v2`, so a hand-made
    document would be refused for its shape and never reach the rule under
    test. Only the request distinguishes the fixtures; the rest is filler of
    the exact form the decoder demands.
    """
    from install.birth_authority_provisioner import TransactionHeaderV2

    journal = cycle.BIRTH / (cycle.JOURNAL_PREFIX + suffix)
    journal.mkdir(exist_ok=True)
    (journal / "transaction-v2.json").write_bytes(TransactionHeaderV2(
        transaction_id="1" * 32,
        provisioner_build_id="sha256:" + "2" * 64,
        request_id=request,
        closed_build_id="sha256:" + "3" * 64,
        previous_set_id="4" * 64,
        distribution_payload_hash="sha256:" + "5" * 64,
        distribution_signature_hash="sha256:" + "6" * 64,
        source_inventory_hash="sha256:" + "7" * 64,
    ).encode())
    return journal


def copy_chain(destination: Path) -> None:
    """Copy what the withdrawal reads or moves; the private lock is stood in for."""
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "releases-v1").mkdir(parents=True)
    (destination / "coordinator-v1").mkdir()
    # The coordinator reader demands the mode the live directory has.
    (destination / "coordinator-v1").chmod(0o755)
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
    cycle.ROOT_OWNED = False

    def open_parent(path: Path, owners=None) -> int:
        item = path
        while True:
            info = item.lstat()
            cycle.require(stat.S_ISDIR(info.st_mode)
                          and (info.st_uid, info.st_gid)
                          in (owners or cycle.OWNERS), "unsafe parent")
            if item == work:
                break
            item = item.parent
        handle = os.open(path, cycle.READ | os.O_DIRECTORY)
        cycle.require(cycle.stamp(os.fstat(handle)) == cycle.stamp(path.lstat()),
                      "parent changed")
        return handle

    cycle.open_parent = open_parent
    # The birth root is service-private; the copy carries a stand-in holding the
    # open journal of the attempt about to be withdrawn, plus one that is not
    # ours to move.
    cycle.BIRTH = work / "birth"
    cycle.BIRTH.mkdir(exist_ok=True)
    cycle.BIRTH_OWNERS = cycle.OWNERS
    # One journal of the attempt about to be withdrawn, one belonging to a
    # crossing the coordinator did record - the abandoned release. Only the
    # first may be retired, and only because this run withdrew its claim.
    recorded = sorted((cycle.COORD / "transactions-v2").iterdir())[-1].name
    withdrawable = seed_pending_attempt(cycle)["request_id"]
    for suffix, request in (("a" * 32, withdrawable), ("b" * 32, recorded)):
        write_journal(cycle, suffix, request)
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
    def fresh_chain():
        """Una catena pulita: le riprese partono da dove si erano rotte."""
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(source, work, symlinks=True)
        return bind(work)

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
    cycle.require(withdrawn == claim["request_id"], "withdrew the wrong claim")
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
    cycle.require(cycle.retire_orphan_journals(withdrawn)
                  == (cycle.JOURNAL_PREFIX + "a" * 32,),
                  "the orphan journal was not the only one retired")
    open_journals = sorted(name for name in os.listdir(cycle.BIRTH)
                           if name.startswith(cycle.JOURNAL_PREFIX))
    cycle.require(open_journals == [cycle.JOURNAL_PREFIX + "b" * 32],
                  "a journal the coordinator records was moved")
    cycle.require(cycle.retire_orphan_journals(withdrawn) == (), "retired twice")
    print("  orphan journal retired, the recorded one left alone")

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
        == successor["request_id"], "the second attempt was not withdrawn")
    cycle.require(len(list(cycle.WITHDRAWN_ROOT.iterdir())) == 2,
                  "the two attempts share one archive")
    print("  two attempts, two archives, nothing overwritten")

    print("5. torn release evidence is resumed, not stared at")
    build = SimpleNamespace(encoded=b'{"release": 5}', signature=b"s" * 64)
    evidence = work / "evidence"
    cycle.publish_evidence(build, evidence)
    cycle.require(sorted(path.name for path in evidence.iterdir())
                  == ["distribution.json", "distribution.sig"],
                  "the evidence pair is incomplete")
    cycle.publish_evidence(build, evidence)
    print("  an identical pair is verified, not rewritten")
    (evidence / "distribution.sig").chmod(0o600)
    (evidence / "distribution.sig").unlink()
    cycle.publish_evidence(build, evidence)
    cycle.require((evidence / "distribution.sig").read_bytes() == build.signature,
                  "the torn pair was not written again")
    cycle.require([path.name for path in work.iterdir()
                   if path.name.startswith("evidence.torn-")]
                  == ["evidence.torn-01"], "the torn set was not set aside")
    cycle.require((work / "evidence.torn-01/distribution.json").read_bytes()
                  == build.encoded, "the torn set was not preserved whole")
    print("  a torn pair is set aside and written again, nothing deleted")
    other = SimpleNamespace(encoded=b'{"release": 6}', signature=b"s" * 64)
    expect_refusal("evidence that is not this build",
                   lambda: cycle.publish_evidence(other, evidence))

    print("6. una scrittura interrotta A META' si riprende")
    # Il caso che la review ha riprodotto: il nome c'e', il contenuto e'
    # troncato. Prima veniva letto come contraddizione e rifiutato per
    # sempre - due tentativi di fila bloccati sulla stessa directory.
    troncato = work / "evidence-troncata"
    cycle.publish_evidence(build, troncato)
    (troncato / "distribution.sig").chmod(0o600)
    (troncato / "distribution.sig").write_bytes(build.signature[:8])
    for giro in (1, 2):
        cycle.publish_evidence(build, troncato)
        cycle.require(
            (troncato / "distribution.sig").read_bytes() == build.signature,
            f"la coppia troncata non e' stata riscritta al giro {giro}")
    print("  due tentativi di fila, entrambi riprendono")

    print("7. il legame col ritiro sopravvive a un'interruzione")
    # Interruzione fra il ritiro della rivendicazione e quello del giornale:
    # il valore in memoria e' perso, ma la prova e' sull'archivio.
    ripresa = fresh_chain()
    claim = seed_pending_attempt(ripresa)
    write_journal(ripresa, "a" * 32, claim["request_id"])
    ripresa.withdraw_superseded_claim("sha256:" + "0" * 64)
    cycle.require(
        ripresa.retire_orphan_journals(None)
        == (ripresa.JOURNAL_PREFIX + "a" * 32,),
        "la ripresa non ha riconosciuto il proprio ritiro")
    print("  ritirato senza il valore in memoria, letto dall'archivio")

    print("8. una copia candidata rifiutata non blocca l'identita'")
    # Misura -> cambio transitorio -> rifiuto -> ripristino -> due tentativi.
    # Prima la copia rifiutata restava nel percorso riutilizzabile e ogni giro
    # successivo rimisurava lei: quell'identita' non tornava piu' usabile.
    sorgente = work / "candidato"
    sorgente.mkdir()
    (sorgente / "uno.txt").write_bytes(b"originale")
    (sorgente / "uno.txt").chmod(0o644)
    sorgente.chmod(0o755)
    cycle.CANDIDATE_ROOT = work / "adozioni"
    cycle.CANDIDATE_ROOT.mkdir()
    reale = cycle.subprocess.run

    def senza_chown(comando, **kw):
        # Il solo passaggio privilegiato: il resto e' copia e misura vere.
        if comando[:1] == ["chown"]:
            return reale(["true"], **kw)
        return reale(comando, **kw)

    cycle.subprocess.run = senza_chown
    try:
        misura = cycle.census(sorgente)
        (sorgente / "uno.txt").write_bytes(b"cambiata")   # cambio transitorio
        expect_refusal("una copia che non misura come l'atteso",
                       lambda: cycle.adopt_candidate(sorgente, *misura))
        (sorgente / "uno.txt").write_bytes(b"originale")  # ripristino
        for giro in (1, 2):
            adottata = cycle.adopt_candidate(sorgente, *misura)
            cycle.require(cycle.census(adottata) == misura,
                          f"la copia adottata non misura come l'atteso ({giro})")
        rifiutate = [p for p in cycle.CANDIDATE_ROOT.iterdir()
                     if ".rejected-" in p.name]
        # Esattamente una, ed e' proprio quella rifiutata. L'asserzione di prima
        # ammetteva zero, e zero era cio' che il codice produceva (review O-03).
        cycle.require(len(rifiutate) == 1,
                      f"copie rifiutate conservate: {len(rifiutate)}, attesa una")
        cycle.require((rifiutate[0] / "uno.txt").read_bytes() == b"cambiata",
                      "la copia conservata non e' quella rifiutata")
    finally:
        cycle.subprocess.run = reale
    print("  rifiutata, messa da parte, e l'identita' torna adottabile")

    print("9. un ritiro fermato fra i due spostamenti si riprende")
    # La release e' gia' nell'archivio del proprio tentativo, la rivendicazione
    # e' ancora pendente: prima ogni nuova chiamata falliva con «the pending
    # claim reserved no release directory» (review O-04).
    altro = "sha256:" + "0" * 64
    fermo = fresh_chain()
    claim = seed_pending_attempt(fermo)
    release = fermo.ROOT / "releases-v1" / f"{claim['release_sequence']:020d}"
    archivio = fermo.WITHDRAWN_ROOT / claim["request_id"][7:]
    archivio.mkdir(mode=0o700, parents=True, exist_ok=True)
    fermo.rename_no_replace(release, archivio / "unselected-release")
    cycle.require(fermo.withdraw_superseded_claim(altro) == claim["request_id"],
                  "il ritiro fermato a meta' non e' stato ripreso")
    cycle.require(fermo.withdraw_superseded_claim(altro) is None,
                  "la ripetizione dopo la ripresa non e' innocua")
    cycle.require(not fermo.pending_claims()
                  and (archivio / "successor-claim.json").is_file()
                  and (archivio / "unselected-release").is_dir(),
                  "l'archivio non contiene i due oggetti del tentativo")
    # L'archivio di un ALTRO tentativo non si adotta: la release manca dal suo
    # posto e il solo archivio che la contiene porta un altro nome.
    estraneo = fresh_chain()
    claim = seed_pending_attempt(estraneo)
    release = estraneo.ROOT / "releases-v1" / f"{claim['release_sequence']:020d}"
    altrui = estraneo.WITHDRAWN_ROOT / ("f" * 64)
    altrui.mkdir(mode=0o700, parents=True, exist_ok=True)
    estraneo.rename_no_replace(release, altrui / "unselected-release")
    expect_refusal("l'archivio di un altro tentativo",
                   lambda: estraneo.withdraw_superseded_claim(altro))
    print("  ripreso, ripetuto senza danno; l'archivio altrui rifiutato")


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

    # A residue nobody can name is not an authorisation to proceed: absence of
    # a transaction says the coordinator never saw that request, not whose the
    # journal is.
    cycle = fresh()
    withdrawn = cycle.withdraw_superseded_claim(other)
    write_journal(cycle, "c" * 32, "sha256:" + "d" * 64)
    expect_refusal("an open journal this run did not withdraw",
                   lambda: cycle.retire_orphan_journals(withdrawn))

    cycle = fresh()
    withdrawn = cycle.withdraw_superseded_claim(other)
    (cycle.BIRTH / (cycle.JOURNAL_PREFIX + "e" * 32)).mkdir()
    (cycle.BIRTH / (cycle.JOURNAL_PREFIX + "e" * 32)
     / "transaction-v2.json").write_bytes(b'{"request_id": "sha256:x"}')
    expect_refusal("a journal header the canonical decoder rejects",
                   lambda: cycle.retire_orphan_journals(withdrawn))


def renumber(release: Path, sequence: int) -> None:
    descriptor = release / "deployment/executor-birth-deployment-v1.json"
    value = json.loads(descriptor.read_bytes())
    value["release_sequence"] = sequence
    descriptor.write_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def seed_unclaimed_release(cycle) -> Path:
    """Leave the next slot installed and unclaimed, the way an audit refusal does.

    Taken from the copy when the live chain still carries one, built on the
    copy otherwise, so the proof keeps running once the live residue is gone.
    """
    claimed = max(json.loads(path.read_bytes())["release_sequence"]
                  for path in (cycle.COORD / "successor-claims-v1").iterdir())
    releases = cycle.ROOT / "releases-v1"
    release = releases / f"{claimed + 1:020d}"
    if not release.exists():
        shutil.copytree(releases / f"{claimed:020d}", release)
        renumber(release, claimed + 1)
    return release


def write_pending_claim(cycle, sequence: int) -> None:
    """A reservation of `sequence` that the product's own reader accepts."""
    from executor_birth_ownership_coordinator import (
        SuccessorClaimV1, _coordinator_request_id_v1,
        _resolve_ownership_coordinator_at_v2, _successor_claim_id_v1,
    )
    graph = _resolve_ownership_coordinator_at_v2(cycle.COORD, root_owned=False)
    current = graph.transactions[-1]
    closed = "sha256:" + "4" * 64
    value = {
        "schema_version": 1,
        "previous_head_id": current.latest.head_id,
        "release_sequence": sequence,
        "request_id": _coordinator_request_id_v1(
            closed, current.records[0].closed_build_id,
            current.latest.cutover_id),
        "source_id": "sha256:" + "5" * 64,
        "closed_build_id": closed,
    }
    fields = {key: item for key, item in value.items() if key != "schema_version"}
    claim = SuccessorClaimV1(claim_id=_successor_claim_id_v1(value), **fields)
    path = (cycle.COORD / "successor-claims-v1"
            / (current.latest.head_id[7:] + ".json"))
    path.write_bytes(claim.encode())
    path.chmod(0o644)


def rehearse_unclaimed(source: Path, work: Path) -> None:
    new_source = "sha256:" + "0" * 64

    def fresh():
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(source, work, symlinks=True)
        cycle = bind(work)
        # The cycle withdraws a pending attempt first; do the same, so the
        # unclaimed release is the only residue left.
        cycle.withdraw_superseded_claim(new_source)
        release = seed_unclaimed_release(cycle)
        sequence = int(release.name)
        cycle.startup_fingerprint = lambda: ("attested", sequence - 1, "head")
        return cycle, release

    def names(cycle) -> set[str]:
        return {path.name for path in (cycle.ROOT / "releases-v1").iterdir()}

    def beyond(release: Path) -> Path:
        return release.with_name(f"{int(release.name) + 1:020d}")

    def leaves(label: str, cycle, source_id: str = new_source) -> None:
        before = names(cycle)
        cycle.require(cycle.withdraw_unclaimed_release(source_id) is None
                      and names(cycle) == before, f"moved {label}")
        print(f"  LEAVES {label}")

    def refuses(label: str, cycle, release: Path) -> None:
        expect_refusal(label, lambda: cycle.withdraw_unclaimed_release(new_source))
        cycle.require(release.is_dir(), f"moved {label}")

    print("11. the slot the builder fills next is parked, once")
    cycle, release = fresh()
    before = {str(path): cycle.snapshot(path) for path in cycle.preserved_paths()}
    others = names(cycle) - {release.name}
    archive = Path(cycle.withdraw_unclaimed_release(new_source))
    cycle.require(names(cycle) == others, "the wrong releases moved")
    cycle.require(sorted(path.name for path in archive.iterdir())
                  == ["unselected-release"], "the archive is incomplete")
    after = {str(path): cycle.snapshot(path) for path in cycle.preserved_paths()}
    cycle.require(after == before, "preserved history changed")
    cycle.require(cycle.withdraw_unclaimed_release(new_source) is None,
                  "parked twice")
    print(f"  slot {int(release.name)} moved, {len(others)} releases and"
          f" {len(after)} preserved objects untouched; a second run is a no-op")

    print("  a second failed build in the same slot, same descriptor, other files")
    parked = archive / "unselected-release"
    shutil.copytree(parked, release)
    lock = release / "requirements.lock"
    lock.write_bytes(lock.read_bytes() + b"# a second build\n")
    second = Path(cycle.withdraw_unclaimed_release(new_source))
    cycle.require(second != archive and not os.path.lexists(release)
                  and parked.is_dir(), "the second build collided with the first")
    print(f"  parked apart: {archive.name} and {second.name}")

    print("12. what it leaves where it is")
    cycle, release = fresh()
    jumped = beyond(release)
    release.rename(jumped)
    renumber(jumped, int(jumped.name))
    leaves("a release beyond the empty slot", cycle)

    cycle, release = fresh()
    jumped = beyond(release)
    shutil.copytree(release, jumped)
    renumber(jumped, int(jumped.name))
    cycle.withdraw_unclaimed_release(new_source)
    cycle.require(jumped.is_dir() and not release.exists(), "the wrong release moved")
    print("  LEAVES a release beyond the slot while parking the slot")

    cycle, release = fresh()
    write_pending_claim(cycle, int(release.name))
    leaves("the slot a standing claim reserves", cycle)

    cycle, release = fresh()
    claims = cycle.COORD / "successor-claims-v1"
    published = max((json.loads(path.read_bytes()) for path in claims.iterdir()),
                    key=lambda value: value["release_sequence"])["source_id"]
    leaves("everything when the source is the one already published",
           cycle, published)

    print("13. what it must refuse")
    cycle, release = fresh()
    sequence = int(release.name)
    cycle.startup_fingerprint = lambda: ("attested", sequence, "head")
    refuses("the release the service starts", cycle, release)

    cycle, release = fresh()
    cycle.startup_fingerprint = lambda: ("refused", "Error", "unobservable")
    refuses("an unobservable startup selection", cycle, release)

    cycle, release = fresh()
    archive = Path(cycle.withdraw_unclaimed_release(new_source))
    shutil.copytree(archive / "unselected-release", release)
    refuses("the same build parked twice", cycle, release)

    cycle, release = fresh()
    renumber(release, int(release.name) + 1)
    refuses("a descriptor naming another release", cycle, release)

    cycle, release = fresh()
    claims = cycle.COORD / "successor-claims-v1"
    latest = max((json.loads(path.read_bytes()) for path in claims.iterdir()),
                 key=lambda value: value["release_sequence"])["request_id"]
    sorted((cycle.COORD / "transactions-v2" / latest).iterdir())[-1].unlink()
    refuses("a crossing neither completed nor abandoned", cycle, release)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: rm0008_rehearse_withdrawal.py <scratch-directory>")
    scratch = Path(sys.argv[1])
    scratch.mkdir(parents=True, exist_ok=True)
    pristine, work = scratch / "pristine", scratch / "work"
    print("copying the live chain objects (read-only)")
    copy_chain(pristine)
    rehearse(pristine, work)
    print("10. what it must refuse")
    rehearse_refusals(pristine, work)
    rehearse_unclaimed(pristine, work)
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(pristine, ignore_errors=True)
    print("REHEARSAL_OK")


if __name__ == "__main__":
    main()

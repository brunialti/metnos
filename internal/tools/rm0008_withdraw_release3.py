#!/usr/bin/python3.12
"""Withdraw the refused Release 3 claim; move nothing else.

The Release 3 crossing was refused by the first reader on the path, before it
opened a transaction. What the chain holds of it is therefore only two things:
a pending successor claim and an installed release directory. While that claim
stands the builder refuses every other source, so the corrected Release 3
cannot be built over it.

Exactly two objects move, the claim last: until the claim is gone the door
stays shut, so an interruption leaves a refusal rather than a half-open chain.
Published heads, receipts, birth journals, the Release 2 abandonment record,
the received sources and every running service stay untouched. Nothing is
stopped and nothing is deleted: the withdrawn objects are preserved under an
archive directory, byte for byte.

Operations:
  census    any user, read-only, no locks. Prints the pins to freeze here.
  audit     root. Takes the product locks and re-measures against the pins.
  withdraw  root. Moves the two objects under the same locks.
"""
from contextlib import ExitStack
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path('/var/lib/metnos/executor-birth')
COORD = ROOT / 'coordinator-v1'
BIRTH = Path('/var/lib/metnos-service/.config/metnos/birth')
ARCHIVE = Path('/var/lib/metnos-admin/rm0008-withdrawn-release3-20260910')

# Identities of the refused attempt, read from the claim the coordinator wrote.
SEQUENCE = 3
HEAD = 'sha256:d302bb32544f2352ad479ae4e529cdc90809be6cb0f7e7e5aaaef22cf3fabe32'
CLAIM = 'sha256:e7e489312219dede0ca05b59f73b1528411ec9a05628b8b603051609cdaf88aa'
REQUEST = 'sha256:e7e2d4bc0cb1cb3346299ca52ab355e2344ed48ad8de9751161c1c2de9389e5b'
BUILD = 'sha256:0f71b2331bbe31e80fb49370c6779ada49d82b6ca219bfa51e93f2b83b201eeb'
SOURCE = 'sha256:5d6448ec8b98b4bac7ae86f03c01dac84978c873ea9a88839a7e767e830332ad'

RELEASE_1 = 'sha256:59f0a756bbf65e46f34f5bcb4edafeff44c1788410ab1123ccbc7a1614e0cf26'
RELEASE_2 = 'sha256:922c17fd696746ddff158ba379204583ad10c0d2da5851a082a61e058a441008'
CLAIM_2 = 'sha256:469df47af79517e225d380a7a318f9cc17fb8747a0193e8d583d908bde7ed8b5'

HELPER = Path('/usr/libexec/metnos/executor-birth-v1/preflight.py')
HELPER_SHA = '35b3dc13800799b5dd2278600f9968b78ad19f4a8d7b1c8a64e7d80ca0c15d90'

# The claim is named after the head it would have succeeded, not after itself.
PAIRS = (
    (ROOT / 'releases-v1' / f'{SEQUENCE:020d}', ARCHIVE / 'unselected-release-3'),
    (COORD / 'successor-claims-v1' / (HEAD[7:] + '.json'), ARCHIVE / 'successor-claim.json'),
)
# Everything the withdrawal must leave exactly as it found it. The release
# directories of 1 and 2 are excluded on purpose: they are large, they are not
# on the path of this operation, and the chain objects below already bind them.
# The chain is pinned member by member rather than as one tree: its lock file
# is root-private, and a lock holds no history worth measuring. The inventory
# of the directory itself is checked instead, so nothing can appear beside it.
CHAIN_MEMBERS = ('builds-v1', 'context-transitions-v1', 'cutovers-v1',
                 'heads-v1', 'required-head-v1.bin')
PRESERVED = (
    *(ROOT / 'chain-v1' / name for name in CHAIN_MEMBERS),
    ROOT / 'preflight-attestations-v1',
    ROOT / 'startup-prerequisites-v1',
    COORD / 'transactions-v2' / RELEASE_1,
    COORD / 'transactions-v2' / RELEASE_2,
    COORD / 'abandoned-crossings-v2',
    COORD / 'successor-claims-v1' / 'initial.json',
    COORD / 'successor-claims-v1' / (CLAIM_2[7:] + '.json'),
)
# Frozen by a `census` run and reviewed before audit/withdraw is enabled.
PINS = {
    "/var/lib/metnos/executor-birth/chain-v1/builds-v1": {
        "bytes": 555611,
        "objects": 5,
        "sha256": "8f8d4f8fae4bca63515a8d3dbb9388e11839be93618a9fbbec369ee745e9f1ea"
    },
    "/var/lib/metnos/executor-birth/chain-v1/context-transitions-v1": {
        "bytes": 2397,
        "objects": 3,
        "sha256": "9b58ec4466a8ddcdfd60de8e4722b1d94cc027d768f04f502de46c1431046682"
    },
    "/var/lib/metnos/executor-birth/chain-v1/cutovers-v1": {
        "bytes": 59257,
        "objects": 5,
        "sha256": "7806cd42b130d9b482316e012468101c7457d2710bf7a30f7b82f5fa1ed6cda7"
    },
    "/var/lib/metnos/executor-birth/chain-v1/heads-v1": {
        "bytes": 1069,
        "objects": 5,
        "sha256": "0c69f28f9242cfe03dc7f84557d1df13d52489df140cb6159983bdbaef5f6dbe"
    },
    "/var/lib/metnos/executor-birth/chain-v1/required-head-v1.bin": {
        "bytes": 607,
        "objects": 1,
        "sha256": "3a2317220b7b0e2f6488c72f5c9604fb1fd623a730ce72c846421e3a94a43199"
    },
    "/var/lib/metnos/executor-birth/coordinator-v1/abandoned-crossings-v2": {
        "bytes": 645,
        "objects": 2,
        "sha256": "a73f28f1684aaf7a734d77bea1edf2c257eecac1b62382f371d304b7a766a381"
    },
    "/var/lib/metnos/executor-birth/coordinator-v1/successor-claims-v1/469df47af79517e225d380a7a318f9cc17fb8747a0193e8d583d908bde7ed8b5.json": {
        "bytes": 484,
        "objects": 1,
        "sha256": "15f586482bab58e8a1d6118ac31e0c33749708cd1e83d5cbf13340cb406a47cd"
    },
    "/var/lib/metnos/executor-birth/coordinator-v1/successor-claims-v1/d302bb32544f2352ad479ae4e529cdc90809be6cb0f7e7e5aaaef22cf3fabe32.json": {
        "bytes": 484,
        "objects": 1,
        "sha256": "5c34c8b9071a2708af7294c0bc52e706a2697c48739248caa6c765bad332c4bd"
    },
    "/var/lib/metnos/executor-birth/coordinator-v1/successor-claims-v1/initial.json": {
        "bytes": 415,
        "objects": 1,
        "sha256": "04be6132f61cb78e9f3a5a237fb2dfc9d275de812cd803d940065e1aa99e10ed"
    },
    "/var/lib/metnos/executor-birth/coordinator-v1/transactions-v2/sha256:59f0a756bbf65e46f34f5bcb4edafeff44c1788410ab1123ccbc7a1614e0cf26": {
        "bytes": 215258,
        "objects": 8,
        "sha256": "d2061622072418bb45d358d4a08baaa023eb04bfa02badf2fd704d4334f3f604"
    },
    "/var/lib/metnos/executor-birth/coordinator-v1/transactions-v2/sha256:922c17fd696746ddff158ba379204583ad10c0d2da5851a082a61e058a441008": {
        "bytes": 180654,
        "objects": 7,
        "sha256": "c8f8f5cc5bfcd6ec01d48b46ae49ecabd6919c0ceacb94f6fe1ee94f6d39bd9a"
    },
    "/var/lib/metnos/executor-birth/preflight-attestations-v1": {
        "bytes": 2255,
        "objects": 2,
        "sha256": "bfac76ac4fd4898d1241a5b89b02b091a5b240fe8e1b25d5abd868fd1813b505"
    },
    "/var/lib/metnos/executor-birth/releases-v1/00000000000000000003": {
        "bytes": 28736128,
        "objects": 1733,
        "sha256": "75b294fc1efc65b7e477de26a03b138ac3fa2619837aa11212b4e574d651ebd4"
    },
    "/var/lib/metnos/executor-birth/startup-prerequisites-v1": {
        "bytes": 3052,
        "objects": 3,
        "sha256": "552399588667c81d0a88253d45ad1aa6ec88287ea35912954fbc14a793532a78"
    }
}

OWNERS = {(0, 0)}
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
JOURNAL_PREFIX = '.birth-provisioning-v2.txn.'


def require(value, detail):
    if not value:
        raise RuntimeError(detail)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_parent(path):
    for item in (path, *path.parents):
        info = item.lstat()
        require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o7022
                and (info.st_uid, info.st_gid) in OWNERS, 'unsafe parent')
    fd = os.open(path, READ | os.O_DIRECTORY)
    try:
        require(stamp(os.fstat(fd)) == stamp(path.lstat()), 'parent changed')
        return fd
    except BaseException:
        os.close(fd)
        raise


def snapshot(path):
    """Hash exact inode metadata and content, never expose confidential bytes."""
    rows, total = [], 0
    parent = open_parent(path.parent)
    device = os.fstat(parent).st_dev

    def visit(container, name, relative):
        nonlocal total
        fd = os.open(name, READ, dir_fd=container)
        try:
            before = os.fstat(fd)
            require((before.st_uid, before.st_gid) in OWNERS
                    and before.st_dev == device and not before.st_mode & 0o7022
                    and not os.listxattr(fd), 'unsafe member metadata')
            row = [relative, *stamp(before)[:-1]]
            if stat.S_ISREG(before.st_mode):
                total += before.st_size
                require(before.st_nlink == 1 and before.st_size <= 16000000
                        and total <= 96000000, 'file bound or hardlink')
                digest = hashlib.sha256()
                remaining = before.st_size
                while remaining:
                    block = os.read(fd, min(65536, remaining))
                    require(block, 'short read')
                    remaining -= len(block)
                    digest.update(block)
                require(not os.read(fd, 1), 'file grew')
                row.append(digest.hexdigest())
                rows.append(row)
            else:
                require(stat.S_ISDIR(before.st_mode), 'unexpected member type')
                names = sorted(os.listdir(fd), key=os.fsencode)
                rows.append(row)
                require(len(rows) + len(names) <= 6000, 'tree bound')
                for child in names:
                    visit(fd, child, child if relative == '.' else relative + '/' + child)
                require(names == sorted(os.listdir(fd), key=os.fsencode), 'inventory changed')
            require(stamp(before) == stamp(os.fstat(fd))
                    == stamp(os.stat(name, dir_fd=container, follow_symlinks=False)),
                    'member changed')
        finally:
            os.close(fd)

    try:
        visit(parent, path.name, '.')
        payload = json.dumps(rows, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        return {'sha256': hashlib.sha256(payload).hexdigest(),
                'objects': len(rows), 'bytes': total}
    finally:
        os.close(parent)


def topology(pairs):
    moved = []
    for source, target in pairs:
        live, saved = os.path.lexists(source), os.path.lexists(target)
        require(live != saved, 'duplicate or missing withdrawal object')
        moved.append(saved)
    require(moved == sorted(moved, reverse=True), 'non-monotone withdrawal')
    return sum(moved)


def selected_paths(pairs):
    count = topology(pairs)
    return tuple(target if index < count else source
                 for index, (source, target) in enumerate(pairs))


def rename_no_replace(source, target):
    left, right = open_parent(source.parent), open_parent(target.parent)
    try:
        require(os.fstat(left).st_dev == os.fstat(right).st_dev, 'cross-device archive')
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(left, os.fsencode(source.name), right, os.fsencode(target.name), 1) != 0:
            raise OSError(ctypes.get_errno(), 'no-replace rename refused')
        os.fsync(left)
        os.fsync(right)
    finally:
        os.close(right)
        os.close(left)


def move_fixed_prefix(pairs, pins, guard):
    """Only a validated prefix can resume; saved bytes are never overwritten."""
    for index in range(topology(pairs), len(pairs)):
        guard()
        for position, path in enumerate(selected_paths(pairs)):
            require(snapshot(path) == pins[position], 'withdrawal object changed')
        source, target = pairs[index]
        rename_no_replace(source, target)
        require(snapshot(target) == pins[index], 'archive changed')
        guard()
    # A replay also flushes both parents after a rename-before-fsync interruption.
    for source, target in pairs:
        for parent in (source.parent, target.parent):
            fd = open_parent(parent)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    guard()


def load_helper():
    fd = os.open(HELPER, READ)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and
                (before.st_uid, before.st_gid, before.st_nlink, stat.S_IMODE(before.st_mode))
                == (0, 0, 1, 0o755) and before.st_size <= 700000, 'unsafe helper')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            payload = stream.read(700001)
        require(stamp(before) == stamp(os.fstat(fd)) == stamp(HELPER.lstat())
                and hashlib.sha256(payload).hexdigest() == HELPER_SHA, 'helper changed')
    finally:
        os.close(fd)
    module = type(sys)('rm0008_release3_withdrawal_startup_probe')
    sys.modules[module.__name__] = module
    exec(compile(payload, str(HELPER), 'exec'), module.__dict__)
    return module


def coordinator_state():
    """Prove the refused attempt is still exactly what the crossing left."""
    require({path.name for path in (COORD / 'transactions-v2').iterdir()}
            == {RELEASE_1, RELEASE_2}, 'a transaction exists for release 3')
    require({path.name for path in (COORD / 'abandoned-crossings-v2').iterdir()}
            == {RELEASE_2[7:] + '.json'}, 'abandonment inventory changed')
    require({path.name for path in (ROOT / 'chain-v1').iterdir()}
            == {*CHAIN_MEMBERS, '.required-head-v1.lock'}, 'chain inventory changed')
    require({path.name for path in (ROOT / 'chain-v1' / 'heads-v1').iterdir()} ==
            {'00000000000000000001-4fbdd1fdc283edd6fb895081c43e2559c25d792cc03e86044d62e7de505271ca.json',
             '00000000000000000001-4fbdd1fdc283edd6fb895081c43e2559c25d792cc03e86044d62e7de505271ca.sig',
             '00000000000000000002-5f7ab361ea2591efec0849e7e7eadcdb42794f813d394d87fbcc307764826741.json',
             '00000000000000000002-5f7ab361ea2591efec0849e7e7eadcdb42794f813d394d87fbcc307764826741.sig'},
            'the chain published a new head')
    _release, claim = selected_paths(PAIRS)
    require(json.loads(claim.read_bytes()) == {
        'claim_id': CLAIM, 'closed_build_id': BUILD, 'previous_head_id': HEAD,
        'release_sequence': SEQUENCE, 'request_id': REQUEST, 'schema_version': 1,
        'source_id': SOURCE}, 'claim is not the refused release 3 attempt')
    if PINS:
        for path in PRESERVED:
            require(snapshot(path) == PINS[str(path)], 'preserved history changed')


def journal_guard():
    """The abandoned crossing keeps its birth journal; nothing else may exist.

    The forward exit preserves the truthful last record of an unattestable
    crossing, and that record includes its provisioning journal: a predecessor
    that was abandoned is not a predecessor that completed, so its journal is
    never archived. Exactly one journal is therefore expected here, and which
    one is read from the abandoned crossing itself instead of being pinned.

    A second journal would belong to release 3, and would mean the crossing got
    further than the two objects this tool moves. That is refused by name, not
    guessed at: the topology would be wrong and the census would have to be
    made again.
    """
    record = json.loads((COORD / 'transactions-v2' / RELEASE_2
                         / 'record-000-v2.json').read_bytes())
    expected = record.get('provisioning_transaction_id')
    require(isinstance(expected, str) and expected,
            'the abandoned crossing declares no birth journal')
    found = sorted(name[len(JOURNAL_PREFIX):] for name in os.listdir(BIRTH)
                   if name.startswith(JOURNAL_PREFIX))
    require(found == [expected], f'unexpected birth journals: {found}')


def startup_fingerprint(helper):
    materials, _entry = helper._attest_service_startup_v1('service-http')
    facts = materials.distribution.facts
    require(facts.release_sequence != SEQUENCE, 'release 3 is already selected')
    return (facts.release_sequence, materials.transaction.head_id)


def semantic_guard(helper, expected=None):
    """Return what the services attest; refuse if withdrawing would move it."""
    coordinator_state()
    journal_guard()
    fingerprint = startup_fingerprint(helper)
    require(expected is None or fingerprint == expected, 'service startup selection moved')
    return fingerprint


def acquire_locks(stack):
    for path, owner, modes in (
        (ROOT / 'ownership-deployment-v1.lock', (0, 0), {0o600}),
        (Path('/run/metnos-executor-birth-v1/startup-v1.lock'), (0, 0), {0o600}),
        (BIRTH / 'provisioning-v1.lock', (995, 985), {0o600, 0o644}),
    ):
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid) == owner
                and stat.S_IMODE(info.st_mode) in modes and info.st_nlink == 1,
                'unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(stamp(info) == stamp(path.lstat()), 'lock rebound')


def census():
    """Read-only measurement. Takes no lock, changes nothing, needs no root."""
    require(topology(PAIRS) == 0, 'census requires the original attempt')
    coordinator_state()
    print(json.dumps({str(path): snapshot(path)
                      for path in (*[pair[0] for pair in PAIRS], *PRESERVED)},
                     indent=4, sort_keys=True))


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in {'census', 'audit', 'withdraw'},
            'exact operation required')
    operation = sys.argv[1]
    os.environ.clear()
    os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')
    if operation == 'census':
        census()
        return
    require(os.geteuid() == 0, 'root required')
    helper = load_helper()
    with ExitStack() as locks:
        acquire_locks(locks)
        if os.path.lexists(ARCHIVE):
            info = ARCHIVE.lstat()
            require(stat.S_ISDIR(info.st_mode) and
                    (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
                    'unsafe archive')
        require(set(PINS) == {str(p) for p in (*[pair[0] for pair in PAIRS], *PRESERVED)},
                'pins not frozen')
        first = semantic_guard(helper)
        pins = tuple(PINS[str(source)] for source, _target in PAIRS)
        for index, path in enumerate(selected_paths(PAIRS)):
            require(snapshot(path) == pins[index], 'attempt changed')
        if operation == 'audit':
            print('RELEASE3_WITHDRAWAL_AUDIT_OK moved', topology(PAIRS),
                  'selected', first[0])
            return
        if not os.path.lexists(ARCHIVE):
            parent = open_parent(ARCHIVE.parent)
            try:
                os.mkdir(ARCHIVE.name, 0o700, dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(parent)
        info = ARCHIVE.lstat()
        require(stat.S_ISDIR(info.st_mode) and
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700),
                'unsafe archive')
        require({p.name for p in ARCHIVE.iterdir()} ==
                {target.name for _source, target in PAIRS[:topology(PAIRS)]},
                'archive inventory changed')
        move_fixed_prefix(PAIRS, pins, lambda: semantic_guard(helper, first))
        require(topology(PAIRS) == len(PAIRS), 'withdrawal incomplete')
        print('RELEASE3_CLAIM_WITHDRAWN; HEADS_UNCHANGED; NO_SERVICE_STOP;'
              ' RECEIPTS_RETAINED; selected', first[0])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('RELEASE3_WITHDRAWAL_REFUSED', type(error).__name__, str(error)[:150],
              flush=True)
        raise SystemExit(78)

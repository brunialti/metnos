#!/usr/bin/env python3
"""Crash-recoverable POSIX store for exact preflight attestations."""
from __future__ import annotations
import os
import stat
from pathlib import Path
from executor_birth_admin_preflight import (
    CODE_RECOVERY, MAX_PREFLIGHT_ATTESTATION_BYTES_V1, PreflightError,
    _decode_preflight_attestation_v1, _invalid, _missing, _recovery,
    _require_digest,
)
from executor_birth_posix_metadata import snapshot_stat_v1
from executor_birth_preflight_store_authority import (
    ACL_NAMES_V1 as _ACL_NAMES_V1,
    authorize_store_effect_v1 as _authorize_v1,
    bind_store_mutation_port_v1,
    bounded_store_names_v1,
    product_store_authority_v1,
    product_store_root_v1 as _product_root_v1,
    require_acl_free_directory_chain_v1,
    require_no_acl_v1 as _require_no_acl_v1,
    same_store_directory_binding_v1 as _same_directory_v1,
    require_store_capacity_v1,
    require_store_platform_v1 as _require_posix_store_v1,
    test_store_authority_v1 as _test_authority_v1,
)

_MODE_FINAL_V1 = frozenset({0o644})
_MODE_TEMP_V1 = frozenset({0o600, 0o644})
_LINK_ONE_V1 = frozenset({1})
_LINK_TWO_V1 = frozenset({2})

def _assert_root_bound_v1(
    root: Path, directory: int, bound: os.stat_result, *,
    uid: int, gid: int, chain_stop: Path | None,
) -> None:
    require_acl_free_directory_chain_v1(
        root, chain_stop, uid=uid, gid=gid,
    )
    try:
        opened = os.fstat(directory)
        named = root.lstat()
    except OSError as exc:
        raise _recovery("preflight attestation directory rebound") from exc
    if (
        not _same_directory_v1(bound, opened)
        or not _same_directory_v1(opened, named)
    ):
        raise _recovery("preflight attestation directory rebound")
    _require_no_acl_v1(directory, "preflight attestation directory ACL")
    try:
        final_opened = os.fstat(directory)
        final_named = root.lstat()
    except OSError as exc:
        raise _recovery("preflight attestation directory rebound") from exc
    if (
        not _same_directory_v1(opened, final_opened)
        or not _same_directory_v1(final_opened, final_named)
    ):
        raise _recovery("preflight attestation directory rebound")

def _assert_child_bound_v1(
    directory: int, basename: str, descriptor: int, bound: os.stat_result,
) -> None:
    expected = snapshot_stat_v1(bound)
    for _attempt in range(2):
        try:
            opened = snapshot_stat_v1(os.fstat(descriptor))
            named = snapshot_stat_v1(os.stat(
                basename, dir_fd=directory, follow_symlinks=False,
            ))
        except OSError as exc:
            raise _recovery("preflight attestation file rebound") from exc
        if opened != expected or named != expected:
            raise _recovery("preflight attestation file rebound")
        _require_no_acl_v1(descriptor, "preflight attestation file ACL")

def _open_store_v1(
    root: Path, *, uid: int, gid: int, chain_stop: Path | None,
) -> tuple[int, os.stat_result]:
    _require_posix_store_v1()
    require_acl_free_directory_chain_v1(
        root, chain_stop, uid=uid, gid=gid,
    )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        before = root.lstat()
        descriptor = os.open(root, flags)
    except FileNotFoundError as exc:
        raise _missing("preflight attestation directory") from exc
    except OSError as exc:
        raise _invalid("preflight attestation directory") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode) or stat.S_ISLNK(before.st_mode)
            or opened.st_uid != uid or opened.st_gid != gid
            or stat.S_IMODE(opened.st_mode) != 0o755
            or snapshot_stat_v1(before) != snapshot_stat_v1(opened)
        ):
            raise _recovery("preflight attestation directory replaced")
        _require_no_acl_v1(descriptor, "preflight attestation directory ACL")
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise

def _read_bounded_fd_v1(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_PREFLIGHT_ATTESTATION_BYTES_V1 + 1
    while remaining > 0:
        try:
            chunk = os.read(descriptor, remaining)
        except OSError as exc:
            raise _recovery("preflight attestation read") from exc
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > MAX_PREFLIGHT_ATTESTATION_BYTES_V1:
        raise _recovery("preflight attestation size")
    return content

def _read_at_v1(
    directory: int, basename: str, *, uid: int, gid: int,
    modes: frozenset[int], links: frozenset[int],
) -> tuple[bytes, os.stat_result, int]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        before = os.stat(basename, dir_fd=directory, follow_symlinks=False)
        descriptor = os.open(basename, flags, dir_fd=directory)
    except OSError as exc:
        raise _recovery("preflight attestation durable state") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(before.st_mode)
            or opened.st_uid != uid or opened.st_gid != gid
            or opened.st_nlink not in links
            or stat.S_IMODE(opened.st_mode) not in modes
            or opened.st_size > MAX_PREFLIGHT_ATTESTATION_BYTES_V1
            or snapshot_stat_v1(before) != snapshot_stat_v1(opened)
        ):
            raise _recovery("preflight attestation durable state")
        _require_no_acl_v1(descriptor, "preflight attestation file ACL")
        content = _read_bounded_fd_v1(descriptor)
        after = os.fstat(descriptor)
        named = os.stat(basename, dir_fd=directory, follow_symlinks=False)
        if (
            snapshot_stat_v1(after) != snapshot_stat_v1(opened)
            or snapshot_stat_v1(named) != snapshot_stat_v1(after)
        ):
            raise _recovery("preflight attestation file rebound")
        return content, after, descriptor
    except BaseException:
        os.close(descriptor)
        raise

def _write_all_exact_v1(
    temporary: str, descriptor: int, content: bytes, effects: object,
) -> None:
    if type(descriptor) is not int or descriptor < 0 or type(content) is not bytes:
        raise _invalid("preflight attestation write")
    offset = 0
    while offset < len(content):
        written = effects.write_staging(temporary, descriptor, content[offset:])
        if type(written) is not int or written <= 0:
            raise _recovery("preflight attestation write")
        offset += written

def _stage_v1(
    temporary: str, encoded: bytes, *, effects: object, uid: int, gid: int,
) -> None:
    output = effects.create_staging(temporary)
    try:
        effects.chown_staging(temporary, output, uid, gid)
        effects.chmod_staging(temporary, output, 0o600)
        _write_all_exact_v1(temporary, output, encoded, effects)
        effects.sync_staging(temporary, output)
        effects.chmod_staging(temporary, output, 0o644)
        effects.sync_staging(temporary, output)
    finally:
        os.close(output)

def _sync_unlink_v1(
    name: str, *, effects: object,
) -> None:
    effects.unlink_temporary(name)
    effects.sync_directory()

def _promote_v1(
    temporary: str, basename: str, *, effects: object,
) -> None:
    effects.link_staging(temporary, basename)
    effects.sync_directory()
    _sync_unlink_v1(temporary, effects=effects)


def _recover_both_v1(
    directory: int, temporary: str, basename: str, encoded: bytes, *,
    effects: object, uid: int, gid: int,
) -> None:
    final, final_info, final_fd = _read_at_v1(
        directory, basename, uid=uid, gid=gid,
        modes=_MODE_FINAL_V1, links=_LINK_TWO_V1,
    )
    try:
        staged, staged_info, staged_fd = _read_at_v1(
            directory, temporary, uid=uid, gid=gid,
            modes=_MODE_FINAL_V1, links=_LINK_TWO_V1,
        )
        try:
            if (
                final != encoded or staged != encoded
                or (final_info.st_dev, final_info.st_ino)
                != (staged_info.st_dev, staged_info.st_ino)
            ):
                raise _recovery("preflight attestation linked recovery")
        finally:
            os.close(staged_fd)
    finally:
        os.close(final_fd)
    _sync_unlink_v1(temporary, effects=effects)


def _recover_temporary_v1(
    directory: int, temporary: str, basename: str, encoded: bytes, *,
    effects: object, uid: int, gid: int,
) -> bool:
    staged, info, staged_fd = _read_at_v1(
        directory, temporary, uid=uid, gid=gid,
        modes=_MODE_TEMP_V1, links=_LINK_ONE_V1,
    )
    os.close(staged_fd)
    if stat.S_IMODE(info.st_mode) == 0o644:
        if staged != encoded:
            raise _recovery("preflight attestation staged mismatch")
        _promote_v1(temporary, basename, effects=effects)
        return True
    _sync_unlink_v1(temporary, effects=effects)
    return False


def _publish_locked_v1(
    encoded: bytes, request_id: str, directory: int, *,
    effects: object, uid: int, gid: int,
) -> None:
    basename = request_id + ".json"
    temporary = "." + request_id.removeprefix("sha256:") + ".tmp"
    names = bounded_store_names_v1(directory)
    require_store_capacity_v1(names, temporary, basename)
    if basename in names and temporary in names:
        _recover_both_v1(
            directory, temporary, basename, encoded,
            effects=effects, uid=uid, gid=gid,
        )
    elif basename in names:
        existing, _, descriptor = _read_at_v1(directory, basename, uid=uid, gid=gid, modes=_MODE_FINAL_V1, links=_LINK_ONE_V1)
        os.close(descriptor)
        if existing != encoded:
            raise _recovery("preflight attestation conflict")
    elif temporary in names and _recover_temporary_v1(
        directory, temporary, basename, encoded,
        effects=effects, uid=uid, gid=gid,
    ):
        pass
    else:
        _stage_v1(
            temporary, encoded, effects=effects, uid=uid, gid=gid,
        )
        if not _recover_temporary_v1(
            directory, temporary, basename, encoded,
            effects=effects, uid=uid, gid=gid,
        ):
            raise _recovery("preflight attestation staging")
    observed, _, descriptor = _read_at_v1(directory, basename, uid=uid, gid=gid, modes=_MODE_FINAL_V1, links=_LINK_ONE_V1)
    os.close(descriptor)
    if observed != encoded:
        raise _recovery("preflight attestation reread")


def _publish_preflight_attestation_core_v1(
    encoded: bytes, request_id: str, *, root: Path, uid: int, gid: int,
    chain_stop: Path | None, authority: object,
) -> None:
    _require_posix_store_v1()
    if (
        type(encoded) is not bytes or not encoded
        or len(encoded) > MAX_PREFLIGHT_ATTESTATION_BYTES_V1
        or _require_digest(request_id, "preflight request") != request_id
        or not isinstance(root, Path) or not root.is_absolute()
    ):
        raise _invalid("preflight attestation publication")
    if _decode_preflight_attestation_v1(encoded).request_id != request_id:
        raise _invalid("preflight attestation publication")
    _authorize_v1(authority, root, chain_stop)
    directory, bound = _open_store_v1(root, uid=uid, gid=gid, chain_stop=chain_stop)
    try:
        effects = bind_store_mutation_port_v1(
            authority, root, chain_stop, directory,
        )
        effects.lock()
        _publish_locked_v1(
            encoded, request_id, directory,
            effects=effects, uid=uid, gid=gid,
        )
        _assert_root_bound_v1(root, directory, bound, uid=uid, gid=gid, chain_stop=chain_stop)
        _authorize_v1(authority, root, chain_stop)
    finally:
        # Closing this function's descriptor releases its flock. Avoiding an
        # explicit LOCK_UN prevents a post-fork child from unlocking the open
        # file description still held by its parent.
        os.close(directory)


def _read_preflight_attestation_core_v1(
    request_id: str, *, root: Path, uid: int, gid: int,
    chain_stop: Path | None,
) -> bytes:
    _require_posix_store_v1()
    if (
        _require_digest(request_id, "preflight request") != request_id
        or not isinstance(root, Path) or not root.is_absolute()
    ):
        raise _invalid("preflight attestation reread")
    directory, bound = _open_store_v1(root, uid=uid, gid=gid, chain_stop=chain_stop)
    child = -1
    try:
        basename = request_id + ".json"
        encoded, child_bound, child = _read_at_v1(
            directory, basename, uid=uid, gid=gid,
            modes=_MODE_FINAL_V1, links=_LINK_ONE_V1,
        )
        decoded = _decode_preflight_attestation_v1(encoded)
        _assert_root_bound_v1(root, directory, bound, uid=uid, gid=gid, chain_stop=chain_stop)
        _assert_child_bound_v1(directory, basename, child, child_bound)
        _assert_root_bound_v1(root, directory, bound, uid=uid, gid=gid, chain_stop=chain_stop)
        if decoded.request_id != request_id:
            raise _recovery("preflight attestation request binding")
    except PreflightError as exc:
        if exc.code == CODE_RECOVERY:
            raise
        raise _recovery("preflight attestation durable state") from exc
    finally:
        if child >= 0:
            os.close(child)
        os.close(directory)
    return encoded


def _read_preflight_attestation_v1(request_id: str) -> bytes:
    root = _product_root_v1()
    return _read_preflight_attestation_core_v1(
        request_id, root=root, uid=0, gid=0, chain_stop=None,
    )


def _read_preflight_attestation_for_test_v1(request_id: str, root: Path) -> bytes:
    authority = _test_authority_v1(root)
    return _read_preflight_attestation_core_v1(
        request_id, root=authority.root, uid=os.getuid(), gid=os.getgid(),
        chain_stop=authority.chain_stop,
    )


def _publish_preflight_attestation_v1(
    encoded: bytes, request_id: str, sessions: tuple[object, ...],
) -> bytes:
    authority = product_store_authority_v1(sessions)
    root = _product_root_v1()
    _publish_preflight_attestation_core_v1(
        encoded, request_id, root=root, uid=0, gid=0,
        chain_stop=None, authority=authority,
    )
    observed = _read_preflight_attestation_v1(request_id)
    _authorize_v1(authority, root, None)
    if observed != encoded:
        raise _recovery("preflight attestation publication reread")
    return observed


def _publish_preflight_attestation_for_test_v1(
    encoded: bytes, request_id: str, root: Path,
) -> bytes:
    authority = _test_authority_v1(root)
    _publish_preflight_attestation_core_v1(
        encoded, request_id, root=authority.root,
        uid=os.getuid(), gid=os.getgid(), chain_stop=authority.chain_stop,
        authority=authority,
    )
    observed = _read_preflight_attestation_for_test_v1(request_id, authority.root)
    _authorize_v1(authority, authority.root, authority.chain_stop)
    if observed != encoded:
        raise _recovery("preflight attestation publication reread")
    return observed


__all__ = []

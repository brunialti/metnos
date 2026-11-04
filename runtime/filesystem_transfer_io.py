"""Bounded transfer I/O shared by the service and its installation-owner host.

Only the parent service owns invocation/grant checks and durable receipts.
The authority pins each check and checkpoint to one signed transfer entry.
"""
from __future__ import annotations

import base64
import os
import re
import time

import config as C
import filesystem_io as io
from filesystem_permissions import PermissionError


def _error(code="ERR_FS_TRANSFER_INVALID"):
    raise PermissionError(code)


def _reserved_stage(parent, row):
    try:
        return io.stage(parent, row["temp_name"])[1]
    except FileExistsError:
        # An earlier attempt stopped between creating it and recording it.
        fd = io.open_file(parent, row["temp_name"], os.O_RDWR)
        os.ftruncate(fd, 0)
        return fd


def _stage_file(parent, row, *, recover_append=False):
    fd = io.open_file(parent, row["temp_name"], os.O_RDWR)
    info = os.fstat(fd)
    size_valid = row["written"] <= info.st_size <= row["written"] + io.CHUNK_SIZE if recover_append else info.st_size == row["written"]
    if info.st_ino != row["inode"] or not size_valid:
        os.close(fd)
        _error()
    return fd


def execute(transfer, turn_id, authority):
    action, path, row = transfer["action"], transfer["path"], transfer["row"]
    check_action = "read" if action == "read" and not transfer["internal"] else "write"
    def check(**kwargs):
        authority.check(transfer["check_path"], check_action, **kwargs)

    if action != "abort":
        check()
    if action == "read":
        if transfer["internal"]:
            root = C.PATH_USER_DATA / "_history" / turn_id / "blob"
            if (os.path.dirname(path) != str(root)
                    or not re.fullmatch(r"[0-9a-f]{64}\.bin", os.path.basename(path))):
                _error("ERR_FS_AUTHORITY_INVALID")
        with io.parent(path, internal=transfer["internal"]) as (parent, name):
            fd = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(fd))
                os.lseek(fd, transfer["offset"], os.SEEK_SET)
                data = os.read(fd, io.CHUNK_SIZE)
                if io.identity(io.regular(fd)) != before:
                    _error("ERR_FS_SOURCE_CHANGED")
                check()
                return {"content": base64.b64encode(data).decode(), "size": before[3],
                        "source_identity": ":".join(map(str, before)), "offset": transfer["offset"]}
            finally:
                os.close(fd)
    if action == "abort" and (row is None or row["committed"]):
        return {"aborted": True}
    with io.parent(path) as (parent, name):
        if action == "abort":
            with authority.mutation():
                try:
                    fd = io.open_file(parent, row["temp_name"])
                except FileNotFoundError:
                    pass
                else:
                    try:
                        if row["inode"] and os.fstat(fd).st_ino != row["inode"]:
                            _error()
                        os.unlink(row["temp_name"], dir_fd=parent)
                        os.fsync(parent)
                    finally:
                        os.close(fd)
                authority.checkpoint(None)
            return {"aborted": True}
        if action == "delete":
            return _delete(parent, name, path, row, turn_id, authority, check,
                           transfer["expected_sha256"])
        if action == "write":
            data, offset = base64.b64decode(transfer["content"], validate=True), transfer["offset"]
            created, fd = False, None
            if row is None:
                if offset != 0:
                    _error()
                # Durable before the file exists: however this stops, cleanup
                # finds this exact name, and never needs to guess by pattern.
                row = {"temp_name": io.staging_name(), "destination": path, "written": 0,
                       "inode": 0, "created_at": int(time.time()), "committed": 0, "receipt": None}
                with authority.mutation():
                    check()
                    authority.checkpoint(row)
            try:
                with authority.mutation():
                    try:
                        check()
                        if row["committed"]:
                            _error()
                        if row["inode"] == 0:
                            fd = _reserved_stage(parent, row)
                            created = True
                            row = {**row, "inode": os.fstat(fd).st_ino}
                        else:
                            fd = _stage_file(parent, row, recover_append=True)
                            # An interrupted end may leave bytes beyond the last
                            # durable offset. They have not been acknowledged.
                            os.ftruncate(fd, row["written"])
                        if offset != row["written"]:
                            if offset + len(data) > row["written"]:
                                _error()
                            os.lseek(fd, offset, os.SEEK_SET)
                            if os.read(fd, len(data)) != data:
                                _error()
                            return {"written": row["written"]}
                        os.lseek(fd, offset, os.SEEK_SET)
                        io.write_all(fd, data)
                        os.fsync(fd)
                        check()
                        authority.checkpoint({**row, "written": offset + len(data)})
                    except BaseException:
                        if fd is not None:
                            os.ftruncate(fd, row["written"])
                            os.fsync(fd)
                            if created:
                                os.unlink(row["temp_name"], dir_fd=parent)
                                os.fsync(parent)
                        raise
                return {"written": offset + len(data)}
            finally:
                # After end, the parent may already have committed even when
                # its reply is lost. Never undo bytes on that uncertain result.
                if fd is not None:
                    os.close(fd)
        digest = transfer["sha256"]
        if row is None or transfer["size"] != row["written"]:
            _error()
        if row["committed"]:
            if row["receipt"]["sha256"] != digest:
                _error("ERR_FS_HASH_MISMATCH")
            return row["receipt"]
        try:
            fd = _stage_file(parent, row)
        except FileNotFoundError:
            # A prepared receipt survives a lost final acknowledgement. Only
            # the staged inode, exact length and digest can finish this retry.
            receipt = row["receipt"]
            if not receipt:
                raise
            check()
            fd = io.open_file(parent, name)
            try:
                recovered = io.identity(io.regular(fd))
                if (recovered[1] != row["inode"] or recovered[3] != row["written"]
                        or io.digest(fd, check) != digest or receipt["sha256"] != digest
                        or io.identity(io.regular(fd)) != recovered):
                    _error("ERR_FS_HASH_MISMATCH")
                with authority.mutation():
                    check()
                    current = io.open_file(parent, name)
                    try:
                        if io.identity(io.regular(current)) != recovered:
                            _error("ERR_FS_SOURCE_CHANGED")
                    finally:
                        os.close(current)
                    authority.checkpoint({**row, "committed": 1})
            finally:
                os.close(fd)
            return receipt
        try:
            staged = io.identity(io.regular(fd))
            if io.digest(fd, check) != digest:
                _error("ERR_FS_HASH_MISMATCH")
            previous = io.existing(parent, name)
            try:
                if previous is not None and not transfer["overwrite"]:
                    _error("ERR_FS_DEST_EXISTS")
                if previous is not None:
                    check(overwrite=True)
                _require_content(previous, transfer["expected_sha256"], check)
                before = io.identity(io.regular(previous)) if previous is not None else None
                blob = io.backup(previous, turn_id, check) if previous is not None else None
                receipt = {"committed": True, "size": row["written"], "sha256": digest,
                           "created": previous is None, "prev_blob_path": blob, "destination_scope": "server"}
                row = {**row, "receipt": receipt}
                authority.checkpoint(row)
                with authority.mutation():
                    check()
                    current = io.existing(parent, name)
                    try:
                        if (io.identity(io.regular(current)) if current is not None else None) != before:
                            _error("ERR_FS_SOURCE_CHANGED")
                    finally:
                        if current is not None:
                            os.close(current)
                    if previous is not None:
                        check(overwrite=True)
                    if io.identity(io.regular(fd)) != staged:
                        _error("ERR_FS_SOURCE_CHANGED")
                    os.fsync(fd)
                    io.commit(parent, row["temp_name"], name, overwrite=previous is not None)
                    authority.checkpoint({**row, "committed": 1})
                return receipt
            finally:
                if previous is not None:
                    os.close(previous)
        finally:
            os.close(fd)


def _require_content(fd, expected, check):
    """An undo acts only on what the original operation left there."""
    if expected is not None and (fd is None or io.digest(fd, check) != expected):
        _error("ERR_FS_SOURCE_CHANGED")


def _delete(parent, name, path, row, turn_id, authority, check, expected=None):
    if row and row["committed"]:
        return row["receipt"]
    fd = io.existing(parent, name)
    try:
        if row:
            receipt = row["receipt"]
            if fd is not None and io.identity(io.regular(fd)) != tuple(receipt["identity"]):
                _error("ERR_FS_SOURCE_CHANGED")
        else:
            if fd is None:
                raise FileNotFoundError(path)
            _require_content(fd, expected, check)
            before = io.identity(io.regular(fd))
            blob = io.backup(fd, turn_id, check)
            receipt = {"result": {"path": path, "deleted": True, "blob_path": blob,
                                  "restore_mode": "create", "destination_scope": "server"}, "identity": before}
            row = {"temp_name": "", "destination": path, "written": before[3], "inode": before[1],
                   "created_at": int(time.time()), "committed": 0, "receipt": receipt}
            authority.checkpoint(row)
        with authority.mutation():
            check()
            current = io.existing(parent, name)
            try:
                if current is not None:
                    if io.identity(io.regular(current)) != tuple(receipt["identity"]):
                        _error("ERR_FS_SOURCE_CHANGED")
                    os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
            finally:
                if current is not None:
                    os.close(current)
            authority.checkpoint({**row, "committed": 1})
        return receipt
    finally:
        if fd is not None:
            os.close(fd)

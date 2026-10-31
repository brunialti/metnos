"""Fresh, signed file-broker authority and bounded device/server transfers.

The device requests no arbitrary server path: each path is derived from an
authenticated, live invocation. Every chunk and commit rechecks current grants.
Server filesystem access uses directory descriptors and refuses links.
"""
from __future__ import annotations

import base64
import hashlib
import json
import ntpath
import os
import re
import secrets
import stat
import time
import sqlite3

import filesystem_io as io

import config as C
import devices
import filesystem_permissions as permissions
import invocations

CHUNK_SIZE = 128 * 1024
MAX_FILE_SIZE = 4 * 1024**3
SCHEMA = """
CREATE TABLE IF NOT EXISTS filesystem_requests (
 device_id TEXT NOT NULL, nonce TEXT NOT NULL, at INTEGER NOT NULL,
 PRIMARY KEY(device_id, nonce)
);
CREATE TABLE IF NOT EXISTS filesystem_transfers (
 invocation_id TEXT NOT NULL, entry_index INTEGER NOT NULL, temp_name TEXT NOT NULL,
 destination TEXT NOT NULL, written INTEGER NOT NULL, inode INTEGER NOT NULL,
 created_at INTEGER NOT NULL, committed INTEGER NOT NULL DEFAULT 0,
 receipt_json TEXT,
 PRIMARY KEY(invocation_id, entry_index)
);
"""


def _error(code="ERR_FS_TRANSFER_INVALID"):
    raise permissions.PermissionError(code)


def _invocation(dev, body, db_path=None, *, conn=None, artifact=None):
    own = conn is None
    conn = invocations._open_db(db_path) if own else conn
    try:
        current = conn.execute("SELECT owner_user_id, revoked_at FROM devices WHERE id=?", (dev.id,)).fetchone()
        row = conn.execute("SELECT * FROM invocations WHERE invocation_id=? AND device_id=?",
                           (body.get("invocation_id"), dev.id)).fetchone()
    finally:
        if own:
            conn.close()
    if (current is None or current["revoked_at"] or current["owner_user_id"] != dev.owner_user_id or row is None or row["state"] != "delivered" or row["abandoned_by_turn"]
            or row["owner_user_id"] != dev.owner_user_id
            or (row["delivered_epoch"] or 0) + row["deadline_ms"] / 1000 < time.time()):
        _error("ERR_FS_INVOCATION_INVALID")
    payload = json.loads(row["payload_json"])
    if not invocations.verify_raw(invocations.server_public_key_b64(), row["server_sig"], invocations.canonical_bytes(payload)):
        _error("ERR_FS_INVOCATION_INVALID")
    artifact = artifact or invocations.load_executor_artifact(payload["executor"])
    if artifact.manifest_sha256 != payload["manifest_sha256"]:
        _error("ERR_FS_INVOCATION_INVALID")
    operation = permissions.contract(artifact.parsed, payload["args"])
    if operation is None or payload.get("operation", "invoke") != "invoke":
        _error("ERR_FS_CONTRACT_INVALID")
    return payload, operation, artifact


def copy_entries(args, device_style):
    entries, template = args.get("entries"), args.get("dst_template")
    if (not isinstance(entries, list) or not 0 <= len(entries) <= 100
            or not isinstance(template, str) or args.get("copy") is not True
            or args.get("allow_dirs") is True):
        _error("ERR_FS_BROKER_UNSUPPORTED")
    result = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _error()
        src = entry.get("path") or entry.get("src")
        if not isinstance(src, str):
            _error()
        # A Windows client can name a POSIX server path explicitly. Two POSIX
        # hosts require explicit locations; never guess from their equal syntax.
        source_scope = entry.get("source_location", args.get("source_location"))
        destination_scope = args.get("destination_location")
        if source_scope is None:
            source_scope = "server" if device_style == "windows" and src.startswith("/") else "device"
        name = ntpath.basename(src) if source_scope == "device" and device_style == "windows" else os.path.basename(src)
        stem, ext = os.path.splitext(name)
        fields = {**{k: v for k, v in entry.items() if isinstance(v, (str, int))}, "name": name, "stem": stem, "ext": ext}
        if re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", template).count("{") or re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", template).count("}"):
            _error("ERR_FS_BROKER_UNSUPPORTED")
        try:
            dst = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: str(fields[m[1]]), template)
        except KeyError:
            _error("ERR_FS_BROKER_UNSUPPORTED")
        if destination_scope is None:
            destination_scope = "server" if device_style == "windows" and dst.startswith("/") else "device"
        if source_scope not in {"device", "server"} or destination_scope not in {"device", "server"}:
            _error()
        src = permissions.normalize_path(src, "posix" if source_scope == "server" else device_style)
        dst = permissions.normalize_path(dst, "posix" if destination_scope == "server" else device_style)
        lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
        same_path = src.translate(lower) == dst.translate(lower) if source_scope == destination_scope == "device" and device_style == "windows" else src == dst
        if same_path and source_scope == destination_scope:
            _error()
        result.append({"index": i, "src": src, "dst": dst, "source_scope": source_scope, "destination_scope": destination_scope})
    if len({(e["destination_scope"], e["dst"].translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")) if device_style == "windows" and e["destination_scope"] == "device" else e["dst"]) for e in result}) != len(result):
        _error()
    return result


def _undo_sources(dev, payload, entries, conn):
    """Private blobs are readable only through a recorded effect's reversal.

    A device result cannot authorize a server-private path: the server must
    also have issued that exact backup receipt for the original destination.
    Destination permissions are always checked normally.
    """
    wanted = {(e["source_scope"], e["src"], e["destination_scope"], e["dst"]) for e in entries}
    found = {}
    rows = conn.execute(
        "SELECT invocation_id, payload_json, result_json FROM invocations "
        "WHERE device_id=? AND owner_user_id=? AND state IN ('done','failed') "
        "AND result_json IS NOT NULL AND json_extract(payload_json,'$.turn_id')=?",
        (dev.id, dev.owner_user_id, payload["turn_id"]))
    for row in rows:
        original = json.loads(row["payload_json"])
        if original.get("turn_id") != payload["turn_id"]:
            continue
        result = json.loads(row["result_json"])
        # The stored row wraps the device's answer in an envelope.
        answer = result.get("payload") if isinstance(result.get("payload"), dict) else result
        for effect in answer.get("results") or []:
            if not isinstance(effect, dict):
                continue
            blob, destination = effect.get("prev_blob_path") or effect.get("blob_path"), effect.get("dst") or effect.get("path")
            scope = effect.get("destination_scope", "device")
            if not isinstance(blob, str) or (scope, blob, scope, destination) not in wanted:
                continue
            basename = ntpath.basename(blob) if scope == "device" and dev.os_family == "windows" else os.path.basename(blob)
            if not re.fullmatch(r"[0-9a-f]{64}\.bin", basename):
                continue
            if scope == "server":
                root = C.PATH_USER_DATA / "_history" / payload["turn_id"] / "blob"
                if os.path.dirname(blob) != str(root):
                    continue
                receipts = conn.execute(
                    "SELECT receipt_json, destination FROM filesystem_transfers "
                    "WHERE invocation_id=? AND committed=1 AND receipt_json IS NOT NULL",
                    (row["invocation_id"],))
                if not any(r["destination"] == destination and
                           blob in (json.loads(r["receipt_json"]).get("prev_blob_path"),
                                    (json.loads(r["receipt_json"]).get("result") or {}).get("blob_path"))
                           for r in receipts):
                    continue
            found[(scope, blob, destination)] = {"path": blob, "sha256": basename[:-4],
                                                  "scope": scope, "destination": destination}
    return list(found.values())


def _undo_source(dev, payload, spec, conn):
    return next((item for item in _undo_sources(dev, payload, [spec], conn)
                 if item["path"] == spec["src"]), None)


def _rights(dev, payload, artifact, spec, conn, db_path):
    for scope, path, action in ((spec["source_scope"], spec["src"], "read"),
                                (spec["destination_scope"], spec["dst"], "write")):
        if action == "read" and spec["src"].endswith(".bin") and _undo_source(dev, payload, spec, conn):
            continue
        permissions.require(dev.owner_user_id, dev.id if scope == "device" else "server", path, action,
                            db_path=db_path, conn=conn, manifest=artifact.parsed, args=payload["args"])


def _fresh(dev, body, artifact, spec, conn, db_path):
    payload, _, _ = _invocation(dev, body, db_path, conn=conn, artifact=artifact)
    _rights(dev, payload, artifact, spec, conn, db_path)


def _row(conn, invocation_id, index):
    return conn.execute("SELECT * FROM filesystem_transfers WHERE invocation_id=? AND entry_index=?",
                        (invocation_id, index)).fetchone()


def _stage_file(parent, row):
    fd = io.open_file(parent, row["temp_name"], os.O_RDWR)
    if os.fstat(fd).st_ino != row["inode"] or os.fstat(fd).st_size != row["written"]:
        os.close(fd)
        _error("ERR_FS_TRANSFER_INVALID")
    return fd


def _transfer(dev, payload, artifact, spec, body, conn, db_path):
    action, index = body.get("action"), body["index"]
    key = (payload["invocation_id"], index)
    # Abort only removes a staging file belonging to this signed invocation.
    # It is allowed after a grant is revoked, so revocation cannot strand data.
    if action != "abort":
        _fresh(dev, body, artifact, spec, conn, db_path)
    if action == "read" and spec["source_scope"] == "server":
        offset = body.get("offset")
        if type(offset) is not int or not 0 <= offset <= MAX_FILE_SIZE:
            _error()
        with io.parent(spec["src"], internal=bool(_undo_source(dev, payload, spec, conn))) as (parent, name):
            fd = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(fd))
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, CHUNK_SIZE)
                if io.identity(io.regular(fd)) != before:
                    _error("ERR_FS_SOURCE_CHANGED")
                _fresh(dev, body, artifact, spec, conn, db_path)
                return {"content": base64.b64encode(data).decode(), "size": before[3],
                        "source_identity": ":".join(map(str, before)), "offset": offset}
            finally:
                os.close(fd)
    if spec["destination_scope"] != "server" or action not in {"write", "commit", "abort"}:
        _error()
    overwrite = payload["args"].get("overwrite") is True
    with io.parent(spec["dst"]) as (parent, name):
        row = _row(conn, *key)
        if action == "abort":
            if row and not row["committed"]:
                try:
                    os.unlink(row["temp_name"], dir_fd=parent)
                except FileNotFoundError:
                    pass
                conn.execute("DELETE FROM filesystem_transfers WHERE invocation_id=? AND entry_index=?", key)
                conn.commit()
            return {"aborted": True}
        if action == "write":
            content, offset = body.get("content"), body.get("offset")
            if (not isinstance(content, str) or len(content) > 4 * CHUNK_SIZE // 3 + 4
                    or type(offset) is not int or offset < 0):
                _error()
            try:
                data = base64.b64decode(content, validate=True)
            except ValueError:
                _error()
            if len(data) > CHUNK_SIZE or offset + len(data) > MAX_FILE_SIZE:
                _error("ERR_FS_SIZE_LIMIT")
            conn.execute("BEGIN IMMEDIATE")
            try:
                _fresh(dev, body, artifact, spec, conn, db_path)
                row = _row(conn, *key)
                if row is None:
                    if offset != 0:
                        _error()
                    temp, fd = io.stage(parent)
                    try:
                        inode = os.fstat(fd).st_ino
                    finally:
                        os.close(fd)
                    conn.execute("INSERT INTO filesystem_transfers VALUES(?,?,?,?,?,?,?,0,NULL)",
                                 (*key, temp, spec["dst"], 0, inode, int(time.time())))
                    row = _row(conn, *key)
                if row["committed"] or row["destination"] != spec["dst"]:
                    _error()
                fd = _stage_file(parent, row)
                try:
                    # Repeating a chunk after a lost reply is safe only when
                    # the exact bytes at that offset have already been stored.
                    if offset != row["written"]:
                        if offset + len(data) > row["written"]:
                            _error()
                        os.lseek(fd, offset, os.SEEK_SET)
                        if os.read(fd, len(data)) != data:
                            _error()
                        return {"written": row["written"]}
                    os.lseek(fd, offset, os.SEEK_SET)
                    with os.fdopen(os.dup(fd), "wb") as out:
                        out.write(data)
                    os.fsync(fd)
                    conn.execute("UPDATE filesystem_transfers SET written=? WHERE invocation_id=? AND entry_index=?",
                                 (offset + len(data), *key))
                finally:
                    os.close(fd)
                conn.commit()
                return {"written": offset + len(data)}
            finally:
                if conn.in_transaction:
                    conn.rollback()
        if row is None or row["destination"] != spec["dst"]:
            _error()
        digest = body.get("sha256")
        if (type(body.get("size")) is not int or body["size"] != row["written"]
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            _error()
        if row["committed"]:
            receipt = json.loads(row["receipt_json"])
            if receipt["sha256"] != digest:
                _error("ERR_FS_HASH_MISMATCH")
            return receipt
        check = lambda: _fresh(dev, body, artifact, spec, conn, db_path)
        try:
            fd = _stage_file(parent, row)
        except FileNotFoundError:
            # Recover a commit whose atomic rename completed before its final
            # database acknowledgement. Never manufacture a second operation.
            receipt = json.loads(row["receipt_json"] or "null")
            if not receipt:
                raise
            fd = io.open_file(parent, name)
            try:
                if (os.fstat(fd).st_ino != row["inode"] or os.fstat(fd).st_size != row["written"]
                        or io.digest(fd, check) != digest or receipt["sha256"] != digest):
                    _error("ERR_FS_HASH_MISMATCH")
            finally:
                os.close(fd)
            conn.execute("UPDATE filesystem_transfers SET committed=1 WHERE invocation_id=? AND entry_index=?", key)
            conn.commit()
            return receipt
        try:
            if io.digest(fd, check) != digest:
                _error("ERR_FS_HASH_MISMATCH")
            previous = io.existing(parent, name)
            try:
                if previous is not None and not overwrite:
                    _error("ERR_FS_DEST_EXISTS")
                if previous is not None:
                    permissions.require(dev.owner_user_id, "server", spec["dst"], "write", conn=conn,
                                        db_path=db_path, manifest=artifact.parsed, args=payload["args"], overwrite=True)
                before = io.identity(io.regular(previous)) if previous is not None else None
                blob = io.backup(previous, payload["turn_id"], check) if previous is not None else None
                receipt = {"committed": True, "size": row["written"], "sha256": digest,
                           "created": previous is None, "prev_blob_path": blob, "destination_scope": "server"}
                # Persist the recovery receipt before the only visible change.
                conn.execute("UPDATE filesystem_transfers SET receipt_json=? WHERE invocation_id=? AND entry_index=?",
                             (json.dumps(receipt), *key))
                conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    check()
                    current = io.existing(parent, name)
                    try:
                        if (io.identity(io.regular(current)) if current is not None else None) != before:
                            _error("ERR_FS_SOURCE_CHANGED")
                    finally:
                        if current is not None:
                            os.close(current)
                    if previous is not None:
                        permissions.require(dev.owner_user_id, "server", spec["dst"], "write", conn=conn,
                                            db_path=db_path, manifest=artifact.parsed, args=payload["args"], overwrite=True)
                    os.fsync(fd)
                    io.commit(parent, row["temp_name"], name, overwrite=previous is not None)
                    conn.execute("UPDATE filesystem_transfers SET committed=1 WHERE invocation_id=? AND entry_index=?", key)
                    conn.commit()
                finally:
                    if conn.in_transaction:
                        conn.rollback()
                return receipt
            finally:
                if previous is not None:
                    os.close(previous)
        finally:
            os.close(fd)


def _delete(dev, payload, artifact, body, conn, db_path):
    paths = payload["args"].get("paths")
    index = body.get("index")
    if (payload["args"].get("location") != "server" or not isinstance(paths, list)
            or len(paths) > 100 or type(index) is not int or not 0 <= index < len(paths)):
        _error()
    path = permissions.normalize_path(paths[index], "posix")
    key = (payload["invocation_id"], index)
    def check():
        _invocation(dev, body, db_path, conn=conn, artifact=artifact)
        permissions.require(dev.owner_user_id, "server", path, "write", db_path=db_path,
                            conn=conn, manifest=artifact.parsed, args=payload["args"])
    check()
    row = _row(conn, *key)
    if row and (row["destination"] != path or row["temp_name"] != ""):
        _error()
    if row and row["committed"]:
        return json.loads(row["receipt_json"])
    with io.parent(path) as (parent, name):
        fd = io.existing(parent, name)
        try:
            if row:
                receipt = json.loads(row["receipt_json"])
                if fd is not None and io.identity(io.regular(fd)) != tuple(receipt["identity"]):
                    _error("ERR_FS_SOURCE_CHANGED")
            else:
                if fd is None:
                    raise FileNotFoundError(path)
                before = io.identity(io.regular(fd))
                blob = io.backup(fd, payload["turn_id"], check)
                receipt = {"result": {"path": path, "deleted": True, "blob_path": blob,
                                      "restore_mode": "create", "destination_scope": "server"},
                           "identity": before}
                conn.execute("INSERT INTO filesystem_transfers VALUES(?,?,?,?,?,?,?,0,?)",
                             (*key, "", path, before[3], before[1], int(time.time()), json.dumps(receipt)))
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
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
                conn.execute("UPDATE filesystem_transfers SET committed=1 WHERE invocation_id=? AND entry_index=?", key)
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
            return receipt
        finally:
            if fd is not None:
                os.close(fd)


def respond(dev, body, *, db_path=None):
    nonce, issued_at = body.get("nonce"), body.get("issued_at")
    if not isinstance(nonce, str) or not re.fullmatch("[0-9a-f]{48}", nonce):
        _error()
    if type(issued_at) is not int or abs(int(time.time()) - issued_at) > 60:
        _error("ERR_FS_REQUEST_EXPIRED")
    conn = invocations._open_db(db_path)
    conn.executescript(permissions.SCHEMA + SCHEMA)
    try:
        payload, operation, artifact = _invocation(dev, body, db_path, conn=conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM filesystem_requests WHERE at<?", (int(time.time()) - 120,))
            conn.execute("INSERT INTO filesystem_requests VALUES(?,?,?)", (dev.id, nonce, int(time.time())))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            _error("ERR_FS_REQUEST_EXPIRED")
        result = {"domain": "metnos-filesystem-v1", "nonce": nonce, "device_id": dev.id,
                  "invocation_id": payload["invocation_id"], "manifest_sha256": payload["manifest_sha256"],
                  "expires_at": int(time.time()) + 5, "operation": operation}
        entries = copy_entries(payload["args"], "windows" if dev.os_family == "windows" else "posix") if operation == "copy" else None
        if body.get("action") == "authority":
            grants = permissions.list_grants(dev.owner_user_id, dev.id, db_path=db_path, active_only=True, conn=conn)
            result["grants"] = [{k: g[k] for k in ("id", "path", "path_style", "kind", "recursive", "can_read", "can_write", "can_overwrite", "expires_at")} for g in grants]
            if entries is not None:
                result["copy_entries"] = entries
                result["undo_sources"] = _undo_sources(dev, payload, entries, conn)
        elif body.get("action") == "delete" and operation == "delete":
            result.update(_delete(dev, payload, artifact, body, conn, db_path))
        else:
            index = body.get("index")
            if entries is None or type(index) is not int or not 0 <= index < len(entries):
                _error()
            result.update(_transfer(dev, payload, artifact, entries[index], body, conn, db_path))
        result["expires_at"] = int(time.time()) + 5
        result["server_sig"] = invocations.sign_payload(result)
        return result
    finally:
        conn.close()


def cleanup_stale_transfers(*, db_path=None):
    """Remove only our pinned staging files, after their authority has ended."""
    conn = invocations._open_db(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='filesystem_transfers'").fetchone():
            return 0
        rows = conn.execute(
            "SELECT f.* FROM filesystem_transfers f JOIN invocations i USING(invocation_id) "
            "JOIN devices d ON d.id=i.device_id WHERE f.committed=0 AND "
            "(i.state<>'delivered' OR i.abandoned_by_turn=1 OR d.revoked_at IS NOT NULL "
            "OR COALESCE(i.delivered_epoch,0)+i.deadline_ms/1000.0<?) LIMIT 128", (time.time(),)).fetchall()
        removed = 0
        for row in rows:
            if not re.fullmatch(r"\.metnos-transfer-[0-9a-f]{48}", row["temp_name"]):
                continue  # a prepared deletion is an audit receipt, not a stage
            try:
                with io.parent(row["destination"]) as (parent, _):
                    info = os.stat(row["temp_name"], dir_fd=parent, follow_symlinks=False)
                    if info.st_ino != row["inode"] or not stat.S_ISREG(info.st_mode):
                        continue
                    os.unlink(row["temp_name"], dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                continue
            conn.execute("DELETE FROM filesystem_transfers WHERE invocation_id=? AND entry_index=? AND committed=0",
                         (row["invocation_id"], row["entry_index"]))
            removed += 1
        conn.commit()
        return removed
    finally:
        conn.close()

"""Fresh, signed file-broker authority and bounded device/server transfers.

The device requests no arbitrary server path: each path is derived from an
authenticated, live invocation. Every chunk and commit rechecks current grants.
Server filesystem access uses directory descriptors and refuses links.
"""
from __future__ import annotations

import json
import ntpath
import os
import re
import time
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import filesystem_transfer_io
import server_filesystem_host as host
import server_filesystem_protocol as protocol

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
 receipt_json TEXT, backend TEXT NOT NULL DEFAULT 'native',
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
        item = {"index": i, "src": src, "dst": dst, "source_scope": source_scope, "destination_scope": destination_scope}
        expected = entry.get("expected_sha256")
        if expected is not None:
            if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
                _error()
            item["expected_sha256"] = expected
        result.append(item)
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
                receipts = conn.execute(
                    "SELECT receipt_json, destination, backend FROM filesystem_transfers "
                    "WHERE invocation_id=? AND committed=1 AND receipt_json IS NOT NULL",
                    (row["invocation_id"],))
                verified = False
                for receipt_row in receipts:
                    recorded = json.loads(receipt_row["receipt_json"])
                    if (receipt_row["destination"] != destination or blob not in
                            (recorded.get("prev_blob_path"), (recorded.get("result") or {}).get("blob_path"))):
                        continue
                    if os.path.dirname(blob) == str(_history_root(receipt_row["backend"], payload["turn_id"])):
                        verified = True
                        break
                if not verified:
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


def _ensure_schema(conn):
    conn.executescript(permissions.SCHEMA + SCHEMA)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "backend" not in {r[1] for r in conn.execute("PRAGMA table_info(filesystem_transfers)")}:
            conn.execute("ALTER TABLE filesystem_transfers ADD COLUMN backend TEXT NOT NULL DEFAULT 'native'")
        conn.commit()
    finally:
        if conn.in_transaction:
            conn.rollback()


def _state(row):
    if row is None:
        return None
    return {**{key: row[key] for key in ("temp_name", "destination", "written", "inode", "created_at", "committed")},
            "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None}


def _history_root(backend, turn_id, binding=None):
    if backend == "host":
        binding = binding or host.configuration()
        if binding is None:
            _error("ERR_FS_HOST_UNAVAILABLE")
        data = Path(binding["state_dir"]) / "data"
    else:
        data = C.PATH_USER_DATA
    return data / "_history" / turn_id / "blob"


class _TransferAuthority:
    """One signed entry and its receipt; the helper never receives a database."""
    def __init__(self, conn, key, transfer, backend, fresh, overwrite, turn_id, binding):
        self.conn, self.key, self.transfer, self.backend = conn, key, transfer, backend
        self.fresh, self.overwrite = fresh, overwrite
        self.turn_id, self.binding = turn_id, binding
        self.current = _state(_row(conn, *key))
        self.active = False

    def check(self, path, action, **kwargs):
        expected = "read" if self.transfer["action"] == "read" and not self.transfer["internal"] else "write"
        if (path != self.transfer["check_path"] or action != expected
                or self.transfer["action"] == "abort" or set(kwargs) - {"overwrite"}
                or kwargs.get("overwrite") and (action != "write" or not self.transfer["overwrite"])):
            _error("ERR_FS_AUTHORITY_INVALID")
        io.reserved(path)
        self.fresh()
        if kwargs.get("overwrite"):
            self.overwrite()

    @contextmanager
    def mutation(self):
        if self.active or self.transfer["action"] == "read":
            _error("ERR_FS_AUTHORITY_INVALID")
        old = self.current
        self.conn.execute("BEGIN IMMEDIATE")
        self.active = True
        try:
            stored = _row(self.conn, *self.key)
            if (_state(stored) != self.current
                    or stored is not None and stored["backend"] != self.backend):
                _error("ERR_FS_TRANSFER_INVALID")
            if self.transfer["action"] != "abort":
                self.fresh()
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            self.current = old
            raise
        finally:
            self.active = False

    def checkpoint(self, row):
        if not self.active:
            with self.mutation():
                self.checkpoint(row)
            return
        protocol._transfer_row(row)
        action = self.transfer["action"]
        if row is None:
            if action != "abort" or self.current is None or self.current["committed"]:
                _error("ERR_FS_AUTHORITY_INVALID")
            self.conn.execute("DELETE FROM filesystem_transfers WHERE invocation_id=? AND entry_index=?", self.key)
        else:
            if (action not in {"write", "commit", "delete"} or row["destination"] != self.transfer["path"]
                    or self.current is not None and self.current["committed"]):
                _error("ERR_FS_AUTHORITY_INVALID")
            # Inode 0 is a staging name made durable before its file exists.
            pinned = ("temp_name", "destination", "created_at")
            if self.current is not None and any(row[k] != self.current[k] for k in (
                    pinned + ("inode",) if self.current["inode"] else pinned)):
                _error("ERR_FS_AUTHORITY_INVALID")
            if action == "write":
                import base64
                expected = self.transfer["offset"] + len(base64.b64decode(self.transfer["content"], validate=True))
                reserving = self.current is None and row["inode"] == 0 and row["written"] == 0
                if (not row["temp_name"] or row["committed"] or row["receipt"] is not None
                        or not reserving and (row["written"] != expected or row["inode"] == 0
                            or self.transfer["offset"] != (self.current["written"] if self.current else 0)
                            or self.current and self.current["receipt"] is not None)):
                    _error("ERR_FS_AUTHORITY_INVALID")
            elif action == "commit":
                receipt = row["receipt"]
                if (self.current is None or row["written"] != self.current["written"]
                        or not isinstance(receipt, dict)
                        or set(receipt) != {"committed", "size", "sha256", "created", "prev_blob_path", "destination_scope"}
                        or receipt["committed"] is not True or receipt["size"] != self.transfer["size"]
                        or receipt["sha256"] != self.transfer["sha256"] or type(receipt["created"]) is not bool
                        or receipt["destination_scope"] != "server"
                        or receipt["created"] and receipt["prev_blob_path"] is not None):
                    _error("ERR_FS_AUTHORITY_INVALID")
                self._blob(receipt["prev_blob_path"], optional=receipt["created"])
                if not receipt["created"]:
                    self.check(self.transfer["check_path"], "write", overwrite=True)
            else:
                receipt = row["receipt"]
                if not isinstance(receipt, dict) or set(receipt) != {"result", "identity"} or row["temp_name"]:
                    _error("ERR_FS_AUTHORITY_INVALID")
                effect, identity = receipt["result"], receipt["identity"]
                if (not isinstance(effect, dict) or set(effect) != {"path", "deleted", "blob_path", "restore_mode", "destination_scope"}
                        or effect["path"] != self.transfer["path"] or effect["deleted"] is not True
                        or effect["restore_mode"] != "create" or effect["destination_scope"] != "server"
                        or not isinstance(identity, (list, tuple)) or len(identity) != 4
                        or identity[1] != row["inode"] or identity[3] != row["written"]):
                    _error("ERR_FS_AUTHORITY_INVALID")
                self._blob(effect["blob_path"])
            if row["committed"] and (self.current is None or self.current["receipt"] != json.loads(json.dumps(row["receipt"]))):
                _error("ERR_FS_AUTHORITY_INVALID")
            self.conn.execute(
                "INSERT INTO filesystem_transfers(invocation_id,entry_index,temp_name,destination,written,inode,created_at,committed,receipt_json,backend) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(invocation_id,entry_index) DO UPDATE SET "
                "written=excluded.written,inode=excluded.inode,committed=excluded.committed,receipt_json=excluded.receipt_json",
                (*self.key, row["temp_name"], row["destination"], row["written"], row["inode"], row["created_at"],
                 row["committed"], json.dumps(row["receipt"]) if row["receipt"] is not None else None, self.backend))
        self.current = json.loads(json.dumps(row))

    def _blob(self, path, optional=False):
        if optional and path is None:
            return
        if (not isinstance(path, str) or os.path.dirname(path) != str(_history_root(self.backend, self.turn_id, self.binding))
                or not re.fullmatch(r"[0-9a-f]{64}\.bin", os.path.basename(path))):
            _error("ERR_FS_AUTHORITY_INVALID")


def _dispatch(dev, payload, body, conn, transfer, fresh, overwrite, *, backend=None):
    row = _row(conn, payload["invocation_id"], body["index"])
    binding = host.configuration()
    if backend is None:
        backend = row["backend"] if row is not None and transfer["action"] != "read" else "host" if binding and not host._workspace(transfer["path"]) else "native"
    if backend not in {"native", "host"} or backend == "host" and binding is None:
        _error("ERR_FS_HOST_UNAVAILABLE")
    request = {"action": "transfer", "owner": dev.owner_user_id, "turn_id": payload["turn_id"], "transfer": transfer}
    protocol.validate_request(request)
    authority = _TransferAuthority(conn, (payload["invocation_id"], body["index"]), transfer,
                                   backend, fresh, overwrite, payload["turn_id"], binding)
    if backend == "host":
        return host._exchange(binding, request, authority)
    return filesystem_transfer_io.execute(transfer, payload["turn_id"], authority)


def _transfer(dev, payload, artifact, spec, body, conn, db_path):
    action = body.get("action")
    undo = _undo_source(dev, payload, spec, conn) if action == "read" else None
    if action == "read" and spec["source_scope"] == "server":
        path, state = spec["src"], None
    elif action in {"write", "commit", "abort"} and spec["destination_scope"] == "server":
        path, state = spec["dst"], _state(_row(conn, payload["invocation_id"], body["index"]))
    else:
        _error()
    transfer = {"action": action, "path": path, "check_path": spec["dst"] if undo else path,
                "internal": bool(undo), "overwrite": payload["args"].get("overwrite") is True, "row": state,
                **{key: body.get(key) if key in {"read": {"offset"}, "write": {"offset", "content"},
                    "commit": {"size", "sha256"}, "abort": set()}[action] else None
                   for key in ("offset", "content", "size", "sha256")},
                "expected_sha256": spec.get("expected_sha256") if action == "commit" else None}
    backend = None
    if undo:
        backend = "native" if os.path.dirname(path) == str(_history_root("native", payload["turn_id"])) else "host"
    return _dispatch(dev, payload, body, conn, transfer,
        lambda: _fresh(dev, body, artifact, spec, conn, db_path),
        lambda: permissions.require(dev.owner_user_id, "server", spec["dst"], "write", conn=conn,
            db_path=db_path, manifest=artifact.parsed, args=payload["args"], overwrite=True), backend=backend)


def _delete(dev, payload, artifact, body, conn, db_path):
    paths, index = payload["args"].get("paths"), body.get("index")
    if (payload["args"].get("location") != "server" or not isinstance(paths, list)
            or len(paths) > 100 or type(index) is not int or not 0 <= index < len(paths)):
        _error()
    path = permissions.normalize_path(paths[index], "posix")
    def check():
        _invocation(dev, body, db_path, conn=conn, artifact=artifact)
        permissions.require(dev.owner_user_id, "server", path, "write", db_path=db_path,
                            conn=conn, manifest=artifact.parsed, args=payload["args"])
    expected = payload["args"].get("expected_sha256")
    transfer = {"action": "delete", "path": path, "check_path": path, "internal": False, "overwrite": False,
                "row": _state(_row(conn, payload["invocation_id"], index)),
                "offset": None, "content": None, "size": None, "sha256": None,
                "expected_sha256": expected.get(paths[index]) if isinstance(expected, dict) else None}
    return _dispatch(dev, payload, body, conn, transfer, check, check)


def respond(dev, body, *, db_path=None):
    nonce, issued_at = body.get("nonce"), body.get("issued_at")
    if not isinstance(nonce, str) or not re.fullmatch("[0-9a-f]{48}", nonce):
        _error()
    if type(issued_at) is not int or abs(int(time.time()) - issued_at) > 60:
        _error("ERR_FS_REQUEST_EXPIRED")
    conn = invocations._open_db(db_path)
    _ensure_schema(conn)
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
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT f.*, i.owner_user_id, i.payload_json FROM filesystem_transfers f JOIN invocations i USING(invocation_id) "
            "JOIN devices d ON d.id=i.device_id WHERE f.committed=0 AND "
            "(i.state<>'delivered' OR i.abandoned_by_turn=1 OR d.revoked_at IS NOT NULL "
            "OR COALESCE(i.delivered_epoch,0)+i.deadline_ms/1000.0<?) LIMIT 128", (time.time(),)).fetchall()
        removed = 0
        for row in rows:
            if not re.fullmatch(r"\.metnos-transfer-[0-9a-f]{48}", row["temp_name"]):
                continue
            transfer = {"action": "abort", "path": row["destination"], "check_path": row["destination"],
                        "internal": False, "overwrite": False, "row": _state(row),
                        "offset": None, "content": None, "size": None, "sha256": None,
                        "expected_sha256": None}
            try:
                from types import SimpleNamespace
                _dispatch(SimpleNamespace(owner_user_id=row["owner_user_id"]), json.loads(row["payload_json"]),
                          {"index": row["entry_index"]}, conn, transfer, lambda: None, lambda: None,
                          backend=row["backend"])
                removed += 1
            except (OSError, ValueError):
                continue
        return removed
    finally:
        conn.close()

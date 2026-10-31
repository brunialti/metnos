"""Closed operations for additional server scopes; existing default paths stay unchanged.

Only signed declarative filesystem contracts enter this broker. It never runs
executor code with a widened parent-directory bind. Paths stay pinned to directory
descriptors, and each chunk/atomic mutation checks the account's current grants.
"""
from __future__ import annotations

import base64
import json
import os
import time

import filesystem_io as io
import filesystem_permissions as permissions

MAX_READ = 256 * 1024


def manifest_for(executor):
    return {"filesystem": getattr(executor, "filesystem", None),
            "capabilities": getattr(executor, "capabilities", []),
            "args": getattr(executor, "args_schema", {})}


def write_specs(args):
    from backends.files.local import _collect_write_specs
    specs, error = _collect_write_specs(args)
    if error or specs is None:
        raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
    return specs


def paths(args):
    result = args.get("paths")
    if result is None:
        result = [e.get("path") for e in args["entries"]] if isinstance(args.get("entries"), list) else [args.get("path")]
    if not isinstance(result, list) or len(result) > 100:
        raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
    return [permissions.normalize_path(p, "posix") for p in result]


def copy_specs(args):
    from device_filesystem import copy_entries
    if args.get("source_location", "server") != "server" or args.get("destination_location", "server") != "server":
        raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
    return copy_entries({**args, "source_location": "server", "destination_location": "server"}, "posix")


def requests(operation, args):
    if operation == "list":
        return [(permissions.normalize_path(args.get("path"), "posix"), "read")]
    if operation == "read":
        return [(p, "read") for p in paths(args)]
    if operation == "write":
        return [(permissions.normalize_path(e["path"], "posix"), "write") for e in write_specs(args)]
    if operation == "delete":
        return [(p, "write") for p in paths(args)]
    if operation == "copy":
        return [(e[k], action) for e in copy_specs(args) for k, action in (("src", "read"), ("dst", "write"))]
    raise permissions.PermissionError("ERR_FS_CONTRACT_INVALID")


def needs_extension(owner, manifest, args):
    operation = permissions.contract(manifest, args)
    if operation is None:
        return False
    return any(not permissions.server_default_permits(owner, manifest, args, path, action)
               for path, action in requests(operation, args))


class Broker:
    def __init__(self, owner, manifest, args, turn_id, *, timeout_s=30, db_path=None):
        if not owner:
            raise permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED")
        self.owner, self.manifest, self.args, self.turn_id = owner, manifest, args, turn_id
        self.deadline, self.db_path = time.monotonic() + timeout_s, db_path

    def check(self, path, action, *, conn=None, **kwargs):
        if time.monotonic() > self.deadline:
            raise permissions.PermissionError("ERR_FS_INVOCATION_INVALID")
        io.reserved(path)
        permissions.require(self.owner, "server", path, action, conn=conn, db_path=self.db_path,
                            manifest=self.manifest, args=self.args, **kwargs)

    def replace(self, path, producer, *, overwrite=True, mode="overwrite", final_check=None):
        self.check(path, "write")
        with io.parent(path) as (parent, name):
            previous = io.existing(parent, name)
            temp = None
            try:
                if previous is not None:
                    if mode == "skip_if_exists":
                        return {"path": path, "skipped": True, "created": False, "bytes_written": 0}
                    if not overwrite or mode == "fail_if_exists":
                        raise permissions.PermissionError("ERR_FS_DEST_EXISTS")
                    self.check(path, "write", overwrite=True)
                before = io.identity(io.regular(previous)) if previous is not None else None
                check = lambda: self.check(path, "write", overwrite=previous is not None)
                blob = io.backup(previous, self.turn_id, check) if previous is not None else None
                temp, out = io.stage(parent)
                try:
                    if mode == "append" and previous is not None:
                        while chunk := os.read(previous, io.CHUNK_SIZE):
                            check()
                            io.write_all(out, chunk)
                    count, digest = producer(out)
                    if os.fstat(out).st_size > io.MAX_FILE_SIZE:
                        raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
                    os.fsync(out)
                    actual_digest = io.digest(out, check)
                    if actual_digest != digest and mode != "append":
                        raise permissions.PermissionError("ERR_FS_HASH_MISMATCH")
                    digest = actual_digest
                    # Grant revocation and the visible mutation serialize on
                    # one short transaction; hashing/backup holds no write lock.
                    conn = permissions._open(self.db_path)
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        self.check(path, "write", conn=conn, overwrite=previous is not None)
                        if final_check is not None:
                            final_check(conn)
                        current = io.existing(parent, name)
                        try:
                            if (io.identity(io.regular(current)) if current is not None else None) != before:
                                raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                        finally:
                            if current is not None:
                                os.close(current)
                        io.commit(parent, temp, name, overwrite=previous is not None)
                        temp = None
                        conn.commit()
                    finally:
                        conn.close()
                    return {"path": path, "created": previous is None, "prev_blob_path": blob,
                            "bytes_written": count, "sha256": digest}
                finally:
                    os.close(out)
            finally:
                if previous is not None:
                    os.close(previous)
                if temp is not None:
                    os.unlink(temp, dir_fd=parent)

    def write(self, spec):
        import hashlib
        path = permissions.normalize_path(spec["path"], "posix")
        content, encoding, mode = spec.get("content"), spec.get("encoding", "utf-8"), spec.get("mode", "overwrite")
        if not isinstance(content, str) or mode not in {"overwrite", "append", "fail_if_exists", "skip_if_exists"}:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        if encoding == "binary":
            data = base64.b64decode(content, validate=True)
        elif encoding in {"utf-8", "latin-1"}:
            data = content.encode(encoding)
        else:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        if len(data) > MAX_READ:
            raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
        def produce(out):
            with os.fdopen(os.dup(out), "wb") as stream:
                stream.write(data)
            return len(data), hashlib.sha256(data).hexdigest()
        return self.replace(path, produce, mode=mode)

    def read(self, path):
        self.check(path, "read")
        if self.args.get("pattern") or self.args.get("parse") == "auto":
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        with io.parent(path) as (parent, name):
            fd = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(fd))
                cap, tail = self.args.get("max_bytes", 0) or 0, self.args.get("tail_bytes", 0) or 0
                if type(cap) is not int or type(tail) is not int or cap < 0 or tail < 0 or cap and tail:
                    raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
                offset = max(0, before[3] - tail) if tail else self.args.get("offset", 0)
                if type(offset) is not int or offset < 0:
                    raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, min((tail or cap or MAX_READ), MAX_READ))
                if io.identity(io.regular(fd)) != before:
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                self.check(path, "read")
                encoding = self.args.get("encoding", "utf-8")
                content = base64.b64encode(data).decode() if encoding == "binary" else data.decode(encoding)
                entry = {"path": path, "content": content, "size": before[3], "bytes_read": len(data),
                         "offset": offset, "encoding": encoding, "truncated": offset + len(data) < before[3]}
                if self.args.get("parse") == "json":
                    parsed = json.loads(content)
                    entry.update({k: v for k, v in parsed.items() if k != "path"} if isinstance(parsed, dict) else {"value": parsed})
                return entry
            finally:
                os.close(fd)

    def copy(self, spec):
        import hashlib
        src, dst = spec["src"], spec["dst"]
        self.check(src, "read")
        with io.parent(src) as (parent, name):
            source = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(source))
                def produce(out):
                    digest, size = hashlib.sha256(), 0
                    while chunk := os.read(source, io.CHUNK_SIZE):
                        self.check(src, "read")
                        self.check(dst, "write")
                        if size + len(chunk) > io.MAX_FILE_SIZE:
                            raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
                        io.write_all(out, chunk)
                        size += len(chunk)
                        digest.update(chunk)
                    if io.identity(io.regular(source)) != before:
                        raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    self.check(src, "read")
                    return size, digest.hexdigest()
                def final_check(conn):
                    self.check(src, "read", conn=conn)
                    if io.identity(io.regular(source)) != before:
                        raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                result = self.replace(dst, produce, overwrite=self.args.get("overwrite") is True,
                                      final_check=final_check)
                return {**result, "src": src, "dst": dst, "copy": True, "source_scope": "server", "destination_scope": "server"}
            finally:
                os.close(source)

    def delete(self, path):
        self.check(path, "write")
        with io.parent(path) as (parent, name):
            fd = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(fd))
                blob = io.backup(fd, self.turn_id, lambda: self.check(path, "write"))
                conn = permissions._open(self.db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    self.check(path, "write", conn=conn)
                    current = io.open_file(parent, name)
                    try:
                        if io.identity(io.regular(current)) != before:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    finally:
                        os.close(current)
                    os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
                    conn.commit()
                finally:
                    conn.close()
                return {"path": path, "blob_path": blob, "deleted": True, "restore_mode": "create"}
            finally:
                os.close(fd)

    def list(self, path):
        recursive = self.args.get("recursive") is True
        cap = self.args.get("max_results", 1000) or 1000
        depth = self.args.get("max_depth", 10) or 64
        if type(cap) is not int or type(depth) is not int or cap < 0 or depth < 0:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        cap, depth = min(cap, 1000), min(depth, 64)
        todo, entries, truncated, output_bytes = [(path, 0)], [], False, 0
        while todo:
            current, level = todo.pop()
            self.check(current, "read", directory=True, recursive=recursive)
            with io.parent(current) as (parent, name):
                fd = os.open(name or "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    with os.scandir(fd) as listing:
                        for entry in listing:
                            st = entry.stat(follow_symlinks=False)
                            item = {"path": os.path.join(current, entry.name), "name": entry.name,
                                    "type": "symlink" if entry.is_symlink() else "dir" if entry.is_dir(follow_symlinks=False) else "file",
                                    "size": st.st_size, "mtime_epoch": st.st_mtime}
                            output_bytes += len(json.dumps(item).encode())
                            if len(entries) >= cap or output_bytes > 256 * 1024:
                                truncated = True
                                break
                            entries.append(item)
                            if recursive and item["type"] == "dir":
                                if level + 1 < depth:
                                    todo.append((item["path"], level + 1))
                                else:
                                    truncated = True
                finally:
                    os.close(fd)
            if len(entries) >= cap or output_bytes > 256 * 1024:
                break
        sort = self.args.get("sort_by", "name")
        if sort not in {"name", "size", "mtime"}:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        entries.sort(key=lambda e: e["name"].casefold() if sort == "name" else e["size" if sort == "size" else "mtime_epoch"], reverse=sort != "name")
        return {"ok": True, "ok_count": len(entries), "entries": entries, "truncated": truncated,
                "truncated_what": "entries", "used": len(entries), "cap_field": "max_results", "cap_value": cap,
                "source_complete": not truncated, "metadata": {"path": path, "truncated": truncated, "source_complete": not truncated}}


def error_result(error):
    from messages import get as _msg
    code = str(error) if isinstance(error, permissions.PermissionError) else "ERR_FS_IO"
    return {"ok": False, "ok_count": 0, "fail_count": 1, "results": [], "failed": [],
            "error_code": code, "error": _msg(code), "error_class": "filesystem_permission"}


def execute(owner, manifest, args, turn_id, *, timeout_s=30, db_path=None):
    try:
        operation = permissions.contract(manifest, args)
        broker = Broker(owner, manifest, args, turn_id, timeout_s=timeout_s, db_path=db_path)
        if operation == "list":
            return broker.list(permissions.normalize_path(args.get("path"), "posix"))
        specs = write_specs(args) if operation == "write" else copy_specs(args) if operation == "copy" else paths(args)
        if len(specs) > 100:
            raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
        results, failed = [], []
        for index, spec in enumerate(specs):
            try:
                results.append(getattr(broker, operation)(spec))
            except (OSError, ValueError, TypeError, LookupError) as exc:
                failure = error_result(exc)
                failed.append({"index": index, "error": failure["error"], "error_code": failure["error_code"]})
        value = {"ok": not failed, "ok_count": len(results), "fail_count": len(failed),
                 "entries" if operation == "read" else "results": results, "failed": failed, "dirs_created": []}
        if failed:
            value.update({"error": failed[0]["error"], "error_code": failed[0]["error_code"], "error_class": "filesystem_permission"})
        if operation in {"copy", "write", "delete"}:
            value["_filesystem"] = {"owner": owner, "manifest": manifest, "args": args, "turn_id": turn_id}
        return value
    except (OSError, ValueError, TypeError, LookupError) as exc:
        return error_result(exc)


def undo_stage(pattern, results, *, db_path=None, actor=None):
    """Undo only recorded native effects, under the account's current rights."""
    import devices
    import hashlib
    import re
    import config as C
    context = results["_filesystem"]
    owner = devices.owner_id_for_actor(actor or os.environ.get("METNOS_ACTOR") or "host")
    if owner != context["owner"]:
        return error_result(permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED"))
    broker = Broker(owner, context["manifest"], context["args"], context["turn_id"], db_path=db_path)
    done, failed = [], []
    for index, entry in enumerate(results.get("results") or []):
        path = entry.get("dst") or entry.get("path")
        if not path or entry.get("skipped"):
            continue
        remove = pattern in {"swap_src_dst", "delete_created_paths"} and entry.get("created")
        blob = (entry.get("prev_blob_path") or entry.get("blob_path")) if pattern == "restore_blob_backup" else None
        if not remove and not blob:
            continue
        try:
            # An undo does not overwrite a newer edit to a created/replaced file.
            if entry.get("sha256"):
                broker.check(path, "write")
                with io.parent(path) as (parent, name):
                    fd = io.open_file(parent, name)
                    try:
                        if io.digest(fd, lambda: broker.check(path, "write")) != entry["sha256"]:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    finally:
                        os.close(fd)
            if remove:
                done.append(broker.delete(path))
            else:
                root = C.PATH_USER_DATA / "_history" / context["turn_id"] / "blob"
                if os.path.dirname(blob) != str(root) or not re.fullmatch("[0-9a-f]{64}\\.bin", os.path.basename(blob)):
                    raise permissions.PermissionError("ERR_FS_RESERVED_PATH")
                with io.parent(blob, internal=True) as (parent, name):
                    source = io.open_file(parent, name)
                    try:
                        expected = os.path.basename(blob)[:-4]
                        def produce(out):
                            hasher, size = hashlib.sha256(), 0
                            while chunk := os.read(source, io.CHUNK_SIZE):
                                broker.check(path, "write")
                                io.write_all(out, chunk)
                                hasher.update(chunk)
                                size += len(chunk)
                            if hasher.hexdigest() != expected:
                                raise permissions.PermissionError("ERR_FS_HASH_MISMATCH")
                            return size, expected
                        done.append(broker.replace(path, produce, overwrite=entry.get("restore_mode") != "create"))
                    finally:
                        os.close(source)
        except (OSError, ValueError, TypeError, LookupError) as exc:
            failed.append({"index": index, **error_result(exc)})
    return {"ok": not failed, "ok_count": len(done), "fail_count": len(failed), "results": done, "failed": failed}

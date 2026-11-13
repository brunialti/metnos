"""Closed operations for explicit server grants and additional server scopes.

Only signed declarative filesystem contracts enter this broker. It never runs
executor code with a widened parent-directory bind. Paths stay pinned to directory
descriptors, and each chunk/atomic mutation checks the account's current grants.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fnmatch
import json
import os
import time

import filesystem_io as io
import filesystem_permissions as permissions

MAX_READ = 256 * 1024


def manifest_for(executor):
    return {"filesystem": getattr(executor, "filesystem", None),
            permissions.SEARCH_TABLE: getattr(executor, permissions.SEARCH_TABLE, None),
            "capabilities": getattr(executor, "capabilities", []),
            "args": getattr(executor, "args_schema", {})}


# What a search asks the closed list operation, and nothing else: an argument
# outside this set is a kind of search the operation cannot do.
_SEARCH_ARGS = frozenset({"pattern", "patterns", "case_sensitive", "recursive", "max_results",
                          "max_depth", "include_dirs", "client"})
_SEARCH_SCAN_MAX = 200_000


def search_request(owner, manifest, args):
    """The list request that serves this search, or None when the tool's own
    sandbox shows the root (it then runs as before) or it declares no search."""
    root = permissions.search_contract(manifest, args)
    if root is None or not isinstance(args.get(root), str) or not args[root].strip():
        return None
    try:
        path = permissions.normalize_path(args[root], "posix")
    except permissions.PermissionError:
        return None                       # relative or malformed: the tool says so itself
    if permissions.manifest_shows(manifest, args, path):
        return None
    given = {key for key in args if not key.startswith("_") and key != root}
    if given - _SEARCH_ARGS:
        raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
    from backends.files.local import _parse_compound_pattern
    patterns = (_parse_compound_pattern(args.get("pattern"))
                + _parse_compound_pattern(args.get("patterns"))) or ["*"]
    request = {"path": path, "recursive": args.get("recursive", True) is not False,
               "name_patterns": patterns, "case_sensitive": args.get("case_sensitive") is True,
               "include_dirs": args.get("include_dirs") is True}
    for name in ("max_results", "max_depth"):
        if args.get(name) is not None:
            request[name] = args[name]
    return request


def search(owner, manifest, args, turn_id, *, timeout_s=30, db_path=None):
    """A local search outside the tool's own scope, done by the closed list
    operation under the account's areas and grants, in the search's own shape."""
    request = search_request(owner, manifest, args)
    listing = {"filesystem": {"protocol": permissions.CAPABILITY, "operation": "list"},
               "capabilities": manifest.get("capabilities", []), "args": manifest.get("args", {})}
    found = execute(owner, listing, request, turn_id, timeout_s=timeout_s, db_path=db_path)
    if not found.get("ok"):
        return {**found, "entries": [], "matches": []}
    from backends.files.local import _kind_for, _mime_for
    entries = []
    for item in found["entries"]:
        mime = _mime_for(item["name"]) if item["type"] == "file" else ""
        entries.append({"path": item["path"], "name": item["name"], "type": item["type"], "mime": mime,
                        "kind": _kind_for(mime) if item["type"] == "file" else item["type"],
                        "size": int(item["size"]), "mtime": float(item["mtime_epoch"])})
    skipped = found.get("skipped", [])
    result = {"ok": True, "entries": entries, "matches": [entry["path"] for entry in entries],
              "ok_count": len(entries), "fail_count": len(skipped), "failed": skipped,
              "metadata": {"base_path": request["path"], "patterns": request["name_patterns"],
                           "recursive": request["recursive"], "case_sensitive": request["case_sensitive"],
                           "count": len(entries), "visited": found.get("visited", len(entries)),
                           "source_complete": found.get("source_complete") is True and not skipped,
                           "truncated": found.get("truncated") is True}}
    if found.get("truncated"):
        result.update({"truncated": True, "truncated_what": "file", "used": len(entries),
                       "cap_field": "max_results", "cap_value": found["cap_value"],
                       "available_at_least": len(entries), "source_scan_truncated": True})
    return result


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
    requested = requests(operation, args)
    if any(not permissions.server_default_permits(owner, manifest, args, path, action)
           for path, action in requested):
        return True
    grants = permissions.list_grants(owner, "server", active_only=True) if owner else []
    return any(permissions.permits(grants, path, action, directory=operation == "list")
               for path, action in requested)


class LocalAuthority:
    """Current server rights, with a short lock around visible file mutations."""

    def __init__(self, owner, manifest, args, *, db_path=None):
        self.owner, self.manifest, self.args, self.db_path = owner, manifest, args, db_path
        self._connection = None

    def check(self, path, action, **kwargs):
        permissions.require(self.owner, "server", path, action, conn=self._connection,
                            db_path=self.db_path, manifest=self.manifest, args=self.args, **kwargs)

    @contextmanager
    def mutation(self):
        if self._connection is not None:
            raise permissions.PermissionError("ERR_FS_INVOCATION_INVALID")
        conn = permissions._open(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._connection = conn
            try:
                yield
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            self._connection = None
            conn.close()


class Broker:
    def __init__(self, owner, manifest, args, turn_id, *, timeout_s=30, db_path=None, authority=None):
        if not owner:
            raise permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED")
        self.owner, self.manifest, self.args, self.turn_id = owner, manifest, args, turn_id
        self.deadline = time.monotonic() + timeout_s
        self.authority = authority if authority is not None else LocalAuthority(owner, manifest, args, db_path=db_path)

    def check(self, path, action, **kwargs):
        if time.monotonic() > self.deadline:
            raise permissions.PermissionError("ERR_FS_INVOCATION_INVALID")
        io.reserved(path)
        self.authority.check(path, action, **kwargs)

    def replace(self, path, producer, *, overwrite=True, mode="overwrite", final_check=None,
                expected_identity=None):
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
                if expected_identity is not None and before != expected_identity:
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
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
                    with self.authority.mutation():
                        self.check(path, "write", overwrite=previous is not None)
                        if final_check is not None:
                            final_check()
                        current = io.existing(parent, name)
                        try:
                            if (io.identity(io.regular(current)) if current is not None else None) != before:
                                raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                        finally:
                            if current is not None:
                                os.close(current)
                        io.commit(parent, temp, name, overwrite=previous is not None)
                        temp = None
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
            try:
                data = content.encode(encoding)
            except UnicodeEncodeError as exc:
                raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED") from exc
        else:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        if len(data) > MAX_READ:
            raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
        def produce(out):
            with os.fdopen(os.dup(out), "wb") as stream:
                stream.write(data)
            return len(data), hashlib.sha256(data).hexdigest()
        return self.replace(path, produce, mode=mode)

    def _read_auto(self, path, fd, before):
        from pathlib import Path
        import document_parse
        limit = self.args.get("max_bytes", 0) or document_parse.MAX_BYTES
        if (type(limit) is not int or limit < 1 or self.args.get("tail_bytes", 0)
                or self.args.get("offset", 0)):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        limit = min(limit, document_parse.MAX_BYTES)
        chunks, count = [], 0
        while count < min(before[3], limit):
            self.check(path, "read")
            chunk = os.read(fd, min(io.CHUNK_SIZE, limit - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        if io.identity(io.regular(fd)) != before:
            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
        self.check(path, "read")
        raw, suffix = b"".join(chunks), Path(path).suffix.casefold()
        if suffix in {".pdf", ".docx", ".xlsx"} and count == before[3]:
            from document_parse_worker import parse_bytes_bounded
            try:
                parsed = parse_bytes_bounded(raw, suffix=suffix, file_size=before[3],
                    max_bytes=limit, timeout_s=self.deadline - time.monotonic())
            except (OSError, ValueError, TimeoutError) as exc:
                raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED") from exc
        else:
            parsed = document_parse.parse_bytes(raw, suffix=suffix, file_size=before[3], max_bytes=limit)
        if io.identity(io.regular(fd)) != before:
            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
        self.check(path, "read")
        source = next((entry for entry in (self.args.get("entries") or [])
                       if isinstance(entry, dict) and entry.get("path") == path), {})
        return {**document_parse.merge_entry(parsed, source), "path": path,
                "name": source.get("name") or Path(path).name, "bytes_read": count}

    def read(self, path):
        self.check(path, "read")
        if self.args.get("pattern"):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        with io.parent(path) as (parent, name):
            fd = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(fd))
                if self.args.get("parse") == "auto":
                    return self._read_auto(path, fd, before)
                cap, tail = self.args.get("max_bytes", 0) or 0, self.args.get("tail_bytes", 0) or 0
                if type(cap) is not int or type(tail) is not int or cap < 0 or tail < 0 or cap and tail:
                    raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
                effective = min(tail or cap or MAX_READ, MAX_READ)
                offset = max(0, before[3] - effective) if tail else self.args.get("offset", 0)
                if type(offset) is not int or offset < 0:
                    raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, effective)
                if io.identity(io.regular(fd)) != before:
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                self.check(path, "read")
                encoding = self.args.get("encoding", "utf-8")
                if encoding not in {"utf-8", "latin-1", "binary"}:
                    raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
                try:
                    content = base64.b64encode(data).decode() if encoding == "binary" else data.decode(encoding)
                except UnicodeDecodeError as exc:
                    raise permissions.PermissionError("ERR_FS_BINARY_ENCODING_REQUIRED") from exc
                truncated = offset > 0 or offset + len(data) < before[3]
                entry = {"path": path, "content": content, "size": before[3], "bytes_read": len(data),
                         "offset": offset, "encoding": encoding, "truncated": truncated,
                         "source_complete": not truncated}
                if truncated:
                    entry.update({"used": len(data), "available_total": before[3],
                                  "cap_field": "tail_bytes" if tail else "max_bytes", "cap_value": effective})
                if self.args.get("parse") == "json":
                    parsed = json.loads(content)
                    entry.update({k: v for k, v in parsed.items() if k != "path"} if isinstance(parsed, dict) else {"value": parsed})
                return entry
            finally:
                os.close(fd)

    @contextmanager
    def copy_source(self, path, source_fd=None):
        self.check(path, "read")
        if source_fd is not None:
            source = os.dup(source_fd)
            try:
                io.regular(source)
                os.lseek(source, 0, os.SEEK_SET)
                yield source
            finally:
                os.close(source)
        else:
            with io.parent(path) as (parent, name):
                source = io.open_file(parent, name)
                try:
                    yield source
                finally:
                    os.close(source)

    def copy(self, spec, *, source_fd=None):
        import hashlib
        src, dst = spec["src"], spec["dst"]
        with self.copy_source(src, source_fd) as source:
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
            def final_check():
                self.check(src, "read")
                if io.identity(io.regular(source)) != before:
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
            result = self.replace(dst, produce, overwrite=self.args.get("overwrite") is True,
                                  final_check=final_check)
            return {**result, "src": src, "dst": dst, "copy": True, "source_scope": "server", "destination_scope": "server"}

    def delete(self, path, *, expected_identity=None):
        self.check(path, "write")
        with io.parent(path) as (parent, name):
            fd = io.open_file(parent, name)
            try:
                before = io.identity(io.regular(fd))
                if expected_identity is not None and before != expected_identity:
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                blob = io.backup(fd, self.turn_id, lambda: self.check(path, "write"))
                with self.authority.mutation():
                    self.check(path, "write")
                    current = io.open_file(parent, name)
                    try:
                        if io.identity(io.regular(current)) != before:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    finally:
                        os.close(current)
                    os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
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
        # A search is this same walk with a filter on the names: what does not
        # match is visited and not returned, and a branch that cannot be opened
        # is reported instead of ending the whole search.
        patterns = self.args.get("name_patterns")
        searching = patterns is not None
        if searching and (not isinstance(patterns, list) or not patterns or len(patterns) > 50
                          or any(not isinstance(p, str) or not p or len(p) > 200 for p in patterns)):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        exact_case = self.args.get("case_sensitive") is True
        with_dirs = not searching or self.args.get("include_dirs") is True
        if searching and not exact_case:
            patterns = [p.lower() for p in patterns]

        def wanted(item):
            if not searching:
                return True
            if item["type"] == "dir" and not with_dirs:
                return False
            name = item["name"] if exact_case else item["name"].lower()
            return any(fnmatch.fnmatchcase(name, p) for p in patterns)

        todo, entries, truncated, output_bytes = [(path, 0)], [], False, 0
        visited, skipped = 0, []
        while todo:
            current, level = todo.pop()
            if searching and (visited >= _SEARCH_SCAN_MAX or time.monotonic() > self.deadline - 5):
                truncated = True
                break
            try:
                self.check(current, "read", directory=True, recursive=recursive)
                with io.parent(current) as (parent, name):
                    fd = os.open(name or "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except (OSError, permissions.PermissionError) as exc:
                if not searching or level == 0:
                    raise
                failure = error_result(exc)
                if len(skipped) < 20:
                    skipped.append({"path": current, "error": failure["error"],
                                    "error_code": failure["error_code"]})
                continue
            try:
                with os.scandir(fd) as listing:
                    for entry in listing:
                        st = entry.stat(follow_symlinks=False)
                        item = {"path": os.path.join(current, entry.name), "name": entry.name,
                                "type": "symlink" if entry.is_symlink() else "dir" if entry.is_dir(follow_symlinks=False) else "file",
                                "size": st.st_size, "mtime_epoch": st.st_mtime}
                        visited += 1
                        if recursive and item["type"] == "dir":
                            if level + 1 < depth:
                                todo.append((item["path"], level + 1))
                            else:
                                truncated = True
                        if not wanted(item):
                            continue
                        output_bytes += len(json.dumps(item).encode())
                        if len(entries) >= cap or output_bytes > 256 * 1024:
                            truncated = True
                            break
                        entries.append(item)
            finally:
                os.close(fd)
            if len(entries) >= cap or output_bytes > 256 * 1024:
                truncated = truncated or bool(todo)
                break
        sort = self.args.get("sort", "name")
        if sort not in {"name", "size", "mtime"}:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        entries.sort(key=lambda e: (e["name"].casefold() if sort == "name" else e["size" if sort == "size" else "mtime_epoch"], e["path"]), reverse=sort != "name")
        result = {"ok": True, "ok_count": len(entries), "entries": entries, "truncated": truncated,
                  "truncated_what": "entries", "used": len(entries), "cap_field": "max_results", "cap_value": cap,
                  "source_complete": not truncated, "metadata": {"path": path, "truncated": truncated, "source_complete": not truncated}}
        if searching:
            result.update({"visited": visited, "skipped": skipped})
        return result


def outside_tool_scope(manifest, args, observation):
    """The truthful answer when a sandboxed tool said «not found» of a place
    its sandbox does not show, or None.

    Such a tool cannot tell a missing folder from one it is not shown: it
    answered «path not found» for folders that exist and even for granted ones
    (13/11/2026). When the service itself can see that the path is there, or
    cannot look, the answer says what is true: this operation does not work
    there. A path that really is not there keeps the tool's own answer.
    """
    if (not isinstance(observation, dict) or observation.get("ok") is not False
            or observation.get("error_code") != "ERR_PATH_NOT_FOUND" or not isinstance(args, dict)):
        return None
    values = []
    for key, value in args.items():
        if not key.startswith("_"):
            values.extend(value if isinstance(value, list) else [value])
    for value in values:
        if not isinstance(value, str) or not value.startswith(("/", "~")):
            continue
        try:
            path = permissions.normalize_path(os.path.expanduser(value), "posix")
        except permissions.PermissionError:
            continue
        if permissions.manifest_shows(manifest, args, path):
            continue
        try:
            there = os.path.lexists(path)
        except OSError:
            there = True
        if not there:
            try:
                os.stat(os.path.dirname(path) or "/")
            except PermissionError:
                there = True               # the service cannot look: unknown, not «missing»
            except OSError:
                pass
        if there:
            from messages import get as _msg
            return {"ok": False, "ok_count": 0, "fail_count": 1, "entries": [], "results": [], "failed": [],
                    "error_code": "ERR_FS_TOOL_SCOPE", "error": _msg("ERR_FS_TOOL_SCOPE", path=value),
                    "error_class": "filesystem_permission"}
    return None


def error_result(error):
    from messages import get as _msg
    code = str(error) if isinstance(error, permissions.PermissionError) else "ERR_FS_IO"
    return {"ok": False, "ok_count": 0, "fail_count": 1, "results": [], "failed": [],
            "error_code": code, "error": _msg(code), "error_class": "filesystem_permission"}


def batch_result(owner, manifest, args, turn_id, operation, results, failed):
    value = {"ok": not failed, "ok_count": len(results), "fail_count": len(failed),
             "entries" if operation == "read" else "results": results, "failed": failed, "dirs_created": []}
    if failed:
        value.update({"error": failed[0]["error"], "error_code": failed[0]["error_code"], "error_class": "filesystem_permission"})
    if operation == "read" and args.get("parse") == "auto":
        import document_parse
        value.update(document_parse.summarize(results, deduplicate=bool(args.get("deduplicate_content"))))
    if operation in {"copy", "write", "delete"}:
        value["_filesystem"] = {"owner": owner, "manifest": manifest, "args": args, "turn_id": turn_id}
    return value


def execute(owner, manifest, args, turn_id, *, timeout_s=30, db_path=None, authority=None, source_fd=None):
    try:
        operation = permissions.contract(manifest, args)
        if not owner:
            raise permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED")
        if source_fd is not None and authority is None:
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        if source_fd is not None and operation != "copy":
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        if authority is None:
            import server_filesystem_host as host
            delegated = host.execute(owner, manifest, args, turn_id, timeout_s=timeout_s, db_path=db_path)
            if delegated is not None:
                return delegated
        broker = Broker(owner, manifest, args, turn_id, timeout_s=timeout_s, db_path=db_path, authority=authority)
        if operation == "list":
            return broker.list(permissions.normalize_path(args.get("path"), "posix"))
        specs = write_specs(args) if operation == "write" else copy_specs(args) if operation == "copy" else paths(args)
        if len(specs) > 100:
            raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
        if source_fd is not None and (operation != "copy" or len(specs) != 1):
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        results, failed = [], []
        for index, spec in enumerate(specs):
            try:
                results.append(broker.copy(spec, source_fd=source_fd) if source_fd is not None
                               else getattr(broker, operation)(spec))
            except (OSError, ValueError, TypeError, LookupError) as exc:
                failure = error_result(exc)
                failed.append({"index": index, "error": failure["error"], "error_code": failure["error_code"]})
        return batch_result(owner, manifest, args, turn_id, operation, results, failed)
    except (OSError, ValueError, TypeError, LookupError) as exc:
        return error_result(exc)


def undo_stage(pattern, results, *, db_path=None, actor=None, authority=None, trusted_owner=None, timeout_s=30):
    """Undo only recorded native effects, under the account's current rights."""
    import hashlib
    import re
    import config as C
    context = results["_filesystem"]
    if trusted_owner is None:
        import devices
        owner = devices.owner_id_for_actor(actor or os.environ.get("METNOS_ACTOR") or "host")
    else:
        owner = trusted_owner
    if owner != context["owner"]:
        return error_result(permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED"))
    if authority is None:
        try:
            import server_filesystem_host as host
            delegated = host.undo(pattern, results, actor=actor, db_path=db_path, timeout_s=timeout_s)
            if delegated is not None:
                return delegated
            if context.get("backend") in {"host", "mixed"}:
                raise permissions.PermissionError("ERR_FS_HOST_UNAVAILABLE")
        except (OSError, ValueError, TypeError, LookupError) as exc:
            return error_result(exc)
    broker = Broker(owner, context["manifest"], context["args"], context["turn_id"], db_path=db_path,
                    authority=authority, timeout_s=timeout_s)
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
            expected_identity = None
            if entry.get("sha256"):
                broker.check(path, "write")
                with io.parent(path) as (parent, name):
                    fd = io.open_file(parent, name)
                    try:
                        expected_identity = io.identity(io.regular(fd))
                        if io.digest(fd, lambda: broker.check(path, "write")) != entry["sha256"]:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                        if io.identity(io.regular(fd)) != expected_identity:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    finally:
                        os.close(fd)
            if remove:
                done.append(broker.delete(path, expected_identity=expected_identity))
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
                        done.append(broker.replace(path, produce, overwrite=entry.get("restore_mode") != "create",
                                                   expected_identity=expected_identity))
                    finally:
                        os.close(source)
        except (OSError, ValueError, TypeError, LookupError) as exc:
            failed.append({"index": index, **error_result(exc)})
    return {"ok": not failed, "ok_count": len(done), "fail_count": len(failed), "results": done, "failed": failed}

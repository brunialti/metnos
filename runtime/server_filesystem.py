"""Closed operations for explicit server grants and additional server scopes.

Only signed declarative filesystem contracts enter this broker. It never runs
executor code with a widened parent-directory bind. Paths stay pinned to directory
descriptors, and each chunk/atomic mutation checks the account's current grants.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import errno
import fnmatch
import json
import os
import stat
import time

import filesystem_io as io
import filesystem_permissions as permissions

MAX_READ = 256 * 1024


def manifest_for(executor):
    return {"filesystem": getattr(executor, "filesystem", None),
            permissions.SEARCH_TABLE: getattr(executor, permissions.SEARCH_TABLE, None),
            permissions.SERVER_TABLE: getattr(executor, permissions.SERVER_TABLE, None),
            "capabilities": getattr(executor, "capabilities", []),
            "args": getattr(executor, "args_schema", {})}


def filesystem_read_extras(executor, args):
    """Prepare exact read binds without changing the child's logical paths.

    A static read root can already expose the named symlink. Binding onto
    that link fails when its target is absent from the namespace; bind the
    exact target instead. Dynamic-only roots retain their logical bind and
    cache identity. No parent or intermediate external link is added.
    """
    import sandbox
    from capabilities import effective_capabilities

    selected = sandbox.filesystem_extras(executor, args)
    effective = effective_capabilities(
        getattr(executor, "capabilities", None) or [],
        getattr(executor, "args_schema", None) or {}, args)
    static = []
    for capability in effective:
        if capability.get("name") != "fs:read":
            continue
        for root in sandbox._expand_hints_to_paths(capability.get("hint", [])):
            if ".." not in root.parts and root.exists():
                static.append(root)
    prepared = []
    for path in selected:
        # A lexical containment check cannot decide traversal across a link.
        # Keep these spellings on the existing preparation path.
        if ".." not in path.parts and any(path.is_relative_to(root) for root in static):
            try:
                physical = path.resolve(strict=True)
                if physical != path:
                    if sandbox.inside_private_root(physical):
                        continue
                    path = physical
            except (OSError, RuntimeError, ValueError):
                pass
        prepared.append(path)
    return prepared


# What a search asks the closed list operation, and nothing else: an argument
# outside this set is a kind of search the operation cannot do.
_SEARCH_ARGS = frozenset({"pattern", "patterns", "case_sensitive", "recursive", "max_results",
                          "max_depth", "include_dirs", "client"})
# Arguments of the other backends of the same tool (a repository, a provider's
# query): a local search does not read them, here as in the tool itself. The
# planner repeats the folder in `repo` (turns 3e4db9f6 and 0a61875a, 13/11/2026).
_OTHER_BACKEND_ARGS = frozenset({"repo", "query"})
_SEARCH_SCAN_MAX = 200_000
# One page of a search: what one answer of the closed operation carries.
_SEARCH_PAGE = 5000
_SEARCH_PAGE_BYTES = 1024 * 1024


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
    if given - _SEARCH_ARGS - _OTHER_BACKEND_ARGS:
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
    operation under the account's areas and grants, in the search's own shape.

    The operation answers one page at a time; a search that asks for more
    than a page goes on from the last entry it was given, within its time."""
    request = search_request(owner, manifest, args)
    listing = {"filesystem": {"protocol": permissions.CAPABILITY, "operation": "list"},
               "capabilities": manifest.get("capabilities", []), "args": manifest.get("args", {})}
    wanted = request.get("max_results") or 1000
    until = time.monotonic() + max(5, timeout_s - 10)
    found = execute(owner, listing, request, turn_id, timeout_s=timeout_s, db_path=db_path)
    if not found.get("ok"):
        return {**found, "entries": [], "matches": []}
    items, more = list(found["entries"]), found.get("more") is True
    while more and len(items) < wanted and items:
        if time.monotonic() > until:
            break
        page = execute(owner, listing, {**request, "max_results": wanted - len(items), "after": items[-1]["path"]},
                       turn_id, timeout_s=timeout_s, db_path=db_path)
        if not page.get("ok") or not page["entries"]:
            break
        items.extend(page["entries"])
        more = page.get("more") is True
    from backends.files.local import _kind_for, _mime_for
    items.sort(key=lambda item: (item["name"].casefold(), item["path"]))
    entries = []
    for item in items[:wanted]:
        mime = _mime_for(item["name"]) if item["type"] == "file" else ""
        entries.append({"path": item["path"], "name": item["name"], "type": item["type"], "mime": mime,
                        "kind": _kind_for(mime) if item["type"] == "file" else item["type"],
                        "size": int(item["size"]), "mtime": float(item["mtime_epoch"])})
    skipped = found.get("skipped", [])
    counted = found.get("counted") is True
    truncated = more or found.get("unseen") is True
    result = {"ok": True, "entries": entries, "matches": [entry["path"] for entry in entries],
              "ok_count": len(entries), "fail_count": len(skipped), "failed": skipped,
              "metadata": {"base_path": request["path"], "patterns": request["name_patterns"],
                           "recursive": request["recursive"], "case_sensitive": request["case_sensitive"],
                           "count": len(entries), "visited": found.get("visited", len(entries)),
                           "source_complete": counted, "truncated": truncated}}
    if truncated:
        result.update({"truncated": True, "truncated_what": "file", "used": len(entries),
                       "cap_field": "max_results", "cap_value": wanted})
        matched = max(int(found.get("matched") or 0), len(entries))
        if counted:
            result["available_total"] = matched
        else:
            result.update({"available_at_least": matched, "source_scan_truncated": True})
    return result

def write_specs(args):
    from backends.files.local import _collect_write_specs
    specs, error = _collect_write_specs(args)
    if error or specs is None:
        raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
    return specs


# Items one call of a closed operation carries (ADR 0237, first protocol).
MAX_ITEMS = 100


class TooMany(permissions.PermissionError):
    """More items than one call carries: refused before any effect, and said
    with the numbers."""

    def __init__(self, count):
        super().__init__("ERR_FS_SIZE_LIMIT")
        self.count = count


def capped(items):
    if len(items) > MAX_ITEMS:
        raise TooMany(len(items))
    return items


def paths(args, *, cap=True):
    result = args.get("paths")
    if result is None:
        result = [e.get("path") for e in args["entries"]] if isinstance(args.get("entries"), list) else [args.get("path")]
    if not isinstance(result, list):
        raise permissions.PermissionError("ERR_FS_SIZE_LIMIT")
    return [permissions.normalize_path(p, "posix") for p in (capped(result) if cap else result)]


def copy_specs(args):
    from device_filesystem import copy_entries
    if args.get("source_location", "server") != "server" or args.get("destination_location", "server") != "server":
        raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
    if isinstance(args.get("entries"), list):
        capped(args["entries"])
    return copy_entries({**args, "source_location": "server", "destination_location": "server"}, "posix")


def dir_paths(args):
    """The folders a creation names. Rows of a step name places that exist,
    never folders to create: without `paths` there is nothing for the closed
    operation (see `settled`). The cap is the closed operation's own: inside
    the tool's scope the tool keeps serving any number."""
    if args.get("paths") is None:
        return []
    return paths(args, cap=False)


_SETTLED_MOVE = "{dst}"


def place(owner, value):
    """Where a path the person gave really is, by the rule the tools follow:
    `~` is the service's home, a path without a root is in the person's folder
    (ADR 0242), and a link on the way, a name in that folder that is a link
    to a mounted archive included, becomes the real place. Rights are then
    checked on that real place, and the closed operations still refuse every
    link they meet."""
    from pathlib import Path
    import person_files
    value = os.path.expanduser(value)
    if not value.startswith("/"):
        value = person_files.absolute(value, person_files.root(owner))
    return str(Path(value).resolve())


def move_rows(args):
    """The rows of a settled move (see `settled`): ``{src, dst}`` to move, or
    ``{src, refused, error}`` that the tool would refuse and nothing executes;
    None when the arguments are not in that form."""
    entries = args.get("entries")
    if args.get("dst_template") != _SETTLED_MOVE or not isinstance(entries, list):
        return None
    rows = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("src", ""), str):
            return None
        if "refused" in entry:
            if not isinstance(entry.get("refused"), str) or not isinstance(entry.get("error"), str):
                return None
            rows.append({key: entry[key] for key in ("src", "refused", "error") if key in entry})
        elif isinstance(entry.get("src"), str) and isinstance(entry.get("dst"), str):
            rows.append({"src": permissions.normalize_path(entry["src"], "posix"),
                         "dst": permissions.normalize_path(entry["dst"], "posix")})
        else:
            return None
    return rows


def fixed_refusal_preview(owner, manifest, args):
    """Pure preparation shared with execution; no new permission decision."""
    if permissions.served_operation(manifest, args) != "move":
        return ""
    rows = move_rows(settled(owner, "move", args))
    if not rows:
        return ""
    refused = [row for row in rows if "refused" in row]
    if not refused:
        return ""
    from collections import Counter
    from list_outcomes import reasons
    from messages import get as message
    # These are the tool's own fixed, translated reasons, not a second
    # classifier. Other validation failures keep their bounded category.
    fixed = Counter(row["error"] for row in refused if row["refused"] == "ERR_REFUSE_MOVE")
    detail = [message("MSG_MUTATION_QUESTION_CAUSE", count=count, cause=cause)
              for cause, count in fixed.items()]
    other = reasons([{"error_code": row["refused"]} for row in refused
                     if row["refused"] != "ERR_REFUSE_MOVE"])
    if other:
        detail.append(other)
    return message("MSG_MUTATION_QUESTION_FIXED", total=len(rows), refused=len(refused),
                   reasons="; ".join(detail))


def _settled_moves(owner, args):
    """Every row's final source and destination, by the tool's own rule
    (`local._entry_fields`, links resolved as the tool resolves them), once,
    here: the host then receives places, never a template. A row the tool
    would refuse keeps the tool's refusal and is never executed."""
    entries, template = args.get("entries"), args.get("dst_template")
    if not isinstance(entries, list) or not isinstance(template, str) or not template:
        return args
    if template == _SETTLED_MOVE and all(isinstance(entry, dict) and set(entry) <= {"src", "dst", "refused", "error"}
                                         for entry in entries):
        return args
    from pathlib import Path
    from backends.files import local
    from messages import get as _msg

    rows = []
    for index, entry in enumerate(entries):
        source = entry.get("path") or entry.get("src") if isinstance(entry, dict) else None
        if not isinstance(source, str) or not source.strip():
            rows.append({"refused": "ERR_ARG_MISSING", "error": _msg("ERR_ARG_MISSING", arg=f"entries[{index}].path")})
            continue
        src = Path(place(owner, source))
        if entry.get("kind") == "dir":
            rows.append({"src": str(src), "refused": "ERR_REFUSE_MOVE", "error": _msg("MSG_MOVE_CAUSE_FOLDER")})
            continue
        if args.get("allow_system") is not True and local.is_system_file(src.name):
            rows.append({"src": str(src), "refused": "ERR_REFUSE_MOVE", "error": _msg("MSG_MOVE_CAUSE_SYSTEM_FILE")})
            continue
        try:
            destination = template.format(**local._entry_fields(entry, src))
        except (KeyError, IndexError, ValueError, AttributeError) as exc:
            rows.append({"src": str(src), "refused": "ERR_TEMPLATE_FAIL",
                         "error": _msg("ERR_TEMPLATE_FAIL", stage="placeholder", reason=str(exc))})
            continue
        rows.append({"src": str(src), "dst": place(owner, destination)})
    rest = {key: value for key, value in args.items() if key not in {"entries", "dst_template", "from_step"}}
    return {**rest, "entries": rows, "dst_template": _SETTLED_MOVE}


def settled(owner, operation, args):
    """What the closed operation receives: the final list of folders to create,
    computed once here. Rows of a step with a `path_template` give it by the
    tool's own rule; every path is placed as a move places it (`place`). A
    template that does not fit the rows leaves the arguments as they are: the
    tool then says what is wrong and creates nothing."""
    if operation == "move":
        return _settled_moves(owner, args)
    if operation != "mkdir":
        return args
    given = args.get("paths")
    if given is None and args.get("path_template") is not None:
        from backends.files.local import _paths_from_rows
        given, problem = _paths_from_rows(args.get("entries"), args.get("path_template"))
        if problem is not None:
            return args
    if not isinstance(given, list):
        return args
    final = [place(owner, path) if path.strip() else path for path in given if isinstance(path, str)]
    if len(final) != len(given):
        return args
    rest = {key: value for key, value in args.items() if key not in {"entries", "path_template", "from_step"}}
    return {**rest, "paths": final}


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
    if operation == "mkdir":
        return [(p, "write") for p in dir_paths(args)]
    if operation == "move":
        # Removing the source and making the destination are writes: what a
        # delete and a write already need, nothing more.
        return [(row[end], "write") for row in move_rows(args) or [] if "dst" in row for end in ("src", "dst")]
    raise permissions.PermissionError("ERR_FS_CONTRACT_INVALID")


def _in_workspace(path):
    import config as C
    root = str(C.PATH_WORKSPACE).rstrip("/")
    return path == root or path.startswith(root + "/")


def sandbox_shows(manifest, args, path, action):
    """Whether the tool's own sandbox shows ``path`` for ``action``: a static
    signed hint covers it, or a signed ``arg:<name>`` hint binds the exact
    place that argument names (`sandbox._filesystem_argument_extras`)."""
    if permissions.manifest_shows(manifest, args, path, action):
        return True
    from capabilities import effective_capabilities
    for capability in effective_capabilities(manifest.get("capabilities", []), manifest.get("args", {}), args or {}):
        if capability.get("name") != "fs:" + action:
            continue
        for hint in capability.get("hint", []) or []:
            if not isinstance(hint, str) or not hint.startswith("arg:"):
                continue
            value = (args or {}).get(hint[4:])
            for named in value if isinstance(value, list) else [value]:
                if not isinstance(named, str) or not named.startswith(("/", "~")):
                    continue
                named = os.path.normpath(os.path.expanduser(named))
                if path == named or path.startswith(named.rstrip("/") + "/"):
                    return True
    return False


def needs_extension(owner, manifest, args):
    operation = permissions.served_operation(manifest, args)
    if operation is None:
        return False
    requested = requests(operation, settled(owner, operation, args))
    if any(not permissions.server_default_permits(owner, manifest, args, path, action)
           for path, action in requested):
        return True
    grants = permissions.list_grants(owner, "server", active_only=True) if owner else []
    if any(permissions.permits(grants, path, action, directory=operation == "list")
           for path, action in requested):
        return True
    # The person's own folder (ADR 0242) is theirs without a grant, but a
    # tool's sandbox shows only its own signed scope: there the closed
    # operation does the work, as the service itself (the host keeps every
    # workspace path native). Outside the workspace it would act as the
    # installation owner, which only an explicit grant decides.
    return any(_in_workspace(path) and not sandbox_shows(manifest, args, path, action)
               for path, action in requested)


class LocalAuthority:
    """Current server rights, with a short lock around visible file mutations."""

    def __init__(self, owner, manifest, args, *, db_path=None):
        self.owner, self.manifest, self.args, self.db_path = owner, manifest, args, db_path
        self._connection = None

    def check(self, path, action, **kwargs):
        permissions.require(self.owner, "server", path, action, conn=self._connection,
                            db_path=self.db_path, manifest=self.manifest, args=self.args, **kwargs)

    def checkpoint(self, row):
        from undo import row_checkpoint
        row_checkpoint(row)

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


def _settle(directory):
    """Make a name change durable. Once a source name is gone the move has
    happened: a failed sync must never be taken for a move not done (the
    copy would then be removed as if the source were still there)."""
    try:
        os.fsync(directory)
    except OSError:
        pass


def _folder(parent, name):
    """The folder ``name`` in ``parent``, opened without following a link, or
    None when nothing is there. A file or a link in its place is refused."""
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno not in (errno.ENOTDIR, errno.ELOOP):
            raise
        try:
            link = stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode)
        except OSError:
            link = exc.errno == errno.ELOOP
        raise permissions.PermissionError("ERR_FS_LINK_REFUSED" if link else "ERR_FS_NOT_A_FOLDER") from exc


class Broker:
    def __init__(self, owner, manifest, args, turn_id, *, timeout_s=30, db_path=None, authority=None):
        if not owner:
            raise permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED")
        self.owner, self.manifest, self.args, self.turn_id = owner, manifest, args, turn_id
        self.deadline = time.monotonic() + timeout_s
        self.authority = authority if authority is not None else LocalAuthority(owner, manifest, args, db_path=db_path)

    def check(self, path, action, *, late=False, **kwargs):
        """The account's current right on ``path``, within the call's time.
        ``late`` is for putting back what a stopped item had made: it keeps
        every right and boundary, only not the clock."""
        if not late and time.monotonic() > self.deadline:
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

    def mkdir(self, path):
        """Create one folder and, unless `parents` is false, its missing parents.

        The path is walked from the root one component at a time, each opened
        relative to its parent's descriptor without following a link. A missing
        component is created only after the account's write right on that very
        component is checked again inside the mutation lock: nothing is made
        outside what the account may write. An existing folder is success
        without a creation; a component that is a file or a link is refused.
        """
        path = permissions.normalize_path(path, "posix")
        parents, exist_ok, mode = (self.args.get("parents", True), self.args.get("exist_ok", True),
                                   self.args.get("mode"))
        if (type(parents) is not bool or type(exist_ok) is not bool
                or mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777)):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        self.check(path, "write")
        created, identities = self._make_folders(path, parents=parents, mode=mode)
        if not created and not exist_ok:
            raise permissions.PermissionError("ERR_FS_DEST_EXISTS")
        # Fields with `_` stay out of the person's tables: they serve the undo.
        return {"path": path, "created": path in created, "dirs_created": created,
                "_dirs_created_ids": identities}

    def _make_folders(self, path, *, parents=True, mode=None):
        """The missing folders of ``path``, made one at a time from the root;
        returns the folders made and their identities."""
        parts = path[1:].split("/") if path != "/" else []
        created, identities = [], []
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            return self._walk_folders(fd, parts, created, identities, parents=parents, mode=mode)
        except BaseException:
            # A walk stopped half way (time, a component refused) leaves
            # nothing of its own behind.
            self._unmake(created, identities)
            raise
        finally:
            os.close(fd)

    def _unmake(self, folders, identities):
        for folder, identity in reversed(list(zip(folders, identities))):
            try:
                self.rmdir(folder, identity, late=True)
            except (OSError, ValueError):
                break

    def _walk_folders(self, fd, parts, created, identities, *, parents, mode):
        fd = os.dup(fd)
        try:
            for index, name in enumerate(parts):
                current = "/" + "/".join(parts[:index + 1])
                child = _folder(fd, name)
                if child is None:
                    if index < len(parts) - 1 and not parents:
                        raise permissions.PermissionError("ERR_FS_PARENT_MISSING")
                    try:
                        with self.authority.mutation():
                            self.check(current, "write")
                            os.mkdir(name, 0o777 if mode is None else mode, dir_fd=fd)
                    except FileExistsError:
                        made = False                  # someone else's: never recorded as ours
                    else:
                        made = True
                    child = _folder(fd, name)
                    if child is None:
                        raise permissions.PermissionError("ERR_FS_IO")
                    if made:
                        info = os.fstat(child)
                        created.append(current)
                        identities.append([info.st_dev, info.st_ino])
                        _settle(fd)
                os.close(fd)
                fd = child
        finally:
            os.close(fd)
        return created, identities

    def move(self, spec):
        """Move one file. The destination is named and checked first: one that
        exists is refused before anything is touched. Missing folders are made
        inside what the account may write. Then the same file gets its new name
        on the same filesystem (a link, then the old name removed), or it is
        copied, the copy verified, and only then the source removed: never a
        removal without a confirmed copy. Times and mode bits travel with it."""
        import hashlib
        src = permissions.normalize_path(spec["src"], "posix")
        dst = permissions.normalize_path(spec["dst"], "posix")
        parents = self.args.get("parents", True)
        if (self.args.get("overwrite") is True or self.args.get("allow_dirs") is True
                or type(parents) is not bool):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        if src == dst:
            raise permissions.PermissionError("ERR_FS_DEST_EXISTS")
        self.check(src, "write")
        self.check(dst, "write")
        try:
            with io.parent(src) as (sparent, sname):
                source = io.open_file(sparent, sname)
        except FileNotFoundError as exc:
            raise permissions.PermissionError("ERR_FS_SOURCE_MISSING") from exc
        made, made_ids, copied_hash = [], [], None
        try:
            info = io.regular(source)
            before = io.identity(info)
            expected = spec.get("expected_identity")
            if expected is not None and list(before) != list(expected):
                raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
            if self._present(dst):
                raise permissions.PermissionError("ERR_FS_DEST_EXISTS")
            if parents:
                made, made_ids = self._make_folders(os.path.dirname(dst))
            elif not self._present(os.path.dirname(dst)):
                raise permissions.PermissionError("ERR_FS_PARENT_MISSING")
            with io.parent(src) as (sparent, sname), io.parent(dst) as (dparent, dname):
                moved_identity = before          # a new name for the same file
                if not self._relink(src, dst, sparent, sname, dparent, dname, info, before):
                    def produce(out):
                        digest, size = hashlib.sha256(), 0
                        os.lseek(source, 0, os.SEEK_SET)
                        while chunk := os.read(source, io.CHUNK_SIZE):
                            self.check(src, "write")
                            self.check(dst, "write")
                            io.write_all(out, chunk)
                            size += len(chunk)
                            digest.update(chunk)
                        if io.identity(io.regular(source)) != before:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                        try:
                            os.fchmod(out, stat.S_IMODE(info.st_mode))
                        except OSError:
                            pass              # a share that fixes the mode keeps its own
                        os.utime(out, ns=(info.st_atime_ns, info.st_mtime_ns))
                        return size, digest.hexdigest()

                    def final_check():
                        self.check(src, "write")
                        if io.identity(io.regular(source)) != before:
                            raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")

                    # The verified copy is committed only where nothing is.
                    copied = self.replace(dst, produce, overwrite=False, final_check=final_check)
                    copied_hash = copied["sha256"]
                    own = moved_identity = self._identity_at(dparent, dname)
                    try:
                        if own[3] != info.st_size:
                            raise permissions.PermissionError("ERR_FS_HASH_MISMATCH")
                        self._unlink_same(src, sparent, sname, before)
                    except BaseException:
                        # The source stays: the copy made for it goes, if it is still ours.
                        try:
                            self._unlink_same(dst, dparent, dname, own, late=True)
                        except (OSError, ValueError):
                            pass
                        raise
        except BaseException:
            self._unmake(made, made_ids)
            raise
        finally:
            os.close(source)
        # The tool's row is {src, dst}; what the undo needs is kept out of
        # the person's tables (`_`).
        return {"src": src, "dst": dst, "_identity": list(moved_identity),
                "_dirs_created": made, "_dirs_created_ids": made_ids,
                **({"_sha256": copied_hash} if copied_hash else {})}

    def move_back(self, row):
        """The undo of a move: the file goes back where it was, only while it
        is still the file the move left there, under the current rights."""
        identity = row.get("_identity")
        if (not isinstance(identity, list) or len(identity) != 4
                or any(type(value) is not int for value in identity)):
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        self.check(row["src"], "write")
        self.check(row["dst"], "write")
        def identity_at(path):
            try:
                with io.parent(path) as (parent, name):
                    return self._identity_at(parent, name)
            except FileNotFoundError:
                return None
        source, target = identity_at(row["dst"]), identity_at(row["src"])
        same_target = target == tuple(identity)
        if target is not None and not same_target and row.get("_sha256"):
            with io.parent(row["src"]) as (parent, name):
                fd = io.open_file(parent, name)
                try:
                    same_target = (io.digest(fd, lambda: self.check(row["src"], "write"))
                                   == row["_sha256"] and self._identity_at(parent, name) == target)
                finally:
                    os.close(fd)
        if same_target:
            if source == tuple(identity):
                with io.parent(row["dst"]) as (parent, name):
                    self._unlink_same(row["dst"], parent, name, identity)
            elif source is not None:
                raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
            return {"src": row["dst"], "dst": row["src"], "restored": True,
                    "already_restored": True}
        back = self.move({"src": row["dst"], "dst": row["src"], "expected_identity": identity})
        return {"src": back["src"], "dst": back["dst"], "restored": True}

    def _present(self, path):
        try:
            with io.parent(path) as (parent, name):
                os.stat(name, dir_fd=parent, follow_symlinks=False)
                return True
        except FileNotFoundError:
            return False

    def _identity_at(self, parent, name):
        fd = io.open_file(parent, name)
        try:
            return io.identity(io.regular(fd))
        finally:
            os.close(fd)

    def _finish_linked_move(self, src, dst, before):
        """Finish only the exact two-name interval of a recorded move.

        Ordinary hardlinks remain refused by ``io.regular``. Here both named
        entries must be the frozen regular file, with exactly these two links;
        a third link or any changed identity leaves both names untouched.
        """
        self.check(src, "write")
        self.check(dst, "write")
        try:
            with io.parent(src) as (sparent, sname), io.parent(dst) as (dparent, dname):
                def pair():
                    infos = [os.stat(name, dir_fd=parent, follow_symlinks=False)
                             for parent, name in ((sparent, sname), (dparent, dname))]
                    return all(stat.S_ISREG(info.st_mode) and info.st_nlink == 2
                               and list(io.identity(info)) == before for info in infos)
                if not pair():
                    return False
                with self.authority.mutation():
                    self.check(src, "write")
                    self.check(dst, "write")
                    if not pair():
                        raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    os.unlink(sname, dir_fd=sparent)
                _settle(sparent)
                return True
        except FileNotFoundError:
            return False

    def _unlink_same(self, path, parent, name, identity, *, late=False):
        """Remove ``name`` only while it is still the file ``identity`` names,
        under the account's current right."""
        with self.authority.mutation():
            self.check(path, "write", late=late)
            if self._identity_at(parent, name) != tuple(identity):
                raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
            os.unlink(name, dir_fd=parent)
        _settle(parent)

    def _relink(self, src, dst, sparent, sname, dparent, dname, info, before):
        """The same file under its new name, on one filesystem: a link that
        fails where any name already is, then the old name removed. False when
        the filesystem cannot link across these folders (the copy then runs)."""
        with self.authority.mutation():
            self.check(src, "write")
            self.check(dst, "write")
            if self._identity_at(sparent, sname) != before:
                raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
            try:
                os.link(sname, dname, src_dir_fd=sparent, dst_dir_fd=dparent, follow_symlinks=False)
            except FileExistsError as exc:
                raise permissions.PermissionError("ERR_FS_DEST_EXISTS") from exc
            except OSError as exc:
                if exc.errno in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP,
                                 errno.EMLINK, errno.ENOSYS, errno.EACCES):
                    return False
                raise
            try:
                linked = os.stat(dname, dir_fd=dparent, follow_symlinks=False)
                if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                os.unlink(sname, dir_fd=sparent)
            except BaseException:
                os.unlink(dname, dir_fd=dparent)
                raise
        _settle(dparent)
        _settle(sparent)
        return True

    def rmdir(self, path, identity, *, late=False):
        """Remove a folder a creation made, only while it is that same folder
        and still empty; None when it is already gone."""
        self.check(path, "write", late=late)

        def same(parent, name):
            fd = _folder(parent, name)
            if fd is None:
                return False
            try:
                info = os.fstat(fd)
                if [info.st_dev, info.st_ino] != list(identity):
                    raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                return True
            finally:
                os.close(fd)

        try:
            with io.parent(path) as (parent, name):
                if not same(parent, name):
                    return None
                with self.authority.mutation():
                    self.check(path, "write", late=late)
                    if not same(parent, name):
                        return None
                    try:
                        os.rmdir(name, dir_fd=parent)
                    except OSError as exc:
                        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                            raise permissions.PermissionError("ERR_FS_FOLDER_NOT_EMPTY") from exc
                        raise
                    os.fsync(parent)
        except FileNotFoundError:
            return None                       # a parent is gone, and the folder with it
        return {"path": path, "removed": True}

    def _scan(self, current, recursive):
        """The entries of one folder the account may read, as the closed list
        operation reports them."""
        self.check(current, "read", directory=True, recursive=recursive)
        with io.parent(current) as (parent, name):
            fd = os.open(name or "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            with os.scandir(fd) as listing:
                for entry in listing:
                    st = entry.stat(follow_symlinks=False)
                    yield {"path": os.path.join(current, entry.name), "name": entry.name,
                           "type": "symlink" if entry.is_symlink() else "dir" if entry.is_dir(follow_symlinks=False) else "file",
                           "size": st.st_size, "mtime_epoch": st.st_mtime}
        finally:
            os.close(fd)

    def list(self, path):
        recursive = self.args.get("recursive") is True
        cap = self.args.get("max_results", 1000) or 1000
        depth = self.args.get("max_depth", 10) or 64
        if type(cap) is not int or type(depth) is not int or cap < 0 or depth < 0:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        depth = min(depth, 64)
        if self.args.get("name_patterns") is not None:
            return self._search(path, recursive, min(cap, _SEARCH_PAGE), depth)
        if self.args.get("after") is not None:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        cap = min(cap, 1000)
        todo, entries, truncated, output_bytes = [(path, 0)], [], False, 0
        while todo:
            current, level = todo.pop()
            listing = self._scan(current, recursive)
            try:
                for item in listing:
                    if recursive and item["type"] == "dir":
                        if level + 1 < depth:
                            todo.append((item["path"], level + 1))
                        else:
                            truncated = True
                    output_bytes += len(json.dumps(item).encode())
                    if len(entries) >= cap or output_bytes > 256 * 1024:
                        truncated = True
                        break
                    entries.append(item)
            finally:
                listing.close()
            if len(entries) >= cap or output_bytes > 256 * 1024:
                truncated = truncated or bool(todo)
                break
        sort = self.args.get("sort", "name")
        if sort not in {"name", "size", "mtime"}:
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        entries.sort(key=lambda e: (e["name"].casefold() if sort == "name" else e["size" if sort == "size" else "mtime_epoch"], e["path"]), reverse=sort != "name")
        return {"ok": True, "ok_count": len(entries), "entries": entries, "truncated": truncated,
                "truncated_what": "entries", "used": len(entries), "cap_field": "max_results", "cap_value": cap,
                "source_complete": not truncated, "metadata": {"path": path, "truncated": truncated, "source_complete": not truncated}}

    def _search(self, path, recursive, cap, depth):
        """The list walk with a filter on the names, one page at a time.

        What does not match is visited and not returned; a branch that cannot
        be opened is reported instead of ending the whole search. The walk has
        one order, the same at every call: folder by folder, parents first,
        names sorted inside a folder. So a page can go on from the last entry
        of the page before (``after``) without walking again what was given.
        The first page goes on counting what matches after it is full, as the
        tool's own wide probe does: «how many» is answered with the number.
        """
        patterns = self.args["name_patterns"]
        if (not isinstance(patterns, list) or not patterns or len(patterns) > 50
                or any(not isinstance(p, str) or not p or len(p) > 200 for p in patterns)):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        exact_case = self.args.get("case_sensitive") is True
        with_dirs = self.args.get("include_dirs") is True
        if not exact_case:
            patterns = [p.lower() for p in patterns]

        def wanted(item):
            if item["type"] == "dir" and not with_dirs:
                return False
            name = item["name"] if exact_case else item["name"].lower()
            return any(fnmatch.fnmatchcase(name, p) for p in patterns)

        after, cursor = self.args.get("after"), None
        if after is not None:
            if not isinstance(after, str) or not after:
                raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
            inside = os.path.relpath(permissions.normalize_path(after, "posix"), path).split(os.sep)
            if inside[0] in (os.curdir, os.pardir):
                raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
            cursor = (tuple(inside[:-1]), inside[-1])
        counting = cursor is None
        todo, entries, skipped = [(path, 0, ())], [], []
        visited = matched = output_bytes = 0
        full = unseen = False
        while todo:
            current, level, here = todo.pop()
            if visited >= _SEARCH_SCAN_MAX or time.monotonic() > self.deadline - 5:
                unseen = True
                break
            try:
                items = sorted(self._scan(current, recursive), key=lambda item: item["name"])
            except (OSError, permissions.PermissionError) as exc:
                if level == 0:
                    raise
                failure = error_result(exc)
                if len(skipped) < 20:
                    skipped.append({"path": current, "error": failure["error"],
                                    "error_code": failure["error_code"]})
                continue
            visited += len(items)
            # Folders above the cursor's own were given whole by earlier pages.
            given = cursor is not None and here < cursor[0]
            below = []
            for item in items:
                if recursive and item["type"] == "dir":
                    branch = here + (item["name"],)
                    if cursor is not None and branch < cursor[0] and cursor[0][:len(branch)] != branch:
                        pass          # a branch wholly before the cursor
                    elif level + 1 < depth:
                        below.append((item["path"], level + 1, branch))
                    else:
                        unseen = True
                if given or (cursor is not None and here == cursor[0] and item["name"] <= cursor[1]):
                    continue
                if not wanted(item):
                    continue
                matched += 1
                if full:
                    continue
                output_bytes += len(json.dumps(item).encode())
                if len(entries) >= cap or output_bytes > _SEARCH_PAGE_BYTES:
                    full = True
                    continue
                entries.append(item)
            if full and not counting:
                break
            todo.extend(reversed(below))
        truncated = full or unseen
        result = {"ok": True, "ok_count": len(entries), "entries": entries, "truncated": truncated,
                  "truncated_what": "entries", "used": len(entries), "cap_field": "max_results", "cap_value": cap,
                  "source_complete": not truncated,
                  "metadata": {"path": path, "truncated": truncated, "source_complete": not truncated},
                  "visited": visited, "skipped": skipped, "more": full, "unseen": unseen}
        if counting:
            result.update({"matched": matched, "counted": not unseen and not skipped})
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
    import device_paths
    import person_files
    # A name without a root belongs to this invocation's person, including
    # links to mounted archives. Never recover it from the tool's diagnostic.
    resolved = device_paths.rooted(
        args, lambda value: person_files.absolute(value, person_files.for_args(args)))
    values = []
    for key, value in resolved.items():
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
    text = (_msg("ERR_FS_TOO_MANY", count=error.count, cap=MAX_ITEMS) if isinstance(error, TooMany)
            else _msg(code))
    return {"ok": False, "ok_count": 0, "fail_count": 1, "results": [], "failed": [],
            "error_code": code, "error": text, "error_class": "filesystem_permission"}


# A move that could not be done for a file says why in the tool's own words
# (`local._move_cause`) for the causes the tool itself names.
_MOVE_CAUSES = {"ERR_FS_SOURCE_MISSING": "ERR_PATH_NOT_FOUND", "ERR_FS_DEST_EXISTS": "ERR_DST_EXISTS"}


def move_failure(index, row, error):
    """One row of a move not done, in the shape the tool gives it."""
    from backends.files import local
    if "refused" in row:
        code, text = row["refused"], row["error"]
    else:
        failure = error_result(error)
        code = _MOVE_CAUSES.get(failure["error_code"], failure["error_code"])
        text = local._move_cause(code, None) if code != failure["error_code"] else failure["error"]
    return {"index": index, **{key: row[key] for key in ("src", "dst") if key in row},
            "error_code": code, "error": text}


def batch_result(owner, manifest, args, turn_id, operation, results, failed):
    attention = {"error_class": "publication_ambiguous"} if any(row.get("_unsure") for row in failed) else {}
    if operation == "move":
        from backends.files import local
        folders = sorted({folder for entry in results for folder in entry.get("_dirs_created") or []},
                         key=lambda folder: folder.count("/"), reverse=True)
        value = local._move_result(results, failed, dirs_created=folders)
        value["_filesystem"] = {"owner": owner, "manifest": manifest, "args": args, "turn_id": turn_id}
        return {**value, **attention}
    value = {"ok": not failed, "ok_count": len(results), "fail_count": len(failed),
             "entries" if operation == "read" else "results": results, "failed": failed,
             "dirs_created": [folder for entry in results for folder in entry.get("dirs_created") or []]
             if operation == "mkdir" else []}
    if failed:
        value.update({"error": failed[0]["error"], "error_code": failed[0]["error_code"], "error_class": "filesystem_permission"})
    if operation == "read" and args.get("parse") == "auto":
        import document_parse
        value.update(document_parse.summarize(results, deduplicate=bool(args.get("deduplicate_content"))))
    if operation in {"copy", "write", "delete", "mkdir"}:
        value["_filesystem"] = {"owner": owner, "manifest": manifest, "args": args, "turn_id": turn_id}
    return {**value, **attention}


# One group of a server-only operation has at most this long, as one call to
# the host has (`server_filesystem_host.execute`); the whole call has the
# executor's own timeout. A group is not started with less than a second left.
GROUP_SECONDS = 30
_GROUP_FLOOR = 1.0
# Codes of an item stopped by the clock: nothing of it was done.
_TIME_CODES = frozenset({"ERR_FS_INVOCATION_INVALID", "ERR_FS_REQUEST_EXPIRED"})


def _in_groups(owner, manifest, args, turn_id, operation, *, timeout_s, db_path):
    """The items of a server-only operation in consecutive groups of at most
    MAX_ITEMS, each served exactly as one call is (same checks, same exchange
    per item), until the call's own time ends. An item the time did not reach
    is said, in the tool's own failure shape, never dropped and never undone;
    the result then says how many were done of how many (§2.7, §2.11)."""
    from messages import get as _msg
    key = "paths" if operation == "mkdir" else "entries"
    items = args.get(key)
    if not isinstance(items, list):
        raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
    until = time.monotonic() + timeout_s
    results, failed, reached, stopped = [], [], 0, 0
    backends = set()
    for start in range(0, len(items), MAX_ITEMS):
        left = until - time.monotonic()
        if left < _GROUP_FLOOR:
            break
        group = items[start:start + MAX_ITEMS]
        budget = min(GROUP_SECONDS, left)
        group_until = time.monotonic() + budget
        answer = execute(owner, manifest, {**args, key: group}, turn_id, timeout_s=budget,
                         db_path=db_path, whole=False)
        backend = (answer.get("_filesystem") or {}).get("backend") or "native"
        for entry in answer.get("results") or []:
            entry.setdefault("_filesystem_backend", backend)
            backends.add(entry["_filesystem_backend"])
            results.append(entry)
        rows = answer.get("failed") or []
        if not rows and not answer.get("ok", True):
            # The whole group was refused before any item: each item says why.
            code = answer.get("error_code")
            error = permissions.PermissionError(code if code in permissions.ERROR_CODES else "ERR_FS_IO")
            rows = [_failure(operation, index, item, error) for index, item in enumerate(group)]
        out_of_time = time.monotonic() >= group_until
        for row in rows:
            row = {**row, "index": row["index"] + start}
            if out_of_time and row.get("error_code") in _TIME_CODES:
                if row.get("_unsure"):
                    # The host had it when the time ended: say so, never «left as it was».
                    row.update(error_code="ERR_FS_REQUEST_EXPIRED", error=_msg("MSG_FS_CAUSE_TIME_UNSURE"))
                else:
                    row.update(error_code="ERR_FS_REQUEST_EXPIRED", error=_msg("MSG_FS_CAUSE_TIME"))
                    stopped += 1
            failed.append(row)
        reached = start + len(group)
    for index in range(reached, len(items)):
        refused = operation == "move" and "refused" in items[index]
        failed.append(_failure(operation, index, items[index], None, timed_out=not refused))
        stopped += 0 if refused else 1
    value = batch_result(owner, manifest, args, turn_id, operation, results, failed)
    if backends:
        value["_filesystem"]["backend"] = backends.pop() if len(backends) == 1 else "mixed"
    if stopped:
        # Time is a ceiling of the call, not a limit the person can widen:
        # asking again does what is left.
        value.update({"truncated": True, "truncated_what": "file" if operation == "move" else "folder",
                      "used": len(results), "available_total": len(items), "cap_field": "timeout_s",
                      "cap_value": timeout_s, "cap_expandable": False, "partial": bool(results),
                      "partial_reason": _msg("MSG_FS_TIME_PARTIAL", done=len(results), total=len(items),
                                             left=stopped)})
    return value


def _failure(operation, index, item, error, *, timed_out=False):
    """One item not done, in the tool's own failure shape."""
    from messages import get as _msg
    if timed_out:
        row = {"error_code": "ERR_FS_REQUEST_EXPIRED", "error": _msg("MSG_FS_CAUSE_TIME")}
        if operation == "move":
            return {"index": index, **{key: item[key] for key in ("src", "dst") if key in item}, **row}
        return {"index": index, "path": item, **row}
    if operation == "move":
        return move_failure(index, item, error)
    failure = error_result(error)
    return {"index": index, "error": failure["error"], "error_code": failure["error_code"], "path": item}


def execute(owner, manifest, args, turn_id, *, timeout_s=30, db_path=None, authority=None, source_fd=None,
            whole=True):
    try:
        operation = permissions.served_operation(manifest, args)
        if operation is None:
            raise permissions.PermissionError("ERR_FS_CONTRACT_INVALID")
        if not owner:
            raise permissions.PermissionError("ERR_FS_PERMISSION_REQUIRED")
        if source_fd is not None and authority is None:
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        if source_fd is not None and operation != "copy":
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        if authority is None:
            args = settled(owner, operation, args)
        if (operation == "mkdir" and not isinstance(args.get("paths"), list)
                or operation == "move" and move_rows(args) is None):
            raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        if operation == "move":
            # What the closed move does not do is refused before any effect.
            if args.get("overwrite") is True or args.get("allow_dirs") is True:
                raise permissions.PermissionError("ERR_FS_BROKER_UNSUPPORTED")
        if authority is None and whole and operation in permissions.SERVER_OPERATIONS:
            return _in_groups(owner, manifest, args, turn_id, operation, timeout_s=timeout_s, db_path=db_path)
        if authority is None:
            import server_filesystem_host as host
            delegated = host.execute(owner, manifest, args, turn_id, timeout_s=timeout_s, db_path=db_path)
            if delegated is not None:
                return delegated
        broker = Broker(owner, manifest, args, turn_id, timeout_s=timeout_s, db_path=db_path, authority=authority)
        if operation == "list":
            return broker.list(permissions.normalize_path(args.get("path"), "posix"))
        specs = capped(write_specs(args) if operation == "write" else copy_specs(args) if operation == "copy"
                       else dir_paths(args) if operation == "mkdir" else move_rows(args) if operation == "move"
                       else paths(args))
        if source_fd is not None and (operation != "copy" or len(specs) != 1):
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        results, failed = [], []
        for index, spec in enumerate(specs):
            try:
                if "_row_journal" in args:
                    from reverse_patterns import recognize_file
                    ordinal = args.get("_row_offset", 0) + index
                    saved = args["_row_journal"].get(str(ordinal))
                    if saved is not None and (saved["item"] != spec or saved["pattern"] != args["_row_pattern"]):
                        raise permissions.PermissionError("ERR_FS_SOURCE_CHANGED")
                    if saved is not None:
                        value = recognize_file(broker, saved["pattern"], spec, saved["evidence"])
                        if value is not None:
                            results.append(value)
                            continue
                    elif not args.get("_row_observe"):
                        evidence = recognize_file(broker, args["_row_pattern"], spec)
                        saved = {"pattern": args["_row_pattern"], "evidence": evidence}
                        broker.authority.checkpoint({"index": ordinal, "item": spec,
                            "pattern": args["_row_pattern"], "evidence": evidence})
                    if args.get("_row_observe"):
                        continue
                if operation == "move" and "refused" in spec:
                    failed.append(move_failure(index, spec, None))
                    continue
                results.append(broker.copy(spec, source_fd=source_fd) if source_fd is not None
                               else getattr(broker, operation)(spec))
            except (OSError, ValueError, TypeError, LookupError) as exc:
                uncertain = False
                if "_row_journal" in args and saved is not None:
                    try:
                        value = recognize_file(broker, saved["pattern"], spec, saved["evidence"])
                        if value is not None:
                            results.append(value)
                            continue
                    except (OSError, ValueError, TypeError, LookupError):
                        uncertain = True
                failed.append(_failure(operation, index, spec, exc))
                if uncertain:
                    failed[-1]["_unsure"] = True
                    failed.extend(_failure(operation, later, other, exc)
                                  for later, other in enumerate(specs[index + 1:], index + 1))
                    break
                continue
        return batch_result(owner, manifest, args, turn_id, operation, results, failed)
    except (OSError, ValueError, TypeError, LookupError) as exc:
        return error_result(exc)


def created_folders(results):
    """Every folder a creation recorded, as (entry index, entry, path,
    identity), deepest first: a parent goes after what it contained."""
    found = []
    for index, entry in enumerate(results.get("results") or []):
        folders = entry.get("_dirs_created", entry.get("dirs_created")) or []
        identities = entry.get("_dirs_created_ids") or []
        if (not isinstance(folders, list) or not isinstance(identities, list) or len(folders) != len(identities)
                or any(not isinstance(item, list) or len(item) != 2 or any(type(v) is not int for v in item)
                       for item in identities)):
            raise permissions.PermissionError("ERR_FS_AUTHORITY_INVALID")
        found.extend((index, entry, permissions.normalize_path(folder, "posix"), identity)
                     for folder, identity in zip(folders, identities))
    return sorted(found, key=lambda item: item[2].count("/"), reverse=True)


def undo_plan(operation, pattern, results):
    """What the undo of a server-only operation does, as (entry index, entry,
    folder, identity): a folder to remove (deepest first, only folders the call
    made) or, with no folder, a moved file to put back. None for the other
    operations, which keep their own undo."""
    if operation not in permissions.SERVER_OPERATIONS:
        return None
    if pattern == "delete_created_dirs" or operation == "mkdir" and pattern == "delete_created_paths":
        return created_folders(results)
    if operation == "move" and pattern == "swap_src_dst":
        # The last file moved goes back first.
        return [(index, entry, None, None) for index, entry in reversed(list(enumerate(results.get("results") or [])))]
    return []


# One budget per invocation of the unchanged undo executor. A context-local
# value also makes in-process tests and concurrent runtime calls independent.
from contextvars import ContextVar
_UNDO_DEADLINE = ContextVar("filesystem_undo_deadline", default=None)


def begin_undo_budget():
    from executor_helpers import granted_seconds
    seconds = granted_seconds(30)
    _UNDO_DEADLINE.set(time.monotonic() + max(0, seconds - min(5, seconds / 10)))


def undo_seconds_remaining():
    deadline = _UNDO_DEADLINE.get()
    return max(0, deadline - time.monotonic()) if deadline is not None else 30.0


def _undo_key(index, folder):
    return "folder:" + folder if folder is not None else "row:" + str(index)


def undo_stage(pattern, results, *, db_path=None, actor=None, authority=None, trusted_owner=None, timeout_s=30):
    """Reverse recorded effects within the caller's remaining budget.

    Completed keys are journalled once per operation, not once per row. A
    missing receipt after a crash is reconciled by the native identity checks.
    Unreached rows are counts, never thousands of synthetic failure records.
    Each helper exchange retains its existing 30-second ceiling.
    """
    context = results["_filesystem"]
    operation = permissions.served_operation(context["manifest"], context["args"])
    try:
        plan = undo_plan(operation, pattern, results)
    except (OSError, ValueError, TypeError, LookupError) as exc:
        return error_result(exc)
    if plan is None:
        plan = [(i, row, None, None) for i, row in enumerate(results.get("results") or [])]
    completed = set((results.get("_undo_completed") or {}).get(pattern, []))
    todo = [(i, row, folder, identity) for i, row, folder, identity in plan
            if _undo_key(i, folder) not in completed]
    deadline = time.monotonic() + max(0, timeout_s)
    done, failed = [], []
    left = {"time": 0, "refused": 0, "uncertain": 0}
    cost = 0.0
    for position, (index, entry, folder, identity) in enumerate(todo):
        remaining = deadline - time.monotonic()
        if remaining < max(0.05, cost):
            left["time"] += len(todo) - position
            break
        item_entry = entry if folder is None else {
            **entry, "_dirs_created": [folder], "_dirs_created_ids": [identity],
            "dirs_created": [folder]}
        item = {**results, "results": [item_entry]}
        item.pop("_undo_completed", None)
        started = time.monotonic()
        try:
            reply = None
            if authority is None:
                import server_filesystem_host as host
                reply = host.undo(pattern, item, actor=actor, db_path=db_path,
                                  timeout_s=min(30, remaining))
                if reply is None and context.get("backend") in {"host", "mixed"}:
                    raise permissions.PermissionError("ERR_FS_HOST_UNAVAILABLE")
            if reply is None:
                reply = _undo_stage_native(pattern, item, db_path=db_path, actor=actor,
                    authority=authority, trusted_owner=trusted_owner, timeout_s=remaining)
            if reply.get("ok", True):
                completed.add(_undo_key(index, folder))
            errors = reply.get("failed") or ([] if reply.get("ok", True) else [reply])
            for error in errors:
                code = error.get("error_code")
                category = "time" if code in _TIME_CODES else "uncertain" if error.get("_unsure") else "refused"
                left[category] += 1
                if len(failed) < 5:
                    failed.append({**error, "index": index})
            done.extend(reply.get("results") or [])
        except (OSError, ValueError, TypeError, LookupError) as exc:
            error = error_result(exc)
            left["time" if error.get("error_code") in _TIME_CODES else "refused"] += 1
            if len(failed) < 5:
                failed.append({"index": index, **error})
        cost = max(cost, time.monotonic() - started)
    pending = sum(left.values())
    return {"ok": pending == 0, "ok_count": len(done), "fail_count": pending,
            "results": done, "failed": failed, "_left_counts": left,
            "_undo_progress": {"completed": sorted(completed), "pending_time": left["time"]}}


def _undo_stage_native(pattern, results, *, db_path=None, actor=None, authority=None, trusted_owner=None, timeout_s=30):
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
    broker = Broker(owner, context["manifest"], context["args"], context["turn_id"], db_path=db_path,
                    authority=authority, timeout_s=timeout_s)
    done, failed = [], []
    try:
        plan = undo_plan(permissions.served_operation(context["manifest"], context["args"]), pattern, results)
    except (OSError, ValueError, TypeError, LookupError) as exc:
        return error_result(exc)
    if plan is not None:
        for index, entry, folder, identity in plan:
            try:
                if folder is None:
                    done.append(broker.move_back(entry))
                    continue
                removed = broker.rmdir(folder, identity)
                if removed is not None:
                    done.append(removed)
            except (OSError, ValueError, TypeError, LookupError) as exc:
                failed.append({"index": index, "path": folder or entry.get("dst"), **error_result(exc)})
        return {"ok": not failed, "ok_count": len(done), "fail_count": len(failed), "results": done, "failed": failed}
    for index, entry in enumerate(results.get("results") or []):
        path = entry.get("dst") or entry.get("path")
        if not path or entry.get("skipped"):
            continue
        remove = pattern in {"swap_src_dst", "delete_created_paths"} and entry.get("created")
        blob = (entry.get("prev_blob_path") or entry.get("blob_path")) if pattern == "restore_blob_backup" else None
        if not remove and not blob:
            continue
        try:
            broker.check(path, "write")
            # A crash may have committed the inverse before its journal
            # receipt. Recognize that state before checking the old output.
            if remove and not broker._present(path):
                done.append({"path": path, "deleted": True, "already_restored": True})
                continue
            if blob and broker._present(path):
                expected_blob = os.path.basename(blob)[:-4]
                with io.parent(path) as (parent, name):
                    fd = io.open_file(parent, name)
                    try:
                        identity = io.identity(io.regular(fd))
                        restored = (io.digest(fd, lambda: broker.check(path, "write")) == expected_blob
                                    and broker._identity_at(parent, name) == identity)
                    finally:
                        os.close(fd)
                if restored:
                    done.append({"path": path, "restored": True, "already_restored": True})
                    continue
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


# A durable caller prepares a group once before its first visible effect. The
# evidence is ordinary metadata, not write authority: every use checks today's
# rights through the same broker as a normal call.

"""Check known server destinations before admitting a long plan; never list them."""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import re
import sqlite3

import filesystem_permissions as permissions
import server_filesystem as files


@contextmanager
def _grants():
    import devices
    path = Path(os.environ.get("METNOS_DEVICES_DB") or devices.DEFAULT_DB_PATH)
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) if path.is_file() else None
        if conn is not None and not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='filesystem_grants'").fetchone():
            conn.close()
            conn = None
        if conn is None:
            conn = sqlite3.connect(":memory:")
            conn.executescript(permissions.SCHEMA)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        if conn is not None:
            conn.close()


def _literal(value):
    return isinstance(value, str) and bool(value) and not any(c in value for c in "{}*?[")


def _write_flags(path, operation, args):
    replaces = (operation == "write" and args.get("mode", "overwrite")
                not in {"skip_if_exists", "fail_if_exists"}
                or operation == "copy" and args.get("overwrite") is True)
    return {"overwrite": True} if replaces and os.path.isfile(path) else {}


def _placed(owner, args, originals):
    """Resolve only the closed protocol's path fields, as execution does."""
    result = dict(args)
    for key in ("path", "src", "dst"):
        if _literal(result.get(key)):
            real = files.place(owner, result[key])
            originals[real] = result[key]
            result[key] = real
    if isinstance(result.get("paths"), list):
        result["paths"] = [_placed(owner, {"path": value}, originals)["path"]
                           for value in result["paths"]]
    for key in ("entries", "files"):
        if isinstance(result.get(key), list):
            result[key] = [_placed(owner, row, originals) if isinstance(row, dict) else row
                           for row in result[key]]
    return result


def _row_roots(steps, executors, number, seen=()):
    """Follow declared directory inputs or row lineage; arbitrary results stay unknown."""
    import partitioned_runs
    if type(number) is not int or not 0 < number <= len(steps) or number in seen:
        return []
    step = steps[number - 1]
    args = step.args if isinstance(step.args, dict) else {}
    executor = executors.get(step.tool)
    policy = partitioned_runs.policy_of(executor)
    manifest = files.manifest_for(executor)
    search = permissions.search_contract(manifest, args)
    if policy and policy["kind"] == "directory":
        values = args.get(policy["list_arg"], [args.get(policy["arg"])])
    elif search:
        values = [args.get(search)]
    elif permissions.served_operation(manifest, args) == "list":
        values = [args.get("path")]
    else:
        return _row_roots(steps, executors, args.get("from_step"), (*seen, number))
    roots = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str) and value.endswith("/*"):
            value = value[:-2]
        if _literal(value):
            roots.append(value)
        elif isinstance(value, str):
            reference = re.fullmatch(r"\$\{step(\d+)\.(?:entries|results)\.\*\.path\}", value)
            if reference:
                roots.extend(_row_roots(steps, executors, int(reference[1]), (*seen, number)))
    return roots


def _partial_right(owner, manifest, args, root, conn):
    """A narrower right may cover the unknown result: leave that to execution."""
    from capabilities import effective_capabilities
    from invocation_scope import _within
    import person_files
    from workspace_policy import get_scope
    candidates = [grant["path"] for grant in permissions.list_grants(
        owner, "server", active_only=True, conn=conn) if grant.get("can_write")]
    candidates.append(str(person_files.root(owner)))
    hints = [hint for actor in permissions._actors(owner) for hint in get_scope(actor, "fs:write")]
    hints.extend(hint for cap in effective_capabilities(
        manifest.get("capabilities", []), manifest.get("args", {}), args)
        if cap.get("name") == "fs:write" for hint in cap.get("hint", []) or []
        if isinstance(hint, str) and not hint.startswith("arg:"))
    for hint in hints:
        prefix = re.split(r"[*?\[]", hint, maxsplit=1)[0].rstrip("/") or "/"
        if prefix != hint:
            # A glob may cover unknown children even when its literal prefix
            # is not itself writable. Defer; this does not grant that prefix.
            prefix = files.place(owner, prefix)
            if (_within(Path(prefix), root.rstrip("/") + "/**")
                    or _within(Path(root), prefix.rstrip("/") + "/**")):
                return True
        else:
            candidates.append(hint)
    for candidate in candidates:
        candidate = files.place(owner, candidate)
        if not _within(Path(candidate), root.rstrip("/") + "/**"):
            continue
        try:
            permissions.require(owner, "server", candidate, "write", manifest=manifest, args=args, conn=conn)
            return True
        except permissions.PermissionError:
            pass
    return False


def check(framework, executors, owner, *, target_device=None):
    try:
        return _check(framework, executors, owner, target_device=target_device)
    except (OSError, sqlite3.Error):
        return None  # An unavailable preview leaves the existing execution check in charge.


def _check(framework, executors, owner, *, target_device):
    if target_device is not None:
        return None
    steps = tuple(getattr(framework, "steps", ()) or ())
    with ExitStack() as stack:
        conn = None
        for step in steps:
            args = step.args if isinstance(step.args, dict) else {}
            manifest = files.manifest_for(executors.get(step.tool))
            operation = permissions.served_operation(manifest, args)
            if operation not in {"mkdir", "write", "copy", "move", "delete"}:
                continue
            originals = {}
            placed = files.settled(owner, operation, _placed(owner, args, originals))
            originals.update(placed.get("_filesystem_original_paths") or {})
            try:
                if operation == "write":
                    requests = [(permissions.normalize_path(row["path"], "posix"), row)
                                for row in files.write_specs(placed) if _literal(row["path"])]
                else:
                    requests = [(path, placed) for path, action in files.requests(operation, placed)
                                if action == "write" and _literal(path)]
                requests = [(path, _write_flags(path, operation, row)) for path, row in requests]
            except permissions.PermissionError:
                requests = []  # The unresolved/invalid request still meets the execution check.
            template = args.get("dst_template" if operation in {"move", "copy"} else "path_template")
            if _literal(template):
                path = files.place(owner, template)
                requests.append((path, _write_flags(path, operation, args)))
                originals[path] = template
            if operation == "write" and not requests and _literal(args.get("path")):
                path = files.place(owner, args["path"])
                requests.append((path, _write_flags(path, operation, args)))
            roots = _row_roots(steps, executors, args.get("from_step"))
            dynamic = roots if operation in {"move", "delete"} else []
            if isinstance(template, str) and "{" in template and "${" not in template:
                if template.startswith(("{partition}/", "{path}/")):
                    dynamic += roots
                elif not template.startswith("{"):
                    prefix = template.split("{", 1)[0]
                    dynamic.append(str(Path(prefix).parent) if not prefix.endswith("/") else prefix)
            requests.extend((files.place(owner, root), {"directory": True, "recursive": True}) for root in dynamic)
            if not requests:
                continue
            if conn is None:
                conn = stack.enter_context(_grants())
            denied = []
            for path, flags in requests:
                try:
                    permissions.require(owner, "server", path, "write", manifest=manifest,
                                        args=args, conn=conn, **flags)
                except permissions.PermissionError as exc:
                    if str(exc) != "ERR_FS_PERMISSION_REQUIRED":
                        raise
                    if flags.get("recursive") and _partial_right(owner, manifest, args, path, conn):
                        continue
                    denied.append({"path": path, "original": originals.get(path) or next(
                        (root for root in dynamic if files.place(owner, root) == path), None),
                        "action": "write", **flags})
            if denied:
                return files.permission_result(owner, manifest, args, denied, conn=conn)
    return None

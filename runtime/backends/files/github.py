# SPDX-License-Identifier: MIT
"""Expose a GitHub repository through the generic file backend protocol.

The existing skill bridge owns API access and credentials. This adapter
returns vector results, explicit truncation and per-path failure details.
"""
from __future__ import annotations

import fnmatch
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from messages import get as _msg

from .. import _github_bridge as _gh

_SKILL = _gh.SKILL
# Bound independent network reads to avoid flooding the provider.
_READ_WORKERS = 8
_DEFAULT_MAX_RESULTS = 1000

# Share provider coordinates, invocation and credential errors across domains.
_repo_of = _gh.repo_of
_as_list = _gh.as_list
_call = _gh.call


def _fail(error_code: str, error: str, *, error_class: str) -> dict:
    return _gh.fail(error_code, error, error_class=error_class)


def _auth_required() -> dict:
    return _gh.auth_required()


def _collect(args: dict, records: list[dict], *, available: int,
             cap: int, cap_field: str) -> dict:
    out = {"ok": True, "entries": records, "used": len(records),
           "available_total": available, "files_source": _SKILL}
    if cap and available > cap:
        out.update({"truncated": True, "truncated_what": "file",
                    "cap_field": cap_field, "cap_value": cap})
    return out


def _tree(args: dict, repo: str, *, include_dirs: bool):
    argv = ["repos_tree", "--repo", repo]
    ref = args.get("ref")
    if ref:
        argv.extend(["--ref", str(ref)])
    return _call(argv)


def find(args: dict) -> dict:
    """Find repository paths by glob, defaulting to all paths as locally."""
    if not isinstance(args, dict):
        return _fail("ERR_ARG_INVALID",
                     _msg("ERR_ARGS_NOT_OBJECT"),
                     error_class="invalid_args")
    repo = _repo_of(args)
    if not repo:
        return _fail("ERR_ARG_MISSING",
                     _msg("ERR_ARG_MISSING", arg="repo"),
                     error_class="invalid_args")

    patterns = _as_list(args.get("pattern")) + _as_list(args.get("patterns"))
    if not patterns:
        patterns = ["*"]
    include_dirs = bool(args.get("include_dirs", False))
    prefix = (args.get("path_prefix") or "").strip("/")

    records, error = _tree(args, repo, include_dirs=include_dirs)
    if error is not None:
        if error["error_class"] == "auth_required":
            return _auth_required()
        return _fail("ERR_OP_FAILED", error["error"],
                     error_class=error["error_class"])

    entries = []
    for record in records:
        path = record.get("path")
        if not isinstance(path, str):
            continue
        kind = record.get("type") or record.get("kind")
        if kind == "tree" and not include_dirs:
            continue
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            continue
        name = path.rsplit("/", 1)[-1]
        if not any(fnmatch.fnmatch(path.lower(), p.lower())
                   or fnmatch.fnmatch(name.lower(), p.lower())
                   for p in patterns):
            continue
        entries.append({
            "kind": "dir" if kind == "tree" else "file",
            "repo": repo, "path": path, "name": name,
            "size": record.get("size"), "sha": record.get("sha"),
            "ref": record.get("ref") or args.get("ref"),
        })

    available = len(entries)
    cap = args.get("max_results", _DEFAULT_MAX_RESULTS)
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = _DEFAULT_MAX_RESULTS
    kept = entries if cap <= 0 else entries[:cap]
    return _collect(args, kept, available=available, cap=max(cap, 0),
                    cap_field="max_results")


def _read_one(repo: str, path: str, ref) -> tuple[str, object]:
    argv = ["repos_read_file", "--repo", repo, "--path", path]
    if ref:
        argv.extend(["--ref", str(ref)])
    records, error = _call(argv)
    if error is not None:
        if error["error_class"] == "auth_required":
            return "error", {"path": path, **_auth_required()}
        return "error", {"path": path, **error}
    return "entry", [{
        "kind": "file", "repo": repo, "path": record.get("path") or path,
        "ref": record.get("ref") or ref, "sha": record.get("sha"),
        "size": record.get("size"), "is_text": record.get("is_text"),
        "content": record.get("content"), "encoding": record.get("encoding"),
    } for record in records]


def read(args: dict) -> dict:
    """Read repository files and return vector entries in input order."""
    if not isinstance(args, dict):
        return _fail("ERR_ARG_INVALID",
                     _msg("ERR_ARGS_NOT_OBJECT"),
                     error_class="invalid_args")
    repo = _repo_of(args)
    if not repo:
        return _fail("ERR_ARG_MISSING", _msg("ERR_ARG_MISSING", arg="repo"),
                     error_class="invalid_args")

    paths = _as_list(args.get("paths")) or _as_list(args.get("path"))
    # A path used as a repository coordinate is not also a file to read.
    paths = [p for p in paths if p.strip("/") != repo]
    if not paths:
        return _fail("ERR_ARG_MISSING", _msg("ERR_ARG_MISSING", arg="paths"),
                     error_class="invalid_args")

    ref = args.get("ref")
    outcomes: list = [None] * len(paths)
    with ThreadPoolExecutor(max_workers=min(_READ_WORKERS, len(paths))) as pool:
        futures = {pool.submit(_read_one, repo, path, ref): index
                   for index, path in enumerate(paths)}
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()

    entries: list = []
    errors: list = []
    for outcome in outcomes:
        if outcome is None:
            continue
        kind, payload = outcome
        if kind == "error":
            errors.append(payload)
        else:
            entries.extend(payload)

    out = {"ok": True, "entries": entries, "used": len(entries),
           "available_total": len(entries), "files_source": _SKILL}
    if errors:
        out["errors"] = errors
        out["error_count"] = len(errors)
        out["ok"] = bool(entries)
        if not entries and all(e.get("error_class") == "auth_required" for e in errors):
            out.update(_auth_required())
    return out


def list_dirs(args: dict) -> dict:
    """List immediate children, including both files and directories."""
    return _walk_level(args, only_dirs=False)


def find_dirs(args: dict) -> dict:
    """Find immediate directories, including files only when requested."""
    return _walk_level(args, only_dirs=not bool(args.get("include_files"))
                       if isinstance(args, dict) else True)


def _walk_level(args: dict, *, only_dirs: bool) -> dict:
    if not isinstance(args, dict):
        return _fail("ERR_ARG_INVALID",
                     _msg("ERR_ARGS_NOT_OBJECT"),
                     error_class="invalid_args")
    repo = _repo_of(args)
    if not repo:
        return _fail("ERR_ARG_MISSING", _msg("ERR_ARG_MISSING", arg="repo"),
                     error_class="invalid_args")

    parents = _as_list(args.get("paths")) or _as_list(args.get("path")) or [""]
    parents = ["" if p in (".", "/", repo) else p.strip("/") for p in parents]
    ref = args.get("ref")

    entries: list = []
    errors: list = []
    for parent in parents:
        argv = ["repos_list_dir", "--repo", repo, "--path", parent]
        if ref:
            argv.extend(["--ref", str(ref)])
        records, error = _call(argv)
        if error is not None:
            if error["error_class"] == "auth_required":
                error = _auth_required()
            errors.append({"path": parent, **error})
            continue
        for record in records:
            entries.append({
                "kind": "dir" if record.get("type") == "dir" else "file",
                "repo": repo, "parent": parent, "name": record.get("name"),
                "path": record.get("path"), "type": record.get("type"),
                "size": record.get("size"), "sha": record.get("sha"),
            })

    if only_dirs:
        entries = [e for e in entries if e["kind"] == "dir"]

    available = len(entries)
    cap = args.get("max_results", _DEFAULT_MAX_RESULTS)
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = _DEFAULT_MAX_RESULTS
    kept = entries if cap <= 0 else entries[:cap]
    out = _collect(args, kept, available=available, cap=max(cap, 0),
                   cap_field="max_results")
    if out.get("truncated"):
        out["truncated_what"] = "dir"
    if errors:
        out["errors"] = errors
        out["error_count"] = len(errors)
        out["ok"] = bool(kept)
        if not kept and all(e.get("error_class") == "auth_required" for e in errors):
            out.update(_auth_required())
    return out

# SPDX-License-Identifier: MIT
"""GitHub issue operations for provider-neutral executors."""
from __future__ import annotations

from .. import _github_bridge as _gh

_SEARCH = {"repo": "--repo", "state": "--state", "limit": "--limit",
           "labels": "--labels", "since": "--since"}
_CREATE = {"repo": "--repo", "title": "--title", "body": "--body",
           "labels": "--labels", "assignees": "--assignees"}
_SET = {"repo": "--repo", "number": "--number", "state": "--state",
        "labels": "--labels", "assignees": "--assignees",
        "milestone": "--milestone"}


def _run(args, subcommand: str, mapping: dict, *, shape: str = "entries"):
    """Validate arguments and normalize the shared bridge response."""
    if not isinstance(args, dict):
        return None, _gh.not_an_object(shape)
    repo = _gh.repo_of(args)
    if not repo:
        return None, _gh.arg_missing("repo", shape=shape)
    argv = ["issues", subcommand] + _gh.flags({**args, "repo": repo}, mapping)
    records, error = _gh.call(argv)
    if error is not None:
        if error["error_class"] == "auth_required":
            return None, _gh.auth_required(shape)
        return None, _gh.fail("ERR_OP_FAILED", error["error"],
                              error_class=error["error_class"], shape=shape)
    return [{**record, "repo": repo} for record in records], None


def find_issues(args: dict) -> dict:
    """Find issues by state, labels and date."""
    records, error = _run(args, "search", _SEARCH)
    return error if error is not None else _gh.cap(args, records,
                                                   what="issues")


def read_issues(args: dict) -> dict:
    """Read an issue by number, optionally including comments."""
    if isinstance(args, dict) and not args.get("number"):
        return _gh.arg_missing("number")
    records, error = _run(args, "search",
                          {"repo": "--repo", "number": "--number",
                           "include_comments": "--include-comments"})
    return error if error is not None else _gh.cap(args, records,
                                                   what="issues")


def create_issues(args: dict) -> dict:
    """Create an issue; reject a missing title before contacting GitHub."""
    if isinstance(args, dict) and not str(args.get("title") or "").strip():
        return _gh.arg_missing("title", shape="results")
    records, error = _run(args, "create", _CREATE, shape="results")
    if error is not None:
        return error
    identifiers = [r["number"] for r in records if r.get("number") is not None]
    return {"ok": True, "results": records, "used": len(records),
            "n_created": len(records), "source": _gh.SKILL,
            "issue_ids": identifiers,
            "_undo": {"ids": identifiers,
                      "scope": {"repo": _gh.repo_of(args), "client": _gh.SKILL}}}


def delete_issues(args: dict) -> dict:
    """Close an issue by number."""
    if isinstance(args, dict) and not args.get("number"):
        return _gh.arg_missing("number", shape="results")
    records, error = _run(args, "delete",
                          {"repo": "--repo", "number": "--number"},
                          shape="results")
    if error is not None:
        return error
    # Closing is the compensating action: the issue and its history remain.
    records = [{**record, "ok": True} for record in records]
    return {"ok": True, "results": records, "used": len(records),
            "n_deleted": len(records), "source": _gh.SKILL}


def set_issues(args: dict) -> dict:
    """Update issue state, labels, assignees or milestone."""
    if isinstance(args, dict) and not args.get("number"):
        return _gh.arg_missing("number", shape="results")
    records, error = _run(args, "set", _SET, shape="results")
    if error is not None:
        return error
    return {"ok": True, "results": records, "used": len(records),
            "n_updated": len(records), "source": _gh.SKILL}

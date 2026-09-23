# SPDX-License-Identifier: MIT
"""GitHub pull-request operations for provider-neutral executors."""
from __future__ import annotations

from .. import _github_bridge as _gh

_SEARCH = {"repo": "--repo", "state": "--state", "limit": "--limit",
           "base": "--base", "head": "--head"}
_SET = {"repo": "--repo", "number": "--number", "state": "--state",
        "title": "--title", "labels": "--labels", "reviewers": "--reviewers"}
# Closed protocol values: normalize exact names, never glob review decisions.
_REVIEWS = {"approve": "APPROVE", "request_changes": "REQUEST_CHANGES",
            "comment": "COMMENT"}
_CHANGE = {"repo": "--repo", "number": "--number",
           "merge_method": "--merge-method", "commit_title": "--commit-title",
           "commit_message": "--commit-message"}


def _run(args, subcommand: str, mapping: dict, *, shape: str = "entries"):
    if not isinstance(args, dict):
        return None, _gh.not_an_object(shape)
    repo = _gh.repo_of(args)
    if not repo:
        return None, _gh.arg_missing("repo", shape=shape)
    argv = ["pulls", subcommand] + _gh.flags({**args, "repo": repo}, mapping)
    records, error = _gh.call(argv)
    if error is not None:
        if error["error_class"] == "auth_required":
            return None, _gh.auth_required(shape)
        return None, _gh.fail("ERR_OP_FAILED", error["error"],
                              error_class=error["error_class"], shape=shape)
    return [{**record, "repo": repo} for record in records], None


def find_pulls(args: dict) -> dict:
    """Find pull requests by state and branches."""
    records, error = _run(args, "search", _SEARCH)
    return error if error is not None else _gh.cap(args, records, what="pulls")


def read_pulls(args: dict) -> dict:
    """Read a pull request by number, optionally including its diff."""
    if isinstance(args, dict) and not args.get("number"):
        return _gh.arg_missing("number")
    records, error = _run(args, "read",
                          {"repo": "--repo", "number": "--number",
                           "include_diff": "--include-diff"})
    return error if error is not None else _gh.cap(args, records, what="pulls")


def _review(args: dict, repo: str) -> tuple[list[dict] | None, dict | None]:
    """Submit a review decision, distinct from an ordinary issue comment."""
    event = _REVIEWS.get(str(args.get("review") or "").strip().lower())
    if event is None:
        return None, _gh.fail(
            "ERR_ARG_ENUM", _gh._msg("ERR_ARG_ENUM", arg="review",
                                      allowed=" | ".join(_REVIEWS)),
            error_class="invalid_args", shape="results")
    body = str(args.get("review_body") or "").strip()
    if not body:
        # Review text expresses the user's judgment; never invent it.
        return None, _gh.arg_missing("review_body", shape="results")
    argv = ["comments", "send", "--repo", repo,
            "--target", f"pr:{args['number']}",
            "--body", body, "--review-event", event]
    records, error = _gh.call(argv)
    if error is not None:
        if error["error_class"] == "auth_required":
            return None, _gh.auth_required("results")
        return None, _gh.fail("ERR_OP_FAILED", error["error"],
                              error_class=error["error_class"],
                              shape="results")
    return [{**record, "repo": repo, "review": event.lower()}
            for record in records], None


def set_pulls(args: dict) -> dict:
    """Apply review and metadata updates, preserving each completed effect."""
    if not isinstance(args, dict):
        return _gh.not_an_object("results")
    if not args.get("number"):
        return _gh.arg_missing("number", shape="results")
    fields = [key for key in _SET if key not in ("repo", "number")
              and args.get(key) not in (None, "", [], {})]
    wants_review = bool(str(args.get("review") or "").strip())
    if not fields and not wants_review:
        return _gh.arg_missing("review", shape="results")

    results: list[dict] = []
    if wants_review:
        repo = _gh.repo_of(args)
        if not repo:
            return _gh.arg_missing("repo", shape="results")
        records, error = _review(args, repo)
        if error is not None:
            return error
        results.extend(records)
    if fields:
        records, error = _run(args, "set", _SET, shape="results")
        if error is not None:
            return {**error, "results": results, "used": len(results),
                    "n_updated": len(results), "source": _gh.SKILL}
        results.extend(records)
    return {"ok": True, "results": results, "used": len(results),
            "n_updated": len(results), "source": _gh.SKILL}


def change_pulls(args: dict) -> dict:
    """Merge a pull request without claiming automatic undo support."""
    if isinstance(args, dict) and not args.get("number"):
        return _gh.arg_missing("number", shape="results")
    records, error = _run(args, "change", _CHANGE, shape="results")
    if error is not None:
        return error
    return {"ok": True, "results": records, "used": len(records),
            "n_changed": len(records), "source": _gh.SKILL,
            "reversible": False}

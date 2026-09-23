# SPDX-License-Identifier: MIT
"""GitHub comments through the shared skill bridge.

Pull-request reviews belong to ``set_pulls``. Comment creation records
exact identifiers for undo. Partial failures preserve successful receipts.
"""
from __future__ import annotations

from .. import _github_bridge as _gh

# Issues and pull requests share numbering and the issue-comment endpoint.
# Bare numbers are therefore unambiguous here, unlike review operations.
_BARE_TARGET_KIND = "issue"
_KINDS = ("issue", "pr")


def _normalized_targets(args: dict) -> tuple[list[str], str | None]:
    """Return bridge-compatible targets or the first invalid target."""
    raw = (_gh.as_list(args.get("targets")) or _gh.as_list(args.get("target"))
           or _gh.as_list(args.get("number")))
    targets: list[str] = []
    for item in raw:
        candidate = item.strip()
        if candidate.isdigit():
            targets.append(f"{_BARE_TARGET_KIND}:{candidate}")
            continue
        kind, _, number = candidate.partition(":")
        if kind.strip().lower() in _KINDS and number.strip().isdigit():
            targets.append(f"{kind.strip().lower()}:{number.strip()}")
            continue
        return [], candidate
    return targets, None


def _opening(args, shape: str):
    """Validate the shared argument object and repository requirement."""
    if not isinstance(args, dict):
        return None, _gh.not_an_object(shape)
    repo = _gh.repo_of(args)
    if not repo:
        return None, _gh.arg_missing("repo", shape=shape)
    return repo, None


def _call(argv: list[str], shape: str):
    records, error = _gh.call(argv)
    if error is None:
        return records, None
    if error["error_class"] == "auth_required":
        return None, _gh.auth_required(shape)
    return None, _gh.fail("ERR_OP_FAILED", error["error"],
                          error_class=error["error_class"], shape=shape)


def create_comments(args: dict) -> dict:
    """Create comments and retain their identifiers for exact deletion."""
    repo, error = _opening(args, "results")
    if error is not None:
        return error
    body = str(args.get("body") or "").strip()
    if not body:
        return _gh.arg_missing("body", shape="results")
    targets, invalid = _normalized_targets(args)
    if invalid is not None:
        return _gh.arg_invalid("targets",
                               "issue:<number> | pr:<number>",
                               shape="results")
    if not targets:
        return _gh.arg_missing("targets", shape="results")

    results: list[dict] = []
    failed: list[dict] = []
    for target in targets:
        argv = ["comments", "send", "--repo", repo,
                "--target", target, "--body", body]
        records, call_error = _call(argv, "results")
        if call_error is not None:
            if call_error.get("error_class") == "auth_required" and not results:
                return call_error
            failed.append({"target": target, "error": call_error["error"],
                           "error_class": call_error["error_class"]})
            continue
        results.extend({**record, "repo": repo, "target": target}
                       for record in records)
    identifiers = [record["id"] for record in results if record.get("id") is not None]
    return {
        "ok": not failed,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
        "used": len(results),
        "n_created": len(results),
        "source": _gh.SKILL,
        "comment_ids": identifiers,
        "_undo": {"ids": identifiers, "scope": {"repo": repo, "client": _gh.SKILL}},
    }


def delete_comments(args: dict) -> dict:
    """Delete one or more comments by identifier."""
    repo, error = _opening(args, "results")
    if error is not None:
        return error
    ids = _gh.as_list(args.get("comment_ids")) or _gh.as_list(
        args.get("comment_id"))
    if not ids:
        return _gh.arg_missing("comment_ids", shape="results")

    results: list[dict] = []
    failed: list[dict] = []
    for comment_id in ids:
        argv = ["comments", "delete", "--repo", repo,
                "--comment-id", str(comment_id)]
        records, call_error = _call(argv, "results")
        if call_error is not None:
            if call_error.get("error_class") == "auth_required" and not results:
                return call_error
            failed.append({"comment_id": comment_id,
                           "error_class": call_error["error_class"],
                           "error": call_error["error"]})
            continue
        results.extend({**record, "repo": repo, "ok": True} for record in records)
    return {
        "ok": not failed,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
        "used": len(results),
        "n_deleted": len(results),
        "source": _gh.SKILL,
    }

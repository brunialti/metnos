# SPDX-License-Identifier: MIT
"""Run and inspect existing remote workflows through the GitHub bridge."""
from __future__ import annotations

from .. import _github_bridge as _gh

_DISPATCH = {"repo": "--repo", "workflow": "--workflow", "ref": "--ref",
             "inputs": "--inputs"}
_RUNS = {"repo": "--repo", "status": "--status", "limit": "--limit",
         "workflow_id": "--workflow-id", "branch": "--branch"}


def _run(args, subcommand: str, mapping: dict, *, shape: str = "entries"):
    if not isinstance(args, dict):
        return None, _gh.not_an_object(shape)
    repo = _gh.repo_of(args)
    if not repo:
        return None, _gh.arg_missing("repo", shape=shape)
    argv = ["workflows", subcommand] + _gh.flags({**args, "repo": repo},
                                                 mapping)
    records, error = _gh.call(argv)
    if error is not None:
        if error["error_class"] == "auth_required":
            return None, _gh.auth_required(shape)
        return None, _gh.fail("ERR_OP_FAILED", error["error"],
                              error_class=error["error_class"], shape=shape)
    return [{**record, "repo": repo} for record in records], None


def run_workflows(args: dict) -> dict:
    """Start a workflow; cancellation is not an undo of its effects."""
    if isinstance(args, dict) and not str(args.get("workflow") or "").strip():
        return _gh.arg_missing("workflow", shape="results")
    records, error = _run(args, "dispatch", _DISPATCH, shape="results")
    if error is not None:
        return error
    return {"ok": True, "results": records, "used": len(records),
            "n_started": len(records), "source": _gh.SKILL,
            "reversible": False}


def find_workflows(args: dict) -> dict:
    """Find workflow runs by status, workflow and branch."""
    records, error = _run(args, "runs_read", _RUNS)
    return error if error is not None else _gh.cap(args, records,
                                                   what="workflow runs")

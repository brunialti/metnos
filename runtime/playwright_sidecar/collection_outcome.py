"""How a collection search ended, as data: a pure function of the search's
raw result, its state and the texts it retained (modularity review
21/10/2026: the policy was spread over the end of the search function).
"""
from __future__ import annotations

from playwright_sidecar import login_navigation

_STEP_LIMITS = {"login_step_limit", "goal_step_limit"}


def outcome(result: dict, state: dict, texts: list[str]) -> tuple[dict, set[str]]:
    """The collection result and its coverage reasons; nothing is mutated."""
    result = dict(result)
    reasons = set(state.get("coverage_reasons") or ())
    found = bool(state.get("found"))
    if (state.get("observation_limit") or result.get("depth_limited")
            or result.get("error_class") in _STEP_LIMITS):
        # The exhausted action budget ended the search: a wider page limit
        # would not have continued it.
        budget = ("max_actions", login_navigation.COLLECTION_MAX_ACTIONS)
        if result.get("error_class") in _STEP_LIMITS:
            cap_field, cap_value = budget
        else:
            cap_field, cap_value = state.get("observation_limit") or (
                ("max_depth", login_navigation.COLLECTION_MAX_DEPTH)
                if result.get("depth_limited") else budget)
        result = {"ok": found, "truncated": True,
                  "truncated_what": "MSG_TRUNCATED_DEFAULT_WHAT",
                  # Action/page counts are not counts of matching records.
                  "used": 0, "available_total": None,
                  "cap_field": result.get("cap_field") or cap_field,
                  "cap_value": result.get("cap_value") or cap_value,
                  "error_class": "goal_step_limit"}
        if state.get("unresolved_control"):
            # A wider limit cannot resolve a refused branch: report its cause
            # with the limit, as an incomplete result keeping the records.
            result.update(ok=False, error_class=state["unresolved_control"])
    elif result.get("exhausted"):
        # Exhausting routes on an unrelated page does not attest an empty
        # collection. Preserve the first refused branch, or report that the
        # requested collection was never observed.
        partition_coverage = result.get("partition_coverage")
        result = ({"ok": False, "error_class": state["unresolved_control"]}
                  if state.get("unresolved_control") else
                  {"ok": True, "no_match": False} if found else
                  {"ok": False, "error_class": "selector_missing"})
        if partition_coverage:
            result["partition_coverage"] = partition_coverage
    if not result.get("ok") and found:
        # A later blocked branch does not erase already observed records.
        # Return saved evidence only: the current page may be outside scope.
        chunks = [text for text in texts if text]
        if chunks:
            result = {**result, "collection_partial": True, "text": "\n\n".join(chunks)}
    if (state.get("record_scope") or {}).get("kind") in {"limit", "single"}:
        # Presence is not an identity/count witness. Until extraction confirms
        # the requested bound, do not claim that the numeric/entity goal ended.
        reasons.add("record_bound_unverified")
    if reasons:
        result.update(collection_partial=True,
                      collection_coverage={"status": "unverified", "reason": sorted(reasons)[0],
                                           "reasons": sorted(reasons)})
    if state.get("record_scope"):
        result["record_scope"] = state["record_scope"]
    if state.get("year_boundary"):
        result["search_boundary"] = {"kind": "invoice_year", "year": state["year_boundary"],
                                     "scope": "source_witnessed_ordered_streams",
                                     "evidence": "logical_or_physical_sequence",
                                     "archive_completeness": "unverified"}
    return result, reasons

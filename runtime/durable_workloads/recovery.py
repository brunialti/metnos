"""Conservative budget projection for an explicitly admitted continuation.

Historical usage is never rewritten. Missing reports reserve their entire
frozen call allowance, in addition to known consumption. This is a budget
reservation, not a reconstruction of measured tokens. Callers must keep the
predecessor quiescent and retain this assessment with the new admission.
Attempts whose allowance the store already charged (``reserved_tokens``) are
part of the known counters and are not reserved twice.
"""
from __future__ import annotations

import math
from datetime import datetime

from .coordinator import parse_instant


_TOKEN_LIMIT_MAXIMA = (64, 16_777_216, 1_000_000)


def frozen_token_allowance(model) -> tuple[int, int] | None:
    """Return the (input, output) tokens a frozen zero-cost model attempt may use.

    ``None`` means the attempt has no frozen zero-cost bound, so a missing
    usage report stays genuinely unknown.
    """
    if (not isinstance(model, dict)
            or model.get("schema_version") != "metnos.durable-model-snapshot/2"
            or model.get("mode") != "llm"
            or model.get("cost_policy") != "zero"):
        return None
    limits = (model.get("max_calls"), model.get("max_input_tokens"), model.get("max_output_tokens"))
    if any(type(value) is not int or not 1 <= value <= maximum
           for value, maximum in zip(limits, _TOKEN_LIMIT_MAXIMA, strict=True)):
        return None
    return limits[0] * limits[1], limits[0] * limits[2]


def remaining_recovery_budgets(plan, usage, attempts, *, unit_count: int, now: datetime):
    budgets = dict(plan["budgets"])
    if type(unit_count) is not int or unit_count < 0:
        raise ValueError("recovery unit count is invalid")
    if any(attempt["state"] in {"leased", "running"} for attempt in attempts):
        raise ValueError("recovery requires quiescent attempts")
    uncertain = []
    reserved = 0
    for attempt in attempts:
        model = attempt["model"]
        if model.get("mode") != "llm":
            continue
        measured = attempt["usage"]
        if (isinstance(measured, dict) and measured.get("usage_missing") is False
                and measured.get("cost_unknown") is False):
            continue
        if type(attempt.get("reserved_tokens")) is int:
            continue
        bound = frozen_token_allowance(model)
        if bound is None:
            raise ValueError("unknown model consumption has no frozen zero-cost token bound")
        allowance = bound[0] + bound[1]
        reserved += allowance
        uncertain.append({"attempt_id": attempt["attempt_id"], "reserved_tokens": allowance})
    if bool(usage["usage_unknown"]) != bool(uncertain):
        raise ValueError("historical accounting uncertainty is not fully explained")
    consumed = {
        "max_units": unit_count,
        "max_tokens": usage["input_tokens"] + usage["output_tokens"] + reserved,
        "max_cost_micros": usage["cost_micros"],
        "max_bytes_read": usage["input_bytes"],
        "max_bytes_written": usage["output_bytes"],
        "max_artifacts": usage["artifact_count"],
    }
    started = usage.get("started_at")
    if started is not None:
        elapsed = (now - parse_instant(started)).total_seconds()
        if elapsed < 0 or (usage.get("clock_high_water_at") is not None
                           and now < parse_instant(usage["clock_high_water_at"])):
            raise ValueError("recovery clock regressed")
        consumed["max_wall_time_s"] = math.ceil(elapsed)
    for name, amount in consumed.items():
        if type(amount) is not int or amount < 0 or amount > budgets[name]:
            raise ValueError("recovery exceeds the original budget")
        budgets[name] -= amount
    if budgets["max_units"] < 1 or budgets["max_wall_time_s"] < 1:
        raise ValueError("recovery has no remaining execution budget")
    # A continuation grants one execution per new unit, without automatic
    # repetitions of the attempts already made under historical contracts.
    budgets["max_attempts_per_unit"] = 1
    return budgets, {"known_input_tokens": usage["input_tokens"],
                     "known_output_tokens": usage["output_tokens"],
                     "uncertain_attempts": uncertain, "reserved_tokens": reserved,
                     "consumed_or_reserved": consumed}

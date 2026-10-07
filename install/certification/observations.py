"""Pure HTTP/native observation interpretation shared with the installed oracle."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any

def _timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="milliseconds",
    ).replace("+00:00", "Z")


def _tools(raw: dict[str, Any], record: dict[str, Any] | None) -> list[str]:
    if raw.get("transport_error"):
        return ["http_turn"]
    steps = raw.get("steps_summary") or []
    tools = [str(step.get("tool") or "") for step in steps if step.get("tool")]
    if not tools and record:
        tools = [
            str(step.get("chosen_tool") or "")
            for step in record.get("steps", []) if step.get("chosen_tool")
        ]
    if not tools and (record or {}).get("mode") == "tutor":
        tools = ["tutor_explain"]
    if raw.get("final_message") and (not tools or tools[-1] != "final_answer"):
        tools.append("final_answer")
    return tools


def _route(tools: list[str], record: dict[str, Any] | None) -> str:
    if tools == ["http_turn"]:
        return "transport"
    if (record or {}).get("match_source") in {"lre", "technical_lre"}:
        return "lre"
    productive = [tool for tool in tools if tool != "final_answer"]
    if productive == ["tutor_explain"] or (record or {}).get("mode") == "tutor":
        return "tutor"
    if productive and productive[0] == "get_now":
        return "direct"
    if productive and productive[0].startswith("durable_"):
        return "lre"
    return "engine"


def _terminal(raw: dict[str, Any]) -> str:
    if raw.get("outcome"):
        return {
            "completed": "completed", "failed": "failed", "partial": "partial",
            "awaiting_input": "waiting_input", "cancelled": "cancelled",
        }.get(raw["outcome"], "failed")
    kind = str(raw.get("final_kind") or "error")
    steps = raw.get("steps_summary") or []
    succeeded = any(step.get("ok") is True for step in steps)
    failed = any(step.get("ok") is False for step in steps)
    if succeeded and failed:
        return "partial"
    if failed:
        return "failed"
    return {
        "answer": "completed",
        "ask": "waiting_input",
        "needs_inputs": "waiting_input",
        "error": "failed",
        "loop_break": "failed",
    }.get(kind, "failed")


def _response_checks(
    text: str, locale: str, record: dict[str, Any] | None,
) -> list[str]:
    low = text.casefold()
    checks: set[str] = set()
    if any(token in low for token in ("/admin/", "settings", "impostazioni")):
        checks.update({"source_visible", "navigation_visible"})
    if any(token in low for token in (
        "senza", "without", "soltanto", "only", "separat", "separate",
    )):
        checks.add("explain_act_boundary")
    if any(token in low for token in ("conferm", "approv", "confirm", "approval")):
        checks.add("approval_boundary")
    if re.search(r"\b(?:utc|cet|cest|europe/rome|roma|rome)\b", low):
        checks.add("timezone_visible")
    if re.search(r"\b\d+\b", low):
        checks.update({"honest_count", "limit_visible"})
    # A complete rendered result set is also honest count evidence even when
    # the renderer chooses a table without a numeric preamble.  Require one
    # stable visible marker for every output entry; partial tables do not pass.
    entry_results = [
        step.get("result") for step in (record or {}).get("steps", [])
        if isinstance(step.get("result"), dict)
        and isinstance(step.get("result", {}).get("entries"), list)
    ]
    if entry_results:
        visible_entries = entry_results[-1].get("entries") or []
        markers = []
        for entry in visible_entries:
            if not isinstance(entry, dict):
                markers = []
                break
            marker = next((
                str(entry.get(key)) for key in ("name", "title", "path", "id")
                if entry.get(key) not in (None, "")
            ), "")
            if not marker:
                markers = []
                break
            markers.append(marker.casefold())
        if markers and all(marker in low for marker in markers):
            checks.add("honest_count")
    if (("metnos-cert-note" in low and "seconda riga" in low)
            or "metnos-cert-summary" in low):
        checks.add("content_complete")
    if "riepilogo" in low or "summary" in low:
        checks.add("source_visible")
    if any(token in low for token in ("recente", "recent", "nuovo", "newest")):
        checks.add("ordering_visible")
    sort_result = next((
        step.get("result") for step in (record or {}).get("steps", [])
        if step.get("chosen_tool") == "sort_entries"
        and isinstance(step.get("result"), dict)
    ), {})
    ordered = sort_result.get("entries") if isinstance(sort_result, dict) else None
    ordered_names = [str(entry.get("name") or "") for entry in (ordered or [])
                     if isinstance(entry, dict) and entry.get("name")]
    positions = [low.find(name.casefold()) for name in ordered_names]
    if positions and all(pos >= 0 for pos in positions) and positions == sorted(positions):
        checks.add("ordering_visible")
    if "food" in low and "travel" in low:
        checks.add("grouping_visible")
    return sorted(checks)


def sum_model_calls(records: list[dict[str, Any]]) -> int | None:
    """Sum actual counters only; absent, unfinished or invalid capture is unknown."""
    if not records:
        return None
    counts = [item.get("model_calls") for item in records]
    if any(type(count) is not int or not 0 <= count <= 10**12 for count in counts):
        return None
    total = sum(counts)
    return total if total <= 10**12 else None


def build_observation(
    case: dict[str, Any], cycle: int, raw: dict[str, Any], *,
    turn_records: list[dict[str, Any]] | None = None,
    probe_outcomes: dict[str, tuple[bool, str]] | None = None,
    response_checks: list[str] | None = None,
    approval_count: int = 0,
    effects: list[str] | None = None,
    terminal: str | None = None,
) -> dict[str, Any]:
    records = turn_records or []
    if records:
        record = {
            **records[-1],
            "ts_start": records[0].get("ts_start"),
            "ts_end": records[-1].get("ts_end"),
            "steps": [
                step for item in records for step in item.get("steps", [])
            ],
        }
    else:
        record = None
    model_calls = sum_model_calls([raw]) if "model_calls" in raw else sum_model_calls(records)
    if model_calls is None:
        from .oracle import CertificationError

        raise CertificationError("actual model-call capture is missing or incomplete")
    tools = _tools(raw, record)
    start = float((record or {}).get("ts_start") or raw.get("ts_end") or 0)
    end = float((record or {}).get("ts_end") or raw.get("ts_end") or start)
    if not start:
        start = end
    probes = []
    for name in case["postcondition_probes"]:
        if probe_outcomes is None or name not in probe_outcomes:
            from .oracle import CertificationError
            raise CertificationError("postcondition input missing")
        passed, detail = probe_outcomes[name]
        probes.append({"name": name, "passed": passed, "detail": detail})
    route = _route(tools, record)
    return {
        "case_id": case["case_id"],
        "cycle": cycle,
        "started_at": _timestamp(start),
        "finished_at": _timestamp(end),
        "duration_ms": max(0, int(raw.get("total_ms") or ((end - start) * 1000))),
        "route": route,
        "plan": tools,
        "placement": raw.get("target_device") or "server",
        "approval_count": approval_count,
        "model_calls": model_calls,
        "terminal": terminal or _terminal({**(record or {}), **raw}),
        "effects": effects or ["no_action"],
        "probes": probes,
        "response_checks": sorted(set(_response_checks(
            str(raw.get("final_message") or ""), case["locale"], record,
        )) | set(response_checks or [])),
    }


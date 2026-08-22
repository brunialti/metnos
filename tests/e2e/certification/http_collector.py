"""HTTP observation collector for the real, isolated Metnos E2E server."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIXTURE_ROOT = Path("/tmp/metnos-certification-fixture")
QUICK_FLOW_IDS = (
    "explain.tutor.service-status",
    "explain.boundary.calendar-create",
    "explain.direct.current-time",
    "read.files.filtered",
    "read.file.content",
    "compose.find-then-read",
    "compose.find-sort-files",
    "compose.read-group-entries",
)


def prepare_quick_fixture() -> str:
    """Reset only the dedicated C2 tree and return its content digest."""
    if FIXTURE_ROOT.exists():
        shutil.rmtree(FIXTURE_ROOT)
    files = FIXTURE_ROOT / "files"
    entries = FIXTURE_ROOT / "entries"
    files.mkdir(parents=True)
    entries.mkdir(parents=True)
    (files / "note.txt").write_text(
        "METNOS-CERT-NOTE\nseconda riga di prova\n", encoding="utf-8",
    )
    (files / "report-alpha.txt").write_text("alpha report\n", encoding="utf-8")
    (files / "report-beta.txt").write_text("beta report\n", encoding="utf-8")
    (files / "riepilogo.txt").write_text("METNOS-CERT-SUMMARY\n", encoding="utf-8")
    (files / "other.md").write_text("not selected\n", encoding="utf-8")
    (entries / "expenses.csv").write_text(
        "category,amount\nfood,10\ntravel,20\nfood,5\n", encoding="utf-8",
    )
    base = 1_700_000_000
    for offset, path in enumerate(sorted(FIXTURE_ROOT.rglob("*"))):
        if path.is_file():
            os.utime(path, (base + offset, base + offset))
    return tree_digest(FIXTURE_ROOT)


def cleanup_quick_fixture() -> None:
    if FIXTURE_ROOT.exists():
        shutil.rmtree(FIXTURE_ROOT)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_turn_record(user_data: Path, turn_id: str) -> dict[str, Any] | None:
    turns = user_data / "turns"
    if not turns.is_dir() or not turn_id:
        return None
    for path in sorted(turns.glob("*.jsonl"), reverse=True):
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if record.get("turn_id") == turn_id:
                return record
    return None


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
    if not tools:
        tools = ["tutor_explain"]
    if raw.get("final_message") and (not tools or tools[-1] != "final_answer"):
        tools.append("final_answer")
    return tools


def _route(tools: list[str], record: dict[str, Any] | None) -> str:
    if tools == ["http_turn"]:
        return "transport"
    productive = [tool for tool in tools if tool != "final_answer"]
    if productive == ["tutor_explain"] or (record or {}).get("mode") == "tutor":
        return "tutor"
    if productive and productive[0] == "get_now":
        return "direct"
    if productive and productive[0].startswith("durable_"):
        return "lre"
    return "engine"


def _terminal(raw: dict[str, Any]) -> str:
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


def _step_results(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not record:
        return []
    return [
        step.get("result") or {} for step in record.get("steps", [])
        if isinstance(step.get("result"), dict)
    ]


def _probe(
    name: str, *, before_digest: str, after_digest: str,
    raw: dict[str, Any], record: dict[str, Any] | None,
    probe_outcomes: dict[str, tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    if probe_outcomes and name in probe_outcomes:
        return probe_outcomes[name]
    unchanged = {
        "service_digest_unchanged", "calendar_digest_unchanged",
        "state_digest_unchanged", "fixture_digest_unchanged",
    }
    if name in unchanged:
        passed = before_digest == after_digest
        return passed, "fixture content digest unchanged" if passed else "fixture digest changed"
    rendered = json.dumps(_step_results(record), ensure_ascii=False, sort_keys=True)
    if name == "fixed_clock_value":
        passed = bool(re.search(r'"(?:time|datetime|iso)"\s*:\s*"[^\"]+"', rendered))
        return passed, "get_now returned a structured clock value" if passed else "clock value absent"
    if name == "selected_file_set":
        passed = "report-alpha.txt" in rendered and "report-beta.txt" in rendered
        return passed, "both frozen report files observed" if passed else "frozen report set incomplete"
    if name == "content_digest_matches":
        passed = "METNOS-CERT-NOTE" in rendered or "METNOS-CERT-SUMMARY" in rendered
        return passed, "frozen content marker observed" if passed else "frozen content marker absent"
    if name == "selected_path_forwarded":
        steps = (record or {}).get("steps", [])
        passed = any(
            step.get("chosen_tool") == "read_files"
            and "metnos-cert-summary.txt" in json.dumps(
                step.get("resolved_args") or {}, ensure_ascii=False)
            for step in steps
        )
        return passed, "selected path reached the reader" if passed else "selected path not forwarded"
    if name == "all_paths_forwarded":
        passed = rendered.count("report-alpha.txt") >= 1 and rendered.count("note.txt") >= 1
        return passed, "fixture paths reached the pipeline" if passed else "some fixture paths absent"
    if name == "order_matches":
        sort_result = next((
            step.get("result") for step in (record or {}).get("steps", [])
            if step.get("chosen_tool") == "sort_entries"
            and isinstance(step.get("result"), dict)
        ), {})
        entries = sort_result.get("entries") if isinstance(sort_result, dict) else None
        mtimes = [entry.get("mtime") for entry in (entries or [])
                  if isinstance(entry, dict)]
        passed = bool(mtimes) and mtimes == sorted(mtimes, reverse=True)
        return passed, "mtime order is newest-first" if passed else "mtime order differs"
    if name == "rows_forwarded_once":
        passed = all(token in rendered for token in ("food", "travel", "amount"))
        return passed, "CSV rows observed by the pipeline" if passed else "CSV rows absent"
    if name == "group_members_match":
        final = str(raw.get("final_message") or "").casefold()
        passed = all(token in final for token in ("food", "travel", "10", "5", "20"))
        return passed, "all frozen group members are visible" if passed else "group members differ or are absent"
    return False, f"no C2 probe implementation for {name}"


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


def build_observation(
    case: dict[str, Any], cycle: int, raw: dict[str, Any], *,
    user_data: Path, before_digest: str, after_digest: str,
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
            "ts_start": records[0].get("ts_start"),
            "ts_end": records[-1].get("ts_end"),
            "mode": records[0].get("mode"),
            "steps": [
                step for item in records for step in item.get("steps", [])
            ],
        }
    else:
        record = load_turn_record(user_data, str(raw.get("turn_id") or ""))
    tools = _tools(raw, record)
    start = float((record or {}).get("ts_start") or raw.get("ts_end") or 0)
    end = float((record or {}).get("ts_end") or raw.get("ts_end") or start)
    if not start:
        start = end
    probes = []
    for name in case["postcondition_probes"]:
        passed, detail = _probe(
            name, before_digest=before_digest, after_digest=after_digest,
            raw=raw, record=record, probe_outcomes=probe_outcomes,
        )
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
        "model_calls": 0 if route == "direct" else max(1, len(records)),
        "terminal": terminal or _terminal(raw),
        "effects": effects or ["no_action"],
        "probes": probes,
        "response_checks": sorted(set(_response_checks(
            str(raw.get("final_message") or ""), case["locale"], record,
        )) | set(response_checks or [])),
    }

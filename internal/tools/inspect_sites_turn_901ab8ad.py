"""Read bounded, selected diagnostic fields; never print credential values."""
import json
from pathlib import Path

TURN = "901ab8ad5fb44a7a"
ROOT = Path("/var/lib/metnos-service/.local")


def rows(path):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise RuntimeError("diagnostic file too large")
    with path.open() as stream:
        for line in stream:
            if len(line) > 4 * 1024 * 1024:
                raise RuntimeError("diagnostic row too large")
            yield json.loads(line)


for row in rows(ROOT / "share/metnos/turns/2026-09-09.jsonl"):
    if row.get("turn_id") != TURN:
        continue
    print("TURN_FIELDS", sorted(row))
    print("INTENT", row.get("intent"))
    framework = row.get("framework")
    print("FRAMEWORK", framework if isinstance(framework, str) else None)
    for step in row.get("steps", []):
        print("STEP_FIELDS", sorted(step))
        print("STEP", {k: step.get(k) for k in
                       ("step_num", "chosen_tool", "subquery", "error_class")})
        for key in ("raw_args", "resolved_args"):
            value = step.get(key)
            if isinstance(value, dict):
                print("ARGS", {k: value[k] for k in
                      ("action", "goal", "from_step", "form_hint", "ambito")
                      if k in value})
        result = step.get("result") or {}
        print("RESULT", {k: result.get(k) for k in
              ("ok", "error_class", "error", "reason_code")})
        for entry in result.get("entries", []):
            print("ENTRY", {k: entry.get(k) for k in
                  ("session_id", "logged_in", "reason_code", "message")})

for row in rows(ROOT / "state/metnos/sites_audit.jsonl"):
    if "2026-09-09T09:01:39Z" <= row.get("ts", "") <= "2026-09-09T09:04:00Z":
        print("AUDIT", {k: row[k] for k in (
            "ts", "event", "session_id", "procedure", "method", "outcome",
            "reason_code", "error_class", "primitive", "purpose", "attempt", "phase",
        ) if k in row})

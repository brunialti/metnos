"""Read bounded, selected diagnostic fields of one turn; never print secrets.

Read-only. It takes a turn id or an unambiguous prefix, prints the plan, the
per-step outcome and the cookie/audit trail of that turn's browser session,
and refuses an ambiguous prefix rather than guessing.

    sudo /usr/bin/python3.12 internal/tools/inspect_sites_turn.py <turn-id>
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path("/var/lib/metnos-service/.local")
SELECTED_STEP = ("step_num", "chosen_tool", "subquery", "error_class")
SELECTED_ARGS = ("action", "goal", "from_step", "form_hint", "ambito", "url")
SELECTED_RESULT = ("ok", "error_class", "error", "reason_code")
SELECTED_ENTRY = (
    "session_id", "logged_in", "reason_code", "message", "obstruction_kind",
    "obstruction_reason", "url", "title", "screenshot_path", "error_class",
)
SELECTED_AUDIT = (
    "ts", "event", "session_id", "domain", "procedure", "method", "outcome",
    "reason_code", "error_class", "primitive", "purpose", "attempt", "phase",
)


MAX_ROW_BYTES = 4 * 1024 * 1024


def rows(path):
    """Stream one diagnostic file; the bound is per row, not per file.

    These logs grow without limit, so refusing a large file would refuse the
    only place the answer lives. Reading a line at a time keeps the memory
    bound; an oversized or malformed row is skipped and counted, so one bad
    line never hides the rest of the trace.
    """
    if not path.exists():
        return
    skipped = 0
    with path.open(errors="replace") as stream:
        for line in stream:
            if len(line) > MAX_ROW_BYTES:
                skipped += 1
                continue
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        print(f"SKIPPED {skipped} unreadable rows in {path.name}")


def selected(value, keys):
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in keys if key in value}


def main(argv):
    if len(argv) != 2 or not argv[1].strip():
        raise SystemExit("usage: inspect_sites_turn.py <turn-id-or-prefix>")
    wanted = argv[1].strip()
    turns = sorted((ROOT / "share/metnos/turns").glob("*.jsonl"))
    matches = {}
    for path in turns:
        for row in rows(path):
            identifier = row.get("turn_id") or ""
            if identifier.startswith(wanted):
                matches.setdefault(identifier, []).append(row)
    if not matches:
        raise SystemExit(f"no turn matches {wanted}")
    if len(matches) > 1:
        raise SystemExit("ambiguous prefix: " + " ".join(sorted(matches)))

    identifier, found = next(iter(matches.items()))
    print("TURN", identifier)
    sessions = set()
    shots = set()
    for row in found:
        print("INTENT", row.get("intent"))
        print("USER_QUERY", row.get("user_query"))
        print("ANSWER_CLASS", row.get("error_class"), "|", row.get("outcome"))
        for step in row.get("steps", []):
            print("STEP", selected(step, SELECTED_STEP))
            for key in ("raw_args", "resolved_args"):
                picked = selected(step.get(key), SELECTED_ARGS)
                if picked:
                    print("  ARGS", key, picked)
            result = step.get("result") or {}
            print("  RESULT", selected(result, SELECTED_RESULT))
            for entry in result.get("entries", []) or []:
                picked = selected(entry, SELECTED_ENTRY)
                if picked:
                    print("  ENTRY", picked)
                    if picked.get("session_id"):
                        sessions.add(picked["session_id"])
                    if picked.get("screenshot_path"):
                        shots.add(picked["screenshot_path"])
            for key in ("screenshot_path",):
                if result.get(key):
                    shots.add(result[key])

    for row in rows(ROOT / "state/metnos/sites_audit.jsonl"):
        if row.get("session_id") in sessions:
            print("AUDIT", selected(row, SELECTED_AUDIT))
    if not sessions:
        print("AUDIT none: the turn recorded no browser session")

    if shots:
        copied = Path(f"/tmp/metnos-turn-{identifier}")
        copied.mkdir(mode=0o755, exist_ok=True)
        owner = Path(__file__).resolve().stat()
        for source in sorted(shots):
            path = Path(source)
            if not path.is_file():
                print("SHOT missing", source)
                continue
            target = copied / path.name
            shutil.copyfile(path, target)
            os.chmod(target, 0o644)
            os.chown(target, owner.st_uid, owner.st_gid)
            print("SHOT", target)
        os.chown(copied, owner.st_uid, owner.st_gid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

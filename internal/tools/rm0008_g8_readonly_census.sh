#!/bin/bash
# Inspect only schema and aggregate lifecycle counts in the running service's
# configured stores. No product imports, keys, row contents or schema changes.
set -euo pipefail
case "${2:-}" in census|producer-metadata|producer-terminal-shape) ;; *) exit 64 ;; esac
/usr/bin/python3 -I - "$1" "$2" <<'PY'
import json
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import subprocess
import sys
import time

def main_pid():
    return int(subprocess.check_output(
        ["/usr/bin/systemctl", "show", "metnos-http.service", "-p", "MainPID", "--value"],
        timeout=5, text=True,
    ).strip())

pid = main_pid()
if pid <= 1:
    raise RuntimeError("http_process_unavailable")
account = pwd.getpwnam("metnos")
if Path(f"/proc/{pid}").stat().st_uid != account.pw_uid:
    raise RuntimeError("http_process_owner_mismatch")
with open(f"/proc/{pid}/environ", "rb") as stream:
    raw = stream.read(65537)
if len(raw) > 65536:
    raise RuntimeError("environment_size_limit")
allowed = {b"HOME", b"METNOS_USER_DATA",
           b"METNOS_USER_STATE", b"METNOS_EXECUTOR_STATS_DB", b"METNOS_PROMOTER_DB"}
environment = {}
for entry in raw.split(b"\0"):
    key, separator, value = entry.partition(b"=")
    if separator and key in allowed:
        environment[key.decode()] = value.decode()
del raw
service_home = Path(environment.get("HOME") or account.pw_dir)
# Mirror config._env_path and its actual defaults, not generic XDG rules.
data = Path(environment.get("METNOS_USER_DATA") or service_home / ".local/share/metnos")
state = Path(environment.get("METNOS_USER_STATE") or service_home / ".local/state/metnos")
sources = {
    "statistics": Path(environment.get("METNOS_EXECUTOR_STATS_DB", str(state / "executor_stats.db"))),
    "promotions": Path(environment.get("METNOS_PROMOTER_DB", str(data / "promoter.sqlite"))),
}
if sys.argv[2] in {"producer-metadata", "producer-terminal-shape"}:
    sources = {"producer_metadata": state / "birth" / "producer_receipts.sqlite"}
if any(not path.is_absolute() for path in sources.values()):
    raise RuntimeError("relative_store_path_requires_owner_resolution")
if main_pid() != pid:
    raise RuntimeError("http_process_changed")
output = os.open(str(Path(sys.argv[1]) / "result.json"),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
os.setgroups([])
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
result = {"http_pid": pid, "reader_uid": os.geteuid(), "reader_gid": os.getegid(),
          "scope": "read_only_schema_and_counts_not_a_certification_frontier", "stores": {}}
for kind, path in sources.items():
    item = {"configured_path": str(path)}
    result["stores"][kind] = item
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("store_not_regular")
        item.update(resolved_path=str(path.resolve(strict=True)), uid=before.st_uid,
                    gid=before.st_gid, mode=oct(stat.S_IMODE(before.st_mode)))
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            deadline = time.monotonic() + 15
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            item["schema_version"] = connection.execute("PRAGMA user_version").fetchone()[0]
            item["journal_mode"] = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if kind == "statistics":
                item["statistics_columns"] = [row[1] for row in connection.execute("PRAGMA table_info(executor_stats)")]
                item["history_columns"] = [row[1] for row in connection.execute("PRAGMA table_info(executor_history)")]
                counts = connection.execute(
                    "SELECT COUNT(*),COUNT(NULLIF(deprecated_at,'')),"
                    "COUNT(NULLIF(archived_at,'')) FROM executor_stats"
                ).fetchone()
                item.update(rows=counts[0], deprecated_rows=counts[1], archived_rows=counts[2],
                            history_rows=connection.execute("SELECT COUNT(*) FROM executor_history").fetchone()[0])
            elif kind == "promotions":
                item["columns"] = [row[1] for row in connection.execute("PRAGMA table_info(proposal_promote)")]
                item["rows"] = connection.execute("SELECT COUNT(*) FROM proposal_promote").fetchone()[0]
                item["state_counts"] = {value: connection.execute(
                    "SELECT COUNT(*) FROM proposal_promote WHERE state=?", (value,),
                ).fetchone()[0] for value in (
                    "pending", "promoted_grace", "promoted_finalized", "rolled_back", "archived", "review_needed",
                )}
                item["unclassified_rows"] = item["rows"] - sum(item["state_counts"].values())
            elif sys.argv[2] == "producer-terminal-shape":
                # Aggregate wire shape only: never expose request text,
                # diagnostics, signatures or receipt payloads.
                columns = ("encoded", "terminal_envelope", "terminal_auth")
                item["maximum_bytes"] = dict(zip(columns, connection.execute(
                    "SELECT MAX(length(encoded)),MAX(length(terminal_envelope)),"
                    "MAX(length(terminal_auth)) FROM birth_producer_receipts"
                ).fetchone()))
                item["terminal_rows"] = connection.execute(
                    "SELECT COUNT(*) FROM birth_producer_receipts WHERE terminal_envelope IS NOT NULL"
                ).fetchone()[0]
                item["invalid_json_rows"] = connection.execute(
                    "SELECT COUNT(*) FROM birth_producer_receipts WHERE terminal_envelope IS NOT NULL "
                    "AND NOT json_valid(terminal_envelope)"
                ).fetchone()[0]
                profiles = {}
                for field in (
                    "$.schema_version", "$.error_code", "$.diagnostic", "$.admission_receipt",
                    "$.publication", "$.publication.operation", "$.report.changed_dimensions",
                    "$.report.checks", "$.report.candidate_id", "$.report.admission_context_id",
                    "$.report.revision_class", "$.report.error_code",
                ):
                    profiles[field] = {kind or "missing": count for kind, count in connection.execute(
                        "SELECT json_type(terminal_envelope,?),COUNT(*) FROM birth_producer_receipts "
                        "WHERE json_valid(terminal_envelope) GROUP BY json_type(terminal_envelope,?)",
                        (field, field),
                    )}
                item["field_type_counts"] = profiles
                item["qualification"] = "wire_shape_only_no_authentication_or_admission_count"
            else:
                item["rows"] = connection.execute("SELECT COUNT(*) FROM birth_producer_receipts").fetchone()[0]
                item["state_counts"] = {value: connection.execute(
                    "SELECT COUNT(*) FROM birth_producer_receipts WHERE state=?", (value,),
                ).fetchone()[0] for value in ("available", "in_progress", "committed", "rejected")}
                candidates = connection.execute(
                    "SELECT COUNT(*),COUNT(DISTINCT issuer_id) FROM birth_producer_receipts "
                    "WHERE state='committed' AND CASE WHEN json_valid(terminal_envelope) THEN "
                    "json_extract(terminal_envelope,'$.report.outcome')='admitted' AND "
                    "json_extract(terminal_envelope,'$.report.revision_class') IN "
                    "('first_birth','code_revision','authority_revision','contract_revision') AND "
                    "json_extract(terminal_envelope,'$.publication.operation')='commit_birth_snapshot' AND "
                    "json_type(terminal_envelope,'$.admission_receipt')='text' "
                    "ELSE 0 END"
                ).fetchone()
                item.update(unauthenticated_candidate_rows=candidates[0],
                            unauthenticated_candidate_issuers=candidates[1],
                            qualification="metadata_screen_only_signatures_and_bindings_not_verified")
            connection.rollback()
        finally:
            connection.close()
        after = path.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RuntimeError("store_identity_changed")
        item["status"] = "observed"
    except FileNotFoundError:
        item["status"] = "missing_not_empty"
    except Exception as exc:
        item.update(status="unavailable", error_class=type(exc).__name__)
payload = json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
if len(payload) > 8192:
    raise RuntimeError("output_size_limit")
with os.fdopen(output, "wb") as stream:
    stream.write(payload)
PY

#!/usr/bin/env python3
"""Run the bilingual RM-0006 C3 matrix against isolated Metnos HTTP.

Each locale owns a distinct Metnos process and data tree.  Remote provider
boundaries are deterministic local fixtures; unavailable model/provider
conditions remain ordinary case failures and are never converted to skips.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socketserver
import ssl
import subprocess
import shutil
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from certification.coordinator import canonical_json, read_jsonl, run_batch
from certification.golden_matrix import CASE_PATH, FLOW_PATH
from certification.http_collector import (
    FIXTURE_ROOT,
    build_observation,
    load_turn_record,
    tree_digest,
)
from certification.run_synthetic import build_manifest
from driver.http_client import E2EClient
from driver.server import E2EServer


HERE = Path(__file__).resolve().parent
PROVIDER_MOCK = HERE / "fixtures" / "google_workspace_mock"
NON_DURABLE_FAMILIES = {
    "explain_tutor", "read_select", "compose_dataflow", "mutation_undo",
    "dialog_resume", "failure_partial",
}


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes()) if path.is_file() else _digest_bytes(b"")


def _prepare_fixture() -> str:
    if FIXTURE_ROOT.exists():
        shutil.rmtree(FIXTURE_ROOT)
    files = FIXTURE_ROOT / "files"
    entries = FIXTURE_ROOT / "entries"
    mutation = FIXTURE_ROOT / "mutation"
    undo = FIXTURE_ROOT / "undo"
    dialog = FIXTURE_ROOT / "dialog"
    partial = FIXTURE_ROOT / "partial"
    for directory in (files, entries, mutation, undo, dialog, partial):
        directory.mkdir(parents=True)
    (files / "note.txt").write_text(
        "METNOS-CERT-NOTE\nseconda riga di prova\n", encoding="utf-8",
    )
    (files / "report-alpha.txt").write_text("alpha report\n", encoding="utf-8")
    (files / "report-beta.txt").write_text("beta report\n", encoding="utf-8")
    (files / "metnos-cert-summary.txt").write_text(
        "METNOS-CERT-SUMMARY\n", encoding="utf-8")
    (files / "other.md").write_text("not selected\n", encoding="utf-8")
    (entries / "expenses.csv").write_text(
        "category,amount\nfood,10\ntravel,20\nfood,5\n", encoding="utf-8",
    )
    (mutation / "modifica.txt").write_text("METNOS-CERT-OLD\n", encoding="utf-8")
    (mutation / "unrelated.txt").write_text("METNOS-CERT-UNRELATED\n", encoding="utf-8")
    (undo / "annullabile.txt").write_text("METNOS-CERT-RESTORE\n", encoding="utf-8")
    (undo / "unrelated.txt").write_text("METNOS-CERT-UNDO-UNRELATED\n", encoding="utf-8")
    (partial / "report.txt").write_text("METNOS-CERT-PARTIAL-REPORT\n", encoding="utf-8")
    base = 1_700_000_000
    for offset, path in enumerate(sorted(FIXTURE_ROOT.rglob("*"))):
        if path.is_file():
            os.utime(path, (base + offset, base + offset))
    return tree_digest(FIXTURE_ROOT)


class _WebFixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path.startswith("/search?"):
            payload = {"results": [
                {"url": f"{base}/docs/a", "title": "METNOS-CERT-DOCS A", "content": "A"},
                {"url": f"{base}/docs/b", "title": "METNOS-CERT-DOCS B", "content": "B"},
                {"url": f"{base}/docs/c", "title": "METNOS-CERT-DOCS C", "content": "C"},
            ]}
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/robots.txt"):
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        else:
            name = self.path.rsplit("/", 1)[-1] or "index"
            body = (
                f"<html><head><title>METNOS-CERT-DOCS {name}</title></head>"
                f"<body>Documentation fixture {name}</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebFixture:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _WebFixtureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "WebFixture":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class _RevokedIMAPHandler(socketserver.StreamRequestHandler):
    """Minimal TLS IMAP endpoint that deterministically rejects LOGIN."""

    def handle(self) -> None:
        self.wfile.write(b"* OK [CAPABILITY IMAP4rev1] RM-0006 fixture ready\r\n")
        self.wfile.flush()
        while line := self.rfile.readline(8192):
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            tag = parts[0]
            command = parts[1].upper()
            if command == b"CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n" + tag + b" OK CAPABILITY\r\n")
            elif command == b"LOGIN":
                self.wfile.write(
                    tag + b" NO [AUTHENTICATIONFAILED] credentials revoked\r\n")
            elif command == b"LOGOUT":
                self.wfile.write(b"* BYE fixture closing\r\n" + tag + b" OK LOGOUT\r\n")
                self.wfile.flush()
                return
            else:
                self.wfile.write(tag + b" BAD unsupported fixture command\r\n")
            self.wfile.flush()


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RevokedIMAPFixture:
    def __init__(self) -> None:
        import tempfile
        self.tmp_root = Path(tempfile.mkdtemp(prefix="metnos-rm0006-imap-"))
        cert = self.tmp_root / "cert.pem"
        key = self.tmp_root / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=127.0.0.1",
        ], check=True, capture_output=True)
        self.server = _ThreadingTCPServer(
            ("127.0.0.1", 0), _RevokedIMAPHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        self.server.socket = context.wrap_socket(
            self.server.socket, server_side=True)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def __enter__(self) -> "RevokedIMAPFixture":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.tmp_root)


def _spawn_hook(locale: str, web_url: str, revoked_imap_port: int):
    def hook(env: dict[str, str], tmp_root: Path) -> None:
        env.update({
            "METNOS_LANG": locale,
            "METNOS_SEARXNG_URL": web_url,
            "METNOS_SEARXNG_TOP_N": "3",
            "METNOS_FINDURLS_RERANK_TIMEOUT_S": "0.1",
            "METNOS_SKILL_CODE_HOME": str(PROVIDER_MOCK),
            "METNOS_SKILL_HOME": str(tmp_root / "data" / "skills" / "google-workspace"),
            "METNOS_MAIL_READ_DEADLINE_S": "5",
        })
        skill_home = tmp_root / "data" / "skills" / "google-workspace"
        skill_home.mkdir(parents=True, exist_ok=True)
        (skill_home / "google_token.json").write_text(
            json.dumps({"refresh_token": "rm0006-fixture-token"}) + "\n",
            encoding="utf-8",
        )
        mail_dir = tmp_root / "config" / "mail"
        mail_dir.mkdir(parents=True, exist_ok=True)
        (mail_dir / "revoked_cert.env").write_text(
            f"HOST_IMAP=127.0.0.1\nPORT_IMAP={revoked_imap_port}\n"
            "USER=revoked@cert.invalid\n"
            "PASS=revoked-fixture-only\nVERIFY_TLS=false\n",
            encoding="utf-8",
        )
        # Do not inherit the live owner's Full autonomy grant in the realistic
        # users snapshot.  ``restricted`` is the persisted Supervised profile.
        users_path = tmp_root / "data" / "users.db"
        if users_path.is_file():
            with sqlite3.connect(str(users_path)) as conn:
                conn.execute(
                    "UPDATE users SET autonomy_level='restricted' "
                    "WHERE lower(name)='roberto'"
                )
    return hook


_TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
 owner_user_id TEXT NOT NULL, scheduler_name TEXT NOT NULL UNIQUE,
 schedule TEXT NOT NULL, query TEXT NOT NULL, actor TEXT NOT NULL,
 channel TEXT NOT NULL, chat_id TEXT, label TEXT,
 callback_key TEXT NOT NULL DEFAULT 'run_user_query', times INTEGER,
 fired_count INTEGER NOT NULL DEFAULT 0, grace_window_minutes INTEGER,
 mandates TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, UNIQUE(owner_user_id, name)
)
"""


def _seed_tasks(server: E2EServer, flow_id: str) -> str:
    path = server.user_state / "recurring_tasks.db"
    rows: list[tuple[str, str]] = []
    if flow_id == "read.tasks.open":
        rows = [
            ("cert-task-open-a", "METNOS-CERT-TASK-A"),
            ("cert-task-open-b", "METNOS-CERT-TASK-B"),
        ]
    elif flow_id == "dialog.approval-resume-once":
        rows = [("cert-task-delete", "METNOS-CERT-TASK-DELETE")]
    owner_user_id = "host"
    users_path = server.user_data / "users.db"
    if users_path.is_file():
        with sqlite3.connect(users_path) as users_conn:
            row = users_conn.execute(
                "SELECT id FROM users WHERE role='host' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row and row[0]:
                owner_user_id = str(row[0])
    with sqlite3.connect(path) as conn:
        conn.execute(_TASK_SCHEMA)
        conn.execute("DELETE FROM recurring_tasks")
        for index, (name, query) in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO recurring_tasks "
                "(name,owner_user_id,scheduler_name,schedule,query,actor,channel,"
                "label,created_at,enabled) VALUES (?,?,?,?,?,?,?,?,?,1)",
                (name, owner_user_id, f"user.{owner_user_id}.{name}", "daily@23:59", query,
                 "host", "http", name, f"2030-01-0{index}T00:00:00Z"),
            )
        conn.commit()
    return _task_digest(path)


def _task_rows(path: Path) -> list[tuple]:
    if not path.is_file():
        return []
    with sqlite3.connect(path) as conn:
        try:
            return conn.execute(
                "SELECT name,owner_user_id,schedule,query,enabled "
                "FROM recurring_tasks ORDER BY name"
            ).fetchall()
        except sqlite3.Error:
            return []


def _task_digest(path: Path) -> str:
    return _digest_bytes(canonical_json(_task_rows(path)).encode("utf-8"))


def _step_payload(records: list[dict]) -> str:
    return json.dumps(
        [step for record in records for step in record.get("steps", [])],
        ensure_ascii=False, sort_keys=True,
    )


def _chosen_tools(records: list[dict]) -> list[str]:
    return [
        str(step.get("chosen_tool"))
        for record in records for step in record.get("steps", [])
        if step.get("chosen_tool")
    ]


def _outcome(
    case: dict, records: list[dict], responses: list[dict], *,
    before_tree: str, after_tree: str, task_before: str, task_after: str,
    task_rows_after: list[tuple], calendar_after: dict,
) -> tuple[dict[str, tuple[bool, str]], list[str], list[str]]:
    flow = case["logical_flow_id"]
    payload = _step_payload(records)
    low_payload = payload.casefold()
    final = str(responses[-1].get("final_message") or "")
    low_final = final.casefold()
    tools = _chosen_tools(records)
    probes: dict[str, tuple[bool, str]] = {}
    effects: list[str] = ["no_action"]
    checks: set[str] = set()

    if flow == "read.tasks.open":
        found = all(name in payload for name in ("cert-task-open-a", "cert-task-open-b"))
        probes["open_task_set"] = (found, "both frozen open tasks observed" if found else "open task set differs")
        probes["task_digest_unchanged"] = (task_before == task_after, "task rows unchanged" if task_before == task_after else "task rows changed")
    elif flow == "read.urls.limit":
        found = all(f"/docs/{name}" in payload for name in "abc")
        probes["selected_url_set"] = (found, "three frozen documentation URLs observed" if found else "URL set differs")
        probes["fixture_digest_unchanged"] = (before_tree == after_tree, "fixture content digest unchanged" if before_tree == after_tree else "fixture changed")
        if all(f"/docs/{name}" in final for name in "abc"):
            checks.add("attribution_visible")
    elif flow == "compose.contacts-filter":
        forwarded = "ada-cert" in low_payload and "cora-cert" in low_payload
        filtered = all(token in low_final for token in ("ada", "cora")) and "bruno" not in low_final
        probes["contact_ids_forwarded"] = (forwarded, "contact ids reached the filter" if forwarded else "contact ids not forwarded")
        probes["filtered_set_matches"] = (filtered, "only contacts with email are visible" if filtered else "filtered contact set differs")
    elif flow == "mutation.file-create":
        target = FIXTURE_ROOT / "mutation" / "prova-creazione.txt"
        ok = target.is_file() and "METNOS-CERT-CREATED" in target.read_text(encoding="utf-8")
        unrelated = _digest_file(FIXTURE_ROOT / "mutation" / "unrelated.txt") == _digest_bytes(b"METNOS-CERT-UNRELATED\n")
        probes["target_content_digest"] = (ok, "created target contains frozen marker" if ok else "created target differs")
        probes["unrelated_digest"] = (unrelated, "unrelated file unchanged" if unrelated else "unrelated file changed")
        effects = ["target_created"] if ok else ["no_action"]
    elif flow == "mutation.file-update":
        target = FIXTURE_ROOT / "mutation" / "modifica.txt"
        ok = target.is_file() and "METNOS-CERT-UPDATED" in target.read_text(encoding="utf-8")
        probes["target_content_digest"] = (ok, "updated target contains frozen marker" if ok else "updated target differs")
        probes["single_target_exists"] = (target.is_file() and len(list(target.parent.glob("modifica*"))) == 1, "one updated target exists" if target.is_file() else "updated target absent")
        effects = ["target_updated"] if ok else ["no_action"]
    elif flow == "mutation.file-delete-undo":
        target = FIXTURE_ROOT / "undo" / "annullabile.txt"
        deleted = any(
            step.get("chosen_tool") == "delete_files"
            and any(item.get("removed") is True
                    for item in ((step.get("result") or {}).get("results") or [])
                    if isinstance(item, dict))
            for record in records for step in record.get("steps", [])
        )
        restored = target.is_file() and _digest_file(target) == _digest_bytes(b"METNOS-CERT-RESTORE\n")
        probes["delete_receipt"] = (deleted, "delete receipt observed" if deleted else "delete receipt absent")
        probes["undo_round_trip"] = ("undo_last_turn" in tools and restored, "delete followed by successful undo" if restored else "undo round trip incomplete")
        probes["restored_digest"] = (restored, "restored content matches" if restored else "restored content differs")
        effects = ["delete_observed", "target_restored"] if deleted and restored else ["no_action"]
        if any(token in low_final for token in ("annull", "undo", "riprist")):
            checks.add("undo_visible")
    elif flow == "mutation.event-create-delete":
        created = "create_events" in tools and "rm0006-event-001" in payload
        removed = "delete_events" in tools and not calendar_after
        probes["event_receipt"] = (created, "provider creation receipt observed" if created else "creation receipt absent")
        probes["calendar_round_trip"] = (created and removed, "calendar returned to empty state" if created and removed else "calendar round trip incomplete")
        effects = ["event_created", "event_removed"] if created and removed else ["no_action"]
        if "primary" in low_final or "metnos-cert-event" in low_final:
            checks.add("calendar_visible")
    elif flow == "dialog.missing-input-resume":
        targets = list((FIXTURE_ROOT / "dialog").glob("*"))
        target = FIXTURE_ROOT / "dialog" / "cert-dialog.txt"
        ok = target.is_file() and "METNOS-CERT-DIALOG" in target.read_text(encoding="utf-8")
        initial_wait = any(r.get("final_kind") in {"ask", "needs_inputs"} for r in responses[:-1])
        probes["dialog_bound_to_sender"] = (initial_wait, "pending input was bound to the HTTP sender" if initial_wait else "no pending input observed")
        # The HTTP response contains the proposing turn; the nested callback
        # is intentionally not spliced into that immutable turn record.  The
        # one create-only target with the frozen digest is the authoritative
        # exactly-once proof for the continuation.
        single = ok and len(targets) == 1
        probes["single_continuation"] = (single, "write resumed exactly once" if single else "write continuation count differs")
        probes["target_digest"] = (ok and len(targets) == 1, "single resumed target has frozen content" if ok else "resumed target differs")
        effects = ["single_target_created"] if ok and len(targets) == 1 else ["no_action"]
        if initial_wait:
            checks.add("input_request_visible")
        if ok and responses[-1].get("final_kind") == "answer":
            checks.add("completion_visible")
    elif flow == "dialog.approval-resume-once":
        pending = (
            "get_approval" in tools
            and any(r.get("pending_decision")
                    or r.get("final_kind") in {"ask", "needs_inputs"}
                    for r in responses[:-1])
        )
        absent = not any(row[0] == "cert-task-delete" for row in task_rows_after)
        probes["approval_bound_to_invocation"] = (pending, "approval proposal observed on the same invocation" if pending else "approval proposal absent")
        declared_deletes = sum(
            (step.get("resolved_args") or {}).get("on_approve", {}).get("tool")
            == "delete_tasks"
            for record in records for step in record.get("steps", [])
            if isinstance((step.get("resolved_args") or {}).get("on_approve"), dict)
        )
        continuation_count = tools.count("delete_tasks") + declared_deletes
        probes["single_continuation"] = (
            continuation_count == 1,
            "delete resumed exactly once" if continuation_count == 1
            else "delete continuation count differs",
        )
        probes["task_absent"] = (absent, "approved fixture task is absent" if absent else "fixture task still present")
        effects = ["single_task_deleted"] if absent and pending else ["no_action"]
        if pending:
            checks.add("approval_visible")
    elif flow == "failure.credential-revoked":
        failed = "read_messages" in tools and any(
            step.get("chosen_tool") == "read_messages" and not (step.get("result") or {}).get("ok", True)
            for record in records for step in record.get("steps", [])
        )
        probes["provider_call_failed"] = (failed, "revoked mailbox provider call failed" if failed else "provider failure absent")
        probes["mailbox_digest_unchanged"] = (before_tree == after_tree, "mail fixture unchanged" if before_tree == after_tree else "mail fixture changed")
        if failed or any(token in low_final for token in ("errore", "error", "fall", "imposs")):
            checks.add("failure_visible")
        if any(token in low_final for token in ("credenzial", "credential", "account", "ricolleg", "reconnect")):
            checks.add("credential_action_visible")
        if not any(token in low_final for token in ("completat", "success", "riuscit")):
            checks.add("no_false_success")
    elif flow == "failure.partial-compose":
        read_ok = "read_files" in tools and "METNOS-CERT-PARTIAL-REPORT" in payload
        send_failed = "send_messages" in tools and any(
            step.get("chosen_tool") == "send_messages" and not (step.get("result") or {}).get("ok", True)
            for record in records for step in record.get("steps", [])
        )
        probes["read_receipt"] = (read_ok, "report read receipt observed" if read_ok else "report read receipt absent")
        probes["outbox_empty"] = (send_failed, "unavailable channel produced no send receipt" if send_failed else "outbox failure not proven")
        probes["failure_localized_to_send"] = (read_ok and send_failed, "failure localized after successful read" if read_ok and send_failed else "partial boundary differs")
        effects = ["file_read", "message_not_sent"] if read_ok and send_failed else ["no_action"]
        if read_ok and send_failed:
            checks.update({"partial_visible", "no_false_success"})

    if case["required_approval"] and (
            any(r.get("pending_decision") for r in responses[:-1])
            or ("get_approval" in tools and any(
                r.get("final_kind") in {"ask", "needs_inputs"}
                for r in responses[:-1]))):
        checks.add("approval_visible")
    if any(path in final for path in (
        "/tmp/metnos-certification-fixture", "prova-creazione.txt",
        "modifica.txt", "annullabile.txt",
    )):
        checks.add("path_visible")
    return probes, effects, sorted(checks)


async def _run_case(client: E2EClient, case: dict) -> list[dict]:
    responses: list[dict] = []
    first = await client.chat(case["request"], lang=case["locale"], timeout_s=300)
    responses.append(first.raw or {
        "final_kind": "error", "final_message": first.error or "",
        "steps_summary": [], "transport_error": first.error or "empty response",
    })
    flow = case["logical_flow_id"]
    if flow == "dialog.missing-input-resume" and responses[-1].get("final_kind") in {"ask", "needs_inputs"}:
        resumed = await client.chat("cert-dialog.txt", lang=case["locale"], timeout_s=300)
        responses.append(resumed.raw or {"final_kind": "error", "final_message": resumed.error or "", "steps_summary": []})
    if (flow == "dialog.approval-resume-once"
            and responses[-1].get("final_kind") in {"ask", "needs_inputs"}):
        answer = "approva" if case["locale"] == "it" else "approve"
        resumed = await client.chat(answer, lang=case["locale"], timeout_s=300)
        responses.append(resumed.raw or {"final_kind": "error", "final_message": resumed.error or "", "steps_summary": []})
    elif responses[-1].get("pending_decision"):
        answer = "sì" if case["locale"] == "it" else "yes"
        resumed = await client.chat(answer, lang=case["locale"], timeout_s=300)
        responses.append(resumed.raw or {"final_kind": "error", "final_message": resumed.error or "", "steps_summary": []})
    return responses


def _merge_raw(responses: list[dict]) -> dict:
    last = dict(responses[-1])
    last["steps_summary"] = [
        step for response in responses for step in response.get("steps_summary", [])
    ]
    last["total_ms"] = sum(int(response.get("total_ms") or 0) for response in responses)
    return last


def _read_calendar(server: E2EServer) -> dict:
    path = server.user_data / "skills" / "google-workspace" / "rm0006-calendar.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


async def _collect_locale(
    server: E2EServer, cases: list[dict], observations_path: Path,
    completed: set[tuple[str, int]], cycle: int,
) -> None:
    async with E2EClient(server.url, server.admin_key, timeout_s=300) as client:
        catalog = await client.admin_get("/admin/executors")
        rows = catalog.get("rows") or catalog.get("executors") or []
        relevant = [
            row for row in rows if row.get("name") in {
                "find_contacts", "filter_entries", "find_urls", "read_messages",
                "create_events", "delete_events", "send_messages",
            }
        ]
        observations_path.parent.mkdir(parents=True, exist_ok=True)
        (observations_path.parent / f"catalog-{cases[0]['locale']}.json").write_text(
            json.dumps(relevant, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        for case in cases:
            key = (case["case_id"], cycle)
            if key in completed:
                continue
            before_tree = _prepare_fixture()
            task_before = _seed_tasks(server, case["logical_flow_id"])
            calendar_path = server.user_data / "skills" / "google-workspace" / "rm0006-calendar.json"
            calendar_path.unlink(missing_ok=True)
            responses = await _run_case(client, case)
            records = [
                record for response in responses
                if (record := load_turn_record(server.user_data, str(response.get("turn_id") or "")))
            ]
            after_tree = tree_digest(FIXTURE_ROOT)
            tasks_path = server.user_state / "recurring_tasks.db"
            task_after = _task_digest(tasks_path)
            probes, effects, checks = _outcome(
                case, records, responses, before_tree=before_tree,
                after_tree=after_tree, task_before=task_before,
                task_after=task_after, task_rows_after=_task_rows(tasks_path),
                calendar_after=_read_calendar(server),
            )
            confirmations = sum(bool(
                response.get("pending_decision")
                or ("get_approval" in _chosen_tools(records)
                    and response.get("final_kind") in {"ask", "needs_inputs"})
            ) for response in responses[:-1])
            approval_count = max(confirmations, int(case["required_approval"] and bool(effects != ["no_action"])))
            observation = build_observation(
                case, cycle, _merge_raw(responses), user_data=server.user_data,
                before_digest=before_tree, after_digest=after_tree,
                turn_records=records, probe_outcomes=probes,
                response_checks=checks, approval_count=approval_count,
                effects=effects,
            )
            trace_path = (
                observations_path.parent / "traces" / case["case_id"]
                / f"cycle-{cycle}.json"
            )
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(json.dumps({
                "case_id": case["case_id"],
                "responses": responses,
                "turn_records": records,
            }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            observations_path.parent.mkdir(parents=True, exist_ok=True)
            with observations_path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(observation) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.add(key)
            print(
                f"locale={case['locale']} case={case['case_id']} "
                f"terminal={observation['terminal']} plan={observation['plan']} "
                f"probes={sum(p['passed'] for p in observation['probes'])}/{len(observation['probes'])}",
                flush=True,
            )


def _cases(selected: list[str]) -> list[dict]:
    document = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
    allowed = {
        flow["logical_flow_id"] for flow in document["flows"]
        if flow["family"] in NON_DURABLE_FAMILIES
    }
    if selected:
        unknown = set(selected) - allowed
        if unknown:
            raise SystemExit(f"unknown/non-C3 flows: {sorted(unknown)}")
        allowed &= set(selected)
    return [case for case in read_jsonl(CASE_PATH) if case["logical_flow_id"] in allowed]


def _manifest(cases: list[dict]) -> dict:
    manifest = build_manifest(cases, cycles=[1])
    manifest.update({
        "certification_id": "rm0006-c3-nondurable-v1",
        "oracle_version": "rm0006-golden-oracle/4",
        "platform": "isolated-metnos-http-bilingual",
        "fixture": "rm0006-nondurable-v1",
        "locales": sorted({case["locale"] for case in cases}),
    })
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flow", action="append", default=[])
    args = parser.parse_args()
    cases = _cases(args.flow)
    observations_path = args.output / "observations.redacted.jsonl"
    existing = read_jsonl(observations_path)
    completed = {(item["case_id"], item["cycle"]) for item in existing}
    try:
        with WebFixture() as web, RevokedIMAPFixture() as revoked_imap:
            for locale in ("it", "en"):
                locale_cases = [case for case in cases if case["locale"] == locale]
                if not any((case["case_id"], 1) not in completed for case in locale_cases):
                    continue
                server = E2EServer.spawn(
                    seed_realistic=True, ready_timeout_s=60,
                    pre_spawn_hook=_spawn_hook(
                        locale, web.url, revoked_imap.port),
                )
                try:
                    asyncio.run(_collect_locale(
                        server, locale_cases, observations_path, completed, 1,
                    ))
                finally:
                    log_path = server.tmp_root / "server.log"
                    if log_path.is_file():
                        args.output.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(log_path, args.output / f"server-{locale}.log")
                    server.shutdown(cleanup=True)
        observations = read_jsonl(observations_path)
        summary = run_batch(
            output_dir=args.output, manifest=_manifest(cases), cases=cases,
            observations=observations,
        )
    finally:
        if FIXTURE_ROOT.exists():
            shutil.rmtree(FIXTURE_ROOT)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["objective_achieved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the complete bilingual RM-0006 matrix against isolated Metnos HTTP.

C4 uses one cycle; C6 uses two fresh cycles.  The ordinary 38 cases reuse the
C3 collector.  The ten remaining cases exercise a real Rust device client,
owner-scoped task storage and the production durable worker/storage boundary.
Only OCR/model provider output is deterministic in the durable fixture.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from certification.coordinator import canonical_json, read_jsonl, run_batch
from certification.golden_matrix import CASE_PATH
from certification.http_collector import build_observation, load_turn_record, tree_digest
from certification.run_nondurable_http import (
    RevokedIMAPFixture,
    WebFixture,
    _TASK_SCHEMA,
    _cases as _c3_cases,
    _collect_locale,
    _merge_raw,
    _run_case,
    _spawn_hook,
    _step_payload,
    _task_digest,
    _task_rows,
)
from certification.run_synthetic import build_manifest
from driver.http_client import E2EClient
from driver.server import E2EServer


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
DEVICE_CLIENT = (
    REPO_ROOT / "client-rs" / "target" / "x86_64-unknown-linux-musl"
    / "release" / "metnos-client"
)
DEVICE_ROOT = Path("/tmp/metnos-certification-device")
DURABLE_ROOT = Path("/tmp/metnos-certification-fixture/durable")
DURABLE_WORKER = Path(__file__).resolve().parent / "durable_worker_fixture.py"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TERMINAL_WORKLOAD_STATES = {
    "cancelled", "needs_attention", "failed", "completed_with_errors", "completed",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until(predicate: Callable[[], Any], timeout_s: float, what: str) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for {what}")


def _wait_http(url: str, process: subprocess.Popen, timeout_s: float = 30) -> None:
    def ready() -> bool:
        if process.poll() is not None:
            raise RuntimeError(f"process exited while starting {url}")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return response.status == 200
        except OSError:
            return False

    _wait_until(ready, timeout_s, url)


def _stop_process(process: subprocess.Popen | None, *, abrupt: bool = False) -> None:
    if process is None or process.poll() is not None:
        return
    if abrupt:
        process.kill()
    else:
        process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _full_spawn_hook(locale: str, web_url: str, imap_port: int):
    base = _spawn_hook(locale, web_url, imap_port)

    def hook(env: dict[str, str], tmp_root: Path) -> None:
        base(env, tmp_root)
        env.update({
            "METNOS_DURABLE_WORKLOADS_ENABLED": "1",
            "METNOS_LOADER_VERIFY": "1",
            "METNOS_DEVICES_DB": str(tmp_root / "state" / "devices.db"),
            "METNOS_AGENT_LOCKFILE": str(tmp_root / "state" / "agent-server.lock"),
            "PYTHONPATH": str(REPO_ROOT / "runtime"),
        })
        # The isolated remote protocol must sign pairing and invocation data
        # with the same author key that signed the checked-in executor bundles.
        # The private half exists only in this disposable tree and is removed
        # with it; it is never included in evidence or command output.
        source = Path.home() / ".config" / "metnos" / "keys" / "author_priv.bin"
        target = tmp_root / "config" / "keys" / "author_priv.bin"
        if not source.is_file():
            raise RuntimeError("author signing key unavailable for remote fixture")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o600)

    return hook


class RemoteDeviceFixture:
    """One paired real Rust client sharing only the isolated server databases."""

    def __init__(self, server: E2EServer) -> None:
        self.server = server
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.agent: subprocess.Popen | None = None
        self.client: subprocess.Popen | None = None
        self.device_id = ""
        self.device_name = "rm0006-device"
        self._files: list[Any] = []

    @property
    def database(self) -> Path:
        return Path(self.server.runtime_env["METNOS_DEVICES_DB"])

    def start(self) -> "RemoteDeviceFixture":
        if not DEVICE_CLIENT.is_file():
            raise RuntimeError(f"real Rust client unavailable: {DEVICE_CLIENT}")
        agent_log = (self.server.tmp_root / "agent-server.log").open("w")
        self._files.append(agent_log)
        self.agent = subprocess.Popen(
            [str(PYTHON), "-u", "-m", "runtime.agent_server",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=REPO_ROOT,
            env=self.server.runtime_env,
            stdout=agent_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _wait_http(f"{self.url}/agent/health", self.agent)

        client_env = dict(self.server.runtime_env)
        client_root = self.server.tmp_root / "device-client"
        client_env.update({
            "XDG_DATA_HOME": str(client_root / "data"),
            "XDG_CACHE_HOME": str(client_root / "cache"),
            "XDG_CONFIG_HOME": str(client_root / "config"),
        })
        for key in ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
            Path(client_env[key]).mkdir(parents=True, exist_ok=True)
        token = subprocess.run(
            [str(PYTHON), "runtime/devices.py", "generate-token", self.device_name],
            cwd=REPO_ROOT,
            env=self.server.runtime_env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            [str(DEVICE_CLIENT), "register", "--server", self.url, "--token", token],
            cwd=REPO_ROOT,
            env=client_env,
            check=True,
            capture_output=True,
            text=True,
        )
        state = json.loads(
            (Path(client_env["XDG_DATA_HOME"]) / "metnos" / "state.json")
            .read_text(encoding="utf-8")
        )
        self.device_id = str(state["device_id"])
        client_log = (self.server.tmp_root / "device-client.log").open("w")
        self._files.append(client_log)
        self.client = subprocess.Popen(
            [str(DEVICE_CLIENT), "run", "--server", self.url],
            cwd=REPO_ROOT,
            env=client_env,
            stdout=client_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def polled() -> bool:
            if self.client is not None and self.client.poll() is not None:
                raise RuntimeError("real Rust device client exited before polling")
            if not self.database.is_file():
                return False
            with sqlite3.connect(self.database) as connection:
                row = connection.execute(
                    "SELECT last_poll FROM devices WHERE id=? AND revoked_at IS NULL",
                    (self.device_id,),
                ).fetchone()
            return bool(row and row[0])

        _wait_until(polled, 30, "first signed device poll")
        return self

    def invocations(self, turn_ids: set[str], executor: str) -> list[dict[str, Any]]:
        if not self.database.is_file():
            return []
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT invocation_id,device_id,state,payload_json,result_json "
                "FROM invocations ORDER BY invocation_id"
            ).fetchall()
        selected = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("executor") != executor or payload.get("turn_id") not in turn_ids:
                continue
            result = json.loads(row["result_json"]) if row["result_json"] else None
            selected.append({**dict(row), "payload": payload, "result": result})
        return selected

    def stop(self) -> None:
        _stop_process(self.client)
        _stop_process(self.agent)
        self.client = None
        self.agent = None
        for handle in self._files:
            handle.close()
        self._files.clear()

    def __enter__(self) -> "RemoteDeviceFixture":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()


class DurableWorkerFixture:
    def __init__(self, server: E2EServer) -> None:
        self.server = server
        self.process: subprocess.Popen | None = None
        self.generation = 0
        self._files: list[Any] = []

    @property
    def database(self) -> Path:
        return self.server.user_state / "durable_workloads" / "state.sqlite3"

    @property
    def health(self) -> Path:
        return self.server.user_state / "durable_workloads" / "service_health.json"

    def start(self, *, model_delay_ms: int = 0, internal_delay_ms: int = 0) -> None:
        self.stop()
        self.generation += 1
        self.health.unlink(missing_ok=True)
        env = dict(self.server.runtime_env)
        env["METNOS_RM0006_MODEL_DELAY_MS"] = str(model_delay_ms)
        env["METNOS_RM0006_INTERNAL_DELAY_MS"] = str(internal_delay_ms)
        log_handle = (
            self.server.tmp_root / f"durable-worker-{self.generation}.log"
        ).open("w")
        self._files.append(log_handle)
        self.process = subprocess.Popen(
            [str(PYTHON), "-u", str(DURABLE_WORKER)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def ready() -> bool:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError("durable worker fixture exited during startup")
            try:
                value = json.loads(self.health.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            snapshot = value.get("snapshot") if isinstance(value, dict) else None
            return (
                isinstance(snapshot, dict)
                and snapshot.get("state") == "ready"
                and snapshot.get("worker_available") is True
            )

        _wait_until(ready, 30, "durable worker readiness")

    def stop(self, *, abrupt: bool = False) -> None:
        _stop_process(self.process, abrupt=abrupt)
        self.process = None

    def close(self) -> None:
        self.stop()
        for handle in self._files:
            handle.close()
        self._files.clear()


def _prepare_device_fixture() -> str:
    if DEVICE_ROOT.exists():
        shutil.rmtree(DEVICE_ROOT)
    (DEVICE_ROOT / "files").mkdir(parents=True)
    (DEVICE_ROOT / "output").mkdir(parents=True)
    (DEVICE_ROOT / "files" / "ricevuta-receipt-cert.txt").write_text(
        "METNOS-CERT-RECEIPT\n", encoding="utf-8",
    )
    (DEVICE_ROOT / "files" / "nota.txt").write_text(
        "METNOS-CERT-OTHER\n", encoding="utf-8",
    )
    return tree_digest(DEVICE_ROOT)


def _host_user_id(server: E2EServer) -> str:
    with sqlite3.connect(server.user_data / "users.db") as connection:
        row = connection.execute(
            "SELECT id FROM users WHERE role='host' ORDER BY created_at LIMIT 1"
        ).fetchone()
    if not row or not row[0]:
        raise RuntimeError("isolated host user unavailable")
    return str(row[0])


def _seed_owner_tasks(server: E2EServer) -> tuple[str, str]:
    path = server.user_state / "recurring_tasks.db"
    owner = _host_user_id(server)
    foreign = "usr_rm0006_foreign"
    with sqlite3.connect(path) as connection:
        connection.execute(_TASK_SCHEMA)
        connection.execute("DELETE FROM recurring_tasks")
        for index, (name, row_owner, marker) in enumerate((
            ("cert-owner-task", owner, "METNOS-CERT-OWNER"),
            ("cert-foreign-task", foreign, "METNOS-CERT-FOREIGN"),
        ), 1):
            connection.execute(
                "INSERT INTO recurring_tasks "
                "(name,owner_user_id,scheduler_name,schedule,query,actor,channel,"
                "label,created_at,enabled) VALUES (?,?,?,?,?,?,?,?,?,1)",
                (name, row_owner, f"user.{row_owner}.{name}", "daily@23:59", marker,
                 "host", "http", name, f"2030-02-0{index}T00:00:00Z"),
            )
        connection.commit()
    return _task_digest(path), _foreign_task_digest(path, foreign)


def _foreign_task_digest(path: Path, owner: str = "usr_rm0006_foreign") -> str:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name,owner_user_id,schedule,query,enabled FROM recurring_tasks "
            "WHERE owner_user_id=? ORDER BY name", (owner,),
        ).fetchall()
    return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()


def _append_observation(path: Path, observation: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(observation) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _records(server: E2EServer, responses: list[dict]) -> list[dict]:
    return [
        record for response in responses
        if (record := load_turn_record(
            server.user_data, str(response.get("turn_id") or ""),
        ))
    ]


def _trace(output: Path, case: dict, cycle: int, payload: dict[str, Any]) -> None:
    path = output / "traces" / case["case_id"] / f"cycle-{cycle}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


async def _collect_device_case(
    client: E2EClient,
    server: E2EServer,
    device: RemoteDeviceFixture,
    case: dict,
    cycle: int,
    output: Path,
) -> dict[str, Any]:
    before = _prepare_device_fixture()
    server_guard = server.tmp_root / "server-placement-guard.txt"
    server_guard.write_text("METNOS-CERT-SERVER-GUARD\n", encoding="utf-8")
    guard_before = hashlib.sha256(server_guard.read_bytes()).hexdigest()
    responses = await _run_case(client, case)
    records = _records(server, responses)
    executor = (
        "find_files" if case["logical_flow_id"] == "placement.device-read"
        else "write_files"
    )
    turn_ids = {str(response.get("turn_id") or "") for response in responses}
    invocations = device.invocations(turn_ids, executor)
    remote_ok = (
        len(invocations) == 1
        and invocations[0]["device_id"] == device.device_id
        and invocations[0]["state"] == "done"
        and bool((invocations[0].get("result") or {}).get("ok"))
    )
    final = "\n".join(str(response.get("final_message") or "") for response in responses)
    checks = {"device_visible"} if remote_ok else set()
    probes: dict[str, tuple[bool, str]] = {}
    effects = ["no_action"]
    if executor == "find_files":
        rendered = json.dumps(invocations, ensure_ascii=False, sort_keys=True)
        found = remote_ok and "ricevuta-receipt-cert.txt" in rendered
        probes = {
            "device_id_matches": (found, "signed result came from the paired device" if found else "paired-device result missing"),
            "device_digest_unchanged": (before == tree_digest(DEVICE_ROOT), "device fixture unchanged" if before == tree_digest(DEVICE_ROOT) else "device fixture changed"),
        }
        if found and "ricevuta-receipt-cert.txt" in final:
            checks.add("honest_count")
    else:
        target = DEVICE_ROOT / "output" / "dispositivo-prova.txt"
        created = (
            remote_ok and target.is_file()
            and target.read_text(encoding="utf-8") == "METNOS-CERT-DEVICE"
        )
        guard_after = hashlib.sha256(server_guard.read_bytes()).hexdigest()
        probes = {
            "device_target_digest": (created, "remote target has the frozen digest" if created else "remote target differs"),
            "server_digest_unchanged": (guard_before == guard_after, "server placement guard unchanged" if guard_before == guard_after else "server placement guard changed"),
        }
        effects = ["device_target_created"] if created else ["no_action"]
        if str(target) in final or created:
            checks.add("path_visible")

    merged = _merge_raw(responses)
    merged["steps_summary"] = [{"tool": executor, "ok": remote_ok}]
    merged["target_device"] = "owned_device" if remote_ok else "server"
    observation = build_observation(
        case, cycle, merged, user_data=server.user_data,
        before_digest=before, after_digest=tree_digest(DEVICE_ROOT),
        turn_records=records, probe_outcomes=probes,
        response_checks=sorted(checks),
        approval_count=int(case["required_approval"] and remote_ok),
        effects=effects,
    )
    _trace(output, case, cycle, {
        "case_id": case["case_id"], "responses": responses,
        "turn_records": records, "remote_invocations": invocations,
    })
    return observation


async def _collect_owner_case(
    client: E2EClient,
    server: E2EServer,
    case: dict,
    cycle: int,
    output: Path,
) -> dict[str, Any]:
    task_before, foreign_before = _seed_owner_tasks(server)
    responses = await _run_case(client, case)
    records = _records(server, responses)
    tasks_path = server.user_state / "recurring_tasks.db"
    payload = _step_payload(records)
    owner_only = "cert-owner-task" in payload and "cert-foreign-task" not in payload
    foreign_after = _foreign_task_digest(tasks_path)
    probes = {
        "owner_scope_matches": (owner_only, "only the caller's task was returned" if owner_only else "owner task projection differs"),
        "foreign_owner_digest_unchanged": (foreign_before == foreign_after, "foreign row unchanged" if foreign_before == foreign_after else "foreign row changed"),
    }
    merged = _merge_raw(responses)
    observation = build_observation(
        case, cycle, merged, user_data=server.user_data,
        before_digest=task_before, after_digest=_task_digest(tasks_path),
        turn_records=records, probe_outcomes=probes,
        response_checks=["honest_count"] if owner_only else [],
        effects=["no_action"],
    )
    _trace(output, case, cycle, {
        "case_id": case["case_id"], "responses": responses,
        "turn_records": records, "task_rows": _task_rows(tasks_path),
    })
    return observation


def _prepare_durable_fixture(flow_id: str) -> str:
    path = DURABLE_ROOT / ("complete" if flow_id.endswith("complete-artifact") else "restart")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / "question.png").write_bytes(PNG)
    return tree_digest(path)


def _workload_ids(database: Path) -> set[str]:
    if not database.is_file():
        return set()
    with sqlite3.connect(database) as connection:
        return {str(row[0]) for row in connection.execute("SELECT id FROM workloads")}


def _new_workload(database: Path, before: set[str]) -> str | None:
    created = _workload_ids(database) - before
    if len(created) == 1:
        return next(iter(created))
    return None


def _workload_state(database: Path, workload_id: str) -> str:
    if not database.is_file() or not workload_id:
        return ""
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT state FROM workloads WHERE id=?", (workload_id,),
        ).fetchone()
    return str(row[0]) if row else ""


def _effect_profile_running(database: Path, workload_id: str, effect_profile: str) -> bool:
    if not database.is_file() or not workload_id:
        return False
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT 1 FROM units u JOIN stages s "
            "ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id "
            "JOIN revisions r ON r.owner_user_id=u.owner_user_id AND r.id=u.revision_id "
            "WHERE r.workload_id=? AND u.state='running' AND s.effect_profile=? "
            "LIMIT 1", (workload_id, effect_profile),
        ).fetchone()
    return bool(row)


def _artifacts_match_store_contract(
    server: E2EServer,
    database: Path,
    artifacts: list[dict[str, Any]],
) -> bool:
    """Verify registered private artifacts through the production store API."""

    if len(artifacts) != 3 or len({row["logical_name"] for row in artifacts}) != 3:
        return False
    if not all(
        row["state"] in {"committed", "published"}
        and row["digest_verified"] == 1
        and row["schema_valid"] == 1
        and row["postconditions_valid"] == 1
        for row in artifacts
    ):
        return False

    runtime_path = str(REPO_ROOT / "runtime")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    from durable_workloads.artifacts import ArtifactRepository, ArtifactStore

    store = ArtifactStore(
        server.user_data / "durable_workloads",
        ArtifactRepository.open(database),
    )
    try:
        for row in artifacts:
            registered, stream = store.open_registered_download(
                str(row["owner_user_id"]), str(row["id"]),
            )
            with stream:
                payload = stream.read()
            observed = "sha256:" + hashlib.sha256(payload).hexdigest()
            if (
                registered.digest != row["digest"]
                or observed != row["digest"]
                or len(payload) != int(row["size_bytes"])
            ):
                return False
    finally:
        store.close()
    return True


def _durable_snapshot(database: Path, workload_id: str) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        workload = connection.execute(
            "SELECT * FROM workloads WHERE id=?", (workload_id,),
        ).fetchone()
        revision = connection.execute(
            "SELECT active_revision_id FROM workloads WHERE id=?", (workload_id,),
        ).fetchone()
        revision_id = str(revision[0]) if revision and revision[0] else ""
        units = [dict(row) for row in connection.execute(
            "SELECT u.id,u.state,u.attempt_count,u.active_attempt_id,u.fence,"
            "u.lease_worker_id,u.lease_expires_at,u.committed_result_id "
            "FROM units u WHERE u.revision_id=? ORDER BY u.created_at,u.id",
            (revision_id,),
        )]
        attempts = [dict(row) for row in connection.execute(
            "SELECT a.id,a.unit_id,a.number,a.fence,a.worker_id,a.state,a.ended_at "
            "FROM attempts a JOIN units u ON u.owner_user_id=a.owner_user_id "
            "AND u.id=a.unit_id WHERE u.revision_id=? ORDER BY a.unit_id,a.number",
            (revision_id,),
        )]
        results = [dict(row) for row in connection.execute(
            "SELECT id,unit_id,attempt_id,fence,digest FROM results "
            "WHERE revision_id=? ORDER BY unit_id", (revision_id,),
        )]
        artifacts = [dict(row) for row in connection.execute(
            "SELECT owner_user_id,id,logical_name,digest,size_bytes,state,blob_ref,digest_verified,"
            "schema_valid,postconditions_valid FROM artifacts "
            "WHERE workload_id=? ORDER BY logical_name", (workload_id,),
        )]
        publications = [dict(row) for row in connection.execute(
            "SELECT artifact_id,state,expected_digest,observed_digest "
            "FROM publications WHERE artifact_id IN "
            "(SELECT id FROM artifacts WHERE workload_id=?) ORDER BY artifact_id",
            (workload_id,),
        )]
        workload_count = connection.execute(
            "SELECT COUNT(*) FROM workloads WHERE id=?", (workload_id,),
        ).fetchone()[0]
    return {
        "workload": dict(workload) if workload else {},
        "workload_count": int(workload_count),
        "units": units,
        "attempts": attempts,
        "results": results,
        "artifacts": artifacts,
        "publications": publications,
    }


async def _collect_durable_case(
    client: E2EClient,
    server: E2EServer,
    worker: DurableWorkerFixture,
    case: dict,
    cycle: int,
    output: Path,
) -> dict[str, Any]:
    flow = case["logical_flow_id"]
    fixture_before = _prepare_durable_fixture(flow)
    restart = flow == "durable.stop-restart-resume"
    worker.start(internal_delay_ms=3000 if restart else 0)
    known = _workload_ids(worker.database)
    responses = await _run_case(client, case)
    if responses[-1].get("final_kind") in {"ask", "needs_inputs"}:
        answer = "approva" if case["locale"] == "it" else "approve"
        resumed = await client.chat(answer, lang=case["locale"], timeout_s=300)
        responses.append(resumed.raw or {
            "final_kind": "error",
            "final_message": resumed.error or "",
            "steps_summary": [],
        })
    records = _records(server, responses)
    workload_id = _wait_until(
        lambda: _new_workload(worker.database, known), 30, "one admitted workload",
    )
    restarted = False
    count_before_restart = 0
    if restart:
        _wait_until(
            lambda: _effect_profile_running(worker.database, workload_id, "idempotent"),
            120, "a running idempotent unit",
        )
        count_before_restart = len(_workload_ids(worker.database))
        worker.stop(abrupt=True)
        worker.start()
        restarted = True
    state = _wait_until(
        lambda: (
            value if (value := _workload_state(worker.database, workload_id))
            in TERMINAL_WORKLOAD_STATES else ""
        ),
        180, "durable workload completion",
    )
    snapshot = _durable_snapshot(worker.database, workload_id)
    units = snapshot["units"]
    attempts = snapshot["attempts"]
    results = snapshot["results"]
    artifacts = snapshot["artifacts"]
    all_committed = bool(units) and all(unit["state"] == "committed" for unit in units)
    one_result_per_unit = (
        all_committed and len(results) == len(units)
        and len({row["unit_id"] for row in results}) == len(units)
    )
    artifacts_ok = _artifacts_match_store_contract(
        server, worker.database, artifacts,
    )
    response_payload = json.dumps(responses, ensure_ascii=False, sort_keys=True)
    receipt_matches = workload_id in response_payload
    probes: dict[str, tuple[bool, str]]
    effects: list[str]
    checks = set()
    if receipt_matches:
        checks.add("receipt_visible")
    if artifacts_ok:
        checks.add("artifact_visible")
    if restart:
        fences_by_unit: dict[str, list[int]] = {}
        for attempt in attempts:
            fences_by_unit.setdefault(str(attempt["unit_id"]), []).append(int(attempt["fence"]))
        fence_ok = (
            restarted
            and any(max(values) >= 2 for values in fences_by_unit.values())
            and all(values == sorted(set(values)) for values in fences_by_unit.values())
        )
        no_orphans = (
            all(
                not unit["active_attempt_id"] and not unit["lease_worker_id"]
                and not unit["lease_expires_at"] for unit in units
            )
            and all(attempt["state"] not in {"leased", "running"} for attempt in attempts)
        )
        same_workload = (
            restarted and count_before_restart == len(_workload_ids(worker.database))
            and snapshot["workload_count"] == 1
        )
        probes = {
            "same_workload_after_restart": (same_workload, "restart continued the admitted workload" if same_workload else "workload identity changed"),
            "fence_monotonic": (fence_ok, "the interrupted unit resumed under a higher monotonic fence" if fence_ok else "retry fence not proven"),
            "single_committed_result": (one_result_per_unit, "one committed result exists per unit" if one_result_per_unit else "committed result cardinality differs"),
        }
        effects = ["workload_resumed", "single_result_committed"] if (
            state == "completed" and same_workload and fence_ok and one_result_per_unit and no_orphans
        ) else ["no_action"]
        if restarted and no_orphans:
            checks.add("resume_visible")
        pseudo_plan = ["durable_submit", "durable_restart", "durable_wait"]
    else:
        probes = {
            "receipt_matches_workload": (receipt_matches, "submission receipt names the committed workload" if receipt_matches else "submission receipt differs"),
            "artifact_digest": (artifacts_ok, "all required artifacts were verified and published" if artifacts_ok else "artifact verification differs"),
            "single_committed_result": (one_result_per_unit, "one committed result exists per unit" if one_result_per_unit else "committed result cardinality differs"),
        }
        effects = ["workload_committed", "artifact_committed"] if (
            state == "completed" and one_result_per_unit and artifacts_ok
        ) else ["no_action"]
        pseudo_plan = ["durable_submit", "durable_wait"]

    merged = _merge_raw(responses)
    merged["steps_summary"] = [{"tool": name, "ok": True} for name in pseudo_plan]
    merged["final_kind"] = "answer" if state == "completed" else "error"
    merged["target_device"] = "server"
    observation = build_observation(
        case, cycle, merged, user_data=server.user_data,
        before_digest=fixture_before,
        after_digest=tree_digest(
            DURABLE_ROOT / ("complete" if flow.endswith("complete-artifact") else "restart")
        ),
        turn_records=records, probe_outcomes=probes,
        response_checks=sorted(checks), approval_count=1,
        effects=effects, terminal="completed" if state == "completed" else "failed",
    )
    _trace(output, case, cycle, {
        "case_id": case["case_id"], "responses": responses,
        "turn_records": records, "workload_id": workload_id,
        "restarted": restarted, "snapshot": snapshot,
    })
    return observation


async def _collect_special_locale(
    server: E2EServer,
    cases: list[dict],
    cycle: int,
    output: Path,
    observations_path: Path,
    completed: set[tuple[str, int]],
) -> None:
    pending = [case for case in cases if (case["case_id"], cycle) not in completed]
    if not pending:
        return
    worker = DurableWorkerFixture(server)
    try:
        with RemoteDeviceFixture(server) as device:
            async with E2EClient(server.url, server.admin_key, timeout_s=300) as client:
                for case in pending:
                    flow = case["logical_flow_id"]
                    if flow.startswith("placement.device-"):
                        observation = await _collect_device_case(
                            client, server, device, case, cycle, output,
                        )
                    elif flow == "placement.owner-isolation":
                        observation = await _collect_owner_case(
                            client, server, case, cycle, output,
                        )
                    else:
                        observation = await _collect_durable_case(
                            client, server, worker, case, cycle, output,
                        )
                    _append_observation(observations_path, observation)
                    completed.add((case["case_id"], cycle))
                    print(
                        f"locale={case['locale']} case={case['case_id']} "
                        f"terminal={observation['terminal']} plan={observation['plan']} "
                        f"probes={sum(p['passed'] for p in observation['probes'])}/"
                        f"{len(observation['probes'])}",
                        flush=True,
                    )
    finally:
        worker.close()


def _manifest(cases: list[dict], cycles: list[int]) -> dict[str, Any]:
    manifest = build_manifest(cases, cycles=cycles)
    manifest.update({
        "certification_id": (
            "rm0006-c4-complete-v1" if cycles == [1]
            else "rm0006-c6-two-cycle-v1"
        ),
        "oracle_version": "rm0006-golden-oracle/4",
        "platform": "isolated-metnos-http-bilingual-real-device-durable",
        "fixture": "rm0006-complete-v1",
        "locales": ["en", "it"],
    })
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", default="1")
    parser.add_argument("--flow", action="append", default=[])
    args = parser.parse_args()
    cycles = sorted({int(value) for value in args.cycles.split(",") if value.strip()})
    if cycles not in ([1], [1, 2]):
        raise SystemExit("--cycles must be 1 or 1,2")
    cases = read_jsonl(CASE_PATH)
    if args.flow:
        available = {case["logical_flow_id"] for case in cases}
        unknown = set(args.flow) - available
        if unknown:
            raise SystemExit(f"unknown flows: {sorted(unknown)}")
        cases = [case for case in cases if case["logical_flow_id"] in set(args.flow)]
    all_ordinary = _c3_cases([])
    ordinary = [case for case in all_ordinary if case["case_id"] in {
        selected["case_id"] for selected in cases
    }]
    ordinary_ids = {case["case_id"] for case in ordinary}
    special = [case for case in cases if case["case_id"] not in ordinary_ids]
    observations_path = args.output / "observations.redacted.jsonl"
    completed = {
        (item["case_id"], item["cycle"]) for item in read_jsonl(observations_path)
    }
    try:
        with WebFixture() as web, RevokedIMAPFixture() as imap:
            for cycle in cycles:
                for locale in ("it", "en"):
                    locale_cases = [case for case in cases if case["locale"] == locale]
                    if not any((case["case_id"], cycle) not in completed for case in locale_cases):
                        continue
                    server = E2EServer.spawn(
                        seed_realistic=True,
                        ready_timeout_s=60,
                        pre_spawn_hook=_full_spawn_hook(locale, web.url, imap.port),
                    )
                    try:
                        locale_ordinary = [case for case in ordinary if case["locale"] == locale]
                        if any((case["case_id"], cycle) not in completed for case in locale_ordinary):
                            asyncio.run(_collect_locale(
                                server, locale_ordinary, observations_path, completed, cycle,
                            ))
                        asyncio.run(_collect_special_locale(
                            server,
                            [case for case in special if case["locale"] == locale],
                            cycle, args.output, observations_path, completed,
                        ))
                    finally:
                        for name in (
                            f"server-{locale}-cycle-{cycle}.log",
                            f"agent-server-{locale}-cycle-{cycle}.log",
                            f"device-client-{locale}-cycle-{cycle}.log",
                        ):
                            source_name = name.split(f"-{locale}-cycle-{cycle}")[0] + ".log"
                            source = server.tmp_root / source_name
                            if source.is_file():
                                args.output.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(source, args.output / name)
                        for source in server.tmp_root.glob("durable-worker-*.log"):
                            shutil.copy2(
                                source,
                                args.output / f"{source.stem}-{locale}-cycle-{cycle}.log",
                            )
                        server.shutdown(cleanup=True)
        observations = read_jsonl(observations_path)
        summary = run_batch(
            output_dir=args.output,
            manifest=_manifest(cases, cycles),
            cases=cases,
            observations=observations,
        )
    finally:
        if DEVICE_ROOT.exists():
            shutil.rmtree(DEVICE_ROOT)
        fixture_root = DURABLE_ROOT.parent
        if fixture_root.exists():
            shutil.rmtree(fixture_root)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["objective_achieved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

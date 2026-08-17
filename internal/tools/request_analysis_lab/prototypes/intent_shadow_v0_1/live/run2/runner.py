#!/usr/bin/env python3
"""Single-use RUN 2 runner; only ``--execute-once`` can touch the endpoint."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from .protocol import (
    AUTHORIZATION_FORMAT,
    AUTHORIZATION_PATH,
    CREATED_DATE,
    FREEZE_PATH,
    HERE,
    HTTP_LIMITS,
    MANIFEST_PATH,
    PROTOCOL_PATH,
    RUN_ARTIFACT_NAMES,
    RUN_ID,
    RUNNER_VERSION,
    SEALED_BATCH_FORMAT,
    ProtocolError,
    canonical_json_bytes,
    file_sha256,
    exact_json_equal,
    load_manifest,
    load_protocol,
    pretty_json_bytes,
    request_for,
    strict_json_file,
    strict_json_loads,
)


@dataclass(frozen=True, slots=True)
class RunPaths:
    consumption: Path
    journal: Path
    checkpoint: Path
    partial: Path
    sealed_batch: Path
    seal: Path
    evaluation: Path

    @classmethod
    def in_directory(cls, directory: Path) -> "RunPaths":
        return cls(
            directory / "consumption_run2.json",
            directory / "journal_run2.jsonl",
            directory / "checkpoint_run2.json",
            directory / "partial_batch_run2.json",
            directory / "sealed_batch_run2.json",
            directory / "seal_run2.json",
            directory / "evaluation_run2.json",
        )


DEFAULT_PATHS = RunPaths.in_directory(HERE)


@dataclass(frozen=True, slots=True)
class TransportResponse:
    accepted: bool
    http_status: int | None
    headers: tuple[tuple[str, str], ...]
    body: bytes
    elapsed_ms: int
    error_code: str | None


Transport = Callable[[dict[str, Any], int], TransportResponse]
_LIVE_EXECUTION_SEAL = object()
_FAKE_EXECUTION_SEAL = object()


def _atomic_write(path: Path, value: Any) -> None:
    data = pretty_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _exclusive_write(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, pretty_json_bytes(value))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_journal(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, canonical_json_bytes(value) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def existing_run_artifacts(paths: RunPaths = DEFAULT_PATHS) -> list[str]:
    return sorted(name for name in RUN_ARTIFACT_NAMES if (paths.sealed_batch.parent / name).exists())


def validate_authorization(path: Path = AUTHORIZATION_PATH) -> dict[str, Any]:
    value = strict_json_file(path)
    expected_keys = {
        "authorization_format", "run_id", "created_date", "protocol_file_sha256",
        "protocol_freeze_file_sha256", "protocol_payload_sha256",
        "manifest_file_sha256", "iteration_authorization", "independent_audit",
        "root_authorization", "single_use_nonce_sha256",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise RuntimeError("authorization closed schema mismatch")
    protocol = load_protocol()
    load_manifest()
    expected = {
        "authorization_format": AUTHORIZATION_FORMAT,
        "run_id": RUN_ID,
        "created_date": CREATED_DATE,
        "protocol_file_sha256": file_sha256(PROTOCOL_PATH),
        "protocol_freeze_file_sha256": file_sha256(FREEZE_PATH),
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "manifest_file_sha256": file_sha256(MANIFEST_PATH),
    }
    for key, item in expected.items():
        if type(value.get(key)) is not type(item) or value.get(key) != item:
            raise RuntimeError(f"authorization binding mismatch:{key}")
    if not exact_json_equal(value["iteration_authorization"], {
        "authorized_by": "Roberto",
        "authorization_scope": "automatic_iterative_cycle_after_single_universal_fix_and_independent_audit",
        "this_run": 2,
        "rigid_run_limit": None,
    }):
        raise RuntimeError("iteration authorization mismatch")
    audit = value["independent_audit"]
    if (
        type(audit) is not dict
        or set(audit) != {"reviewer", "report_path", "report_sha256", "pass"}
        or audit.get("reviewer") != "reviewer_b_independent"
        or audit.get("pass") is not True
        or type(audit.get("report_path")) is not str
        or type(audit.get("report_sha256")) is not str
    ):
        raise RuntimeError("independent audit mismatch")
    report_path = Path(audit["report_path"])
    if not report_path.is_absolute():
        report_path = HERE.parents[6] / report_path
    if not report_path.is_file() or file_sha256(report_path) != audit["report_sha256"]:
        raise RuntimeError("audit report drift")
    root = value["root_authorization"]
    if (
        type(root) is not dict
        or set(root) != {"authorized", "authorized_by", "authorization_id"}
        or root.get("authorized") is not True
        or root.get("authorized_by") != "root"
        or type(root.get("authorization_id")) is not str
        or not root["authorization_id"]
    ):
        raise RuntimeError("root final authorization missing")
    nonce = value["single_use_nonce_sha256"]
    if type(nonce) is not str or len(nonce) != 64 or any(char not in "0123456789abcdef" for char in nonce):
        raise RuntimeError("single-use nonce mismatch")
    return value


def urllib_transport(request_body: dict[str, Any], timeout_seconds: int) -> TransportResponse:
    endpoint = load_protocol()["backend"]["endpoint"]
    request = urllib.request.Request(
        endpoint,
        data=canonical_json_bytes(request_body),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(HTTP_LIMITS.max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            error = "HTTP_BODY_LIMIT" if len(body) > HTTP_LIMITS.max_bytes else None
            return TransportResponse(True, response.status, tuple(response.headers.items()), body, elapsed, error)
    except urllib.error.HTTPError as exc:
        body = exc.read(HTTP_LIMITS.max_bytes + 1)
        return TransportResponse(False, exc.code, tuple(exc.headers.items()), body, int((time.monotonic() - started) * 1000), "HTTP_REJECTED")
    except TimeoutError:
        return TransportResponse(False, None, (), b"", int((time.monotonic() - started) * 1000), "TRANSPORT_TIMEOUT")
    except Exception as exc:
        return TransportResponse(False, None, (), b"", int((time.monotonic() - started) * 1000), "TRANSPORT_" + type(exc).__name__.upper())


def _content_from_http(body: bytes) -> tuple[bytes | None, str | None]:
    try:
        wrapper = strict_json_loads(body, HTTP_LIMITS)
        if type(wrapper) is not dict:
            return None, "HTTP_RESPONSE_SCHEMA"
        choices = wrapper.get("choices")
        if type(choices) is not list or not choices or type(choices[0]) is not dict:
            return None, "HTTP_RESPONSE_SCHEMA"
        message = choices[0].get("message")
        if type(message) is not dict:
            return None, "HTTP_RESPONSE_SCHEMA"
        content = message.get("content")
        if type(content) is not str:
            return None, "HTTP_RESPONSE_SCHEMA"
        return content.encode("utf-8", errors="strict"), None
    except (ProtocolError, KeyError, IndexError, TypeError, UnicodeEncodeError) as exc:
        return None, "HTTP_RESPONSE_" + type(exc).__name__.upper()


def _extract(record: dict[str, Any], content: bytes) -> dict[str, Any]:
    if record["arm"] == "A":
        from .arm_a import extract_response
        return extract_response(content, query=record["query"], language=record["language"])
    from .arm_b import extract_response
    return extract_response(content)


def _execute_manifest(
    manifest: dict[str, Any], *, authorization_sha256: str,
    transport: Transport, paths: RunPaths = DEFAULT_PATHS, _seal: object,
) -> dict[str, Any]:
    if _seal is _LIVE_EXECUTION_SEAL:
        if transport is not urllib_transport:
            raise RuntimeError("live path requires concrete live transport")
    elif _seal is _FAKE_EXECUTION_SEAL:
        if transport is urllib_transport:
            raise RuntimeError("fake runner cannot receive network transport")
    else:
        raise RuntimeError("internal execution seal required")
    existing = existing_run_artifacts(paths)
    if existing:
        raise RuntimeError("single-use guard:" + ",".join(existing))
    marker = {
        "runner_version": RUNNER_VERSION, "run_id": RUN_ID,
        "state": "socket_attempt_committed_rerun_forbidden",
        "authorization_sha256": authorization_sha256,
        "protocol_freeze_sha256": file_sha256(FREEZE_PATH),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "accepted_http_posts": 0, "first_request_ordinal": 1,
    }
    _exclusive_write(paths.consumption, marker)
    records: list[dict[str, Any]] = []
    accepted_posts = 0
    stopped_reason: str | None = None
    for public in manifest["records"]:
        request = request_for(public["arm"], public["query"], public["language"])
        if sha256(canonical_json_bytes(request)).hexdigest() != public["request_sha256"]:
            raise RuntimeError("request drift after marker")
        response = transport(request, 120)
        saved: dict[str, Any] = {
            "request_ordinal": public["request_ordinal"],
            "sample_index": public["sample_index"], "panel": public["panel"],
            "panel_ordinal": public["panel_ordinal"], "opaque_case_id": public["opaque_case_id"],
            "query_sha256": public["query_sha256"], "arm": public["arm"],
            "request_sha256": public["request_sha256"], "http_accepted": response.accepted,
            "http_status": response.http_status, "elapsed_ms": response.elapsed_ms,
            "transport_error": response.error_code,
            "raw_http_response": {
                "body_b64": base64.b64encode(response.body).decode("ascii"),
                "body_sha256": sha256(response.body).hexdigest(),
                "headers": [[key, value] for key, value in response.headers],
            },
            "model_content_b64": None, "model_content_sha256": None,
            "extraction": None,
        }
        if not response.accepted:
            stopped_reason = response.error_code or "TRANSPORT_FAILURE"
        else:
            accepted_posts += 1
            marker["accepted_http_posts"] = accepted_posts
            if accepted_posts == 1:
                marker["state"] = "measurement_consumed_first_post_accepted"
            _atomic_write(paths.consumption, marker)
            if response.error_code is not None:
                stopped_reason = response.error_code
            else:
                content, wrapper_error = _content_from_http(response.body)
                if content is None:
                    saved["transport_error"] = wrapper_error
                    stopped_reason = wrapper_error or "HTTP_RESPONSE_ENVELOPE"
                else:
                    saved["model_content_b64"] = base64.b64encode(content).decode("ascii")
                    saved["model_content_sha256"] = sha256(content).hexdigest()
                    try:
                        saved["extraction"] = _extract(public, content)
                    except Exception as exc:
                        failure = "ADAPTER_" + type(exc).__name__.upper()
                        saved["extraction"] = {
                            "status": "technical_invalid", "semantic_document": None,
                            "technical_failure": failure,
                        }
                        saved["transport_error"] = failure
                        stopped_reason = failure
        records.append(saved)
        _append_journal(paths.journal, saved)
        _atomic_write(paths.checkpoint, {
            "runner_version": RUNNER_VERSION, "run_id": RUN_ID, "state": "in_progress",
            "authorization_sha256": authorization_sha256,
            "manifest_payload_sha256": manifest["manifest_payload_sha256"],
            "record_count": len(records), "accepted_http_posts": accepted_posts,
            "last_request_ordinal": saved["request_ordinal"],
            "journal_sha256": file_sha256(paths.journal),
        })
        if stopped_reason is not None:
            break
    complete = len(records) == 316 and accepted_posts == 316 and stopped_reason is None
    batch = {
        "batch_format": SEALED_BATCH_FORMAT if complete else "metnos.intent-live-partial-batch/0.3-run2",
        "runner_version": RUNNER_VERSION, "run_id": RUN_ID,
        "state": "complete" if complete else "partial_transport_stop", "gold_opened": False,
        "authorization_sha256": authorization_sha256,
        "protocol_file_sha256": file_sha256(PROTOCOL_PATH),
        "protocol_freeze_file_sha256": file_sha256(FREEZE_PATH),
        "manifest_file_sha256": file_sha256(MANIFEST_PATH),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "record_count": len(records), "accepted_http_posts": accepted_posts,
        "stopped_reason": stopped_reason, "records": records,
    }
    target = paths.sealed_batch if complete else paths.partial
    _atomic_write(target, batch)
    _atomic_write(paths.seal, {
        "freeze_format": "metnos.intent-live-batch-seal/0.3-run2",
        "algorithm": "sha256", "run_id": RUN_ID,
        "state": "complete" if complete else "partial",
        "batch_file": target.name, "batch_sha256": file_sha256(target),
        "journal_sha256": file_sha256(paths.journal),
        "consumption_marker_sha256": file_sha256(paths.consumption),
        "authorization_sha256": authorization_sha256,
        "record_count": len(records), "accepted_http_posts": accepted_posts,
        "stopped_reason": stopped_reason,
    })
    return batch


def preflight(
    *, authorization_state: str = "disarmed", authorization_path: Path = AUTHORIZATION_PATH,
    paths: RunPaths = DEFAULT_PATHS, check_runtime_environment: bool = True,
) -> dict[str, Any]:
    from .verify import verify_protocol
    report = verify_protocol(
        authorization_state=authorization_state,
        authorization_path=authorization_path,
        check_runtime_environment=check_runtime_environment,
    )
    if report["error_count"]:
        raise RuntimeError("protocol verification failed")
    existing = existing_run_artifacts(paths)
    if existing:
        raise RuntimeError("single-use guard active:" + ",".join(existing))
    authorization = validate_authorization(authorization_path) if authorization_state == "armed" else None
    return {
        "status": "armed_ready" if authorization is not None else "disarmed_ready_for_audit",
        "run_id": RUN_ID, "request_count": 316, "authorization_present": authorization is not None,
        "single_use_guard_clear": True, "network_touched": False, "gpu_touched": False,
    }


def run_authorized_once(authorization_path: Path) -> dict[str, Any]:
    preflight(authorization_state="armed", authorization_path=authorization_path)
    return _execute_manifest(
        load_manifest(), authorization_sha256=file_sha256(authorization_path),
        transport=urllib_transport, _seal=_LIVE_EXECUTION_SEAL,
    )


def execute_fake_full_run(
    manifest: dict[str, Any], *, transport: Transport, paths: RunPaths,
) -> dict[str, Any]:
    """Offline-only test entrypoint; rejects the concrete network transport."""
    return _execute_manifest(
        manifest, authorization_sha256="f" * 64, transport=transport,
        paths=paths, _seal=_FAKE_EXECUTION_SEAL,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--preflight-armed", action="store_true")
    modes.add_argument("--execute-once", action="store_true")
    parser.add_argument("--authorization", type=Path, default=AUTHORIZATION_PATH)
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(preflight(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.preflight_armed:
        print(json.dumps(preflight(authorization_state="armed", authorization_path=args.authorization), ensure_ascii=False, sort_keys=True))
        return 0
    batch = run_authorized_once(args.authorization)
    print(json.dumps({"state": batch["state"], "record_count": batch["record_count"]}, sort_keys=True))
    return 0 if batch["state"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

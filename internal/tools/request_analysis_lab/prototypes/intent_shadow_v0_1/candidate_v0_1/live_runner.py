#!/usr/bin/env python3
"""Single-use live runner; its default and preflight modes perform no I/O.

The only network-capable path is ``--execute-once`` with a separately created,
strictly bound authorization file.  A fail-closed O_EXCL marker is persisted
and fsynced before the first socket attempt.  There is no retry path.
"""
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

from intent_shadow_io import StrictJsonError, TechnicalLimits, canonical_json_bytes, file_sha256, strict_json_file, strict_json_loads
from live_protocol import (
    AUTHORIZATION_FORMAT,
    AUTHORIZATION_PATH,
    CREATED_DATE,
    HERE,
    LIVE_LIMITS,
    PROTOCOL_FREEZE_PATH,
    PROTOCOL_PATH,
    REQUEST_MANIFEST_PATH,
    SEALED_BATCH_FORMAT,
    canonical_write_bytes,
    load_protocol,
    load_request_manifest,
)


RUNNER_VERSION = "metnos.intent-shadow-live-runner/0.1"
HTTP_RESPONSE_LIMITS = TechnicalLimits(
    max_bytes=1024 * 1024,
    max_depth=64,
    max_nodes=20_000,
    max_string_chars=512 * 1024,
    max_integer_digits=64,
)


@dataclass(frozen=True, slots=True)
class RunPaths:
    consumption: Path
    journal: Path
    checkpoint: Path
    partial: Path
    sealed_batch: Path
    sealed_freeze: Path

    @classmethod
    def in_directory(cls, directory: Path) -> "RunPaths":
        return cls(
            directory / "live_run_consumption_v0_1.json",
            directory / "live_run_journal_v0_1.jsonl",
            directory / "live_run_checkpoint_v0_1.json",
            directory / "live_run_partial_v0_1.json",
            directory / "live_run_sealed_batch_v0_1.json",
            directory / "live_run_sealed_batch_v0_1.freeze.json",
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


def _atomic_write(path: Path, value: Any) -> None:
    data = canonical_write_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _exclusive_marker(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = canonical_write_bytes(value)
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _append_journal(path: Path, value: Any) -> None:
    data = canonical_json_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _existing_run_files(paths: RunPaths) -> list[str]:
    candidates = (
        paths.consumption, paths.journal, paths.checkpoint, paths.partial,
        paths.sealed_batch, paths.sealed_freeze,
    )
    existing = {path.name for path in candidates if path.exists()}
    directory = paths.sealed_batch.parent
    existing.update(
        path.name
        for path in directory.glob("live_run_*")
        if path.name not in {"live_runner.py", AUTHORIZATION_PATH.name}
    )
    evaluation_path = directory / "live_evaluation_v0_1.json"
    if evaluation_path.exists():
        existing.add(evaluation_path.name)
    return sorted(existing)


def validate_authorization(path: Path) -> dict[str, Any]:
    value = strict_json_file(path, limits=LIVE_LIMITS)
    expected_keys = {
        "authorization_format", "created_date", "protocol_file_sha256",
        "protocol_freeze_file_sha256", "protocol_payload_sha256",
        "manifest_file_sha256", "independent_audit", "root_authorization",
        "single_use_nonce_sha256",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise RuntimeError("authorization closed schema mismatch")
    protocol = load_protocol(PROTOCOL_PATH)
    load_request_manifest(REQUEST_MANIFEST_PATH)
    expected = {
        "authorization_format": AUTHORIZATION_FORMAT,
        "created_date": CREATED_DATE,
        "protocol_file_sha256": file_sha256(PROTOCOL_PATH),
        "protocol_freeze_file_sha256": file_sha256(PROTOCOL_FREEZE_PATH),
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "manifest_file_sha256": file_sha256(REQUEST_MANIFEST_PATH),
    }
    for key, item in expected.items():
        if type(value.get(key)) is not type(item) or value.get(key) != item:
            raise RuntimeError(f"authorization binding mismatch: {key}")
    audit = value["independent_audit"]
    if (
        type(audit) is not dict
        or set(audit) != {"reviewer", "report_path", "report_sha256", "pass"}
        or audit.get("reviewer") != "reviewer_b_independent"
        or type(audit.get("report_path")) is not str
        or type(audit.get("report_sha256")) is not str
        or audit.get("pass") is not True
    ):
        raise RuntimeError("authorization independent audit mismatch")
    report_path = Path(audit["report_path"])
    if not report_path.is_absolute():
        report_path = HERE.parents[5] / report_path
    if not report_path.is_file() or file_sha256(report_path) != audit["report_sha256"]:
        raise RuntimeError("authorization audit report drift")
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
    if type(nonce) is not str or len(nonce) != 64 or any(c not in "0123456789abcdef" for c in nonce):
        raise RuntimeError("authorization nonce mismatch")
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
            body = response.read(HTTP_RESPONSE_LIMITS.max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(body) > HTTP_RESPONSE_LIMITS.max_bytes:
                return TransportResponse(True, response.status, tuple(response.headers.items()), body, elapsed, "HTTP_BODY_LIMIT")
            return TransportResponse(True, response.status, tuple(response.headers.items()), body, elapsed, None)
    except urllib.error.HTTPError as exc:
        body = exc.read(HTTP_RESPONSE_LIMITS.max_bytes + 1)
        return TransportResponse(False, exc.code, tuple(exc.headers.items()), body, int((time.monotonic() - started) * 1000), "HTTP_REJECTED")
    except TimeoutError:
        return TransportResponse(False, None, (), b"", int((time.monotonic() - started) * 1000), "TRANSPORT_TIMEOUT")
    except Exception as exc:
        return TransportResponse(False, None, (), b"", int((time.monotonic() - started) * 1000), "TRANSPORT_" + type(exc).__name__.upper())


def _content_from_http(body: bytes) -> tuple[bytes | None, str | None]:
    try:
        wrapper = strict_json_loads(body, limits=HTTP_RESPONSE_LIMITS)
    except StrictJsonError as exc:
        return None, exc.code
    try:
        choices = wrapper["choices"]
        if type(choices) is not list or not choices or type(choices[0]) is not dict:
            return None, "HTTP_RESPONSE_CHOICES"
        message = choices[0]["message"]
        if type(message) is not dict or type(message.get("content")) is not str:
            return None, "HTTP_RESPONSE_CONTENT"
        return message["content"].encode("utf-8", errors="strict"), None
    except (KeyError, UnicodeEncodeError):
        return None, "HTTP_RESPONSE_SCHEMA"


def _extract(record: dict[str, Any], content: bytes) -> dict[str, Any]:
    if record["arm"] == "A":
        from live_arm_current import extract_response
        return extract_response(content, query=record["query"], language=record["language"])
    from live_arm_candidate import extract_response
    return extract_response(content)


def execute_manifest(
    manifest: dict[str, Any],
    *,
    authorization_sha256: str,
    transport: Transport,
    paths: RunPaths = DEFAULT_PATHS,
) -> dict[str, Any]:
    existing = _existing_run_files(paths)
    if existing:
        raise RuntimeError("single-run guard: existing artifacts=" + ",".join(sorted(existing)))
    marker = {
        "runner_version": RUNNER_VERSION,
        "state": "socket_attempt_committed_rerun_forbidden",
        "authorization_sha256": authorization_sha256,
        "protocol_freeze_sha256": file_sha256(PROTOCOL_FREEZE_PATH),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "accepted_http_posts": 0,
        "first_request_ordinal": 1,
    }
    _exclusive_marker(paths.consumption, marker)
    records: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    accepted_posts = 0
    for public_record in manifest["records"]:
        response = transport(public_record["request"], 120)
        raw_capture = {
            "body_b64": base64.b64encode(response.body).decode("ascii"),
            "body_sha256": sha256(response.body).hexdigest(),
            "headers": [[key, value] for key, value in response.headers],
        }
        saved: dict[str, Any] = {
            "request_ordinal": public_record["request_ordinal"],
            "sample_index": public_record["sample_index"],
            "panel": public_record["panel"],
            "panel_ordinal": public_record["panel_ordinal"],
            "opaque_case_id": public_record["opaque_case_id"],
            "query_sha256": public_record["query_sha256"],
            "arm": public_record["arm"],
            "request_sha256": public_record["request_sha256"],
            "http_accepted": response.accepted,
            "http_status": response.http_status,
            "elapsed_ms": response.elapsed_ms,
            "transport_error": response.error_code,
            "raw_http_response": raw_capture,
            "model_content_b64": None,
            "model_content_sha256": None,
            "extraction": None,
        }
        if not response.accepted:
            stopped_reason = response.error_code or "TRANSPORT_FAILURE"
            records.append(saved)
            _append_journal(paths.journal, saved)
            break
        accepted_posts += 1
        if accepted_posts == 1:
            marker["state"] = "measurement_consumed_first_post_accepted"
        marker["accepted_http_posts"] = accepted_posts
        _atomic_write(paths.consumption, marker)
        content, wrapper_error = _content_from_http(response.body)
        if content is not None:
            saved["model_content_b64"] = base64.b64encode(content).decode("ascii")
            saved["model_content_sha256"] = sha256(content).hexdigest()
            try:
                saved["extraction"] = _extract(public_record, content)
            except Exception as exc:
                saved["transport_error"] = "ADAPTER_" + type(exc).__name__.upper()
        else:
            saved["transport_error"] = wrapper_error
        records.append(saved)
        _append_journal(paths.journal, saved)
        checkpoint = {
            "runner_version": RUNNER_VERSION,
            "state": "in_progress",
            "authorization_sha256": authorization_sha256,
            "manifest_payload_sha256": manifest["manifest_payload_sha256"],
            "record_count": len(records),
            "accepted_http_posts": accepted_posts,
            "last_request_ordinal": saved["request_ordinal"],
            "journal_sha256": file_sha256(paths.journal),
        }
        _atomic_write(paths.checkpoint, checkpoint)
    complete = len(records) == 316 and stopped_reason is None
    batch = {
        "batch_format": SEALED_BATCH_FORMAT if complete else "metnos.intent-shadow-live-partial-batch/0.1",
        "runner_version": RUNNER_VERSION,
        "state": "complete" if complete else "partial_transport_stop",
        "gold_opened": False,
        "authorization_sha256": authorization_sha256,
        "protocol_file_sha256": file_sha256(PROTOCOL_PATH),
        "protocol_freeze_file_sha256": file_sha256(PROTOCOL_FREEZE_PATH),
        "manifest_file_sha256": file_sha256(REQUEST_MANIFEST_PATH),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "record_count": len(records),
        "accepted_http_posts": accepted_posts,
        "stopped_reason": stopped_reason,
        "records": records,
    }
    target = paths.sealed_batch if complete else paths.partial
    _atomic_write(target, batch)
    if complete:
        seal = {
            "freeze_format": "metnos.intent-shadow-live-sealed-batch-freeze/0.1",
            "algorithm": "sha256",
            "batch_sha256": file_sha256(target),
            "journal_sha256": file_sha256(paths.journal),
            "consumption_marker_sha256": file_sha256(paths.consumption),
            "authorization_sha256": authorization_sha256,
            "record_count": 316,
            "accepted_http_posts": 316,
        }
        _atomic_write(paths.sealed_freeze, seal)
    return batch


def preflight(
    *,
    authorization_state: str = "disarmed",
    authorization_path: Path = AUTHORIZATION_PATH,
    paths: RunPaths = DEFAULT_PATHS,
    check_runtime_environment: bool = True,
) -> dict[str, Any]:
    from verify_live_protocol import verify_live_protocol
    report = verify_live_protocol(
        check_runtime_environment=check_runtime_environment,
        authorization_state=authorization_state,
        authorization_path=authorization_path,
    )
    if report["error_count"]:
        raise RuntimeError("live protocol verification failed")
    existing = _existing_run_files(paths)
    if existing:
        raise RuntimeError("single-run guard already active: " + ",".join(sorted(existing)))
    authorization = None
    if authorization_state == "armed":
        authorization = validate_authorization(authorization_path)
    return {
        "status": "armed_ready" if authorization_state == "armed" else "prepared_not_authorized",
        "network_touched": False,
        "gpu_touched": False,
        "request_count": 316,
        "authorization_present": authorization is not None,
        "single_run_guard_clear": True,
    }


def run_authorized_once(
    authorization_path: Path,
    *,
    transport: Transport = urllib_transport,
    paths: RunPaths = DEFAULT_PATHS,
    check_runtime_environment: bool = True,
) -> dict[str, Any]:
    """Run only after the exact armed authorization and empty guard validate."""
    preflight(
        authorization_state="armed",
        authorization_path=authorization_path,
        paths=paths,
        check_runtime_environment=check_runtime_environment,
    )
    manifest = load_request_manifest()
    return execute_manifest(
        manifest,
        authorization_sha256=file_sha256(authorization_path),
        transport=transport,
        paths=paths,
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
        print(json.dumps(preflight(
            authorization_state="armed",
            authorization_path=args.authorization,
        ), ensure_ascii=False, sort_keys=True))
        return 0
    batch = run_authorized_once(
        args.authorization,
        transport=urllib_transport,
    )
    print(json.dumps({"state": batch["state"], "record_count": batch["record_count"]}, sort_keys=True))
    return 0 if batch["state"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

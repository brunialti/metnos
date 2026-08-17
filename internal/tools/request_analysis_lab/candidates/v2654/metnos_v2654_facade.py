#!/usr/bin/env python3
"""Narrow V26.5.4 facade for an isolated, one-shot semantic validator.

The sole supported API is :func:`evaluate`.  It snapshots exact JSON values,
verifies the fixed worker byte-for-byte, seals those bytes in memory, and runs
them under a fresh ``/usr/bin/python3 -I -B`` process.  No caller-controlled
path, source, import, entry, injection, or module crosses the process boundary.

Security boundary: this resists untrusted request/frame data and mutation of
the repository artifacts after their hashes were pinned.  It does not claim to
resist arbitrary code already executing in this trusted facade process, a
same-UID debugger, kernel compromise, replacement of the system interpreter,
or mutation of the explicitly external Python package environment.
"""
from __future__ import annotations

import fcntl as _fcntl
import hashlib as _hashlib
import json as _json
import math as _math
import os as _os
from pathlib import Path as _Path, PurePosixPath as _PurePosixPath
import stat as _stat
import subprocess as _subprocess


__all__ = ("evaluate",)


def evaluate(original_request, frame):
    """Evaluate one compact frame against one exact request snapshot.

    Semantic invalidity is returned as ``status=evaluated_invalid``.  A broken
    trust, process, or protocol boundary raises ``RuntimeError``.
    """
    worker_relative = (
        "internal/tools/request_analysis_lab/candidates/v2654/"
        "metnos_v2654_worker.py"
    )
    worker_sha256 = (
        "c57c8ececdfffdc2380c7e318e1c9fe8070d074fadf641e0c458367704c20e22"
    )
    worker_size = 19601
    request_version = "metnos.v26.5.4-worker-request/1.0"
    response_version = "metnos.v26.5.4-worker-response/1.0"

    if type(original_request) is not str or len(original_request) > 100_000:
        raise TypeError("original_request must be an exact bounded string")
    if type(frame) is not dict:
        raise TypeError("frame must be an exact JSON object")

    active_containers = set()
    node_count = 0

    def exact_json_snapshot(value, depth=0):
        nonlocal node_count
        node_count += 1
        if node_count > 100_000 or depth > 64:
            raise ValueError("frame exceeds exact JSON snapshot bounds")
        value_type = type(value)
        if value is None or value_type is bool:
            return value
        if value_type is int:
            if not -(2 ** 63) <= value <= 2 ** 63 - 1:
                raise ValueError("frame integer exceeds snapshot bounds")
            return value
        if value_type is float:
            if not _math.isfinite(value):
                raise ValueError("frame contains a non-finite number")
            return value
        if value_type is str:
            if len(value) > 200_000:
                raise ValueError("frame string exceeds snapshot bounds")
            return value
        if value_type not in (dict, list):
            raise TypeError("frame contains a non-JSON or subclassed value")
        identity = id(value)
        if identity in active_containers:
            raise ValueError("frame contains a container cycle")
        active_containers.add(identity)
        try:
            if value_type is list:
                return [
                    exact_json_snapshot(item, depth + 1)
                    for item in tuple(value)
                ]
            items = tuple(value.items())
            result = {}
            for key, item in items:
                if type(key) is not str:
                    raise TypeError("frame object key is not an exact string")
                if key in result:
                    raise ValueError("frame object contains a duplicate key")
                result[key] = exact_json_snapshot(item, depth + 1)
            return result
        finally:
            active_containers.remove(identity)

    snapshot = exact_json_snapshot(frame)
    envelope = {
        "version": request_version,
        "original_request": original_request,
        "frame": snapshot,
    }
    try:
        request_bytes = _json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("request is not canonical UTF-8 JSON") from error
    if len(request_bytes) > 1_500_000:
        raise ValueError("request exceeds worker protocol byte bound")

    facade_file = _Path(__file__).resolve(strict=True)
    try:
        repository = facade_file.parents[5]
    except IndexError as error:
        raise RuntimeError("facade is outside its fixed repository layout") from error
    expected_facade = (
        "internal/tools/request_analysis_lab/candidates/v2654/"
        "metnos_v2654_facade.py"
    )
    try:
        observed_facade = facade_file.relative_to(repository).as_posix()
    except ValueError as error:
        raise RuntimeError("facade repository layout is invalid") from error
    if observed_facade != expected_facade:
        raise RuntimeError("facade repository-relative identity changed")

    pure_worker = _PurePosixPath(worker_relative)
    if (
        pure_worker.is_absolute()
        or pure_worker.as_posix() != worker_relative
        or not pure_worker.parts
        or "." in pure_worker.parts
        or ".." in pure_worker.parts
    ):
        raise RuntimeError("fixed worker identity is not canonical")
    if not hasattr(_os, "O_NOFOLLOW") or not hasattr(_os, "O_DIRECTORY"):
        raise RuntimeError("secure no-follow file API is unavailable")

    directory_flags = _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW
    file_flags = _os.O_RDONLY | _os.O_NOFOLLOW
    if hasattr(_os, "O_CLOEXEC"):
        directory_flags |= _os.O_CLOEXEC
        file_flags |= _os.O_CLOEXEC
    directory_fds = []
    worker_fd = None
    try:
        directory_fds.append(_os.open(repository, directory_flags))
        for component in pure_worker.parts[:-1]:
            directory_fds.append(
                _os.open(component, directory_flags, dir_fd=directory_fds[-1])
            )
        worker_fd = _os.open(
            pure_worker.parts[-1], file_flags, dir_fd=directory_fds[-1]
        )
        before = _os.fstat(worker_fd)
        if not _stat.S_ISREG(before.st_mode) or before.st_size != worker_size:
            raise RuntimeError("worker is not the pinned regular file")
        chunks = []
        remaining = worker_size
        while remaining:
            chunk = _os.read(worker_fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        worker_bytes = b"".join(chunks)
        after = _os.fstat(worker_fd)
        before_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if (
            remaining
            or len(worker_bytes) != worker_size
            or before_identity != after_identity
            or _hashlib.sha256(worker_bytes).hexdigest() != worker_sha256
        ):
            raise RuntimeError("worker bytes failed fixed pin or TOCTOU check")
    finally:
        if worker_fd is not None:
            _os.close(worker_fd)
        for descriptor in reversed(directory_fds):
            _os.close(descriptor)

    if not hasattr(_os, "memfd_create") or not hasattr(_fcntl, "F_ADD_SEALS"):
        raise RuntimeError("sealed in-memory worker execution is unavailable")
    memfd = _os.memfd_create(
        "metnos-v2654-worker",
        _os.MFD_CLOEXEC | _os.MFD_ALLOW_SEALING,
    )
    try:
        view = memoryview(worker_bytes)
        written = 0
        while written < len(view):
            count = _os.write(memfd, view[written:])
            if count <= 0:
                raise RuntimeError("worker memfd write did not progress")
            written += count
        _os.lseek(memfd, 0, _os.SEEK_SET)
        seals = (
            _fcntl.F_SEAL_SEAL
            | _fcntl.F_SEAL_SHRINK
            | _fcntl.F_SEAL_GROW
            | _fcntl.F_SEAL_WRITE
        )
        _fcntl.fcntl(memfd, _fcntl.F_ADD_SEALS, seals)
        if _fcntl.fcntl(memfd, _fcntl.F_GET_SEALS) != seals:
            raise RuntimeError("worker memfd seal set is incomplete")
        try:
            completed = _subprocess.run(
                ["/usr/bin/python3", "-I", "-B", f"/proc/self/fd/{memfd}"],
                input=request_bytes,
                stdout=_subprocess.PIPE,
                stderr=_subprocess.PIPE,
                cwd=repository,
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin"},
                pass_fds=(memfd,),
                close_fds=True,
                check=False,
                timeout=20.0,
            )
        except _subprocess.TimeoutExpired as error:
            raise RuntimeError("one-shot worker exceeded its time bound") from error
    finally:
        _os.close(memfd)

    if completed.stderr:
        raise RuntimeError("one-shot worker wrote outside the closed protocol")
    if (
        not completed.stdout.endswith(b"\n")
        or completed.stdout.count(b"\n") != 1
        or len(completed.stdout) > 2_000_001
    ):
        raise RuntimeError("one-shot worker stdout is not one bounded JSON line")

    def reject_constant(token):
        raise RuntimeError(f"worker emitted non-finite JSON constant: {token}")

    def closed_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeError("worker emitted a duplicate JSON key")
            result[key] = value
        return result

    try:
        response = _json.loads(
            completed.stdout,
            object_pairs_hook=closed_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, _json.JSONDecodeError, RecursionError) as error:
        raise RuntimeError("one-shot worker response is not strict JSON") from error
    if type(response) is not dict or response.get("version") != response_version:
        raise RuntimeError("one-shot worker response version is invalid")
    if response.get("ok") is True:
        if set(response) != {"version", "ok", "result"} or completed.returncode != 0:
            raise RuntimeError("one-shot worker success envelope is inconsistent")
        result = response["result"]
        if type(result) is not dict or type(result.get("codes")) is not list:
            raise RuntimeError("one-shot worker result envelope is invalid")
        if any(type(code) is not str for code in result["codes"]):
            raise RuntimeError("one-shot worker result codes are invalid")
        status = result.get("status")
        if status == "evaluated_valid":
            if (
                set(result) != {
                    "status", "stage", "codes", "expanded_frame",
                }
                or result.get("stage") != "accepted"
                or result["codes"]
                or type(result["expanded_frame"]) is not dict
            ):
                raise RuntimeError("one-shot worker valid result is not closed")
        elif status == "evaluated_invalid":
            if (
                set(result) != {"status", "stage", "codes"}
                or result.get("stage") not in {"schema", "adapter", "validator"}
            ):
                raise RuntimeError("one-shot worker invalid result is not closed")
        else:
            raise RuntimeError("one-shot worker result status is invalid")
        return result
    if response.get("ok") is False:
        if set(response) != {"version", "ok", "error"} or completed.returncode == 0:
            raise RuntimeError("one-shot worker failure envelope is inconsistent")
        error = response["error"]
        if (
            type(error) is not dict
            or set(error) != {"code", "type"}
            or type(error["code"]) is not str
            or type(error["type"]) is not str
        ):
            raise RuntimeError("one-shot worker failure payload is invalid")
        raise RuntimeError(
            f"one-shot worker rejected request [{error['code']}/{error['type']}]"
        )
    raise RuntimeError("one-shot worker response discriminator is invalid")

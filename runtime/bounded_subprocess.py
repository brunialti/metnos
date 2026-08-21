# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded subprocess transport for durable executor invocations.

``subprocess.run(capture_output=True)`` retains all child output in memory.
That is acceptable for short interactive calls, but not for an unattended
runner: a faulty executor could grow the parent indefinitely before its JSON
result reached validation.  This module keeps the same one-shot contract while
bounding both captured streams and enforcing one wall-clock deadline.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence


_CHUNK_BYTES = 65_536
_TERMINATION_DRAIN_S = 1.0


class SubprocessOutputLimitExceeded(subprocess.SubprocessError):
    """A child wrote more bytes than its bounded transport permits."""

    def __init__(
        self,
        *,
        cmd: Sequence[str],
        stream: str,
        limit_bytes: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.cmd = tuple(cmd)
        self.stream = stream
        self.limit_bytes = limit_bytes
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"subprocess {stream} exceeded {limit_bytes} bytes")


class SubprocessTerminationError(subprocess.SubprocessError):
    """A killed child did not become waitable within the bounded drain time."""


def _positive_limit(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the process and descendants without ever targeting our group."""

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _close_registered(
    selector: selectors.BaseSelector,
    stream: object,
) -> None:
    try:
        selector.unregister(stream)
    except (KeyError, ValueError):
        pass
    try:
        stream.close()  # type: ignore[attr-defined]
    except OSError:
        pass


def run_bounded_subprocess(
    cmd: Sequence[str],
    *,
    input_text: str,
    timeout_s: float,
    env: Mapping[str, str] | None,
    stdout_limit_bytes: int,
    stderr_limit_bytes: int,
) -> subprocess.CompletedProcess[str]:
    """Run one process with constant-memory stdout/stderr capture.

    Pipes are non-blocking and multiplexed with ``selectors``: stdin can be
    consumed while both output streams are drained, so neither direction can
    deadlock.  POSIX children start in a private session; a timeout or output
    breach therefore stops descendants as well as the immediate process.
    """

    if not isinstance(input_text, str):
        raise TypeError("input_text must be a string")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be positive")
    stdout_limit = _positive_limit(
        stdout_limit_bytes, name="stdout_limit_bytes",
    )
    stderr_limit = _positive_limit(
        stderr_limit_bytes, name="stderr_limit_bytes",
    )
    command = tuple(str(part) for part in cmd)
    if not command or any(not part for part in command):
        raise ValueError("cmd must contain non-empty arguments")

    payload = input_text.encode("utf-8")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        bufsize=0,
        start_new_session=os.name == "posix",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    for pipe in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(pipe.fileno(), False)

    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    input_offset = 0
    if payload:
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    else:
        process.stdin.close()

    deadline = time.monotonic() + float(timeout_s)
    termination_deadline: float | None = None
    exceeded_stream: str | None = None
    timed_out = False
    try:
        while selector.get_map():
            now = time.monotonic()
            active_deadline = (
                termination_deadline
                if termination_deadline is not None
                else deadline
            )
            remaining = active_deadline - now
            if remaining <= 0:
                if termination_deadline is None:
                    timed_out = True
                    _kill_process_group(process)
                    termination_deadline = now + _TERMINATION_DRAIN_S
                    _close_registered(selector, process.stdin)
                    continue
                break

            for key, _mask in selector.select(min(remaining, 0.25)):
                stream = key.fileobj
                name = str(key.data)
                if name == "stdin":
                    try:
                        written = os.write(
                            process.stdin.fileno(),
                            payload[input_offset:input_offset + _CHUNK_BYTES],
                        )
                    except (BrokenPipeError, OSError):
                        _close_registered(selector, process.stdin)
                        continue
                    input_offset += written
                    if input_offset >= len(payload):
                        _close_registered(selector, process.stdin)
                    continue

                try:
                    chunk = os.read(stream.fileno(), _CHUNK_BYTES)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    _close_registered(selector, stream)
                    continue

                totals[name] += len(chunk)
                room = limits[name] - len(buffers[name])
                if room > 0:
                    buffers[name].extend(chunk[:room])
                if totals[name] > limits[name] and exceeded_stream is None:
                    exceeded_stream = name
                    _kill_process_group(process)
                    termination_deadline = time.monotonic() + _TERMINATION_DRAIN_S
                    _close_registered(selector, process.stdin)

            if (
                process.poll() is not None
                and not any(
                    key.data in {"stdout", "stderr"}
                    for key in selector.get_map().values()
                )
            ):
                break
    finally:
        for key in tuple(selector.get_map().values()):
            _close_registered(selector, key.fileobj)
        selector.close()
        if process.poll() is None:
            _kill_process_group(process)
        try:
            process.wait(timeout=_TERMINATION_DRAIN_S)
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(process)
            raise SubprocessTerminationError(
                "subprocess did not terminate after a forced group kill"
            ) from exc

    stdout = bytes(buffers["stdout"]).decode("utf-8", errors="replace")
    stderr = bytes(buffers["stderr"]).decode("utf-8", errors="replace")
    if timed_out:
        raise subprocess.TimeoutExpired(
            command,
            timeout_s,
            output=stdout,
            stderr=stderr,
        )
    if exceeded_stream is not None:
        raise SubprocessOutputLimitExceeded(
            cmd=command,
            stream=exceeded_stream,
            limit_bytes=limits[exceeded_stream],
            stdout=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(
        command,
        int(process.returncode or 0),
        stdout,
        stderr,
    )


__all__ = [
    "SubprocessOutputLimitExceeded",
    "SubprocessTerminationError",
    "run_bounded_subprocess",
]

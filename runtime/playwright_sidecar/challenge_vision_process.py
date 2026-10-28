"""Cancellable one-shot boundary around the existing central model provider.

The retained thread only runs bounded_subprocess. Its cooperative stop event
forces process-group kill and wait; cancellation never cancels that thread's
future. Incomplete drain is a blocked condition, never model abstention.
"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
from dataclasses import dataclass
import functools
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from bounded_subprocess import (SubprocessCancelled, SubprocessOutputLimitExceeded,
    SubprocessTerminationError, run_bounded_subprocess)
from llm_telemetry import TRANSPORT_USAGE_KEY, prepare_child_model_calls
from monitor_capture import MAX_TRANSPORT_JSON_BYTES, TRANSPORT_KEY, prepare_child
from .challenge_vision import (MAX_REQUEST_SECONDS, ModelLease, VisionUnavailable,
    _closed_plan, _language, _validate_request)

DRAIN_GRACE_SECONDS = 2.5
WIRE_SCHEMA = 'metnos.sites-vision/1'
_ERROR_CODES = frozenset({'deadline', 'crop_invalid', 'challenge_unsupported',
    'reservation_invalid', 'prompt_unavailable', 'unconfigured',
    'provider_unsupported', 'provider_error', 'worker_protocol', 'worker_error'})


@dataclass
class VisionTransportState:
    """Content-free local evidence; worker start is NOT a provider call count."""
    reservation_claimed: bool = False
    worker_started: bool = False
    drained: bool = False
    receipt_received: bool = False
    input_accounting: str = 'estimated'


class VisionDrainIncomplete(VisionUnavailable):
    def __init__(self, pending: tuple[asyncio.Task, ...] = ()):
        super().__init__('drain_incomplete')
        self.pending = pending


def _worker_command() -> list[str]:
    return [sys.executable, str(Path(__file__).with_name('challenge_vision_worker.py'))]


async def _retain_completion(future, state):
    # Retain the concurrent Future, whose running thread cannot be cancelled.
    # asyncio cancellation only wakes this join; it never discards the handle.
    loop, ready = asyncio.get_running_loop(), asyncio.Event()
    def completed(_):
        try:
            loop.call_soon_threadsafe(ready.set)
        except RuntimeError:
            pass  # A closed caller loop cannot turn incomplete drain into OK.
    future.add_done_callback(completed)
    while not future.done():
        try:
            await ready.wait()
        except asyncio.CancelledError:
            continue
    try:
        result = future.result()
    except FutureCancelledError:
        state.drained = True  # Executor confirmed cancellation before starting.
        raise SubprocessCancelled() from None
    except (SubprocessCancelled, SubprocessOutputLimitExceeded, subprocess.TimeoutExpired):
        state.drained = True  # These helper outcomes require successful wait.
        raise
    else:
        state.drained = True
        return result


async def _stop_and_drain(task, stop, state, future):
    stop.set()
    if task.cancelled():
        task = asyncio.create_task(_retain_completion(future, state))
    deadline = time.monotonic() + DRAIN_GRACE_SECONDS
    while not task.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VisionDrainIncomplete((task,))
        try:
            await asyncio.wait_for(asyncio.shield(task), remaining)
        except asyncio.CancelledError:
            continue
        except asyncio.TimeoutError:
            if not task.done():
                raise VisionDrainIncomplete((task,)) from None
        except Exception:
            break
    # A completed thread with an unconfirmed kernel wait is still blocked.
    try:
        task.result()
    except SubprocessTerminationError:
        raise VisionDrainIncomplete() from None
    except (SubprocessCancelled, SubprocessOutputLimitExceeded, subprocess.TimeoutExpired):
        pass
    except BaseException:
        if not state.drained:
            raise VisionDrainIncomplete() from None
    if not state.drained:
        raise VisionDrainIncomplete()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def _parse_receipt(raw):
    def invalid_number(_):
        raise ValueError('non-finite number')
    def finite_number(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite number')
        return number
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object,
                            parse_constant=invalid_number, parse_float=finite_number)
    except (ValueError, RecursionError):
        raise VisionUnavailable('worker_protocol') from None
    if (not isinstance(parsed, dict) or parsed.get('schema') != WIRE_SCHEMA
            or type(parsed.get('ok')) is not bool):
        raise VisionUnavailable('worker_protocol')
    required = {'schema', 'ok', 'plan' if parsed['ok'] else 'error'}
    if not required <= parsed.keys() or parsed.keys() - required - {TRANSPORT_USAGE_KEY, TRANSPORT_KEY}:
        raise VisionUnavailable('worker_protocol')
    if not parsed['ok'] and not isinstance(parsed['error'], str):
        raise VisionUnavailable('worker_protocol')
    return parsed


async def choose_widget_action_async(crop_png: bytes, provider: str, lease: ModelLease,
                                    *, lang: str | None = None,
                                    state: VisionTransportState | None = None) -> dict:
    """Use one already-reserved lease, without retries or browser effects.

    The aggregate 8192-token reservation remains an estimate, not an enforced
    input cap. The provider's configured output cap remains 128 tokens.
    """
    state = state if state is not None else VisionTransportState()
    _validate_request(crop_png, provider, lease)
    language = _language(lang)
    deadline = min(float(lease.deadline), time.monotonic() + MAX_REQUEST_SECONDS)
    command = _worker_command()
    payload = json.dumps({'schema': WIRE_SCHEMA, 'crop_png': base64.b64encode(crop_png).decode('ascii'),
        'provider': provider, 'deadline': deadline, 'max_tokens': lease.max_tokens, 'lang': language},
        separators=(',', ':'), allow_nan=False)
    try:
        lease.claim()
    except Exception:
        raise VisionUnavailable('reservation_invalid') from None
    state.reservation_claimed = True
    if time.monotonic() >= deadline:
        raise VisionUnavailable('deadline')
    env = dict(os.environ)
    child_calls = prepare_child_model_calls(env)
    child_monitor = prepare_child(env)
    receipt = None
    stop = threading.Event()
    task = None
    future = None
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sites-vision')
    try:
        future = executor.submit(functools.partial(
            run_bounded_subprocess, command, input_text=payload, env=env,
            timeout_s=MAX_REQUEST_SECONDS, deadline_at=deadline, stop_event=stop,
            on_spawn=lambda: setattr(state, 'worker_started', True),
            stdout_limit_bytes=MAX_TRANSPORT_JSON_BYTES + 65536, stderr_limit_bytes=8192))
        task = asyncio.create_task(_retain_completion(future, state))
        try:
            result = await asyncio.wait_for(asyncio.shield(task), max(0, deadline - time.monotonic()))
        except asyncio.CancelledError:
            await _stop_and_drain(task, stop, state, future)
            raise
        except asyncio.TimeoutError:
            await _stop_and_drain(task, stop, state, future)
            raise VisionUnavailable('deadline') from None
        except (subprocess.TimeoutExpired, SubprocessCancelled):
            raise VisionUnavailable('deadline') from None
        except SubprocessOutputLimitExceeded:
            raise VisionUnavailable('output_limit') from None
        except SubprocessTerminationError:
            raise VisionDrainIncomplete() from None
        except Exception:
            # An unexpected helper failure cannot certify process termination.
            if not state.drained:
                raise VisionDrainIncomplete() from None
            raise VisionUnavailable('worker_error') from None
        if result.returncode:
            raise VisionUnavailable('worker_error')
        parsed = _parse_receipt(result.stdout)
        receipt = parsed
        state.receipt_received = True
        if time.monotonic() >= deadline:
            raise VisionUnavailable('deadline')
        if not parsed['ok']:
            code = parsed.get('error')
            raise VisionUnavailable(code if code in _ERROR_CODES else 'worker_error')
        return _closed_plan(json.dumps(parsed.get('plan'), allow_nan=False))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        # A receipt may arrive while cancellation/deadline drains the worker.
        # Count its confirmed calls once, but never use the late action plan.
        if receipt is None and state.drained and future is not None and future.done():
            try:
                completed = future.result()
                if completed.returncode == 0:
                    receipt = _parse_receipt(completed.stdout)
                    state.receipt_received = True
            except (Exception, FutureCancelledError):
                pass  # Missing/invalid receipt remains UNKNOWN below.
        # No speculative mark_call_started here: the child central provider
        # counts actual attempts. Missing/killed receipts become UNKNOWN.
        if child_calls is not None:
            child_calls.finish(receipt, retain=False)
        if child_monitor is not None:
            child_monitor.finish(receipt)

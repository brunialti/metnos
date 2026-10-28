"""One reserved visual decision through the central Frontier provider.

The supervisor supplies ONLY a verified widget crop and owns all effects,
session/frame fences, re-observation and success evidence. Use the separate
process adapter for cancellation and drain; this synchronous core is not a
safe asynchronous race participant on its own.
"""
from __future__ import annotations

import json
import math
import struct
import time
from typing import Protocol

import prompt_loader
from llm_content import ImageInput
from llm_router import LLMRouter, TierConfigError
from llm_workloads import tier_for

VISION_TOKEN_RESERVATION = 8192  # Aggregate estimate, not verified provider tokenization.
MAX_OUTPUT_TOKENS = 128
MAX_REQUEST_SECONDS = 20.0
MAX_CROP_BYTES = 512 * 1024
_PROVIDERS = frozenset({'recaptcha', 'hcaptcha', 'turnstile', 'generic'})

PROMPT_ROLE = 'sites_challenge_vision'


class ModelLease(Protocol):
    deadline: float
    max_tokens: int

    def claim(self) -> None: ...


class VisionUnavailable(RuntimeError):
    """A closed technical reason; never carries model or page content."""


def _remaining(lease: ModelLease) -> float:
    deadline = getattr(lease, 'deadline', None)
    now = time.monotonic()
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or deadline <= now):
        raise VisionUnavailable('deadline')
    return min(MAX_REQUEST_SECONDS, deadline - now)


def _crop_ok(crop: bytes) -> bool:
    # Pixels cannot prove provenance: a current widget fence is the caller's
    # obligation. Bound bytes and dimensions before any provider selection.
    if (type(crop) is not bytes or not 33 <= len(crop) <= MAX_CROP_BYTES
            or crop[:8] != b'\x89PNG\r\n\x1a\n' or crop[8:16] != b'\0\0\0\rIHDR'):
        return False
    width, height = struct.unpack('!II', crop[16:24])
    return 0 < width <= 1024 and 0 < height <= 1024


def _closed_plan(text) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result

    abstain = {'action': 'abstain'}
    if type(text) is not str or len(text) > 2048:
        return abstain
    try:
        plan = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        return abstain
    if not isinstance(plan, dict):
        return abstain
    if plan == abstain:
        return abstain
    if set(plan) != {'action', 'x', 'y'} or plan['action'] != 'click':
        return abstain
    if any(type(plan[key]) not in (int, float) or not 0 <= plan[key] <= 1
           for key in ('x', 'y')):
        return abstain
    return {'action': 'click', 'x': float(plan['x']), 'y': float(plan['y'])}


def _validate_request(crop_png: bytes, provider: str, lease: ModelLease) -> None:
    if not _crop_ok(crop_png):
        raise VisionUnavailable('crop_invalid')
    if type(provider) is not str or provider not in _PROVIDERS:
        raise VisionUnavailable('challenge_unsupported')
    tokens = getattr(lease, 'max_tokens', None)
    if type(tokens) is not int or tokens < VISION_TOKEN_RESERVATION or not callable(getattr(lease, 'claim', None)):
        raise VisionUnavailable('reservation_invalid')
    _remaining(lease)


def _language(lang: str | None) -> str:
    if lang is None:
        from i18n import current_lang
        lang = current_lang()
    if not isinstance(lang, str) or not lang or len(lang) > 16:
        raise VisionUnavailable('prompt_unavailable')
    return lang


def choose_widget_action(crop_png: bytes, provider: str, lease: ModelLease, *, lang: str | None = None) -> dict:
    """Consume a pre-reserved lease once; never retry, act, or assert success."""
    _validate_request(crop_png, provider, lease)
    try:
        system, identity = prompt_loader.get_with_identity(PROMPT_ROLE, _language(lang))
        if identity.role != PROMPT_ROLE or not system:
            raise ValueError('prompt identity')
    except Exception:
        raise VisionUnavailable('prompt_unavailable') from None
    try:
        model = LLMRouter().provider(tier_for('sites.challenge_vision'))
    except TierConfigError:
        raise VisionUnavailable('unconfigured') from None
    if getattr(model, 'name', None) not in {'openai', 'anthropic'}:
        raise VisionUnavailable('provider_unsupported')
    user = ImageInput(text=json.dumps({'widget_provider': provider}, separators=(',', ':')), png=crop_png)
    _remaining(lease)
    try:
        lease.claim()
    except Exception:
        raise VisionUnavailable('reservation_invalid') from None
    timeout = _remaining(lease)
    try:
        result = model.chat(system, user, max_tokens=MAX_OUTPUT_TOKENS, request_timeout_s=timeout)
    except Exception:
        raise VisionUnavailable('provider_error') from None
    _remaining(lease)
    return _closed_plan(getattr(result, 'text', None))

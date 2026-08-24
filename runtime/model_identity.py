"""Bounded, secret-safe observations of models exposed by configured providers.

Configuration answers what Metnos requests.  This module answers what a
provider currently advertises, without turning that observation into routing
authority.  Discovery adapters are registered by provider protocol; no tier or
model name is encoded here.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_IDENTITIES = 16
_MAX_IDENTITY_CHARS = 180
_DEFAULT_TIMEOUT_S = 1.5
_DEFAULT_STALE_S = 180.0

_LOCK = threading.RLock()
_CACHE: dict[str, dict[str, Any]] = {}
_ADAPTERS: dict[str, tuple[str, Callable[[str, float], list[str]]]] = {}


def register_discovery_adapter(
        providers: Iterable[str], *, source: str,
) -> Callable[[Callable[[str, float], list[str]]], Callable[[str, float], list[str]]]:
    """Register one discovery protocol for one or more provider identifiers."""

    normalized = tuple(str(name).strip().casefold() for name in providers)

    def decorate(func: Callable[[str, float], list[str]]):
        for name in normalized:
            if name:
                _ADAPTERS[name] = (source, func)
        return func

    return decorate


def _safe_endpoint(value: object) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
        if parsed.scheme.casefold() not in {"http", "https"}:
            return ""
        if not parsed.hostname or parsed.username or parsed.password:
            return ""
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urllib.parse.urlunsplit(
            (parsed.scheme.casefold(), f"{host}{port}", parsed.path.rstrip("/"), "", ""))
    except (TypeError, ValueError):
        return ""


def _discovery_key(provider: object, endpoint: object) -> str:
    canonical = json.dumps(
        [str(provider or "").strip().casefold(), _safe_endpoint(endpoint)],
        ensure_ascii=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identity(value: object) -> str:
    """Return a bounded opaque identifier, never a filesystem directory."""

    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    # Both functions are intentional: provider IDs may use either path grammar
    # regardless of the operating system running Metnos.
    text = posixpath.basename(ntpath.basename(text)).strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return ""
    return text[:_MAX_IDENTITY_CHARS]


def _read_json(url: str, timeout_s: float) -> Any:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Metnos/model-observer"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        content_type = str(response.headers.get("Content-Type") or "").casefold()
        if "json" not in content_type:
            raise ValueError("non-json response")
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return json.loads(payload.decode("utf-8"))


def _bounded_identities(values: Iterable[object]) -> list[str]:
    identities = {_identity(value) for value in values}
    identities.discard("")
    return sorted(identities, key=str.casefold)[:_MAX_IDENTITIES]


@register_discovery_adapter(
    ("llamacpp", "openai", "openai-compatible", "http", "remote"),
    source="openai_models",
)
def _discover_openai(endpoint: str, timeout_s: float) -> list[str]:
    parsed = urllib.parse.urlsplit(endpoint)
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/models" if base_path.endswith("/v1") else f"{base_path}/v1/models"
    url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    data = _read_json(url, timeout_s)
    rows = data.get("data") if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        return []
    return _bounded_identities(
        row.get("id") for row in rows if isinstance(row, Mapping))


@register_discovery_adapter(("ollama",), source="ollama_tags")
def _discover_ollama(endpoint: str, timeout_s: float) -> list[str]:
    parsed = urllib.parse.urlsplit(endpoint)
    path = f"{parsed.path.rstrip('/')}/api/tags"
    url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    data = _read_json(url, timeout_s)
    rows = data.get("models") if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        return []
    return _bounded_identities(
        row.get("model") or row.get("name")
        for row in rows if isinstance(row, Mapping))


def _public_observation(row: Mapping[str, Any] | None, *, now: float | None = None) -> dict[str, Any]:
    if not row:
        return {
            "status": "not_observed", "identities": [], "source": "",
            "observed_at": "",
        }
    current = time.time() if now is None else float(now)
    status = str(row.get("status") or "not_observed")
    observed_epoch = float(row.get("observed_epoch") or 0)
    if observed_epoch and current - observed_epoch > _DEFAULT_STALE_S:
        status = "stale"
    return {
        "status": status,
        "identities": list(row.get("identities") or ())[:_MAX_IDENTITIES],
        "source": str(row.get("source") or ""),
        "observed_at": str(row.get("observed_at") or ""),
    }


def observation_for(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Project the cached observation for a role without performing I/O."""

    provider = str(spec.get("provider") or "").strip().casefold()
    endpoint = spec.get("endpoint") or spec.get("base_url") or ""
    safe_endpoint = _safe_endpoint(endpoint)
    adapter = _ADAPTERS.get(provider)
    if not adapter:
        return {
            "status": "unsupported", "identities": [], "source": "",
            "observed_at": "",
        }
    if not safe_endpoint:
        return {
            "status": "not_observed", "identities": [], "source": adapter[0],
            "observed_at": "",
        }
    with _LOCK:
        return _public_observation(_CACHE.get(_discovery_key(provider, safe_endpoint)))


def refresh_specs(
        specs: Iterable[Mapping[str, Any]], *, timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, int]:
    """Refresh each physical provider endpoint once; never retain errors."""

    unique: dict[str, tuple[str, str, str, Callable[[str, float], list[str]]]] = {}
    unsupported = 0
    for spec in specs:
        provider = str(spec.get("provider") or "").strip().casefold()
        endpoint = _safe_endpoint(spec.get("endpoint") or spec.get("base_url") or "")
        adapter = _ADAPTERS.get(provider)
        if not adapter or not endpoint:
            unsupported += 1
            continue
        key = _discovery_key(provider, endpoint)
        unique.setdefault(key, (provider, endpoint, adapter[0], adapter[1]))

    refreshed = failures = 0
    for key, (_provider, endpoint, source, discover) in unique.items():
        now = time.time()
        try:
            identities = _bounded_identities(
                discover(endpoint, max(0.05, float(timeout_s))))
            status = (
                "observed" if len(identities) == 1
                else "ambiguous" if identities else "empty"
            )
            row = {"status": status, "identities": identities, "source": source}
            refreshed += 1
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError,
                urllib.error.URLError, urllib.error.HTTPError):
            row = {"status": "unreachable", "identities": [], "source": source}
            failures += 1
        row["observed_epoch"] = now
        row["observed_at"] = datetime.fromtimestamp(
            now, timezone.utc).isoformat(timespec="seconds")
        with _LOCK:
            _CACHE[key] = row
    return {"bindings": len(unique), "refreshed": refreshed,
            "failures": failures, "unsupported": unsupported}


def configured_specs() -> list[dict[str, Any]]:
    """Resolve effective model specs from the same authorities as factories."""

    from llm_router import FAST_LEVEL_ORDER, LLMRouter, TIER_ORDER, complete_tier_spec
    from virt import DEFAULT_EMBEDDERS, DEFAULT_VLM, tiers

    specs: list[dict[str, Any]] = []
    try:
        resolved = LLMRouter().tiers
        for role in TIER_ORDER:
            spec = resolved.get(role)
            if not isinstance(spec, Mapping):
                continue
            if role == "fast":
                specs.extend(complete_tier_spec(role, spec, level=level)
                             for level in FAST_LEVEL_ORDER)
            else:
                specs.append(complete_tier_spec(role, spec))
    except Exception:
        pass
    for kind, defaults in (("embedding", DEFAULT_EMBEDDERS), ("vlm", DEFAULT_VLM)):
        document = tiers.config_document(kind)
        roles = list(dict.fromkeys([*defaults, *document.sections]))
        for role in roles:
            try:
                specs.append(tiers.spec(kind, role, defaults))
            except Exception:
                continue
    return specs


def refresh_configured(*, timeout_s: float = _DEFAULT_TIMEOUT_S) -> dict[str, int]:
    return refresh_specs(configured_specs(), timeout_s=timeout_s)


def _clear_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()

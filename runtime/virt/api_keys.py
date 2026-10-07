"""Write-only model credentials for the administrator's Models page."""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import urllib.parse

import credentials


PROVIDERS = {"openai": "OpenAI", "anthropic": "Anthropic"}
API_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
}
_SAVE_LOCK = threading.Lock()


def api_endpoint(interface: str, value: str | None = None) -> str:
    """A complete API URL, without credentials or secret-bearing URL parts."""
    if interface not in API_ENDPOINTS:
        raise ValueError("unknown_interface")
    value = API_ENDPOINTS[interface] if value is None else value
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid_url")
    value = value.strip()
    try:
        url = urllib.parse.urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment
                or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value)):
            raise ValueError
        host = url.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = url.port
        if port == 0:
            raise ValueError
        if port is not None and port != {"http": 80, "https": 443}[url.scheme]:
            host += f":{port}"
        return urllib.parse.urlunsplit((url.scheme, host, url.path, "", ""))
    except (ValueError, UnicodeError):
        raise ValueError("invalid_url") from None


def validate_key(raw_key: str) -> str:
    if not isinstance(raw_key, str) or len(raw_key) > 4096:
        raise ValueError("invalid_key")
    key = raw_key.strip()
    if not key or any(not (33 <= ord(char) <= 126) for char in key):
        raise ValueError("invalid_key")
    return key


def key_from_spec(spec: dict) -> str | None:
    """Resolve a scoped reference. None alone permits legacy official keys."""
    interface = spec.get("provider")
    endpoint = api_endpoint(interface, spec.get("endpoint") or spec.get("base_url"))
    if "api_key_ref" in spec:
        reference = spec["api_key_ref"]
        if not reference:
            return ""
        try:
            payload = credentials.load(reference)
            if (not isinstance(payload, dict) or payload.get("interface") != interface
                    or payload.get("endpoint") != endpoint):
                raise ValueError
            return validate_key(payload.get("api_key"))
        except Exception:
            raise ValueError("credential_unavailable") from None
    if spec.get("api_key"):
        return validate_key(spec["api_key"])
    # Never send an ambient OpenAI/Anthropic key to a compatible third party.
    return None if endpoint == API_ENDPOINTS[interface] else ""


def _configuration_overrides() -> set[str]:
    from llm_router import LLMRouter, tier_config_document

    document = tier_config_document()
    if document.error:
        raise ValueError("configuration_unavailable")
    overrides = set()

    def walk(value, provider="", has_key=False):
        if not isinstance(value, dict):
            return
        provider = value.get("provider", provider)
        has_key = bool(value.get("api_key", has_key))
        if has_key and provider in PROVIDERS:
            overrides.add(provider)
        for child in value.values():
            if isinstance(child, dict):
                walk(child, provider, has_key)

    walk(LLMRouter(config_path=document.path).tiers)
    return overrides


def form_token(admin_key: str) -> str:
    if not isinstance(admin_key, str) or not admin_key:
        return ""
    return hmac.new(admin_key.encode(), b"metnos-model-api-keys-v1",
                    hashlib.sha256).hexdigest()


def valid_form_token(token: str, admin_key: str) -> bool:
    expected = form_token(admin_key)
    return bool(expected and isinstance(token, str) and token.isascii()
                and hmac.compare_digest(token, expected))


def statuses() -> list[dict[str, str]]:
    """Return presence only; never expose credential values or errors."""
    result = []
    try:
        overrides = _configuration_overrides()
    except Exception:
        return [{"provider": p, "label": label, "status": "unavailable"}
                for p, label in PROVIDERS.items()]
    for provider, label in PROVIDERS.items():
        if provider in overrides:
            status = "configuration"
        elif os.environ.get(f"{provider.upper()}_API_KEY"):
            status = "environment"
        else:
            try:
                payload = credentials.load(f"{provider}_api_key")
                value = (payload.get("api_key", payload.get("value"))
                         if isinstance(payload, dict) else None)
                status = ("configured" if isinstance(value, str) and value.strip()
                          else "missing")
            except Exception:
                status = "unavailable"
        result.append({"provider": provider, "label": label, "status": status})
    return result


def save(provider: str, raw_key: str) -> None:
    """Replace one encrypted API key; a blank form never erases a key."""
    if provider not in PROVIDERS:
        raise ValueError("unknown_provider")
    if provider in _configuration_overrides():
        raise ValueError("configuration_override")
    if os.environ.get(f"{provider.upper()}_API_KEY"):
        raise ValueError("environment_override")
    key = validate_key(raw_key)
    binding = f"{provider}_api_key"
    with _SAVE_LOCK:
        existing = credentials.load(binding)
        payload = dict(existing) if isinstance(existing, dict) else {}
        payload.pop("value", None)
        payload["api_key"] = key
        credentials.store(binding, payload)

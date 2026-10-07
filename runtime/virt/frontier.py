"""One Frontier binding, saved with an endpoint-scoped encrypted credential."""
from __future__ import annotations

import math
import uuid

import credentials
from llm_provider import REASONING_EFFORTS
from . import api_keys, config_editor as editor
from .configuration import config_revision


FORM_FIELDS = frozenset({
    "interface", "endpoint", "model", "temperature", "think", "reasoning_effort", "reasoning_budget",
    "api_key", "revision", "csrf_token",
})


def _current():
    from llm_router import LLMRouter, complete_tier_spec, tier_config_document

    document = tier_config_document()
    if document.error:
        raise editor.ConfigEditError("invalid_configuration")
    spec = LLMRouter(config_path=document.path).tiers.get("frontier", {"provider": "none"})
    return document.path, complete_tier_spec("frontier", spec)


def _key(spec):
    value = api_keys.key_from_spec(spec)
    if value is None:
        # Migration of an existing official binding only. A custom endpoint
        # never receives a key from the environment or an older provider slot.
        from llm_provider import _read_openai_key, _read_anthropic_key
        value = (_read_openai_key if spec["provider"] == "openai"
                 else _read_anthropic_key)()
    return value or ""


def view() -> dict:
    """Only non-secret form values and credential presence reach HTTP/Jinja."""
    result = {
        "interface": "none", "endpoint": api_keys.API_ENDPOINTS["openai"],
        "model": "", "temperature": 0, "think": False,
        "reasoning_effort": "medium", "reasoning_budget": 1024,
        "reasoning_options": {
            **{effort: " ".join(protocol for protocol, values in REASONING_EFFORTS.items()
                               if effort in values) for effort in REASONING_EFFORTS["openai"]},
            "budget": "anthropic",
        },
        "key_status": "missing", "revision": "", "available": False,
    }
    try:
        path, spec = _current()
        interface = spec.get("provider", "none")
        result.update({"interface": interface, "model": str(spec.get("model") or ""),
                       "temperature": spec.get("temperature", 0), "think": spec.get("think", False),
                       "reasoning_effort": ("budget" if interface == "anthropic"
                                            and spec.get("reasoning_budget", 0) > 0
                                            else spec.get("reasoning_effort") or "medium"),
                       "reasoning_budget": spec.get("reasoning_budget") or 1024,
                       "revision": config_revision(path), "available": True})
        if interface in api_keys.API_ENDPOINTS:
            result["endpoint"] = api_keys.api_endpoint(
                interface, spec.get("endpoint") or spec.get("base_url"))
            try:
                result["key_status"] = "configured" if _key(spec) else "missing"
            except Exception:
                result["key_status"] = "unavailable"
        elif interface != "none":
            result["available"] = False
    except Exception:
        result["key_status"] = "unavailable"
        result["available"] = False
    return result


def save(form) -> editor.EditResult:
    """Activate config + key together through one atomic TOML replacement.

    Keys have immutable references: a failed save cannot overwrite the active
    secret, and private config recovery copies keep their original binding.
    """
    if (set(form) != FORM_FIELDS or any(not isinstance(v, str) or len(v) > 4096
                                      for v in form.values())):
        raise editor.ConfigEditError("invalid_field_set")
    interface = form["interface"]
    if interface not in {*api_keys.API_ENDPOINTS, "none"}:
        raise editor.ConfigEditError("invalid_configuration")
    model = form["model"].strip()
    if interface != "none" and (not model or len(model) > 256
                                or any(c.isspace() or ord(c) < 32 for c in model)):
        raise editor.ConfigEditError("invalid_configuration")
    try:
        endpoint = api_keys.api_endpoint(
            interface if interface != "none" else "openai", form["endpoint"])
        temperature = float(form["temperature"])
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError
        if form["think"] not in {"true", "false"}:
            raise ValueError
        effort = form["reasoning_effort"]
        if effort == "budget" and interface == "anthropic":
            budget = int(form["reasoning_budget"])
            if budget < 1024:
                raise ValueError
            effort = ""
        elif effort in REASONING_EFFORTS.get(interface, REASONING_EFFORTS["openai"]):
            budget = 0
        else:
            raise ValueError
        raw_key = form["api_key"].strip()
        key = api_keys.validate_key(raw_key) if raw_key else ""
    except ValueError:
        raise editor.ConfigEditError("invalid_configuration") from None

    with editor.WRITE_LOCK:
        path, old = _current()
        revision = form["revision"]
        if config_revision(path) != revision:
            raise editor.ConfigEditError("revision_conflict")
        document = editor._read_document(path)
        if "__metnos_invalid_source__" in document:
            raise editor.ConfigEditError("invalid_document")
        same_destination = (interface == old.get("provider")
                            and interface in api_keys.API_ENDPOINTS
                            and endpoint == api_keys.api_endpoint(
                                interface, old.get("endpoint") or old.get("base_url")))
        reference = old.get("api_key_ref", "") if same_destination else ""
        if interface != "none" and not key:
            if not same_destination and old.get("provider") != "none":
                raise editor.ConfigEditError("destination_key_required")
            if same_destination and not reference:
                key = _key(old)
        target = editor._role_container(document, "llm", "frontier")
        # The UI is the complete Frontier authority. No hidden fallback/model
        # or inline secret survives behind the one-model form.
        target.clear()
        target.update({"provider": interface, "endpoint": endpoint, "model": model,
                       "temperature": temperature, "think": form["think"] == "true",
                       "reasoning_effort": effort, "reasoning_budget": budget,
                       "api_key_ref": reference})
        staged = ""
        try:
            if key and interface != "none":
                staged = "frontier_api_key_" + uuid.uuid4().hex
                credentials.store(staged, {"api_key": key, "interface": interface,
                                           "endpoint": endpoint})
                target["api_key_ref"] = staged
            editor._validate("llm", document)
            backup = editor._backup(path, "llm", revision)
            after = editor._atomic_write(path, document, expected_revision=revision)
        except Exception:
            # If replacement completed but its readback failed, keep its key.
            if staged:
                try:
                    active = path.read_text(encoding="utf-8") if path.exists() else ""
                    if staged not in active:
                        credentials.remove(staged)
                except OSError:
                    pass  # Keep encrypted recovery material if state is unknown.
            raise
        editor._invalidate_runtime("llm")
        return editor.EditResult("llm", revision, after, backup)

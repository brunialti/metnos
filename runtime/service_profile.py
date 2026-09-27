"""Data-only bindings for existing companion services; no discovery or effects."""
from __future__ import annotations

from urllib.parse import urlsplit


SERVICE_ENVIRONMENT = {
    "llm": "METNOS_LLM_URL",
    "vlm": "METNOS_VLM_URL",
    "searxng": "METNOS_SEARXNG_URL",
    "photon": "METNOS_PHOTON_URL",
    "playwright": "METNOS_PLAYWRIGHT_URL",
}


def validate_profile(value: object, *, resolved: bool = False) -> dict[str, dict]:
    """Return a fresh normalized profile; resolved model bindings are explicit."""
    if type(value) is not dict or set(value) - SERVICE_ENVIRONMENT.keys():
        raise ValueError("service profile fields")
    result = {}
    for name, source in value.items():
        allowed = {"url"}
        if name in {"llm", "vlm"}:
            allowed.add("model")
        if name == "llm":
            allowed.add("frontier")
        if type(source) is not dict or set(source) - allowed:
            raise ValueError("service profile fields")
        url = source.get("url")
        if not isinstance(url, str) or not url or len(url.encode()) > 4096 or any(
            char.isspace() or char in '\\"\'' or ord(char) < 32 for char in url
        ):
            raise ValueError("service profile url")
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("service profile url")
        _ = parts.port
        entry = {"url": url.rstrip("/")}
        if "model" in source:
            model = source["model"]
            if (not isinstance(model, str) or not model.strip()
                    or len(model.encode()) > 4096
                    or any(ord(char) < 32 for char in model)):
                raise ValueError("service profile model")
            entry["model"] = model
        if "frontier" in source:
            if type(source["frontier"]) is not bool:
                raise ValueError("service profile frontier")
            entry["frontier"] = source["frontier"]
        if resolved:
            if name in {"llm", "vlm"} and "model" not in entry:
                raise ValueError("service profile unresolved model")
            if name == "llm":
                entry.setdefault("frontier", False)
        result[name] = entry
    return result


def target_environment(profile: dict, service_home: str) -> dict[str, str]:
    """Only existing data bindings; no executable, root or environment override."""
    profile = validate_profile(profile, resolved=True)
    environment = {SERVICE_ENVIRONMENT[name]: entry["url"]
                   for name, entry in profile.items()}
    if "llm" in profile:
        environment["METNOS_LLM_TIERS_CONFIG"] = (
            service_home + "/.config/metnos/services-llm-tiers.toml"
        )
    if "vlm" in profile:
        environment["METNOS_VLM_MODEL"] = profile["vlm"]["model"]
    return environment

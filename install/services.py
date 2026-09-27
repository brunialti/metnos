# SPDX-License-Identifier: MIT
"""Optional external-service profile for the six-phase installer.

The profile selects dependencies, never discovers machines or executes shell
content. Runtime consumers keep using their existing environment/tier contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from runtime.service_profile import validate_profile

from . import i18n, llm_manager

# Service key -> existing runtime environment variable and protocol probe.
SERVICES = {
    "llm": ("METNOS_LLM_URL", "/v1/models"),
    "vlm": ("METNOS_VLM_URL", "/v1/models"),
    "searxng": ("METNOS_SEARXNG_URL", "/search?q=metnos-health&format=json"),
    "photon": ("METNOS_PHOTON_URL", "/api/?q=Rome&limit=1"),
    "playwright": ("METNOS_PLAYWRIGHT_URL", "/health"),
}


def config_dir() -> Path:
    return Path(os.environ.get("METNOS_USER_CONFIG", Path.home() / ".config/metnos"))


def _error(key: str, **values) -> ValueError:
    return ValueError(i18n.t(key, **values))


def load() -> dict[str, dict]:
    """Read without creating directories; absent/empty means ordinary install."""
    path = config_dir() / "services.toml"
    try:
        with path.open("rb") as stream:
            raw = stream.read(16385)
    except FileNotFoundError:
        return {}
    except OSError:
        raise _error("services_invalid", path=path) from None
    try:
        if len(raw) > 16384:
            raise ValueError("size")
        profile = validate_profile(tomllib.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeError):
        raise _error("services_invalid", path=path) from None
    return profile


def fingerprint(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def validate_choices(profile: dict, args) -> None:
    from .sidecar import SIDECARS

    unknown = set(getattr(args, "skip", [])) - set(SIDECARS)
    if unknown:
        raise _error("services_skip_unknown", names=", ".join(sorted(unknown)))
    conflict = set(profile) & set(getattr(args, "skip", []))
    if conflict:
        raise _error("services_skip_conflict", names=", ".join(sorted(conflict)))


def validate_units(profile: dict, directory: Path | None = None) -> None:
    """Changing ownership of an existing companion is a separate operation."""
    directory = directory or Path.home() / ".config/systemd/user"
    for unit in sorted(unit_names(profile)):
        path = directory / unit
        if path.exists() or path.is_symlink():
            raise _error("services_unit_conflict", unit=unit)


def probe(profile: dict) -> dict[str, str]:
    """Check the selected protocols, with no inference, download or fallback."""
    import httpx

    models = {}
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for name, entry in profile.items():
            try:
                response = client.get(entry["url"] + SERVICES[name][1])
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("object")
                if name in {"llm", "vlm"}:
                    ids = [row["id"] for row in body["data"]
                           if isinstance(row, dict) and isinstance(row.get("id"), str)
                           and row["id"]]
                    model = entry.get("model")
                    if model is None and len(ids) == 1:
                        model = ids[0]
                    if not model or model not in ids or any(ord(c) < 32 for c in model):
                        raise ValueError("model")
                    models[name] = model
                elif name == "searxng":
                    if not isinstance(body.get("results"), list):
                        raise ValueError("results")
                elif name == "photon":
                    if body.get("type") != "FeatureCollection" or not isinstance(body.get("features"), list):
                        raise ValueError("features")
                elif body.get("ok") is not True or body.get("browser") != "chromium":
                    raise ValueError("health")
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise _error("services_unavailable", name=name, url=entry["url"]) from None
    return models


def resolve(profile: dict) -> dict[str, dict]:
    """Resolve advertised model identities before sealing the service catalog."""
    result = validate_profile(profile)
    models = probe(result) if result else {}
    for name, model in models.items():
        result[name]["model"] = model
    return validate_profile(result, resolved=True)


def _write(path: Path, text: str) -> None:
    """Publish one private generated file atomically in its destination directory."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".services-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def apply(profile: dict, models: dict[str, str]) -> Path:
    """Render selected bindings; keep the ordinary user tier file untouched."""
    import tomlkit

    root = config_dir()
    env = {SERVICES[name][0]: entry["url"] for name, entry in profile.items()}
    if "llm" in profile:
        llm = profile["llm"]
        tiers = {name: {"provider": "llamacpp", "endpoint": llm["url"],
                        "model": models["llm"]} for name in llm_manager.LOCAL_TIER_NAMES}
        tiers["frontier"] = {"provider": "anthropic" if llm.get("frontier", False) else "none"}
        path = root / "services-llm-tiers.toml"
        _write(path, tomlkit.dumps(tiers))
        env["METNOS_LLM_TIERS_CONFIG"] = str(path)
    if "vlm" in profile:
        env["METNOS_VLM_MODEL"] = models["vlm"]
    # EnvironmentFile's double-quoted syntax: only backslash, quote, dollar and
    # backtick need escaping. No shell interprets this file.
    def quote(value: str) -> str:
        for char in ('\\', '"', '$', '`'):
            value = value.replace(char, "\\" + char)
        return '"' + value + '"'

    path = root / "services.env"
    _write(path, "# Generated from services.toml; rerun phases 2 and 5 after edits.\n"
           + "".join(f"{name}={quote(value)}\n" for name, value in sorted(env.items())))
    return path


def unit_names(profile: dict) -> set[str]:
    names = {f"metnos-{name}.service" for name in profile if name != "vlm"}
    if "playwright" in profile:
        names.add("metnos-side-display.service")
    return names

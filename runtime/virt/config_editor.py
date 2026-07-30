"""Validated, atomic administration of virt configuration files.

The UI edits the same TOML documents consumed by the model factories.  This
module is deliberately independent from HTTP/Jinja: it accepts a bounded form
patch, validates the resulting document through the canonical resolvers,
creates a private recovery copy, and replaces the target atomically.
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import shutil
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - supported runtime is 3.11+
    tomllib = None  # type: ignore

import config as _C

from . import DEFAULT_EMBEDDERS, DEFAULT_VLM
from . import tiers as simple_tiers
from .configuration import config_revision, snapshot


FAMILIES = frozenset({"llm", "embedding", "vlm"})
_FORM_FIELD_PREFIX = "field_"
_MAX_FORM_FIELDS = 256
_MAX_FORM_VALUE_CHARS = 4096


@dataclass(frozen=True, slots=True)
class EditResult:
    family: str
    revision_before: str
    revision_after: str
    backup_path: str
    reset: bool = False


class ConfigEditError(ValueError):
    """Safe, localized-by-the-route failure from a configuration edit."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _family_path(family: str) -> Path:
    if family == "llm":
        from llm_router import tier_config_document

        return tier_config_document().path
    if family in {"embedding", "vlm"}:
        return simple_tiers.config_path(family)
    raise ConfigEditError("unknown_family")


def _read_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if tomllib is None:
        raise ConfigEditError("toml_unavailable")
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # An invalid document may still be repaired from the effective
        # fallback values shown by the editor.  Its original bytes are saved
        # before replacement, so starting from an empty mapping is reversible.
        return {"__metnos_invalid_source__": str(type(exc).__name__)}
    if not isinstance(parsed, dict):
        raise ConfigEditError("invalid_document")
    return parsed


def _decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ConfigEditError("invalid_field")
    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    ]


def _role_container(document: dict[str, Any], family: str, role: str) -> dict:
    if family == "llm":
        flat = document.get(role)
        if isinstance(flat, dict):
            return flat
        nested = document.get("tiers")
        if isinstance(nested, dict) and isinstance(nested.get(role), dict):
            return nested[role]
    current = document.get(role)
    if isinstance(current, dict):
        return current
    created: dict[str, Any] = {}
    document[role] = created
    return created


def _set_nested(container: Any, parts: list[str], value: Any) -> None:
    if not parts:
        raise ConfigEditError("invalid_field")
    current = container
    for index, part in enumerate(parts[:-1]):
        following = parts[index + 1]
        if isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                raise ConfigEditError("invalid_field")
            current = current[int(part)]
            continue
        if not isinstance(current, dict):
            raise ConfigEditError("invalid_field")
        if part not in current:
            current[part] = [] if following.isdigit() else {}
        current = current[part]

    leaf = parts[-1]
    if isinstance(current, list):
        if not leaf.isdigit() or int(leaf) >= len(current):
            raise ConfigEditError("invalid_field")
        current[int(leaf)] = value
    elif isinstance(current, dict):
        current[leaf] = value
    else:
        raise ConfigEditError("invalid_field")


def _parse_form_value(raw: str, value_type: str) -> Any:
    if len(raw) > _MAX_FORM_VALUE_CHARS:
        raise ConfigEditError("value_too_long")
    try:
        if value_type == "boolean":
            if raw not in {"true", "false"}:
                raise ValueError
            return raw == "true"
        if value_type == "integer":
            return int(raw, 10)
        if value_type == "number":
            value = float(raw)
            if value != value or value in {float("inf"), float("-inf")}:
                raise ValueError
            return value
        if value_type in {"string", "textarea"}:
            return raw
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigEditError("invalid_value") from exc
    raise ConfigEditError("invalid_field")


def _editable_fields(family: str) -> tuple[dict[str, dict[str, Any]], str]:
    payload = snapshot(edit_family=family)
    family_view = next(
        (item for item in payload["families"] if item["key"] == family), None)
    if family_view is None:
        raise ConfigEditError("unknown_family")
    fields: dict[str, dict[str, Any]] = {}
    for role in family_view["roles"]:
        for field in role["fields"]:
            if field.get("editable"):
                fields[str(field["edit_key"])] = field
    return fields, str(family_view["config"]["revision"])


def _validate_url(value: object, *, field: str) -> None:
    if not value:
        return
    parsed = urllib.parse.urlsplit(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigEditError("invalid_url", field)


def _validate_llm(document: dict[str, Any]) -> None:
    from llm_provider import make_provider_from_spec
    from llm_router import LLMRouter, _normalize_tiers_dict

    tiers = _normalize_tiers_dict(document)
    try:
        router = LLMRouter(tiers_override=copy.deepcopy(tiers))
    except Exception as exc:
        # Some legacy validator errors interpolate the offending mapping,
        # which may contain credentials.  Keep the HTTP-facing detail closed.
        raise ConfigEditError("invalid_configuration") from exc
    for role, spec in router.tiers.items():
        if not isinstance(spec, dict):
            raise ConfigEditError("invalid_configuration", str(role))
        endpoint = spec.get("endpoint") or spec.get("base_url")
        if endpoint:
            _validate_url(endpoint, field=f"{role}.endpoint")
        provider = str(spec.get("provider") or "")
        if role == "frontier" and provider == "none":
            continue
        try:
            make_provider_from_spec(spec)
        except Exception as exc:
            raise ConfigEditError(
                "invalid_configuration", str(role)) from exc
        for key in ("reasoning_budget", "num_predict"):
            if key in spec and int(spec[key]) < 0:
                raise ConfigEditError(
                    "invalid_configuration", f"{role}.{key}")


def _resolved_simple(
        role: str, section: Mapping[str, Any], defaults: dict[str, dict],
) -> dict[str, Any]:
    base = dict(defaults.get(role) or next(iter(defaults.values()), {}))
    base.update(section)
    return base


def _validate_simple(
        family: str, document: dict[str, Any], defaults: dict[str, dict],
) -> None:
    if not isinstance(document, dict):
        raise ConfigEditError("invalid_document")
    role_names = set(defaults)
    role_names.update(
        str(name) for name, value in document.items()
        if isinstance(value, dict)
    )
    for role in role_names:
        section = document.get(role, {})
        if not isinstance(section, dict):
            raise ConfigEditError("invalid_configuration", role)
        spec = _resolved_simple(role, section, defaults)
        provider = str(spec.get("provider") or "").strip().casefold()
        if not provider:
            raise ConfigEditError(
                "invalid_configuration", f"{role}.provider")
        endpoint = spec.get("endpoint") or spec.get("base_url")
        if endpoint:
            _validate_url(endpoint, field=f"{role}.endpoint")
        if family == "embedding":
            allowed = {"bge", "qwen", "siglip", "http", "openai", "remote"}
            if provider not in allowed:
                raise ConfigEditError(
                    "invalid_configuration", f"{role}.provider")
            if provider in {"http", "openai", "remote"} and not endpoint:
                raise ConfigEditError(
                    "invalid_configuration", f"{role}.endpoint")
        if family == "vlm":
            numeric_bounds = {
                "timeout_s": (1, 600),
                "max_edge": (64, 16384),
                "max_tokens": (1, 32768),
                "max_images_per_request": (1, 32),
                "request_budget_s": (1, 90),
            }
            for key, (minimum, maximum) in numeric_bounds.items():
                if key not in spec:
                    continue
                try:
                    value = float(spec[key])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ConfigEditError(
                        "invalid_configuration", f"{role}.{key}") from exc
                if not minimum <= value <= maximum:
                    raise ConfigEditError(
                        "invalid_configuration", f"{role}.{key}")


def _validate(family: str, document: dict[str, Any]) -> None:
    document.pop("__metnos_invalid_source__", None)
    if family == "llm":
        _validate_llm(document)
    elif family == "embedding":
        _validate_simple(family, document, DEFAULT_EMBEDDERS)
    elif family == "vlm":
        _validate_simple(family, document, DEFAULT_VLM)
    else:
        raise ConfigEditError("unknown_family")
    try:
        serialized = tomli_w.dumps(document)
        if tomllib is None:
            raise RuntimeError("tomllib unavailable")
        tomllib.loads(serialized)
    except Exception as exc:
        raise ConfigEditError("invalid_document", str(exc)) from exc


def _history_dir(family: str) -> Path:
    return _C.PATH_USER_STATE / "virt-config-history" / family


def _backup(path: Path, family: str, revision: str) -> str:
    if not path.exists():
        return ""
    target_dir = _history_dir(family)
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = target_dir / f"{stamp}-{revision[:12]}.toml"
    shutil.copyfile(path, target)
    target.chmod(0o600)
    return str(target)


def _atomic_write(
        path: Path, document: dict[str, Any], *, expected_revision: str,
) -> str:
    if config_revision(path) != expected_revision:
        raise ConfigEditError("revision_conflict")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = (
        "# Configurazione virt gestita da Metnos.\n"
        "# I segreti non sono esposti dall'interfaccia amministrativa.\n\n"
        + tomli_w.dumps(document)
    )
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        if config_revision(path) != expected_revision:
            raise ConfigEditError("revision_conflict")
        os.replace(temp_name, path)
        temp_name = ""
        try:
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    return config_revision(path)


def _invalidate_runtime(family: str) -> None:
    if family == "llm":
        import llm_router

        llm_router._TIERS_FILE_CACHE["key"] = None
        llm_router._TIERS_FILE_CACHE["tiers"] = None
        return
    import virt

    if family == "embedding":
        for key in list(virt._cache):
            if key and str(key[0]).startswith("emb"):
                virt._cache.pop(key, None)
    elif family == "vlm":
        virt._vlm_started.clear()


def save(
        family: str, form: Mapping[str, str], *, expected_revision: str,
) -> EditResult:
    """Apply the editor's allowlisted scalar patch and save atomically."""

    if family not in FAMILIES:
        raise ConfigEditError("unknown_family")
    fields, observed_revision = _editable_fields(family)
    path = _family_path(family)
    if expected_revision != observed_revision or config_revision(path) != expected_revision:
        raise ConfigEditError("revision_conflict")
    submitted = {
        key[len(_FORM_FIELD_PREFIX):]: str(value)
        for key, value in form.items() if key.startswith(_FORM_FIELD_PREFIX)
    }
    if len(submitted) > _MAX_FORM_FIELDS:
        raise ConfigEditError("too_many_fields")
    if set(submitted) != set(fields):
        raise ConfigEditError("invalid_field_set")

    document = _read_document(path)
    for edit_key, field in fields.items():
        raw = submitted[edit_key]
        value = _parse_form_value(raw, str(field["value_type"]))
        parts = _decode_pointer(str(field["pointer"]))
        if len(parts) < 2:
            raise ConfigEditError("invalid_field")
        role, nested = parts[0], parts[1:]
        target = _role_container(document, family, role)
        _set_nested(target, nested, value)

    _validate(family, document)
    backup = _backup(path, family, expected_revision)
    revision_after = _atomic_write(
        path, document, expected_revision=expected_revision)
    _invalidate_runtime(family)
    return EditResult(
        family=family, revision_before=expected_revision,
        revision_after=revision_after, backup_path=backup,
    )


def _factory_defaults(family: str) -> dict[str, Any]:
    if family == "llm":
        from llm_router import DEFAULT_TIERS

        return copy.deepcopy(DEFAULT_TIERS)
    if family == "embedding":
        return copy.deepcopy(DEFAULT_EMBEDDERS)
    if family == "vlm":
        return copy.deepcopy(DEFAULT_VLM)
    raise ConfigEditError("unknown_family")


def reset(family: str, *, expected_revision: str) -> EditResult:
    """Restore the factory configuration of the installed Metnos version."""

    if family not in FAMILIES:
        raise ConfigEditError("unknown_family")
    path = _family_path(family)
    if config_revision(path) != expected_revision:
        raise ConfigEditError("revision_conflict")
    document = _factory_defaults(family)
    _validate(family, document)
    backup = _backup(path, family, expected_revision)
    revision_after = _atomic_write(
        path, document, expected_revision=expected_revision)
    _invalidate_runtime(family)
    return EditResult(
        family=family, revision_before=expected_revision,
        revision_after=revision_after, backup_path=backup, reset=True,
    )

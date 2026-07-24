"""Validation for the small F1 console-anchor registry."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import tomllib

import i18n

from .cards import REPO_ROOT


UI_MAP = REPO_ROOT / "tutor" / "ui_map.toml"
_ID_SELECTOR = re.compile(r"^\[data-tutor-id='([a-z0-9][a-z0-9-]{1,63})'\]$")


@lru_cache(maxsize=1)
def load_ui_map() -> tuple[dict, ...]:
    with UI_MAP.open("rb") as handle:
        raw = tomllib.load(handle)
    controls = raw.get("controls") or []
    if not isinstance(controls, list):
        raise ValueError("tutor ui_map controls must be a list")
    return tuple(dict(control) for control in controls)


def validate_ui_map(*, require_i18n: bool = True) -> tuple[str, ...]:
    """Return all findings; no partial map is silently accepted."""

    findings: list[str] = []
    seen_ids: set[str] = set()
    try:
        from http_routes_admin import ROUTES as admin_routes
        routes = {str(item[1]) for item in admin_routes}
    except Exception as exc:
        routes = set()
        findings.append(f"route_registry_unavailable:{type(exc).__name__}")
    for control in load_ui_map():
        control_id = str(control.get("id") or "")
        if not control_id or control_id in seen_ids:
            findings.append(f"duplicate_or_empty_id:{control_id}")
        seen_ids.add(control_id)
        page = str(control.get("page") or "")
        if Path(page).name != page:
            findings.append(f"unsafe_page:{control_id}")
            continue
        template = REPO_ROOT / "runtime" / "templates" / page
        match = _ID_SELECTOR.fullmatch(str(control.get("selector") or ""))
        if not match:
            findings.append(f"invalid_selector:{control_id}")
        elif not template.is_file():
            findings.append(f"missing_template:{control_id}")
        elif f'data-tutor-id="{match.group(1)}"' not in template.read_text(
                encoding="utf-8"):
            findings.append(f"missing_selector:{control_id}")
        route = str(control.get("route") or "")
        if route not in routes:
            findings.append(f"missing_route:{control_id}:{route}")
        key = str(control.get("i18n_key") or "")
        if require_i18n and (not key or not i18n.key_exists(key)):
            findings.append(f"missing_i18n:{control_id}:{key}")
        if control.get("audience") != "instance_admin":
            findings.append(f"unsafe_audience:{control_id}")
    return tuple(findings)

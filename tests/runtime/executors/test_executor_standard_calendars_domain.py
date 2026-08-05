"""Domain gate for Google calendar-container executors."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.create_calendars import create_calendars  # noqa: E402
from executors.delete_calendars import delete_calendars  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


def _manifest(name: str) -> dict:
    path = ROOT / "executors" / name / "manifest.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_domain_is_server_only_and_provider_authority_is_unconditional() -> None:
    for name in ("create_calendars", "delete_calendars"):
        manifest = _manifest(name)
        assert manifest["executor_standard"] == "metnos.executor/1.0"
        assert manifest["platforms"] == ["linux"]
        assert manifest["placement"] == {
            "scope": "server", "device_ok": False}
        assert manifest["capabilities"] == [{
            "name": "provider:access", "hint": ["google-workspace"]}]


def test_create_requires_confirmation_before_provider_call(monkeypatch) -> None:
    touched = []
    monkeypatch.setattr(
        create_calendars.google_workspace, "create_calendar",
        lambda _args: touched.append(True),
    )
    result = create_calendars.invoke({"summary": "Standard test"})
    assert result["decision"] == "needs_inputs"
    assert touched == []


def test_confirmed_create_uses_exact_provider_once(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(create_calendars, "_find_owned_calendar_id",
                        lambda _name: None)
    monkeypatch.setattr(
        create_calendars.google_workspace, "create_calendar",
        lambda args: calls.append(dict(args)) or {
            "ok": True, "results": [{"calendar_id": "cal-1"}],
            "n_created": 1, "used": 1,
        },
    )
    result = create_calendars.invoke({
        "summary": "Standard test", "_confirmed": True,
    })
    assert result["ok"] is True
    assert len(calls) == 1 and calls[0]["summary"] == "Standard test"


def test_delete_never_targets_primary_or_shared_calendar(monkeypatch) -> None:
    index = {
        "primary": {"summary": "Main", "access_role": "owner",
                    "primary": True},
        "shared": {"summary": "Shared", "access_role": "reader",
                   "primary": False},
        "owned": {"summary": "Owned", "access_role": "owner",
                  "primary": False},
    }
    monkeypatch.setattr(
        delete_calendars, "_calendar_index",
        lambda: (index, {"ok": True, "entries": []}),
    )
    calls = []
    monkeypatch.setattr(
        delete_calendars.google_workspace, "delete_calendar",
        lambda args: calls.append(dict(args)) or {
            "ok": True, "results": [{"calendar_id": "owned"}], "used": 1,
        },
    )

    blocked = delete_calendars.invoke({"ids": ["primary", "shared"]})
    pending = delete_calendars.invoke({"ids": ["owned"]})
    confirmed = delete_calendars.invoke({
        "ids": ["owned"], "_confirmed": True, "confirm": True,
    })

    assert blocked["ok"] is False and calls == [{"ids": ["owned"],
        "_confirmed": True, "confirm": True}]
    assert pending["decision"] == "needs_inputs"
    assert confirmed["ok"] is True


def test_calendar_container_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    cases = {
        "crea un nuovo calendario separato chiamato Lavoro":
            "create_calendars",
        "elimina definitivamente il calendario contenitore Prova":
            "delete_calendars",
    }
    for query, expected in cases.items():
        names = [item.name for item in rank(query, entries, k=10, min_score=1)]
        assert expected in names, (query, names)

"""Composite stack health must fail closed without exposing turn contents."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import http_routes_stack as stack


@pytest.fixture(autouse=True)
def _no_background_executor(monkeypatch):
    """Keep route unit tests synchronous and leave no executor thread behind."""
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(stack.asyncio, "to_thread", direct)


class _Request(dict):
    def __init__(self, role: str = "admin"):
        super().__init__(role=role)
        self.app = {
            "catalog_provider": lambda: [
                SimpleNamespace(name="delete_files"),
                SimpleNamespace(name="read_sites"),
            ],
        }


def _sidecar(**overrides):
    payload = {
        "available": True,
        "ok": True,
        "contract_aligned": True,
        "active_sessions": 0,
        "approval_pending_sessions": 0,
        "factor_pending_sessions": 0,
        "pending_opens": 0,
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _idle_durable_activity(monkeypatch):
    monkeypatch.setattr(
        stack,
        "_probe_durable_activity",
        lambda: {
            "schema_version": "metnos.durable-activity/1",
            "known": True,
            "reason_code": "none",
            "active_attempts": 0,
            "leased_attempts": 0,
            "running_attempts": 0,
        },
    )


def test_stack_health_reports_ready_and_quiescent(monkeypatch):
    monkeypatch.setattr(stack, "_probe_sidecar", lambda: _sidecar())
    monkeypatch.setattr(
        stack._contract, "source_status",
        lambda: {
            "contract_loaded": "fp",
            "contract_current": "fp",
            "contract_aligned": True,
        },
    )
    monkeypatch.setattr(
        stack.TurnEventLog, "get",
        classmethod(lambda _cls: SimpleNamespace(
            stats=lambda: {"active": 0, "total_turns": 3, "closed": 3},
        )),
    )

    response = asyncio.run(stack.stack_health(_Request()))
    payload = json.loads(response.body)

    assert response.status == 200
    assert payload["ready"] is True
    assert payload["quiescent"] is True
    assert payload["http"]["active_turns"] == 0
    assert payload["catalog"] == {
        "count": 2, "names": ["delete_files", "read_sites"],
    }
    assert "query" not in response.text


def test_stack_health_detects_turn_and_broker_activity(monkeypatch):
    monkeypatch.setattr(
        stack, "_probe_sidecar",
        lambda: _sidecar(active_sessions=1, approval_pending_sessions=1),
    )
    monkeypatch.setattr(
        stack._contract, "source_status",
        lambda: {"contract_aligned": True},
    )
    monkeypatch.setattr(
        stack.TurnEventLog, "get",
        classmethod(lambda _cls: SimpleNamespace(stats=lambda: {"active": 2})),
    )

    payload = json.loads(asyncio.run(stack.stack_health(_Request())).body)
    assert payload["ready"] is True
    assert payload["quiescent"] is False
    assert payload["http"]["active_turns"] == 2


def test_stack_health_detects_active_durable_attempt(monkeypatch):
    monkeypatch.setattr(stack, "_probe_sidecar", lambda: _sidecar())
    monkeypatch.setattr(
        stack, "_probe_durable_activity",
        lambda: {
            "schema_version": "metnos.durable-activity/1",
            "known": True,
            "reason_code": "none",
            "active_attempts": 2,
            "leased_attempts": 0,
            "running_attempts": 2,
        },
    )
    monkeypatch.setattr(
        stack._contract, "source_status",
        lambda: {"contract_aligned": True},
    )
    monkeypatch.setattr(
        stack.TurnEventLog, "get",
        classmethod(lambda _cls: SimpleNamespace(stats=lambda: {"active": 0})),
    )

    payload = json.loads(asyncio.run(stack.stack_health(_Request())).body)

    assert payload["quiescent"] is False
    assert payload["durable_workloads"]["active_attempts"] == 2


def test_stack_health_fails_closed_when_durable_activity_is_unknown(monkeypatch):
    monkeypatch.setattr(stack, "_probe_sidecar", lambda: _sidecar())
    monkeypatch.setattr(
        stack, "_probe_durable_activity",
        lambda: {
            "schema_version": "metnos.durable-activity/1",
            "known": False,
            "reason_code": "database_unavailable",
            "active_attempts": 0,
            "leased_attempts": 0,
            "running_attempts": 0,
        },
    )
    monkeypatch.setattr(
        stack._contract, "source_status",
        lambda: {"contract_aligned": True},
    )
    monkeypatch.setattr(
        stack.TurnEventLog, "get",
        classmethod(lambda _cls: SimpleNamespace(stats=lambda: {"active": 0})),
    )

    payload = json.loads(asyncio.run(stack.stack_health(_Request())).body)

    assert payload["quiescent"] is False
    assert payload["durable_workloads"]["known"] is False


def test_stack_health_fails_readiness_on_contract_drift(monkeypatch):
    monkeypatch.setattr(
        stack, "_probe_sidecar",
        lambda: _sidecar(ok=False, contract_aligned=False),
    )
    monkeypatch.setattr(
        stack._contract, "source_status",
        lambda: {"contract_aligned": True},
    )
    monkeypatch.setattr(
        stack.TurnEventLog, "get",
        classmethod(lambda _cls: SimpleNamespace(stats=lambda: {"active": 0})),
    )
    payload = json.loads(asyncio.run(stack.stack_health(_Request())).body)
    assert payload["ready"] is False


def test_stack_health_is_admin_only():
    response = asyncio.run(stack.stack_health(_Request(role="user")))
    assert response.status == 403

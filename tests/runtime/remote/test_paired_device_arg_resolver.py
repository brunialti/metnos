from __future__ import annotations

from types import SimpleNamespace
from contextlib import nullcontext

from paired_device_arg_resolver import resolve_paired_device_args


def _device(device_id: str, name: str, owner: str = "owner-a"):
    return SimpleNamespace(id=device_id, name=name, owner_user_id=owner)


def _registry(monkeypatch, devices):
    import sys

    registry = SimpleNamespace(
        owner_id_for_actor=lambda actor: {
            "actor-a": "owner-a", "actor-b": "owner-b",
        }.get(actor, actor),
        list_devices=lambda: list(devices),
    )
    monkeypatch.setitem(sys.modules, "devices", registry)
    return registry


def _schema(projection="name", mode="exact"):
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "paired_device_identity": projection,
                "paired_device_identity_mode": mode,
            },
        },
    }


def test_schema_without_declaration_does_not_read_registry(monkeypatch):
    import sys

    registry = SimpleNamespace(
        owner_id_for_actor=lambda _actor: (_ for _ in ()).throw(AssertionError()),
        list_devices=lambda: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setitem(sys.modules, "devices", registry)
    args = {"target": "PC-ROBERTO"}
    assert resolve_paired_device_args(
        args, {"type": "object", "properties": {}}, actor="actor-a",
    ) is args


def test_exact_name_resolves_to_stable_id_for_same_owner(monkeypatch):
    _registry(monkeypatch, [
        _device("device-a", "PC-ROBERTO"),
        _device("device-b", "PC-ALTRO", "owner-b"),
    ])
    result = resolve_paired_device_args(
        {"target": "pc-roberto"}, _schema("id"), actor="actor-a",
    )
    assert result == {"target": "device-a"}


def test_token_mode_canonicalizes_registered_name_inside_argument(monkeypatch):
    _registry(monkeypatch, [_device("device-a", "PC-ROBERTO")])
    result = resolve_paired_device_args(
        {"target": "ping -c 4 pc-roberto"},
        _schema("name", "token"), actor="actor-a",
    )
    assert result == {"target": "ping -c 4 PC-ROBERTO"}


def test_stable_id_can_project_back_to_curated_name(monkeypatch):
    _registry(monkeypatch, [_device("device-a", "PC-ROBERTO")])
    result = resolve_paired_device_args(
        {"target": "ping device-a"},
        _schema("name", "token"), actor="actor-a",
    )
    assert result == {"target": "ping PC-ROBERTO"}


def test_other_owner_identity_is_not_available(monkeypatch):
    _registry(monkeypatch, [_device("device-b", "PC-ALTRO", "owner-b")])
    args = {"target": "pc-altro"}
    assert resolve_paired_device_args(
        args, _schema("id"), actor="actor-a",
    ) == args


def test_duplicate_name_is_ambiguous_and_remains_unresolved(monkeypatch):
    _registry(monkeypatch, [
        _device("device-a", "PC-DUP"),
        _device("device-b", "pc-dup"),
    ])
    args = {"target": "PC-DUP"}
    assert resolve_paired_device_args(
        args, _schema("id"), actor="actor-a",
    ) == args


def test_nested_and_list_declarations_use_same_resolver(monkeypatch):
    _registry(monkeypatch, [_device("device-a", "PC-ROBERTO")])
    schema = {
        "type": "object",
        "properties": {
            "requests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "targets": {
                            "type": "array",
                            "paired_device_identity": "id",
                        },
                    },
                },
            },
        },
    }
    result = resolve_paired_device_args(
        {"requests": [{"targets": ["pc-roberto", "example.net"]}]},
        schema, actor="actor-a",
    )
    assert result == {
        "requests": [{"targets": ["device-a", "example.net"]}],
    }


def test_verb_unique_invocation_resolves_before_dispatch(monkeypatch):
    _registry(monkeypatch, [_device("device-a", "PC-ROBERTO")])
    import executor_scheduler
    import executor_workers
    import loader
    from agent_runtime import invoke_tool_by_name

    observed = {}

    def invoke(_verb, *, caller, **kwargs):
        observed.update(kwargs)
        assert caller == "agent_runtime"
        return {"ok": True}

    monkeypatch.setattr(loader, "boot_register_verb_unique_builtins", lambda: [])
    monkeypatch.setitem(loader.VERB_UNIQUE_REGISTRY, "device_probe", {
        "expose_to_planner": True,
    })
    monkeypatch.setattr(loader, "invoke_verb_unique", invoke)
    monkeypatch.setattr(executor_scheduler, "assigned_worker_budget", lambda _ex: 1)
    monkeypatch.setattr(
        executor_scheduler, "invoke_scheduled", lambda _ex, callback: callback(),
    )
    monkeypatch.setattr(executor_workers, "worker_budget", lambda _budget: nullcontext())

    executor = SimpleNamespace(
        name="device_probe", args_schema=_schema("name", "token"),
    )
    result = invoke_tool_by_name(
        "device_probe", {"target": "ping pc-roberto"},
        catalog=[executor], actor="actor-a",
    )
    assert result["ok"] is True
    assert observed == {"target": "ping PC-ROBERTO", "actor": "actor-a"}

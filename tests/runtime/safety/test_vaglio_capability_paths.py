"""Catalog authority distinguishes source access from protected writes."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from vaglio import check_executor_guard, guard_check


def _executor(*, name="create_objects_indices", capabilities=None, properties=None):
    return SimpleNamespace(
        name=name, signed_by="fixture-signer", lifecycle="active", dormant=False,
        args_schema={"type": "object", "properties": properties or {
            "base_path": {"type": "string"}, "dst": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        }},
        capabilities=capabilities if capabilities is not None else [
            {"name": "fs:read", "hint": ["arg:base_path"]},
            {"name": "metnos:cache", "hint": ["objects:local"]},
        ],
    )


@pytest.fixture(autouse=True)
def _linux_policy(monkeypatch):
    monkeypatch.setattr("platform_policy.current_os", lambda: "linux")


@pytest.mark.parametrize("path", ["/var/lib/app/user_data/Images", "/usr/share/icons", "/etc/hosts"])
def test_verified_read_binding_allows_source_under_protected_tree(path):
    ex = _executor()
    args = {"base_path": path}
    before = deepcopy(args)
    assert guard_check(ex.name, args, executor=ex) == (True, None)
    assert args == before
    assert guard_check(ex.name, args)[0] is False


def test_array_read_binding_is_exact_and_does_not_exempt_nested_destinations():
    ex = _executor(capabilities=[{"name": "fs:read", "hint": ["arg:paths"]}])
    args = {"paths": ["/usr/share/icons", "/var/lib/photos"]}
    assert guard_check(ex.name, args, executor=ex)[0] is True
    args["entries"] = [{"path": "/var/lib/photos"}]
    assert guard_check(ex.name, args, executor=ex)[0] is False


@pytest.mark.parametrize("field,value", [
    ("signed_by", ""), ("signed_by", None), ("signed_by", "(verify disabled)"),
    ("signed_by", "?"), ("lifecycle", "proposed"), ("dormant", True),
    ("name", "another_executor"),
])
def test_unavailable_or_unverified_catalog_cannot_grant_exemption(field, value):
    ex = _executor()
    name = ex.name
    setattr(ex, field, value)
    assert guard_check(name, {"base_path": "/var/photos"}, executor=ex)[0] is False


def test_caller_context_and_arguments_cannot_supply_catalog_authority():
    ex = _executor()
    fake = {"name": ex.name, "signed_by": ex.signed_by,
            "capabilities": ex.capabilities, "args_schema": ex.args_schema}
    args = {"base_path": "/var/photos", "executor": fake,
            "capabilities": ex.capabilities, "readonly_args": ["base_path"]}
    assert guard_check(ex.name, args, {"executor": ex})[0] is False
    assert guard_check(ex.name, args, executor=fake)[0] is False


@pytest.mark.parametrize("hint,properties,value", [
    (["/var/**"], {"base_path": {"type": "string"}}, "/var/photos"),
    (["arg:other"], {"base_path": {"type": "string"}}, "/var/photos"),
    (["arg:base_path:recursive"], {"base_path": {"type": "string"}}, "/var/photos"),
    (["arg:base_path"], {"base_path": {"type": ["string", "object"]}}, "/var/photos"),
    (["arg:base_path"], {"base_path": {"type": "object"}}, {"path": "/var/photos"}),
    (["arg:base_path"], {"base_path": {"type": "array", "items": {"type": "object"}}},
     [{"path": "/var/photos"}]),
    (["arg:base_path"], {"base_path": {"type": "string"}}, ["/var/photos"]),
])
def test_untyped_or_ambiguous_read_bindings_do_not_relax_guard(hint, properties, value):
    ex = _executor(capabilities=[{"name": "fs:read", "hint": hint}], properties=properties)
    assert guard_check(ex.name, {"base_path": value}, executor=ex)[0] is False


@pytest.mark.parametrize("path", [
    "~/.ssh/id_rsa", "/etc/shadow", "/etc/passwd", "/root/private",
    "/home/user/.aws/credentials", "/home/user/.config/app/credentials.env",
    "/home/user/.gnupg/private-keys", "/var/photos/../../etc/shadow",
])
def test_read_authority_never_exempts_secret_paths(path):
    ex = _executor()
    ok, why = guard_check(ex.name, {"base_path": path}, executor=ex)
    assert ok is False
    assert "forbidden" in why


def test_read_alias_to_credentials_is_still_forbidden(tmp_path):
    source = tmp_path / "photos"
    source.symlink_to(tmp_path / ".ssh", target_is_directory=True)
    ex = _executor()
    assert guard_check(ex.name, {"base_path": str(source)}, executor=ex)[0] is False


def test_declared_path_named_content_is_access_not_prose():
    ex = _executor(properties={"content": {"type": "string"}}, capabilities=[
        {"name": "fs:read", "hint": ["arg:content"]},
    ])
    assert guard_check(ex.name, {"content": "/etc/hosts"}, executor=ex)[0] is True
    assert guard_check(ex.name, {"content": "~/.ssh/id_rsa"}, executor=ex)[0] is False
    ex.capabilities = [{"name": "fs:write", "hint": ["arg:content"]}]
    assert guard_check(ex.name, {"content": "/etc/hosts"}, executor=ex)[0] is False


def test_mixed_read_and_write_bindings_protect_destinations_by_argument_identity():
    ex = _executor(capabilities=[
        {"name": "fs:read", "hint": ["arg:base_path"]},
        {"name": "fs:write", "hint": ["arg:dst"]},
    ])
    assert guard_check(ex.name, {"base_path": "/var/photos", "dst": "/tmp/index"}, executor=ex)[0]
    assert not guard_check(ex.name, {"base_path": "/var/photos", "dst": "/var/photos"}, executor=ex)[0]
    assert not guard_check(ex.name, {"base_path": "/tmp/photos", "dst": "/etc/index"}, executor=ex)[0]


@pytest.mark.parametrize("write", [
    {"name": "fs:write", "hint": ["arg:base_path"]},
    {"name": "fs:write", "hint": ["/tmp/**"]},
    {"name": "fs:write", "hint": []},
    {"name": "fs:write", "hint": ["arg:missing"]},
    {"name": "fs:write", "hint": ["arg:dst"], "when": {"not_a_predicate": True}},
    {"name": "fs:create", "hint": ["arg:dst"]},
    {"name": "code:exec", "hint": ["python"]},
])
def test_conflicting_or_unbounded_write_authority_is_conservative(write):
    ex = _executor(capabilities=[{"name": "fs:read", "hint": ["arg:base_path"]}, write])
    assert not guard_check(ex.name, {"base_path": "/var/photos", "dst": "/tmp/index"}, executor=ex)[0]


def test_write_capability_is_checked_even_for_a_read_named_executor():
    ex = _executor(name="read_objects", capabilities=[
        {"name": "fs:write", "hint": ["arg:dst"]},
    ])
    assert not guard_check(ex.name, {"dst": "/var/output"}, executor=ex)[0]


def test_conditional_read_and_write_use_final_arguments_and_declared_defaults():
    ex = _executor(properties={
        "base_path": {"type": "string", "default": "/var/photos"},
        "mode": {"type": "string", "enum": ["read", "write"], "default": "read"},
    }, capabilities=[
        {"name": "fs:read", "hint": ["arg:base_path"], "when": {"arg": "mode", "values": ["read"]}},
        {"name": "fs:write", "hint": ["arg:base_path"], "when": {"arg": "mode", "values": ["write"]}},
    ])
    assert guard_check(ex.name, {}, executor=ex)[0]
    assert not guard_check(ex.name, {"mode": "write"}, executor=ex)[0]
    assert not guard_check(ex.name, {"mode": "other", "base_path": "/var/photos"}, executor=ex)[0]


def test_adapter_preserves_explicit_two_argument_callbacks_without_retry():
    calls = []
    def legacy(name, args):
        calls.append((name, args))
        return False, "custom_denial"
    assert check_executor_guard(legacy, "create_objects", {}, executor=_executor()) == (False, "custom_denial")
    assert calls == [("create_objects", {})]
    def broken(name, args):
        calls.append((name, args))
        raise TypeError("guard failure is not a signature negotiation")
    with pytest.raises(TypeError):
        check_executor_guard(broken, "create_objects", {}, executor=_executor())
    assert len(calls) == 2


def test_standard_guard_adapter_binds_verified_executor():
    ex = _executor()
    assert check_executor_guard(guard_check, ex.name, {"base_path": "/var/photos"}, executor=ex)[0]

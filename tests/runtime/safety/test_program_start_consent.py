"""Reusable launch permissions are scoped, revocable and submission-owned."""
from __future__ import annotations

import copy
import time
from types import SimpleNamespace

import pytest

import policy
import program_start_consent as consent
from executor_helpers import approval_digest


@pytest.fixture
def context(tmp_path, monkeypatch):
    import devices
    import loader
    monkeypatch.setenv("METNOS_GRANTS_DB", str(tmp_path / "grants.db"))
    executor = SimpleNamespace(name="run_processes_alternate", capabilities=[{
        "name": "system:admin", "hint": ["managed-package-start"]}],
        args_schema={"properties": {key: {"runtime_resolved": True} for key in (
            "authorization_scope", "authorization_boot_id", "actor_consent_token", "lifetime")}})
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda actor: "owner-a")
    monkeypatch.setattr(devices, "get_device", lambda identity: (
        SimpleNamespace(owner_user_id="owner-a") if identity == "pc-id" else None))
    monkeypatch.setattr(loader, "load_catalog", lambda **kwargs: SimpleNamespace(
        executors={executor.name: executor}))
    return executor


def _state(executor, scope="until_restart", boot="133700000000000000", source="http_form_owner"):
    args = {"programs": ["Vendor.App"], "lifetime": "session",
            "authorization_scope": scope,
            "authorization_boot_id": boot if scope == "until_restart" else ""}
    args["actor_consent_token"] = approval_digest(args)
    return {"owner_user_id": "owner-a", "completed": True, "cancelled": False,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expires_at": time.time() + 600,
            "created_at": time.time(), "timeout_s": 600,
            "values_collected": {"decision": scope},
            "submissions": {"decision": {"source": source}},
            "on_complete": {"owner_user_id": "owner-a", "type": "gate_dispatch",
                            "target_device": "pc-id", "branches": {
                                scope: {"tool": executor.name, "args": args}}}}


def _apply(executor, programs=None, **kwargs):
    return consent.apply_saved(executor, {"programs": programs or ["Vendor.App"]},
                               owner=kwargs.get("owner", "owner-a"),
                               device_id=kwargs.get("device", "pc-id"))


@pytest.mark.parametrize("scope", ["until_restart", "always"])
@pytest.mark.parametrize("source", ["http_form_owner", "telegram_button", "telegram_chat"])
def test_reuse_survives_reopen_and_channel_change(context, scope, source):
    consent.remember_verified_dialog(_state(context, scope, source=source), actor="host", owner="owner-a")
    first = _apply(context)
    assert first["authorization_scope"] == scope
    assert first["lifetime"] == "session"  # never automatic startup
    assert first == _apply(context)  # a close/reopen doesn't consume the grant
    assert first["actor_consent_token"] == approval_digest(consent._material(first))


def test_application_device_owner_and_executor_are_separate(context):
    consent.remember_verified_dialog(_state(context, "always"), actor="host", owner="owner-a")
    for kwargs in ({"programs": ["Vendor.Other"]}, {"device": "other-pc"},
                   {"owner": "owner-b"}, {"programs": ["Vendor.App", "Vendor.Other"]}):
        assert "actor_consent_token" not in _apply(context, **kwargs)
    another = copy.copy(context)
    another.name = "another_starter"
    assert "actor_consent_token" not in _apply(another)
    another.name = context.name
    another.capabilities = [{"name": "system:admin", "hint": ["registered-package-state"]}]
    assert "actor_consent_token" not in _apply(another)


def test_existing_revocation_stops_reuse(context):
    consent.remember_verified_dialog(_state(context, "always"), actor="host", owner="owner-a")
    grant = policy.list_grants()[0]
    assert policy.revoke_grant(grant.id)
    assert "actor_consent_token" not in _apply(context)


@pytest.mark.parametrize("change", [
    {"completed": False}, {"cancelled": True}, {"submissions": {}},
    {"owner_user_id": "owner-b"}, {"values_collected": {"decision": "session"}},
    {"started_at": "2000-01-01T00:00:00Z"},
])
def test_historical_or_unsubmitted_flags_do_not_create_permission(context, change):
    state = _state(context)
    state.update(change)
    consent.remember_verified_dialog(state, actor="host", owner="owner-a")
    assert policy.list_grants() == []


def test_once_does_not_create_reusable_permission(context):
    consent.remember_verified_dialog(_state(context, "once"), actor="host", owner="owner-a")
    assert "actor_consent_token" not in _apply(context)


def test_declared_device_name_cannot_replace_runtime_uuid(context):
    state = _state(context, "always")
    state["on_complete"]["target_device"] = "renamed-pc"
    consent.remember_verified_dialog(state, actor="host", owner="owner-a")
    assert policy.list_grants() == []


def test_runtime_binds_prompt_to_observed_device(context):
    result = {"needs_inputs": {"on_complete": {"type": "gate_dispatch", "target_device": "untrusted-name"}}}
    assert consent.bind_prompt(context, result, device_id="pc-id")["needs_inputs"]["on_complete"]["target_device"] == "pc-id"


def test_reapproval_replaces_old_boot_without_duplicate_active_grants(context):
    consent.remember_verified_dialog(_state(context, boot="100"), actor="host", owner="owner-a")
    consent.remember_verified_dialog(_state(context, boot="200"), actor="host", owner="owner-a")
    assert _apply(context)["authorization_boot_id"] == "200"
    assert len(policy.list_grants()) == 1
    assert len(policy.list_grants(include_revoked=True)) == 2


def test_permission_never_authorizes_startup_registration(context):
    consent.remember_verified_dialog(_state(context, "always"), actor="host", owner="owner-a")
    args = {"programs": ["Vendor.App"], "lifetime": "persistent"}
    assert consent.apply_saved(context, args, owner="owner-a", device_id="pc-id") == args


@pytest.mark.parametrize("scope", ["until_restart", "always"])
def test_common_remote_invocation_reuses_grant_and_binds_observed_pc(context, monkeypatch, scope):
    import agent_runtime
    import devices
    import remote_exec
    from timefmt import now_iso_z
    context.placement = {"scope": "device"}
    context.platforms = ["windows"]
    context.skills = []
    device = SimpleNamespace(id="pc-id", name="Renamed PC", owner_user_id="owner-a",
                             os_family="windows", last_heartbeat=now_iso_z(), revoked_at=None)
    monkeypatch.setattr(devices, "list_devices", lambda: [device])
    monkeypatch.setattr(agent_runtime, "_undo_pending", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "_undo_done", lambda *args, **kwargs: None)
    calls = []
    def remote(executor, args, target, **kwargs):
        calls.append((args, target))
        return {"ok": True, "needs_inputs": {"on_complete": {"type": "gate_dispatch"}}}
    monkeypatch.setattr(remote_exec, "invoke_remote", remote)
    consent.remember_verified_dialog(_state(context, scope), actor="host", owner="owner-a")
    for channel in ("http", "telegram"):
        result = agent_runtime._invoke_executor_impl(
            context, {"programs": ["Vendor.App"]}, actor="host", channel=channel,
            target_device="Renamed PC")
        assert calls[-1][0]["authorization_scope"] == scope
        assert calls[-1][1] == "pc-id"
        assert result["needs_inputs"]["on_complete"]["target_device"] == "pc-id"


@pytest.mark.parametrize("channel,source", [("http", "http_form_owner"),
                                            ("telegram", "telegram_button")])
@pytest.mark.parametrize("scope", ["until_restart", "always"])
def test_real_dialog_callback_records_permission_once(context, tmp_path, monkeypatch,
                                                       channel, source, scope):
    import dialog_pending as dp
    import orchestration
    monkeypatch.setattr(dp, "DIALOG_DIR", tmp_path / "dialogs")
    sender, dialog_id = channel + ":host:test", "program-start"
    state = _state(context, scope)
    state.update({"dialog_id": dialog_id, "origin_turn_id": "origin",
                  "completed": False, "step_index": 0, "values_collected": {},
                  "submissions": {}, "dialog": [{"var": "decision", "schema": {
                      "kind": "choice", "choices": [{"value": scope, "label": scope}]}}]})
    dp.save_pending(sender, dialog_id, state)
    calls = []
    def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "summary": "Started."}
    monkeypatch.setattr(orchestration, "_esegui_ramo", launch)
    assert "actor_consent_token" not in _apply(context)
    assert dp.consume_pending_step(sender, dialog_id, "decision", scope,
                                   owner_user_id="owner-a", source=source)["completed"]
    first = orchestration.process_completion_callback(
        sender, dialog_id, owner_user_id="owner-a", actor="host", channel=channel)
    assert first.text == "Started."
    assert _apply(context)["authorization_scope"] == scope
    second = orchestration.process_completion_callback(
        sender, dialog_id, owner_user_id="owner-a", actor="host", channel=channel)
    assert second == first and len(calls) == 1
    assert len(policy.list_grants(include_revoked=True)) == 1

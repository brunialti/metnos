"""Managed package start remains consented, typed, and i18n-complete."""
from __future__ import annotations

import os

import pytest

from executors.run_processes import run_processes
from runtime.device_shim import messages as shim_messages


@pytest.fixture
def windows(monkeypatch):
    calls = []

    def helper(*arguments):
        calls.append(arguments)
        if arguments[0] == "query":
            return {"ok": True, "aligned": True}
        if arguments[0] == "stop":
            return {"ok": True, "aligned": True,
                    "payload": {"restored": True, "stopped": True}}
        lifetime = arguments[-1]
        return {
            "ok": True,
            "aligned": True,
            "payload": ({
                "created_process": True,
                "process": {"pid": 4242, "creation_time": 133700000000000000},
                "persistent_registration_changed": False,
            } if lifetime == "session" else {
                "created_process": True,
                "persistent_registration_changed": True,
            }),
        }

    monkeypatch.setattr(run_processes.sys, "platform", "win32")
    monkeypatch.setattr(run_processes, "_helper_call", helper)
    monkeypatch.setattr(run_processes, "_machine_name", lambda: "PC-TEST")
    monkeypatch.setattr(run_processes, "_boot_id", lambda: "133700000000000000")
    return calls


def _approved(package_ids, lifetime, scope="once", boot_id=""):
    return {
        "programs": package_ids,
        "lifetime": lifetime,
        "authorization_scope": scope,
        "authorization_boot_id": boot_id,
        "actor_consent_token": run_processes._consent_token(package_ids, lifetime, scope, boot_id),
    }


def test_phase_one_only_queries_and_returns_permission_duration_choices(windows):
    result = run_processes.invoke({"programs": ["Vendor.Sensor"]})

    assert result["decision"] == "needs_inputs"
    assert result["started"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert windows == [("query", "--package-id", "Vendor.Sensor")]
    choices = result["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert [choice["value"] for choice in choices] == [
        "once", "until_restart", "always", "reject",
    ]
    for branch in result["needs_inputs"]["on_complete"]["branches"].values():
        assert branch["args"]["lifetime"] == "session"


def test_until_restart_can_be_reused_for_multiple_launches(windows):
    args = _approved(["Vendor.Sensor"], "session", "until_restart", "133700000000000000")
    for _ in range(2):
        result = run_processes.invoke(args)
        assert result["ok"] and result["started"]
        assert "needs_inputs" not in result
    assert windows == [("start", "--package-id", "Vendor.Sensor", "--lifetime", "session")] * 2


def test_until_restart_expires_on_pc_boot_change_before_any_launch(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_boot_id", lambda: "200")
    result = run_processes.invoke(_approved(["Vendor.Sensor"], "session", "until_restart", "100"))
    assert result["decision"] == "needs_inputs"
    assert not result["started"] and result["_undo"]["outcome"] == "no_effect"
    assert windows == [("query", "--package-id", "Vendor.Sensor")]
    branch = result["needs_inputs"]["on_complete"]["branches"]["until_restart"]
    assert branch["args"]["authorization_boot_id"] == "200"


def test_always_does_not_expire_with_pc_or_metnos_restart(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_boot_id", lambda: pytest.fail("permanent permission must not depend on boot"))
    args = _approved(["Vendor.Sensor"], "session", "always")
    assert run_processes.invoke(args)["started"]
    assert run_processes.invoke(dict(args))["started"]


def test_boot_unavailable_cannot_reuse_temporary_permission(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_boot_id", lambda: "")
    result = run_processes.invoke(_approved(["Vendor.Sensor"], "session", "until_restart", "100"))
    assert result["error_code"] == "boot_unverified"
    assert windows == []


@pytest.mark.parametrize("field,value", [("authorization_scope", "always"),
                                         ("authorization_boot_id", "200")])
def test_permission_duration_and_boot_are_bound_to_the_reviewed_choice(windows, field, value):
    args = _approved(["Vendor.Sensor"], "session", "until_restart", "100")
    args[field] = value
    assert run_processes.invoke(args)["error_code"] == "consent_invalid"
    assert windows == []


def test_boot_probe_is_fixed_local_and_locale_independent(tmp_path, monkeypatch):
    import base64
    from types import SimpleNamespace
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    calls = []
    def execute(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="133700000000000000\n")
    monkeypatch.setattr(run_processes.subprocess, "run", execute)
    assert run_processes._boot_id() == "133700000000000000"
    args, options = calls[0]
    source = base64.b64decode(args[-1]).decode("utf-16le")
    assert "Win32_OperatingSystem" in source and "LastBootUpTime" in source
    assert "ToUniversalTime().ToFileTimeUtc()" in source
    assert "-ComputerName" not in source
    assert options["shell"] is False and options["timeout"] == 10


def test_portable_provider_does_not_import_unrelated_desktop_dependencies(windows, monkeypatch):
    import builtins
    original = builtins.__import__
    def import_module(name, *args, **kwargs):
        if name == "windows_desktop_apps":
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", import_module)
    assert run_processes.invoke({"programs": ["Vendor.Sensor"]})["decision"] == "needs_inputs"


@pytest.mark.parametrize(
    ("language", "session_text", "persistent_text"),
    [
        ("it", "riavvio", "sempre"),
        ("en", "restart", "always"),
    ],
)
def test_consent_card_is_localized(windows, monkeypatch, language,
                                   session_text, persistent_text):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", language)
    result = run_processes.invoke({"programs": ["Vendor.Sensor"]})
    choices = result["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert session_text in choices[1]["label"].lower()
    assert persistent_text in choices[2]["label"].lower()
    assert "PC-TEST" in result["needs_inputs"]["description"]


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize(("code", "message_key"), [
    ("package_not_registered", "ERR_CREATE_PROCESSES_TARGET_MISSING"),
    ("package_operation_failed", "ERR_CREATE_PROCESSES_START_FAILED"),
])
def test_launch_lookup_failure_does_not_claim_package_is_absent(
        windows, monkeypatch, language, code, message_key):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", language)
    monkeypatch.setattr(run_processes, "_helper_call", lambda *_: {
        "ok": False, "aligned": True, "error_code": code,
    })
    result = run_processes.invoke(_approved(["Vendor.DesktopApp"], "session"))
    assert result["ok"] is False and result["ok_count"] == 0
    failure = result["failed"][0]
    assert failure["error_code"] == code
    assert failure["error"] == shim_messages.get(
        message_key, package="Vendor.DesktopApp", code=code)
    assert failure["error"] != shim_messages.get(
        "ERR_CREATE_PROCESSES_NOT_INSTALLED", package="Vendor.DesktopApp")


@pytest.mark.parametrize("lifetime", ["session", "persistent"])
def test_approved_choice_starts_only_the_exact_package(windows, lifetime):
    result = run_processes.invoke(_approved(["Vendor.Sensor"], lifetime))

    assert result["ok"] is True
    assert result["ok_count"] == 1
    assert windows == [(
        "start", "--package-id", "Vendor.Sensor", "--lifetime", lifetime,
    )]
    assert result["results"][0]["package_id"] == "Vendor.Sensor"
    assert result["_undo"]["outcome"] == (
        "reversible" if lifetime == "session" else "irreversible")


def test_session_undo_stops_only_exact_receipt_identity(windows):
    forward = run_processes.invoke(
        _approved(["Vendor.Sensor"], "session"))

    result = run_processes.reverse({}, forward)

    assert result["ok"] is True
    assert result["results"] == [{
        "package_id": "Vendor.Sensor",
        "pid": 4242,
        "ok": True,
        "stopped": True,
    }]
    assert windows[-1] == (
        "stop", "--package-id", "Vendor.Sensor",
        "--pid", "4242", "--creation-time", "133700000000000000",
    )


def test_appx_uses_user_session_client_and_offers_permission_scopes(monkeypatch, windows):
    calls = []
    package_id = (
        "appx:Microsoft.WindowsNotepad_11.2606.15.0_x64__8wekyb3d8bbwe")

    def appx(*arguments):
        calls.append(arguments)
        if arguments[0] == "query":
            return {"ok": True, "lifetimes": ["session"]}
        if arguments[0] == "stop":
            return {"ok": True,
                    "payload": {"restored": True, "stopped": True}}
        return {
            "ok": True,
            "payload": {
                "created_process": True,
                "process": {"pid": 5151, "creation_time": 133700000000000001},
                "activation_boundary": 133700000000000000,
                "preexisting_processes": [{
                    "pid": 4040,
                    "creation_time": 133600000000000000,
                }],
                "persistent_registration_changed": False,
            },
        }

    monkeypatch.setattr(run_processes.sys, "platform", "win32")
    monkeypatch.setattr(run_processes, "_appx_call", appx)
    monkeypatch.setattr(
        run_processes, "_helper_call",
        lambda *_: (_ for _ in ()).throw(AssertionError("helper must not run")),
    )
    monkeypatch.setattr(run_processes, "_machine_name", lambda: "PC-TEST")

    phase_one = run_processes.invoke({"programs": [package_id]})
    choices = phase_one["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert [choice["value"] for choice in choices] == ["once", "until_restart", "always", "reject"]
    assert set(phase_one["needs_inputs"]["on_complete"]["branches"]) == {"once", "until_restart", "always"}
    assert calls == [("query", "--package-id", package_id)]

    forward = run_processes.invoke(_approved([package_id], "session"))
    assert forward["ok"] is True
    assert forward["_undo"]["outcome"] == "reversible"
    assert calls[-1] == (
        "start", "--package-id", package_id, "--lifetime", "session")

    reversed_result = run_processes.reverse({}, forward)
    assert reversed_result["ok"] is True
    assert calls[-1] == (
        "stop", "--package-id", package_id,
        "--pid", "5151", "--creation-time", "133700000000000001",
        "--activation-boundary", "133700000000000000",
        "--preexisting-process", "4040:133600000000000000")


def test_reverse_rejects_provider_ok_without_restored_postcondition(
        windows, monkeypatch):
    forward = run_processes.invoke(
        _approved(["Vendor.Sensor"], "session"))
    monkeypatch.setattr(run_processes, "_helper_call", lambda *_: {
        "ok": True,
        "payload": {"stopped": False},
    })

    result = run_processes.reverse({}, forward)

    assert result["ok"] is False
    assert result["ok_count"] == 0
    assert result["fail_count"] == 1
    assert result["failed"][0]["error_class"] == "postcondition_failed"


def test_reverse_accepts_attested_already_restored_state(windows, monkeypatch):
    forward = run_processes.invoke(
        _approved(["Vendor.Sensor"], "session"))
    monkeypatch.setattr(run_processes, "_helper_call", lambda *_: {
        "ok": True,
        "payload": {"restored": True, "stopped": False},
    })

    result = run_processes.reverse({}, forward)

    assert result["ok"] is True
    assert result["results"][0]["stopped"] is False


def test_appx_persistent_choice_is_rejected_before_activation(monkeypatch):
    package_id = "appx:Vendor.App_1.0.0.0_x64__publisher"
    calls = []
    monkeypatch.setattr(run_processes.sys, "platform", "win32")
    monkeypatch.setattr(run_processes, "_appx_call", lambda *args: calls.append(args))

    result = run_processes.invoke(_approved([package_id], "persistent"))

    assert result["error_code"] == "consent_invalid"
    assert result["_undo"] == {"outcome": "no_effect"}
    assert calls == []


def test_desktop_requires_consent_visible_window_and_exact_undo(monkeypatch, windows):
    import windows_desktop_apps as desktop
    package_id = "desktop:" + "a" * 64
    calls = []

    def call(package, operation, *arguments):
        calls.append((package, operation, *arguments))
        if operation == "query":
            return {"ok": True, "name": "Éditeur"}
        if operation == "stop":
            return {"ok": True, "payload": {"restored": True, "stopped": True}}
        return {"ok": True, "payload": {
            "visible_window": True, "created_process": True,
            "process": {"pid": 42, "creation_time": 200},
            "activation_boundary": 190, "preexisting_processes": [],
        }}

    monkeypatch.setattr(desktop, "call", call)
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", "it")
    gate = run_processes.invoke({"programs": [package_id]})
    assert calls == [(package_id, "query")]
    assert gate["started"] is False and gate["_undo"]["outcome"] == "no_effect"
    card = gate["needs_inputs"]
    assert "Éditeur" in card["description"] and "PC-TEST" in card["description"]
    assert [c["value"] for c in card["dialog"][0]["schema"]["choices"]] == ["once", "until_restart", "always", "reject"]
    assert run_processes.invoke(_approved([package_id], "persistent"))["error_code"] == "consent_invalid"
    assert len(calls) == 1
    started = run_processes.invoke(_approved([package_id], "session"))
    assert started["ok"] and started["_undo"]["outcome"] == "reversible"
    assert run_processes.reverse({}, started)["ok"]
    assert calls[-1] == (package_id, "stop", "--pid", "42", "--creation-time", "200",
                         "--activation-boundary", "190")
    assert windows == []


@pytest.mark.parametrize("answer", [
    {"ok": False, "error_code": "package_start_unverified", "effects_attempted": True},
    {"ok": True, "payload": {"created_process": False}},
])
def test_unverified_desktop_start_is_neither_success_nor_no_effect(monkeypatch, windows, answer):
    import windows_desktop_apps as desktop
    monkeypatch.setattr(desktop, "call", lambda *_: answer)
    out = run_processes.invoke(_approved(["desktop:" + "a" * 64], "session"))
    assert out["ok"] is False and out["started"] is False
    assert out["_undo"] == {"outcome": "irreversible"}


def test_already_running_session_is_no_effect(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_helper_call", lambda *_: {
        "ok": True, "aligned": True,
        "payload": {"created_process": False,
                    "persistent_registration_changed": False},
    })

    result = run_processes.invoke(
        _approved(["Vendor.Sensor"], "session"))

    assert result["_undo"] == {"outcome": "no_effect"}
    assert result["results"][0]["already_running"] is True


def test_session_without_strong_process_identity_fails_closed(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_helper_call", lambda *_: {
        "ok": True, "aligned": True,
        "payload": {"created_process": True},
    })

    result = run_processes.invoke(
        _approved(["Vendor.Sensor"], "session"))

    assert result["ok"] is False
    assert result["_undo"] == {"outcome": "irreversible"}


def test_consent_is_bound_to_package_and_lifetime(windows):
    args = _approved(["Vendor.Sensor"], "session")
    args["lifetime"] = "persistent"

    result = run_processes.invoke(args)

    assert result["error_code"] == "consent_invalid"
    assert windows == []


@pytest.mark.parametrize("value", [
    r"C:\Windows\System32\cmd.exe",
    r"MSIX\Microsoft.WindowsNotepad_11.0_x64__8wekyb3d8bbwe",
    "../tool.exe",
    "Vendor.App --flag",
    "Vendor.*",
])
def test_paths_commands_and_patterns_are_rejected_before_helper(windows, value):
    result = run_processes.invoke({"programs": [value]})

    assert result["error_code"] == "invalid_package_id"
    assert result["_undo"] == {"outcome": "no_effect"}
    assert windows == []


def test_typed_appx_identity_is_not_treated_as_a_path(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_processes, "_appx_call",
        lambda *args: calls.append(args) or {"ok": True})
    package_id = "appx:Vendor.App_1.0.0.0_x64__publisher"

    result = run_processes.invoke({"programs": [package_id]})

    assert result["decision"] == "needs_inputs"
    assert calls == [("query", "--package-id", package_id)]
    assert windows == []


def test_duplicate_identity_is_rejected_before_helper(windows):
    result = run_processes.invoke({"programs": ["Vendor.App", "Vendor.App"]})
    assert result["error_code"] == "duplicate_package"
    assert windows == []


def test_duplicate_identity_is_case_insensitive(windows):
    result = run_processes.invoke({"programs": ["Vendor.App", "vendor.app"]})
    assert result["error_code"] == "duplicate_package"
    assert windows == []


def test_scalar_programs_value_is_rejected_before_helper(windows):
    result = run_processes.invoke({"programs": "Vendor.App"})
    assert result["error_code"] == "programs_not_list"
    assert windows == []


def test_helper_failure_is_localized_not_raw_provider_text(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", "en")
    monkeypatch.setattr(
        run_processes,
        "_helper_call",
        lambda *_: {
            "ok": False,
            "aligned": True,
            "error_code": "package_target_ambiguous",
            "detail": "raw non-localized provider output",
        },
    )

    result = run_processes.invoke({"programs": ["Vendor.App"]})

    assert result["error_code"] == "package_target_ambiguous"
    assert "more than one" in result["error"]
    assert "raw non-localized" not in result["error"]


def test_protocol_mismatch_is_reported_as_update_not_start_failure(
        windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", "en")
    monkeypatch.setattr(
        run_processes,
        "_helper_call",
        lambda *_: {
            "ok": True,
            "aligned": False,
            "helper_version": "0.2.40",
            "protocol_version": 1,
            "client_protocol_version": 2,
        },
    )

    result = run_processes.invoke({"programs": ["Vendor.App"]})

    assert result["error_code"] == "helper_protocol_mismatch"
    assert "current protocol" in result["error"]
    assert "start failed" not in result["error"].lower()


def test_lazy_helper_update_is_reported_clearly(windows, monkeypatch):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", "en")
    monkeypatch.setattr(
        run_processes,
        "_helper_call",
        lambda *_: {
            "ok": False,
            "aligned": False,
            "error_code": "helper_update_pending",
        },
    )

    result = run_processes.invoke({"programs": ["Vendor.App"]})

    assert result["error_code"] == "helper_update_pending"
    assert "updating" in result["error"]
    assert "start failed" not in result["error"].lower()


def test_vector_result_preserves_success_and_failure(windows, monkeypatch):
    def helper(*arguments):
        package_id = arguments[2]
        if package_id == "Vendor.Bad":
            return {"ok": False, "error_code": "package_start_failed"}
        return {"ok": True, "aligned": True,
                "payload": {"created_process": False,
                            "persistent_registration_changed": False}}

    monkeypatch.setattr(run_processes, "_helper_call", helper)
    packages = ["Vendor.Good", "Vendor.Bad"]
    result = run_processes.invoke(_approved(packages, "session"))

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["ok_count"] == result["fail_count"] == 1
    assert result["results"][0]["already_running"] is True


def test_non_windows_fails_honestly(monkeypatch):
    monkeypatch.setattr(run_processes.sys, "platform", "linux")
    result = run_processes.invoke({"programs": ["Vendor.App"]})
    assert result["error_code"] == "platform_unsupported"


@pytest.mark.parametrize("package,adapter,code", [
    ("Vendor.App", "_helper_call", "helper_not_available"),
    ("appx:Vendor.App_1.0_x64__test", "_appx_call", "platform_unsupported"),
])
def test_linux_client_not_available_keeps_existing_localized_refusal(monkeypatch, package, adapter, code):
    monkeypatch.setattr(run_processes.sys, "platform", "linux")
    monkeypatch.setenv("METNOS_CLIENT_EXE", "/unit-fixture/client")
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    calls = []
    def helper(*arguments):
        calls.append(arguments)
        return {"ok": False, "error_code": code, "detail": "not user text"}
    monkeypatch.setattr(run_processes, adapter, helper)
    result = run_processes.invoke({"programs": [package]})
    assert calls == [("query", "--package-id", package)]
    assert result["error_code"] == "platform_unsupported"
    assert result["error_class"] == "capability_missing"
    assert result["error"] == shim_messages.get("ERR_CREATE_PROCESSES_WINDOWS_ONLY")


def test_new_message_keys_exist_in_both_languages(monkeypatch):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    keys = [
        "MSG_CREATE_PROCESSES_APPROVAL_TITLE",
        "MSG_CREATE_PROCESSES_APPROVAL_DESCRIPTION",
        "MSG_CREATE_PROCESSES_APPROVAL_PROMPT",
        "MSG_CREATE_PROCESSES_BTN_SESSION",
        "MSG_CREATE_PROCESSES_BTN_PERSISTENT",
        "ERR_CREATE_PROCESSES_HELPER_UNAVAILABLE",
        "ERR_CREATE_PROCESSES_HELPER_UPDATE_PENDING",
        "ERR_CREATE_PROCESSES_PROCESS_PROBE_FAILED",
        "ERR_CREATE_PROCESSES_START_FAILED",
        "ERR_CREATE_PROCESSES_CONSENT_INVALID",
        "ERR_CREATE_PROCESSES_STOP_FAILED",
        "ERR_CREATE_PROCESSES_STOP_IDENTITY",
        "ERR_CREATE_PROCESSES_STOP_UNVERIFIED",
    ]
    for language in ("it", "en"):
        monkeypatch.setenv("METNOS_LANG", language)
        for key in keys:
            rendered = run_processes._msg(
                key, packages="Vendor.App", package="Vendor.App",
                machine="PC-TEST", code="test",
            )
            assert not rendered.startswith(key), (language, key, rendered)

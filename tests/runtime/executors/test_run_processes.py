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
                    "payload": {"stopped": True}}
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
    return calls


def _approved(package_ids, lifetime):
    return {
        "programs": package_ids,
        "lifetime": lifetime,
        "actor_consent_token": run_processes._consent_token(package_ids, lifetime),
    }


def test_phase_one_only_queries_and_returns_two_explicit_choices(windows):
    result = run_processes.invoke({"programs": ["Vendor.Sensor"]})

    assert result["decision"] == "needs_inputs"
    assert result["started"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert windows == [("query", "--package-id", "Vendor.Sensor")]
    choices = result["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert [choice["value"] for choice in choices] == [
        "session", "persistent", "reject",
    ]


@pytest.mark.parametrize(
    ("language", "session_text", "persistent_text"),
    [
        ("it", "riavvio", "accensione"),
        ("en", "restart", "startup"),
    ],
)
def test_consent_card_is_localized(windows, monkeypatch, language,
                                   session_text, persistent_text):
    monkeypatch.setattr(run_processes, "_msg", shim_messages.get)
    monkeypatch.setenv("METNOS_LANG", language)
    result = run_processes.invoke({"programs": ["Vendor.Sensor"]})
    choices = result["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert session_text in choices[0]["label"].lower()
    assert persistent_text in choices[1]["label"].lower()
    assert "PC-TEST" in result["needs_inputs"]["description"]


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

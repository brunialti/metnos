"""Desktop closure is device-local, explicit, snapshot-bound and truthful."""
import copy

import pytest
import windows_desktop_apps as desktop
from executors.set_processes import set_processes as closing
from runtime.device_shim import messages as shim_messages

PACKAGE = "desktop:" + "a" * 64
PROCESSES = [{"pid": 41, "creation_time": 133700000000000000}]


@pytest.fixture
def native(monkeypatch):
    calls = []
    monkeypatch.setattr(closing.sys, "platform", "win32")
    monkeypatch.setattr(closing.socket, "gethostname", lambda: "PC-TEST")
    monkeypatch.setattr(closing, "_msg", shim_messages.get)
    monkeypatch.setattr(desktop, "call", lambda *a: calls.append(("query", a)) or {
        "ok": True, "name": "Éditeur", "processes": copy.deepcopy(PROCESSES)})
    monkeypatch.setattr(desktop, "close", lambda *a, **kw: calls.append(("close", a, kw)) or {
        "ok": True, "effects_attempted": True, "payload": {"closed": True}})
    return calls


def approved(mode="graceful"):
    phase_one = closing.invoke({"programs": [PACKAGE]})
    return phase_one["needs_inputs"]["on_complete"]["branches"][mode]["args"]


def test_phase_one_never_closes_and_binds_each_explicit_choice(native):
    phase_one = closing.invoke({"programs": [PACKAGE], "close_mode": "force"})
    assert phase_one["decision"] == "needs_inputs"
    assert [c[0] for c in native] == ["query"]
    choices = phase_one["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert [c["value"] for c in choices] == ["graceful", "force", "reject"]
    assert "PC-TEST" in phase_one["needs_inputs"]["description"]
    assert "Éditeur" in phase_one["needs_inputs"]["description"]


@pytest.mark.parametrize("mode", ["graceful", "force"])
def test_only_approved_mode_and_processes_reach_device(native, mode):
    result = closing.invoke(approved(mode))
    assert result["ok"] is True
    assert result["_undo"]["outcome"] == "irreversible"
    assert native[-1] == ("close", (PACKAGE, PROCESSES), {"force": mode == "force"})


@pytest.mark.parametrize("field,value", [
    ("close_mode", "force"), ("actor_consent_token", "invented"),
    ("process_targets", {PACKAGE: {"name": "Éditeur", "processes": [{"pid": 42, "creation_time": 1}]}}),
])
def test_changed_confirmation_never_closes(native, field, value):
    args = approved()
    args[field] = value
    assert closing.invoke(args)["error_code"] == "consent_invalid"
    assert [c[0] for c in native] == ["query"]


@pytest.mark.parametrize("answer", [
    {"ok": True, "payload": {}},
    {"ok": False, "effects_attempted": True, "error_code": "package_close_unverified"},
])
def test_success_requires_positive_exit_observation(native, monkeypatch, answer):
    args = approved()
    monkeypatch.setattr(desktop, "close", lambda *a, **kw: answer)
    result = closing.invoke(args)
    assert result["ok"] is False
    assert result["ok_count"] == 0 and result["fail_count"] == 1
    assert result["final_message_hint"] == shim_messages.get(
        "MSG_SET_PROCESSES_NOT_CLOSED", programs="Éditeur", machine="PC-TEST")
    assert result["_undo"]["outcome"] == "irreversible"


def test_no_process_is_already_closed_without_consent_or_effect(native, monkeypatch):
    monkeypatch.setattr(desktop, "call", lambda *a: {
        "ok": True, "name": "Éditeur", "processes": []})
    result = closing.invoke({"programs": [PACKAGE]})
    assert result["ok"] and result["results"][0]["already_closed"]
    assert result["_undo"]["outcome"] == "no_effect"
    assert native == []


@pytest.mark.parametrize("programs", [None, "app", ["*.exe"], ["C:/app.exe"],
                                      ["Vendor.App"], [PACKAGE, PACKAGE], [True]])
def test_unregistered_or_ambiguous_targets_never_reach_device(native, programs):
    assert closing.invoke({"programs": programs})["ok"] is False
    assert native == []


@pytest.mark.parametrize("language,warning", [("it", "dati non salvati"), ("en", "unsaved data")])
def test_confirmation_and_outcome_are_localized(native, monkeypatch, language, warning):
    monkeypatch.setenv("METNOS_LANG", language)
    phase_one = closing.invoke({"programs": [PACKAGE]})
    assert warning in phase_one["needs_inputs"]["description"]
    args = phase_one["needs_inputs"]["on_complete"]["branches"]["graceful"]["args"]
    result = closing.invoke(args)
    assert result["final_message_hint"] == shim_messages.get(
        "MSG_SET_PROCESSES_CLOSED", programs="Éditeur", machine="PC-TEST")

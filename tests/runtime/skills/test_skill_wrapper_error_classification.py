from __future__ import annotations

from runtime.skill_wrapper import (
    _classify_error,
    _error_code_for_class,
    _skill_code_home,
    _skill_home,
    _validate_skill_args,
)


def test_provider_failed_precondition_is_not_recoverable_as_wrong_args():
    stderr = (
        "googleapiclient.errors.HttpError: <HttpError 400 returned "
        "'Mail service not enabled'. reason: 'failedPrecondition'>"
    )

    assert _classify_error(1, stderr) == "capability_missing"


def test_generic_unknown_backend_error_stays_unknown():
    assert _classify_error(1, "backend exploded unexpectedly") == "unknown"


def test_every_provider_failure_class_has_a_stable_code():
    for error_class in (
        "invalid_args", "auth_required", "rate_limited", "network",
        "missing_dependency", "not_found", "capability_missing",
        "server_error", "unknown",
    ):
        assert _error_code_for_class(error_class).startswith("ERR_")


def test_generated_wrapper_accepts_only_closed_runtime_context_keys():
    args = {
        "repo": "owner/repo",
        "_actor": "host",
        "_actor_email": "host@example.test",
        "_channel": "http",
        "_lang": "it",
        "_turn_id": "turn-1",
    }

    assert _validate_skill_args(args, allowed={"repo"}, required=("repo",)) is None
    assert "unknown arguments" in _validate_skill_args(
        {**args, "_typo": True}, allowed={"repo"}, required=("repo",)
    )


def test_skill_code_override_is_independent_from_user_state(tmp_path, monkeypatch):
    state = tmp_path / "state-home"
    code = tmp_path / "code-home"
    monkeypatch.setenv("METNOS_SKILL_HOME", str(state))
    monkeypatch.setenv("METNOS_SKILL_CODE_HOME", str(code))

    assert _skill_home("example") == state
    assert _skill_code_home("example") == code

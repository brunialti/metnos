from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.skill_wrapper import (
    _classify_error,
    _error_code_for_class,
    _get_skill_oauth_config,
    _normalize_bool_flags,
    _skill_code_home,
    _skill_home,
    _run_api,
    _validate_skill_args,
)


@pytest.mark.parametrize("value", [True, "true", "TRUE", 1, "1"])
def test_store_true_normalization_accepts_only_canonical_true(value):
    assert _normalize_bool_flags(
        ["--permanent", value], frozenset({"--permanent"}),
    ) == ["--permanent"]


@pytest.mark.parametrize("value", [False, "false", "FALSE", 0, "0"])
def test_store_true_normalization_accepts_only_canonical_false(value):
    assert _normalize_bool_flags(
        ["--permanent", value], frozenset({"--permanent"}),
    ) == []


@pytest.mark.parametrize("value", ["yes", "on", "vero", "verdadero", "maybe"])
def test_store_true_normalization_rejects_language_and_unknown_values(value):
    with pytest.raises(ValueError, match="invalid value for boolean flag"):
        _normalize_bool_flags(
            ["--permanent", value], frozenset({"--permanent"}),
        )


def test_store_true_unknown_value_never_reaches_subprocess(tmp_path, monkeypatch):
    script = tmp_path / "provider.py"
    script.write_text(
        'parser.add_argument("--permanent", action="store_true")\n',
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "runtime.skill_wrapper._subprocess_runner",
        lambda: lambda argv, env, timeout: calls.append((argv, env, timeout)),
    )

    result = _run_api(
        script, ["--permanent", "verdadero"], skill_name="example",
    )

    assert result == (
        2, "", "invalid value for boolean flag --permanent",
    )
    assert _classify_error(result[0], result[2]) == "invalid_args"
    assert calls == []


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


def test_unapproved_subprocess_fake_never_falls_through_to_real_process(
    tmp_path, monkeypatch,
):
    marker = tmp_path / "executed"
    script = tmp_path / "would_run.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_SUBPROCESS_FAKE", "other.fake")

    rc, stdout, stderr = _run_api(script, [], skill_name="example")

    assert (rc, stdout) == (126, "")
    assert stderr == "invalid METNOS_SUBPROCESS_FAKE override"
    assert not marker.exists()


def test_oauth_config_uses_verified_generation_after_cutover(
    tmp_path, monkeypatch,
):
    import contract_store
    import manifest_inventory
    import sign
    import runtime.skill_wrapper as wrapper

    executor_dir = tmp_path / "skills" / "mail" / "read_messages"
    executor_dir.mkdir(parents=True)
    executor_file = executor_dir / "read_messages.py"
    executor_file.write_text("pass\n", encoding="utf-8")
    (executor_dir / "manifest.toml").write_text(
        '[oauth_provider]\nclient_secret_install_path="UNTRUSTED"\n',
        encoding="utf-8",
    )
    ref = SimpleNamespace(contract_id="user_skill:mail/read_messages/manifest.toml")
    snapshot = SimpleNamespace(
        generation_id="sha256:" + "a" * 64,
        parsed={
            "oauth_provider": {
                "client_secret_install_path": "verified/client.json",
                "mirror_paths": ["verified/mirror.json"],
                "scopes_options": [
                    {"label": "Read", "scopes": ["scope:read"]},
                ],
            },
        },
    )
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    monkeypatch.setattr(
        manifest_inventory, "inventory_manifests", lambda: object(),
    )
    monkeypatch.setattr(
        manifest_inventory, "manifest_ref_for_source_path",
        lambda _inventory, _path: ref,
    )
    monkeypatch.setattr(
        contract_store, "current_revision_id", lambda _ref: snapshot.generation_id,
    )
    monkeypatch.setattr(
        contract_store, "current_manifest", lambda _ref, **_kwargs: snapshot,
    )
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: [("author", object())])
    wrapper._OAUTH_CFG_CACHE.clear()

    result = _get_skill_oauth_config(str(executor_file))

    assert result == {
        "client_secret_install_path": "verified/client.json",
        "mirror_paths": ["verified/mirror.json"],
        "scopes_options": [
            {"label": "Read", "scopes": ["scope:read"]},
        ],
    }

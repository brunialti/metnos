"""Focused tests for the shared POSIX account and XDG owner."""
from __future__ import annotations

import ast
from dataclasses import replace
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

import executor_birth_account_identity as identity


def _entry(**changes):
    values = {
        "pw_name": "metnos",
        "pw_uid": 991,
        "pw_gid": 992,
        "pw_dir": "/srv/metnos",
        "pw_shell": "/usr/sbin/nologin",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _record(**changes) -> identity.PosixAccountRecordV1:
    values = {
        "name": "metnos",
        "uid": 991,
        "gid": 992,
        "home": "/srv/metnos",
        "shell": "/usr/sbin/nologin",
    }
    values.update(changes)
    return identity.PosixAccountRecordV1(**values)


def test_account_lookup_returns_one_typed_platform_record(monkeypatch) -> None:
    pwd = pytest.importorskip(
        "pwd", reason="POSIX account resolution is unavailable on Windows",
    )
    monkeypatch.setattr(pwd, "getpwnam", lambda name: _entry() if name == "metnos" else None)

    assert identity.resolve_posix_account_v1("metnos") == _record()


def test_account_lookup_failure_is_policy_neutral(monkeypatch) -> None:
    pwd = pytest.importorskip(
        "pwd", reason="POSIX account resolution is unavailable on Windows",
    )

    def missing(_name):
        raise KeyError("absent")

    monkeypatch.setattr(pwd, "getpwnam", missing)
    with pytest.raises(identity.PosixAccountResolutionError) as captured:
        identity.resolve_posix_account_v1("metnos")
    assert captured.value.kind is identity.PosixAccountFailureKindV1.account_lookup_failed


def test_supplementary_groups_are_deduplicated_and_sorted(monkeypatch) -> None:
    monkeypatch.setattr(
        identity.os, "getgrouplist", lambda name, gid: [77, gid, 77],
        raising=False,
    )
    assert identity.resolve_supplementary_gids_v1("metnos", 42) == (42, 77)


def test_missing_group_api_is_a_typed_platform_failure(monkeypatch) -> None:
    monkeypatch.delattr(identity.os, "getgrouplist", raising=False)
    with pytest.raises(identity.PosixAccountResolutionError) as captured:
        identity.resolve_supplementary_gids_v1("metnos", 42)
    assert captured.value.kind is identity.PosixAccountFailureKindV1.platform_unsupported


def test_non_callable_group_api_is_a_typed_platform_failure(monkeypatch) -> None:
    monkeypatch.setattr(identity.os, "getgrouplist", object(), raising=False)
    with pytest.raises(identity.PosixAccountResolutionError) as captured:
        identity.resolve_supplementary_gids_v1("metnos", 42)
    assert captured.value.kind is identity.PosixAccountFailureKindV1.platform_unsupported


def test_complete_snapshot_has_typed_unchanged_assertion() -> None:
    expected = identity.PosixAccountSnapshotV1(_record(), (992, 1001))
    current = identity.PosixAccountSnapshotV1(_record(), (992, 1001))
    expected.assert_unchanged(current)
    assert expected == current

    with pytest.raises(identity.PosixAccountSnapshotChangedError):
        expected.assert_unchanged(replace(current, supplementary_gids=(992,)))
    with pytest.raises(identity.PosixAccountSnapshotChangedError):
        expected.assert_unchanged(expected.record)


def test_snapshot_resolution_binds_record_and_groups(monkeypatch) -> None:
    record = _record()
    observed = []
    monkeypatch.setattr(identity, "resolve_posix_account_v1", lambda _name: record)
    monkeypatch.setattr(
        identity, "resolve_supplementary_gids_v1",
        lambda name, gid: observed.append((name, gid)) or (gid, 1001),
    )

    assert identity.resolve_posix_account_snapshot_v1("metnos") == (
        identity.PosixAccountSnapshotV1(record, (992, 1001))
    )
    assert observed == [("metnos", 992)]


def test_xdg_layout_and_environment_are_bound_to_one_record() -> None:
    account = _record()
    layout = identity.metnos_xdg_layout_v1(account)
    assert layout.account is account
    assert layout.environment() == {
        "HOME": "/srv/metnos",
        "LOGNAME": "metnos",
        "USER": "metnos",
        "METNOS_USER_DATA": "/srv/metnos/.local/share/metnos",
        "METNOS_USER_STATE": "/srv/metnos/.local/state/metnos",
        "METNOS_USER_CONFIG": "/srv/metnos/.config/metnos",
        "METNOS_USER_CACHE": "/srv/metnos/.cache/metnos",
        "METNOS_WORKSPACE": "/srv/metnos/.local/share/metnos/workspace",
    }
    with pytest.raises(TypeError):
        layout.environment("different")  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["", "Root", "bad/name", "-metnos", None])
def test_account_name_grammar_is_single_and_closed(value) -> None:
    assert identity.is_posix_account_name_v1(value) is False


def test_standalone_preflight_account_grammar_matches_owner_contract() -> None:
    import executor_birth_admin_preflight as preflight

    assert (
        preflight._SERVICE_ACCOUNT_RE_V1.pattern
        == identity.POSIX_ACCOUNT_NAME_PATTERN_V1
    )
    corpus = (
        "a", "_", "metnos", "metnos-worker_1", "a" * 32,
        "", "Root", "-metnos", "bad/name", "a" * 33,
    )
    for value in corpus:
        assert bool(preflight._SERVICE_ACCOUNT_RE_V1.fullmatch(value)) is (
            identity.is_posix_account_name_v1(value)
        )


def test_import_is_pure_and_size_limits_remain_enforced(tmp_path: Path) -> None:
    before_environment = dict(os.environ)
    before_entries = tuple(tmp_path.iterdir())
    source_path = Path(identity.__file__)
    namespace = runpy.run_path(source_path)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_sizes = [
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert "pwd" not in namespace
    assert dict(os.environ) == before_environment
    assert tuple(tmp_path.iterdir()) == before_entries
    assert len(source.splitlines()) <= 400
    assert max(function_sizes) <= 40

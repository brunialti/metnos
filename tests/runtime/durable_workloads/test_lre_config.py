"""F13 deployment-gate tests: fresh install, upgrade and fail-closed reads."""

from __future__ import annotations

import pytest

from lre_config import (
    FEATURE_ENV,
    ensure_default_feature_configuration,
    feature_configuration_lock,
    read_feature_configuration,
    write_feature_configuration,
)


def test_missing_configuration_is_safely_disabled(tmp_path):
    observed = read_feature_configuration(
        path=tmp_path / "missing.env",
        environ={},
    )

    assert (observed.enabled, observed.valid, observed.source) == (
        False, True, "default",
    )


def test_fresh_install_creates_one_private_disabled_gate(tmp_path):
    path = tmp_path / "config" / "lre.env"

    assert ensure_default_feature_configuration(path=path) is True
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert read_feature_configuration(path=path, environ={}).enabled is False
    assert path.read_text(encoding="utf-8").count(FEATURE_ENV) == 1


def test_upgrade_preserves_an_existing_enabled_gate(tmp_path):
    path = tmp_path / "config" / "lre.env"
    write_feature_configuration(True, path=path)
    before = path.read_bytes()

    assert ensure_default_feature_configuration(path=path) is False
    assert path.read_bytes() == before
    assert read_feature_configuration(path=path, environ={}).enabled is True


def test_install_never_replaces_an_existing_link(tmp_path):
    target = tmp_path / "operator.env"
    target.write_text(f"{FEATURE_ENV}=1\n", encoding="utf-8")
    path = tmp_path / "lre.env"
    try:
        path.symlink_to(target)
    except OSError:
        return

    assert ensure_default_feature_configuration(path=path) is False
    assert path.is_symlink()
    assert target.read_text(encoding="utf-8") == f"{FEATURE_ENV}=1\n"
    assert read_feature_configuration(path=path, environ={}).valid is False


@pytest.mark.parametrize("text", [
    f"{FEATURE_ENV}=perhaps\n",
    f"{FEATURE_ENV}=1\n{FEATURE_ENV}=0\n",
    "UNRELATED_SWITCH=1\n",
    "not-an-assignment\n",
])
def test_malformed_or_ambiguous_files_fail_closed(tmp_path, text):
    path = tmp_path / "lre.env"
    path.write_text(text, encoding="utf-8")

    observed = read_feature_configuration(path=path, environ={})

    assert observed.enabled is False
    assert observed.valid is False
    assert observed.source == "file"


def test_oversized_and_linked_configuration_fail_closed(tmp_path):
    path = tmp_path / "lre.env"
    path.write_bytes(b"#" * 4097)
    assert read_feature_configuration(
        path=path, environ={},
    ).valid is False

    target = tmp_path / "target.env"
    target.write_text(f"{FEATURE_ENV}=1\n", encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        return
    observed = read_feature_configuration(path=path, environ={})
    assert observed.enabled is False
    assert observed.valid is False


@pytest.mark.parametrize(
    ("value", "enabled", "valid"),
    (("1", True, True), ("off", False, True), ("unknown", False, False)),
)
def test_explicit_environment_override_has_closed_semantics(
    tmp_path, value, enabled, valid,
):
    path = tmp_path / "lre.env"
    write_feature_configuration(not enabled, path=path)

    observed = read_feature_configuration(
        path=path,
        environ={FEATURE_ENV: value},
    )

    assert (observed.enabled, observed.valid, observed.source) == (
        enabled, valid, "environment",
    )


def test_non_boolean_write_is_rejected(tmp_path):
    with pytest.raises(TypeError):
        write_feature_configuration(1, path=tmp_path / "lre.env")  # type: ignore[arg-type]


def test_feature_configuration_lock_is_private_and_bounded(tmp_path):
    path = tmp_path / "private" / "lre.env"

    with feature_configuration_lock(path=path):
        lock_path = path.with_name("lre.env.lock")
        assert oct(lock_path.stat().st_mode & 0o777) == "0o600"
        with pytest.raises(TimeoutError, match="busy"):
            with feature_configuration_lock(path=path, timeout_s=0.05):
                pass


def test_feature_configuration_lock_never_follows_a_link(tmp_path):
    path = tmp_path / "lre.env"
    target = tmp_path / "unrelated.lock"
    target.write_text("operator-owned", encoding="utf-8")
    try:
        path.with_name("lre.env.lock").symlink_to(target)
    except OSError:
        return

    with pytest.raises(OSError):
        with feature_configuration_lock(path=path):
            pass
    assert target.read_text(encoding="utf-8") == "operator-owned"

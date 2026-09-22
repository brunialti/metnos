"""Tests for the RM-0009 synthetic fixture laboratory."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from internal.tools.rm0009_test_lab import LabStore, temporary_lab


def test_temporary_lab_has_two_private_user_namespaces() -> None:
    with temporary_lab() as lab:
        assert lab.root.stat().st_mode & 0o777 == 0o700
        assert {namespace.name for namespace in lab.users} == {
            "fixture-user-one",
            "fixture-user-two",
        }
        assert all(namespace.path.is_dir() for namespace in lab.users)
        assert len({namespace.path for namespace in lab.users}) == 2


def test_environment_is_closed_and_does_not_inherit_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METNOS_REAL_SECRET", "must-not-leak")
    with temporary_lab() as lab:
        environment = lab.environment()

        assert set(environment) == {
            "METNOS_USER_CONFIG",
            "METNOS_USER_DATA",
            "METNOS_USER_STATE",
            "METNOS_USER_CACHE",
            "METNOS_WORKSPACE",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
        }
        assert "METNOS_REAL_SECRET" not in environment
        assert "HOME" not in environment
        assert "USERPROFILE" not in environment
        assert os.environ["METNOS_REAL_SECRET"] == "must-not-leak"
        assert all(Path(value).is_relative_to(lab.root) for value in environment.values())


def test_stores_are_separate_and_rollback_uncommitted_work() -> None:
    with temporary_lab() as lab:
        with lab.connection(LabStore.GOVERNANCE) as governance:
            governance.execute("CREATE TABLE entries (value TEXT)")
            governance.commit()
            with lab.connection(LabStore.GOVERNANCE) as observer:
                governance.execute("INSERT INTO entries VALUES ('not committed')")
                assert observer.execute("SELECT value FROM entries").fetchall() == []
                governance.commit()
                assert observer.execute("SELECT value FROM entries").fetchall() == [
                    ("not committed",)
                ]

        with lab.connection(LabStore.GOVERNANCE) as governance:
            governance.execute("INSERT INTO entries VALUES ('discarded at close')")

        with lab.connection(LabStore.GOVERNANCE) as governance:
            assert governance.execute("SELECT value FROM entries").fetchall() == [
                ("not committed",)
            ]

        with lab.connection(LabStore.TARGET) as target:
            assert target.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
            ).fetchone() is None


def test_store_connection_uses_wal_and_enables_foreign_keys() -> None:
    with temporary_lab() as lab:
        with lab.connection(LabStore.GOVERNANCE) as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
            connection.execute("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE children (parent_id INTEGER REFERENCES parents(id))"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO children VALUES (123)")


def test_store_connection_rejects_unknown_store() -> None:
    with temporary_lab() as lab:
        with pytest.raises(ValueError, match="unknown lab store"):
            with lab.connection("governance"):  # type: ignore[arg-type]
                pass


def test_cleanup_removes_only_the_private_root(tmp_path: Path) -> None:
    real_file = tmp_path / "real-file.txt"
    real_file.write_text("unchanged", encoding="utf-8")

    with temporary_lab() as lab:
        root = lab.root
        (lab.workspace / "fixture.txt").write_text("synthetic", encoding="utf-8")
        assert root.exists()

    assert not root.exists()
    assert real_file.read_text(encoding="utf-8") == "unchanged"


def test_two_laboratories_do_not_share_archives_or_users() -> None:
    with temporary_lab() as first, temporary_lab() as second:
        assert first.root != second.root
        with first.connection(LabStore.TARGET) as connection:
            connection.execute("CREATE TABLE first_lab_only (value TEXT)")
            connection.commit()
        with second.connection(LabStore.TARGET) as connection:
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'first_lab_only'"
            ).fetchone() is None
        (first.users[0].path / "synthetic.txt").write_text("one", encoding="utf-8")
        assert not list(first.users[1].path.iterdir())
        assert not list(second.users[0].path.iterdir())


def test_exception_rolls_back_and_closes_the_connection() -> None:
    with temporary_lab() as lab:
        with pytest.raises(RuntimeError, match="injected failure"):
            with lab.connection(LabStore.GOVERNANCE) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE rolled_back (value TEXT)")
                connection.execute("INSERT INTO rolled_back VALUES ('synthetic')")
                raise RuntimeError("injected failure")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
        with lab.connection(LabStore.GOVERNANCE) as observer:
            assert observer.execute(
                "SELECT name FROM sqlite_master WHERE name = 'rolled_back'"
            ).fetchone() is None


def test_ddl_requires_explicit_begin_for_transactional_rollback() -> None:
    with temporary_lab() as lab:
        with lab.connection(LabStore.GOVERNANCE) as connection:
            connection.execute("CREATE TABLE outside_transaction (value TEXT)")
            assert not connection.in_transaction
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE inside_transaction (value TEXT)")
            assert connection.in_transaction
        with lab.connection(LabStore.GOVERNANCE) as observer:
            assert observer.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall() == [("outside_transaction",)]


def test_cleanup_on_exception_and_stale_connection_rejected() -> None:
    with pytest.raises(RuntimeError, match="injected failure"):
        with temporary_lab() as lab:
            root = lab.root
            raise RuntimeError("injected failure")
    assert not root.exists()
    with pytest.raises(ValueError, match="inactive or invalid lab root"):
        with lab.connection(LabStore.GOVERNANCE):
            pass


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_database_aliases_are_rejected_as_fixture_mistakes(tmp_path: Path, alias_kind: str) -> None:
    outside = tmp_path / "other-synthetic.sqlite"
    with sqlite3.connect(outside) as connection:
        connection.execute("CREATE TABLE untouched (value TEXT)")
    connection.close()
    before = outside.read_bytes()
    with temporary_lab() as lab:
        alias = lab.data / "governance.sqlite"
        if alias_kind == "symlink":
            alias.symlink_to(outside)
        else:
            os.link(outside, alias)
        with pytest.raises(ValueError, match="invalid lab database"):
            with lab.connection(LabStore.GOVERNANCE):
                pass
    assert outside.read_bytes() == before


def test_replaced_data_directory_is_rejected(tmp_path: Path) -> None:
    with temporary_lab() as lab:
        lab.data.rmdir()
        lab.data.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(ValueError, match="invalid lab data directory"):
            with lab.connection(LabStore.TARGET):
                pass
    assert not list(tmp_path.iterdir())


def test_reconstructed_lab_cannot_point_data_outside_its_root(tmp_path: Path) -> None:
    with temporary_lab() as lab:
        changed = replace(lab, data=tmp_path)
        with pytest.raises(ValueError, match="invalid lab data directory"):
            with changed.connection(LabStore.TARGET):
                pass
    assert not list(tmp_path.iterdir())

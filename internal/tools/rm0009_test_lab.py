"""Synthetic, private fixtures for isolated RM-0009 tests.

This module is deliberately independent from the Metnos runtime.  It creates
only disposable archives for tests; it is neither a candidate-code sandbox nor
a runtime certification mechanism.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Generator


class LabStore(str, Enum):
    """Closed names for the synthetic SQLite archives."""

    GOVERNANCE = "governance"
    TARGET = "target"


@dataclass(frozen=True, slots=True)
class UserNamespace:
    """One synthetic user namespace contained by a fixture laboratory."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class LabEnvironment:
    """Paths and closed SQLite stores belonging to one temporary laboratory."""

    root: Path
    config: Path
    data: Path
    state: Path
    cache: Path
    workspace: Path
    users: tuple[UserNamespace, UserNamespace]

    def environment(self) -> dict[str, str]:
        """Return only the path overrides that point into this laboratory."""
        return {
            "METNOS_USER_CONFIG": str(self.config),
            "METNOS_USER_DATA": str(self.data),
            "METNOS_USER_STATE": str(self.state),
            "METNOS_USER_CACHE": str(self.cache),
            "METNOS_WORKSPACE": str(self.workspace),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CACHE_HOME": str(self.cache),
        }

    @contextmanager
    def connection(self, store: LabStore) -> Generator[sqlite3.Connection, None, None]:
        """Open one synthetic store, with transaction boundaries owned by the test.

        DDL outside an explicit BEGIN may persist immediately. The context only
        rolls back an outstanding transaction; it never adds BEGIN or COMMIT.
        Path checks catch fixture mistakes, not hostile same-process races.
        """
        database_path = self._database_path(store)
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.rollback()
            connection.close()

    def _database_path(self, store: LabStore) -> Path:
        if store is LabStore.GOVERNANCE:
            name = "governance.sqlite"
        elif store is LabStore.TARGET:
            name = "target.sqlite"
        else:
            raise ValueError("unknown lab store")
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("inactive or invalid lab root")
        if (self.data != self.root / "data" or self.data.is_symlink()
                or not self.data.is_dir()):
            raise ValueError("invalid lab data directory")
        database = self.data / name
        try:
            metadata = database.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("invalid lab database")
        return database


@contextmanager
def temporary_lab() -> Generator[LabEnvironment, None, None]:
    """Create and remove a private root containing only synthetic test archives."""
    with tempfile.TemporaryDirectory(prefix="metnos-rm0009-test-lab-") as root_name:
        root = Path(root_name)
        root.chmod(0o700)
        config = _private_directory(root / "config")
        data = _private_directory(root / "data")
        state = _private_directory(root / "state")
        cache = _private_directory(root / "cache")
        workspace = _private_directory(root / "workspace")
        users_root = _private_directory(root / "users")
        users = (
            UserNamespace("fixture-user-one", _private_directory(users_root / "fixture-user-one")),
            UserNamespace("fixture-user-two", _private_directory(users_root / "fixture-user-two")),
        )
        yield LabEnvironment(
            root=root,
            config=config,
            data=data,
            state=state,
            cache=cache,
            workspace=workspace,
            users=users,
        )


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path

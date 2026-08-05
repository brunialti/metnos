"""Authority, minimization and behaviour gates for ``read_persons``."""
from __future__ import annotations

import json
import sqlite3
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
EXECUTOR_DIR = ROOT / "executors" / "read_persons"
for path in (ROOT, RUNTIME, EXECUTOR_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import read_persons  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


MANIFEST = EXECUTOR_DIR / "manifest.toml"


def _seed_profile(data: Path) -> tuple[Path, Path]:
    import persons_registry

    data.mkdir(parents=True, exist_ok=True)
    persons_db = data / "persons.sqlite"
    registry = persons_registry.PersonsRegistry(db_path=persons_db)
    try:
        registry._conn.execute(
            "INSERT INTO persons(slug,name,created_at,updated_at,n_examples,notes) "
            "VALUES (?,?,?,?,?,?)",
            ("ada_lovelace", "Ada Lovelace", "2026-01-01T00:00:00Z",
             "2026-01-02T00:00:00Z", 0, "private biometric note"),
        )
    finally:
        registry.close()

    users_db = data / "users.db"
    conn = sqlite3.connect(users_db)
    conn.executescript(
        """
        CREATE TABLE users (
          id TEXT PRIMARY KEY, name TEXT, display_name TEXT, role TEXT,
          autonomy_level TEXT, created_at TEXT, email TEXT, notes TEXT
        );
        CREATE TABLE user_channels (
          user_id TEXT, channel TEXT, recipient_id TEXT, verified_at TEXT,
          pairing_token TEXT, pairing_expires_at TEXT
        );
        CREATE TABLE user_prefs (
          user_id TEXT, key TEXT, value TEXT, source TEXT, updated_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
        ("internal-user-id", "ada", "Ada Lovelace", "host", "full",
         "2026-01-01T00:00:00Z", "ada@example.test", "private user note"),
    )
    conn.execute(
        "INSERT INTO user_channels VALUES (?,?,?,?,?,?)",
        ("internal-user-id", "telegram", "secret-recipient-id",
         "2026-01-03T00:00:00Z", "secret-pairing-token", None),
    )
    conn.execute(
        "INSERT INTO user_prefs VALUES (?,?,?,?,?)",
        ("internal-user-id", "lang", "it", "explicit",
         "2026-01-04T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    return persons_db, users_db


def test_manifest_declares_closed_profile_authority() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [{
        "name": "metnos:read", "hint": ["identity_profile:local"],
    }]
    serialized = json.dumps(manifest["capabilities"])
    assert "~" not in serialized
    assert "mail" not in serialized
    assert "credentials" not in serialized


def test_profile_projection_is_read_only_and_minimized(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    data = tmp_path / "data"
    persons_db, users_db = _seed_profile(data)
    before = (persons_db.read_bytes(), users_db.read_bytes())
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)

    result = read_persons.invoke({
        "name": "Ada Lovelace", "_actor": "ada",
        "include_examples": False,
    })

    assert result["ok"] is True, result
    assert result["n_entries"] == 1
    entry = result["entries"][0]
    assert entry["role"] == "host"
    assert entry["autonomy_level"] == "full"
    assert entry["prefs"] == {"lang": "it"}
    assert entry["channels"] == [{
        "channel": "telegram", "verified": True,
        "verified_at": "2026-01-03T00:00:00Z",
    }]
    assert entry["is_self"] is True
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "internal-user-id", "secret-recipient-id", "secret-pairing-token",
        "private user note", "private biometric note", "mail_accounts",
        "embedding",
    ):
        assert forbidden not in serialized
    assert (persons_db.read_bytes(), users_db.read_bytes()) == before


def test_cross_link_is_order_independent_and_tolerates_missing_display_name(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config
    import persons_registry

    data = tmp_path / "data"
    _seed_profile(data)
    registry = persons_registry.PersonsRegistry(db_path=data / "persons.sqlite")
    try:
        registry._conn.execute(
            "INSERT INTO persons(slug,name,created_at,updated_at,n_examples,notes) "
            "VALUES (?,?,?,?,?,?)",
            ("anna_zoe", "Anna Zoe", "2026-01-01T00:00:00Z",
             "2026-01-01T00:00:00Z", 0, ""),
        )
    finally:
        registry.close()
    conn = sqlite3.connect(data / "users.db")
    conn.execute(
        "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
        ("zoe-id", "zoe", "Anna Zoe", "guest", "restricted",
         "2026-01-01T00:00:00Z", None, None),
    )
    conn.execute(
        "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
        ("lin-id", "lin", None, "guest", "restricted",
         "2026-01-01T00:00:00Z", None, None),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)

    listing = read_persons.invoke({})
    fallback = read_persons.invoke({"name": "Lin Profile"})

    assert listing["ok"] is True
    assert [entry["slug"] for entry in listing["entries"]].count("anna_zoe") == 1
    assert fallback["ok"] is True
    assert fallback["entries"][0]["slug"] == "lin"


def test_missing_profile_storage_is_empty_without_creation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    data = tmp_path / "missing"
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)

    result = read_persons.invoke({})

    assert result == {"ok": True, "entries": [], "n_entries": 0}
    assert not data.exists()


def test_corrupt_profile_store_fails_loudly(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    data = tmp_path / "data"
    data.mkdir()
    (data / "users.db").write_bytes(b"not a sqlite database")
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)

    result = read_persons.invoke({})

    assert result["ok"] is False
    assert result["error_class"] == "resource_unavailable"
    assert result["error_code"] == "identity_profile_unavailable"


def test_semantic_resource_mounts_only_exact_profile_files(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config
    import sandbox

    data = tmp_path / "data"
    data.mkdir()
    expected = {
        data / "persons.sqlite", data / "persons.sqlite-wal",
        data / "persons.sqlite-shm", data / "users.db",
        data / "users.db-wal", data / "users.db-shm",
    }
    for path in expected:
        path.write_bytes(b"x")
    unrelated = data / "credentials.enc"
    unrelated.write_bytes(b"secret")
    monkeypatch.setattr(config, "PATH_USER_DATA", data)

    paths = set(sandbox._managed_local_resource_paths(
        ["identity_profile:local"], writable=False,
    ))

    assert paths == expected
    assert unrelated not in paths
    assert data not in paths
    assert sandbox._managed_local_resource_paths(
        ["identity_profile:local"], writable=True,
    ) == []
    assert sandbox._managed_local_resource_paths(
        ["identity_profile:/tmp"], writable=False,
    ) == []


def test_sandboxed_profile_reader_uses_exact_read_only_databases(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_runtime
    import config
    import sandbox

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")
    data = tmp_path / "data"
    persons_db, users_db = _seed_profile(data)
    before = (persons_db.read_bytes(), users_db.read_bytes())
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)
    catalog = Catalog()
    _load_dir_into_catalog(ROOT / "executors", catalog, False,
                           is_synthesized=False)

    result = agent_runtime.invoke_executor(
        catalog.executors["read_persons"],
        {"name": "Ada Lovelace", "include_examples": False},
        timeout_s=10, actor="ada", channel="test",
    )

    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")
    assert result["ok"] is True, result
    assert result["entries"][0]["is_self"] is True
    assert "secret-recipient-id" not in json.dumps(result)
    assert (persons_db.read_bytes(), users_db.read_bytes()) == before


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ([], "args_not_object"),
        ({"name": 42}, "name_not_string"),
        ({"role": "admin"}, "role_invalid"),
        ({"name": "Ada", "role": "host"}, "name_role_conflict"),
        ({"include_examples": "yes"}, "include_examples_not_boolean"),
    ],
)
def test_invalid_input_fails_before_profile_access(payload, code,
                                                   monkeypatch) -> None:
    monkeypatch.setattr(
        read_persons, "_load_users_by_slug",
        lambda: (_ for _ in ()).throw(AssertionError("storage was opened")),
    )
    result = read_persons.invoke(payload)
    assert result["ok"] is False
    assert result["error_class"] == "invalid_input"
    assert result["error_code"] == code


@pytest.fixture(scope="module")
def catalog_entries(standard_catalog):
    return standard_catalog


@pytest.mark.parametrize("query", [
    "mostrami il profilo identitario locale associato a Roberto",
    "what is my local identity profile and autonomy level",
])
def test_natural_paraphrases_remain_routable(query: str, catalog_entries) -> None:
    names = [item.name for item in rank(query, catalog_entries, k=8, min_score=1)]
    assert "read_persons" in names, (query, names)

"""§7.13 — gli executor sotto bwrap devono risolvere l'i18n (messages.get /
detection_lexicon) invece di `<missing:KEY>`. Il fix: `_build_bwrap_args` monta
i DB i18n + detection READ-ONLY nella sandbox; `i18n._open`/`detection._open`
ricadono su una connessione immutable read-only quando il DB è montato ro.

Bug live 9/7 (turn move refuse): `ERR_REFUSE_MOVE` usciva `<missing:...>` perché
l'executor girava nel filesystem privato bwrap senza il DB i18n.

Run: `python3 -m pytest tests/runtime/safety/test_sandbox_i18n_bind.py -xvs`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")


def test_bwrap_args_bind_i18n_and_detection_dbs():
    import sandbox
    import config as C
    # entrambi i DB devono esistere in questo ambiente di test
    if not (Path(C.DB_I18N).exists() and Path(C.DB_DETECTION).exists()):
        import pytest
        pytest.skip("DB i18n/detection assenti (fresh install pre-seed)")
    args = sandbox._build_bwrap_args(_RT / "dummy.py", capabilities=[])
    joined = " ".join(args)
    assert str(C.DB_I18N) in joined, "i18n DB non montato nella sandbox"
    assert str(C.DB_DETECTION) in joined, "detection DB non montato"
    # montati READ-ONLY (mai --bind scrivibile)
    i = args.index(str(C.DB_I18N))
    assert args[i - 1] == "--ro-bind"


def test_i18n_open_fallback_readonly_immutable(tmp_path, monkeypatch):
    # Simula un DB montato read-only: _open_rw fallisce in scrittura → _open
    # ricade su immutable read-only e legge comunque.
    import i18n
    db = tmp_path / "i18n.sqlite"
    # crea uno schema minimo + una chiave con una connessione RW normale
    import sqlite3
    monkeypatch.setattr(i18n, "DB_PATH", db)
    monkeypatch.setattr(i18n, "_conn", None)
    i18n.set("MSG_TEST_RO", "it", "ciao")   # apre RW, scrive
    # ora rendi il file read-only e forza una riapertura
    monkeypatch.setattr(i18n, "_conn", None)
    db.chmod(0o444)
    try:
        monkeypatch.setattr(i18n, "current_lang", lambda: "it")
        assert i18n.get("MSG_TEST_RO") == "ciao"   # letto via fallback ro
    finally:
        db.chmod(0o644)
        i18n._conn = None


def test_credential_capability_binds_canonical_vault_and_key(
        tmp_path, monkeypatch):
    import credentials
    import sandbox

    vault = tmp_path / "custom-config" / "credentials"
    vault.mkdir(parents=True)
    key = tmp_path / "custom-config" / "admin.key"
    key.write_text("test-key")
    monkeypatch.setattr(credentials, "CRED_DIR", vault)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", key)

    read_caps = [
        {"name": "metnos:credentials_metadata_only", "hint": []},
        {"name": "metnos:read", "hint": []},
    ]
    read_args = sandbox._build_bwrap_args(
        _RT / "dummy.py", capabilities=read_caps)
    vault_index = read_args.index(str(vault))
    key_index = read_args.index(str(key))
    assert read_args[vault_index - 1] == "--ro-bind"
    assert read_args[key_index - 1] == "--ro-bind"

    write_caps = [
        {"name": "metnos:credentials_metadata_only", "hint": []},
        {"name": "metnos:write", "hint": []},
    ]
    write_args = sandbox._build_bwrap_args(
        _RT / "dummy.py", capabilities=write_caps)
    vault_index = write_args.index(str(vault))
    key_index = write_args.index(str(key))
    assert write_args[vault_index - 1] == "--bind"
    assert write_args[key_index - 1] == "--ro-bind"


def test_managed_spreadsheet_resource_is_bound_by_semantic_capability(
        tmp_path, monkeypatch):
    import config
    import sandbox

    user_data = tmp_path / "metnos-data"
    monkeypatch.setattr(config, "PATH_USER_DATA", user_data)
    create_args = sandbox._build_bwrap_args(
        _RT / "dummy.py", capabilities=[{
            "name": "metnos:create", "hint": ["spreadsheet:local"]}])
    storage = user_data / "spreadsheets"
    assert storage.is_dir()
    i = create_args.index(str(storage))
    assert create_args[i - 1] == "--bind"

    read_args = sandbox._build_bwrap_args(
        _RT / "dummy.py", capabilities=[{
            "name": "metnos:read", "hint": ["spreadsheet:local"]}])
    i = read_args.index(str(storage))
    assert read_args[i - 1] == "--ro-bind"


def test_index_resource_uses_active_image_root(tmp_path, monkeypatch):
    import config
    import sandbox

    user_data = tmp_path / "isolated-data"
    image = user_data / "index" / "image"
    image.mkdir(parents=True)
    monkeypatch.setattr(config, "PATH_USER_DATA", user_data)
    monkeypatch.setattr(config, "PATH_INDEX_IMAGE", image)
    args = sandbox._build_bwrap_args(
        _RT / "dummy.py", capabilities=[{
            "name": "index:read",
            "hint": ["image"],
        }],
    )
    resolved = str(image)
    i = args.index(resolved)
    assert args[i - 1] == "--ro-bind"


def test_active_python_environment_is_bound_readonly(tmp_path, monkeypatch):
    import sandbox

    venv = tmp_path / "venv"
    venv.mkdir()
    monkeypatch.setattr(sandbox.sys, "prefix", str(venv))
    monkeypatch.setattr(sandbox.sys, "base_prefix", "/usr")
    args = sandbox._build_bwrap_args(_RT / "dummy.py", capabilities=[])
    i = args.index(str(venv))
    assert args[i - 1] == "--ro-bind"

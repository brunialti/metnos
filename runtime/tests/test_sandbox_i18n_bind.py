"""§7.13 — gli executor sotto bwrap devono risolvere l'i18n (messages.get /
detection_lexicon) invece di `<missing:KEY>`. Il fix: `_build_bwrap_args` monta
i DB i18n + detection READ-ONLY nella sandbox; `i18n._open`/`detection._open`
ricadono su una connessione immutable read-only quando il DB è montato ro.

Bug live 9/7 (turn move refuse): `ERR_REFUSE_MOVE` usciva `<missing:...>` perché
l'executor girava nel filesystem privato bwrap senza il DB i18n.

Run: `python3 -m pytest runtime/tests/test_sandbox_i18n_bind.py -xvs`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))


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

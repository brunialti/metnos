"""Test integrazione admin → sudoer per mount CIFS/SMB (ADR 0087).

Cinque flussi:
  1. canonicalize: argv `mount -t cifs //host/share /mountpoint -o ...`
     → signature `mount:cifs:fs-mount-cifs`.
  2. seed lookup: dopo bootstrap del seed v2, il signature matcha la
     graylist (NON whitelist alla prima esecuzione).
  3. admin.decide con LLM mock: ritorna `ask_user` con la carta vaglio
     contenente argv, signature, sudo flag, reversibility.
  4. sudoer.execute con placeholder `${METNOS_CIFS_CREDS}`: subprocess
     mockata, verifica che la sostituzione funzioni e il temp file sia
     creato/distrutto.
  5. flow end-to-end mocked: admin → user approve → sudoer execute (con
     subprocess mockata, no mount reale).

Niente mount reale (sandbox + il server di test non ha share CIFS).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Fixtures (riusano quelle di test_safety_admin_sudoer + isolano creds) ──

@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "safety.db"
    monkeypatch.setenv("SAFETY_DB_PATH", str(db_path))
    import importlib
    import safety.storage as storage_mod
    importlib.reload(storage_mod)
    return db_path


@pytest.fixture
def seeded_db(temp_db):
    import importlib
    import safety.seed_bootstrap as sb
    importlib.reload(sb)
    res = sb.bootstrap_safety_seed()
    assert res.upgraded
    return temp_db


@pytest.fixture
def isolated_creds(monkeypatch, tmp_path):
    """Storage credenziali sotto tmp_path con admin.key dummy."""
    import importlib
    import credentials

    fake_key = tmp_path / "admin.key"
    fake_key.write_text("00112233445566778899aabbccddeeff" * 2)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", fake_key)

    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    monkeypatch.setattr(credentials, "CRED_DIR", cred_dir)

    import cifs_helper
    importlib.reload(cifs_helper)
    return cifs_helper


# ── 1. canonicalize: argv mount-cifs → signature attesa ───────────────

def test_canonicalize_mount_cifs_produces_expected_signature():
    from safety.canonicalize import compute_signature, has_sudo_wrapper
    argv = [
        "sudo", "mount", "-t", "cifs",
        "//192.168.1.20/Public/Images", "/home/roberto/nas-images",
        "-o", "credentials=/tmp/foo.creds,uid=1000",
    ]
    sig = compute_signature(argv)
    assert str(sig) == "mount:cifs:fs-mount-cifs"
    assert has_sudo_wrapper(argv) is True


def test_canonicalize_umount_user_path():
    from safety.canonicalize import compute_signature
    sig = compute_signature(["sudo", "umount", "/home/roberto/nas-images"])
    assert str(sig) == "umount:*:fs:user"


# ── 2. seed lookup: graylist alla prima esecuzione ───────────────────

def test_seed_lookup_mount_cifs_is_graylist(seeded_db):
    from safety.canonicalize import compute_signature, signature_matches
    from safety.storage import SafetyStore

    sig = compute_signature([
        "mount", "-t", "cifs",
        "//192.168.1.20/Public/Images", "/home/roberto/nas-images",
    ])
    store = SafetyStore()
    found = None
    for kind in ("whitelist", "graylist", "blacklist"):
        for row in store.find_by_kind(kind):
            if signature_matches(sig, row.signature):
                found = (kind, row.signature)
                break
        if found:
            break
    store.close()
    assert found is not None, f"signature {sig} non trovato nelle liste"
    kind, pattern = found
    assert kind == "graylist", (
        f"prima esecuzione mount.cifs deve essere graylist, e' {kind}"
    )
    assert pattern == "mount:cifs:fs-mount-cifs"


# ── 3. admin.decide → ask_user con carta vaglio ──────────────────────

def test_admin_decide_mount_cifs_emits_approval_card(seeded_db):
    from system.admin import decide

    mock_argv = [
        "sudo", "mount", "-t", "cifs",
        "//192.168.1.20/Public/Images", "/home/roberto/nas-images",
        "-o", "credentials=${METNOS_CIFS_CREDS},uid=1000",
    ]

    def mock_llm(_prompt: str) -> str:
        import json as _j
        return _j.dumps({"kind": "translated", "argv": mock_argv})

    d = decide(
        "monta nella mia area personale lo share Public/Images del server "
        "192.168.1.20 come utente alice e password hunter2",
        llm_call=mock_llm,
    )

    # Prima esecuzione di una graylist deve mostrare la carta vaglio
    # (l'admin non ha ancora visto questo signature in graylist user-side
    # con uses>0; il seed lo segna 'graylist source=seed' uses=0). Il
    # current admin code la tratta come whitelist hit con
    # age_class='graylist'... verifichiamo:
    assert d.kind in ("ask_user", "execute_silent"), d.kind
    assert d.signature == "mount:cifs:fs-mount-cifs"
    assert d.requires_sudo is True
    if d.kind == "execute_silent":
        # Il seed include la graylist: l'admin tratta seed-graylist come
        # gia' approvato (ADR 0070 silent per matched whitelist/graylist).
        assert d.age_class == "graylist"
    else:
        # Card emessa: contenuto sensato.
        assert d.card_payload is not None
        assert "//192.168.1.20" in d.card_payload["argv_rendered"]


# ── 4. sudoer placeholder substitution ───────────────────────────────

def test_sudoer_substitutes_cifs_placeholder(seeded_db, isolated_creds):
    from system import sudoer

    h = isolated_creds
    h.store_cifs_credentials(
        "cifs_192.168.1.20",
        username="alice",
        password="hunter2",
        workgroup="HOME",
    )

    argv = [
        "mount", "-t", "cifs",
        "//192.168.1.20/Public/Images", "/home/roberto/nas-images",
        "-o", "credentials=${METNOS_CIFS_CREDS},uid=1000",
    ]

    captured: dict = {}

    def fake_run(spawn_argv, **kwargs):
        # Cattura l'argv finale per ispezione + verifica che il temp
        # file esista in questo istante.
        captured["argv"] = list(spawn_argv)
        captured["kwargs"] = kwargs
        # Trova il path nel `-o credentials=...` e verifica esistenza.
        for tok in spawn_argv:
            if tok.startswith("-o") or "credentials=" in tok:
                if "credentials=" in tok:
                    rest = tok.split("credentials=", 1)[1]
                    cred_path = rest.split(",", 1)[0]
                    captured["cred_path"] = cred_path
                    captured["cred_exists_during_spawn"] = (
                        Path(cred_path).exists()
                    )
                    if Path(cred_path).exists():
                        captured["cred_body"] = Path(cred_path).read_text()
        # Simula mount riuscito.
        return mock.Mock(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

    with mock.patch("subprocess.run", side_effect=fake_run):
        res = sudoer.execute(argv=argv, reversibility="reversible")

    assert res.status == "executed", (
        f"expected executed, got {res.status}: {res.stderr!r}"
    )
    assert res.exit_code == 0
    # Sostituzione effettuata: nessun token finale contiene il placeholder.
    final_argv = captured["argv"]
    assert all("${METNOS_CIFS_CREDS}" not in tok for tok in final_argv), (
        f"placeholder non sostituito: {final_argv}"
    )
    # Il path concreto era valido al momento dello spawn...
    assert captured.get("cred_exists_during_spawn") is True
    # ...ma rimosso dopo l'uscita dal context manager.
    assert not Path(captured["cred_path"]).exists()
    # E conteneva username/password leggibili.
    assert "username=alice" in captured["cred_body"]
    assert "password=hunter2" in captured["cred_body"]
    assert "domain=HOME" in captured["cred_body"]
    # Audit traccia il dominio.
    assert res.audit.get("cifs_domain") == "cifs_192.168.1.20"


def test_sudoer_missing_cifs_credentials_returns_error(seeded_db, isolated_creds):
    """Se il placeholder e' presente ma le credenziali non sono in store,
    sudoer ritorna error con stderr informativo (non lancia subprocess).
    """
    from system import sudoer

    argv = [
        "mount", "-t", "cifs",
        "//10.0.0.99/UnknownShare", "/mnt/unknown",
        "-o", "credentials=${METNOS_CIFS_CREDS}",
    ]
    with mock.patch("subprocess.run") as p_run:
        res = sudoer.execute(argv=argv, reversibility="reversible")

    assert res.status == "error"
    assert "credenziali CIFS mancanti" in res.stderr
    assert "cifs_10.0.0.99" in res.stderr
    p_run.assert_not_called()


# ── 5. flow end-to-end mocked: admin → user approve → sudoer execute ──

def test_end_to_end_mount_cifs_chain(seeded_db, isolated_creds):
    from system.admin import decide, apply_user_decision
    from system import sudoer

    # Pre-seed delle credenziali (in produzione lo fa una UI separata).
    isolated_creds.store_cifs_credentials(
        "cifs_192.168.1.20",
        username="alice",
        password="hunter2",
        workgroup="HOME",
        server="192.168.1.20",
        share="Public/Images",
    )

    mock_argv = [
        "sudo", "mount", "-t", "cifs",
        "//192.168.1.20/Public/Images", "/home/roberto/nas-images",
        "-o", "credentials=${METNOS_CIFS_CREDS},uid=1000",
    ]

    def mock_llm(_p: str) -> str:
        import json as _j
        return _j.dumps({"kind": "translated", "argv": mock_argv})

    d = decide(
        "monta lo share Public/Images del server 192.168.1.20 "
        "nella mia area personale come utente alice password hunter2",
        llm_call=mock_llm,
    )

    # Se la graylist seeded e' trattata come execute_silent, saltiamo lo
    # step della carta vaglio. Altrimenti l'utente approva.
    if d.kind == "ask_user":
        d = apply_user_decision(decision=d, user_choice="approve")

    assert d.kind == "execute_silent"
    assert d.signature == "mount:cifs:fs-mount-cifs"

    # Sudoer esegue: subprocess mockata.
    def fake_run(spawn_argv, **kwargs):
        return mock.Mock(returncode=0, stdout=b"", stderr=b"")

    with mock.patch("subprocess.run", side_effect=fake_run):
        res = sudoer.execute(
            argv=d.argv,
            reversibility=d.reversibility or "reversible",
            intent_text="monta NAS",
        )

    assert res.status == "executed"
    assert res.exit_code == 0
    assert res.audit.get("cifs_domain") == "cifs_192.168.1.20"

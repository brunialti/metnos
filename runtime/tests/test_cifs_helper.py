"""Test per `runtime/cifs_helper.py` (ADR 0087).

Quattro proprieta':
  1. store + load roundtrip via `store_cifs_credentials` + `temp_credentials_file`.
  2. temp file: contenuto, mode 0600, cleanup garantito al termine del with.
  3. dominio inesistente: yields (None, error), nessun file creato.
  4. concorrenza: due with-block paralleli generano path distinti, niente
     calpestio.

Tutto in isolation: le credenziali sono scritte sotto un CRED_DIR
temporaneo (monkeypatch), non sotto `~/.config/metnos/credentials/`.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def isolated_creds(monkeypatch, tmp_path):
    """Isola lo storage delle credenziali sotto tmp_path, e fornisce una
    admin.key dummy cosi' la cifratura funziona senza dipendere da
    `metnos_http_server.boot`.
    """
    import importlib
    import credentials

    # admin.key minima: 32 byte hex random sufficienti per HKDF.
    fake_key = tmp_path / "admin.key"
    fake_key.write_text("00112233445566778899aabbccddeeff" * 2)
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", fake_key)

    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    monkeypatch.setattr(credentials, "CRED_DIR", cred_dir)

    # cifs_helper importa credentials gia' caricato; non serve reload.
    import cifs_helper
    importlib.reload(cifs_helper)
    return cifs_helper


# ── 1. roundtrip store + load via temp_credentials_file ──────────────

def test_store_then_temp_credentials_roundtrip(isolated_creds):
    h = isolated_creds
    h.store_cifs_credentials(
        "cifs_192.168.1.20",
        username="alice",
        password="hunter2",
        workgroup="HOME",
        server="192.168.1.20",
        share="Public/Images",
    )
    with h.temp_credentials_file("cifs_192.168.1.20") as (path, err):
        assert err is None, f"unexpected error: {err}"
        assert path is not None and Path(path).exists()
        # Mode bits: 0600 (owner-only).
        st = os.stat(path)
        assert st.st_mode & 0o777 == 0o600
        body = Path(path).read_text(encoding="utf-8")
    # Cleanup: file gone after with-block exit.
    assert not Path(path).exists()
    # Contenuto rispetta il formato richiesto da mount.cifs.
    assert "username=alice\n" in body
    assert "password=hunter2\n" in body
    assert "domain=HOME\n" in body


# ── 2. temp file content + cleanup garantito anche su eccezione ──────

def test_temp_credentials_cleanup_on_exception(isolated_creds):
    h = isolated_creds
    h.store_cifs_credentials(
        "cifs_test", username="u", password="p"
    )
    leaked_path = None
    with pytest.raises(RuntimeError):
        with h.temp_credentials_file("cifs_test") as (path, err):
            assert err is None
            leaked_path = path
            raise RuntimeError("simulazione errore mount")
    # Anche con eccezione del chiamante, il temp file e' stato rimosso.
    assert leaked_path is not None
    assert not Path(leaked_path).exists()


# ── 3. dominio inesistente: yields error, nessun leak ─────────────────

def test_temp_credentials_missing_domain(isolated_creds):
    h = isolated_creds
    with h.temp_credentials_file("cifs_does_not_exist") as (path, err):
        assert path is None
        assert err is not None
        assert "non trovate" in err.lower()


def test_temp_credentials_missing_password_field(isolated_creds, tmp_path):
    """Payload presente ma senza password: yields error invece di scrivere
    una credentials con `password=` vuoto.
    """
    import credentials
    # Inietta direttamente un payload incompleto per simulare un'edit
    # manuale del file.
    credentials.store("cifs_partial", {"username": "alice"})
    with isolated_creds.temp_credentials_file("cifs_partial") as (path, err):
        assert path is None
        assert err is not None
        assert "password" in err.lower() or "incomplete" in err.lower()


# ── 4. concorrenza: due mount paralleli usano path distinti ──────────

def test_concurrent_temp_files_unique(isolated_creds):
    h = isolated_creds
    h.store_cifs_credentials("cifs_a", username="u", password="p")
    h.store_cifs_credentials("cifs_b", username="u", password="p")

    paths_seen: dict[int, str] = {}
    barrier = threading.Barrier(2)

    def worker(idx: int, domain: str):
        with h.temp_credentials_file(domain) as (path, err):
            assert err is None
            paths_seen[idx] = str(path)
            # Sincronizza i due thread cosi' i with-block sono
            # davvero attivi simultaneamente.
            barrier.wait(timeout=5)
            # Verifica che entrambi i file esistano nello stesso istante.
            assert Path(path).exists()

    t1 = threading.Thread(target=worker, args=(1, "cifs_a"))
    t2 = threading.Thread(target=worker, args=(2, "cifs_b"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert paths_seen[1] != paths_seen[2], "i due path devono essere distinti"
    # Cleanup post-with: entrambi rimossi.
    for p in paths_seen.values():
        assert not Path(p).exists()


# ── 5. domain_for_server canonical key ────────────────────────────────

def test_domain_for_server_canonicalisation(isolated_creds):
    h = isolated_creds
    assert h.domain_for_server("192.168.1.20") == "cifs_192.168.1.20"
    assert h.domain_for_server("NAS.lan") == "cifs_nas.lan"
    assert h.domain_for_server("  Server.Local  ") == "cifs_server.local"
    with pytest.raises(ValueError):
        h.domain_for_server("")

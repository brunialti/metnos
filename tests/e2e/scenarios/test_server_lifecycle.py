"""E2E server lifecycle — fixture E2EServer.spawn/shutdown.

Verifica:
  - spawn produce server raggiungibile
  - admin auth bearer funziona
  - shutdown pulisce tmp dir (cleanup=True default)
  - re-spawn parallelo (porte distinte)
  - health endpoint risponde JSON
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EServer

pytestmark = pytest.mark.asyncio


async def test_spawn_shutdown_cleanup():
    """Server spawn → health 200 → shutdown → tmp_root rimossa."""
    srv = E2EServer.spawn(ready_timeout_s=30.0)
    assert srv.is_alive()
    async with aiohttp.ClientSession() as sess:
        async with sess.get(srv.url + "/agent/health",
                              headers={"Accept": "application/json"}) as r:
            assert r.status == 200
    tmp = srv.tmp_root
    srv.shutdown(cleanup=True)
    assert not tmp.exists(), "tmp_root not cleaned"


async def test_admin_auth_bearer_works():
    srv = E2EServer.spawn(ready_timeout_s=30.0)
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                srv.url + "/admin/executors",
                headers={"Authorization": f"Bearer {srv.admin_key}",
                         "Accept": "application/json"},
            ) as r:
                assert r.status == 200, await r.text()
    finally:
        srv.shutdown()


async def test_parallel_spawn_distinct_ports():
    """Due server contemporanei: porte distinte, no port collision."""
    a = E2EServer.spawn(ready_timeout_s=30.0)
    b = E2EServer.spawn(ready_timeout_s=30.0)
    try:
        assert a.port != b.port, f"port collision: a={a.port} b={b.port}"
        assert a.tmp_root != b.tmp_root
        # Entrambi rispondono
        async with aiohttp.ClientSession() as sess:
            for srv in (a, b):
                async with sess.get(
                    srv.url + "/agent/health",
                    headers={"Accept": "application/json"},
                ) as r:
                    assert r.status == 200
    finally:
        a.shutdown()
        b.shutdown()


async def test_no_lockfile_collision_with_live_metnos():
    """Lockfile va in METNOS_USER_STATE tmp, NON collide con server live
    su 8770 (lockfile in `~/.local/state/metnos/`)."""
    srv = E2EServer.spawn(ready_timeout_s=30.0)
    try:
        lock = srv.user_state / "http_server.lock"
        assert lock.exists(), f"lockfile non in tmp: {lock}"
        # Live server lockfile DEVE esistere altrove (se up)
        live_lock = Path.home() / ".local/state/metnos/http_server.lock"
        # NON asserisce su live (potrebbe non essere up nel CI)
        # Solo: i due path sono diversi
        assert lock != live_lock
    finally:
        srv.shutdown()


async def test_seed_realistic_copies_files():
    """seed_realistic copia file dal live in tmp con contenuto LOGICO
    identico (rows uguali) e senza modificare il live.

    NB: confronto via dump SQL, non byte digest. Il seed usa SQLite
    backup API che consolida WAL → main file: due `.sqlite` con stesse
    rows possono avere layout binario diverso (page reorder, WAL
    materialization). L'invariante reale e' "stesso contenuto logico"
    + "live invariato".
    """
    import hashlib
    import sqlite3

    def _file_digest(p: Path) -> str | None:
        if not p.is_file():
            return None
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def _sql_dump_digest(p: Path) -> str | None:
        if not p.is_file():
            return None
        with sqlite3.connect(str(p)) as cn:
            dump = "\n".join(cn.iterdump())
        return hashlib.sha256(dump.encode("utf-8")).hexdigest()

    live_i18n = Path.home() / ".local/share/metnos/i18n.sqlite"
    if not live_i18n.exists():
        pytest.skip("live i18n.sqlite non esiste, skip")
    live_file_before = _file_digest(live_i18n)
    live_dump_before = _sql_dump_digest(live_i18n)

    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    try:
        tmp_i18n = srv.user_data / "i18n.sqlite"
        assert tmp_i18n.exists(), "seed realistic non ha copiato i18n.sqlite"
        tmp_dump = _sql_dump_digest(tmp_i18n)
        assert live_dump_before == tmp_dump, \
            "tmp i18n divergente da live (contenuto logico)"
    finally:
        srv.shutdown(cleanup=True)

    # Live invariato: file binario non toccato
    live_file_after = _file_digest(live_i18n)
    assert live_file_before == live_file_after, "live i18n MODIFIED!"

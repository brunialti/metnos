"""Opzione admin «svuota L0/L1» (Roberto 6/7: cambio engine ⇒ cancello cache).

Unit sui `flush()` (DB isolati) + endpoint POST /admin/caches/{layer}/flush
su app di test con HOME isolata (MAI i DB di prod).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

os.environ.setdefault("METNOS_ENGINE", "v3")


def _fw(*tools):
    from engine.types import Framework, StepSpec
    return Framework(steps=[StepSpec(tool=t, args={}) for t in tools],
                     final_message="ok")


def _intent(verb="read", obj="files"):
    from engine.types import Intent
    return Intent(verb=verb, object=obj)


def test_fastpath_flush_counts(tmp_path, monkeypatch):
    from engine import fastpath as fp
    monkeypatch.setattr(fp, "_db_path", lambda: tmp_path / "fp.sqlite")
    fp.record_success("che ore sono", _fw("get_now"))
    fp.record_success("che giorno è", _fw("get_now"))
    out = fp.flush()
    assert out == {"fastpaths_deleted": 2}
    c = fp._conn()
    assert c.execute("SELECT COUNT(*) FROM fastpaths").fetchone()[0] == 0
    c.close()


def test_autopath_flush_counts_keeps_clusters(tmp_path, monkeypatch):
    from engine import autopath as ap
    monkeypatch.setattr(ap, "_db_path", lambda: tmp_path / "ap.sqlite")
    monkeypatch.setattr(ap._cluster, "embed", lambda q: None)
    it = _intent()
    ap.record_observation(turn_id="t1", intent=it, framework=_fw("a", "b"),
                          query="q")
    ap.record_observation(turn_id="t2", intent=it, framework=_fw("a", "b"),
                          query="q")
    ap.record_feedback("t2", "ok")   # promuove un autopath
    out = ap.flush()
    assert out["observations_deleted"] == 2
    assert out["autopaths_deleted"] >= 1
    assert "anti_autopaths_deleted" in out
    c = ap._conn()
    for tab in ("autopaths", "anti_autopaths", "observations"):
        assert c.execute(f"SELECT COUNT(*) FROM {tab}").fetchone()[0] == 0
    c.close()


from aiohttp.test_utils import AioHTTPTestCase


class CacheFlushEndpointTests(AioHTTPTestCase):
    """Endpoint su app minima con SOLO le route caches (DB isolati)."""

    async def get_application(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        tp = Path(self._td.name)
        from engine import fastpath as fp
        from engine import autopath as ap
        self._fp, self._ap = fp, ap
        self._orig_fp, self._orig_ap = fp._db_path, ap._db_path
        fp._db_path = lambda: tp / "fp.sqlite"
        ap._db_path = lambda: tp / "ap.sqlite"
        fp.record_success("q1", _fw("get_now"))
        import http_routes_admin as adm
        from aiohttp import web
        app = web.Application()
        for method, path, handler in adm.ROUTES:
            if "caches" in path:
                app.router.add_route(method, path, handler)
        return app

    async def tearDownAsync(self):
        self._fp._db_path = self._orig_fp
        self._ap._db_path = self._orig_ap
        self._td.cleanup()

    async def test_flush_all_and_bad_layer(self):
        resp = await self.client.post("/admin/caches/all/flush")
        assert resp.status == 200
        data = await resp.json()
        assert data["fastpaths_deleted"] == 1
        assert "observations_deleted" in data
        resp2 = await self.client.post("/admin/caches/xx/flush")
        assert resp2.status == 404

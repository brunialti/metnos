"""Tests per /agent/turn multipart uploads (ADR 0092, 5/5/2026).

Validano il branch multipart del handler `turn`: estrae `query` + tutti i
campi `image_*` come FileField, salva in /tmp/metnos_uploads/<sender>/, e
chiama run_turn con `reference_images=[paths]`. Il branch JSON resta
parallel-active (no shim retro-compat, just two branches by Content-Type).

Mock di aiohttp.web.Request a livello minimo cosi' da funzionare anche
quando jinja2 non e' nel venv (i template HTML non vengono toccati dal
branch multipart fino al run_turn return).

Run: `python3 -m pytest runtime/tests/test_http_multipart_uploads.py -xvs`.
"""
from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# Jinja2 e' importato indirettamente da http_render (admin templates HTML).
# Il branch multipart del handler `turn` non usa template HTML: stub jinja2
# in sys.modules quando manca dal venv, cosi' i test girano standalone.
def _stub_jinja2():
    if "jinja2" in sys.modules:
        return
    # Preferisci il jinja2 REALE se installato (prod + venv dev lo hanno): lo
    # stub serve SOLO quando manca davvero. Senza questa import-prima, lo stub
    # entrava in sys.modules a import-time di questo modulo e SHADOWAVA il
    # jinja2 reale per tutti i test successivi nel processo (leak ordine-dip.).
    try:
        import jinja2  # noqa: F401
        return
    except ImportError:
        pass
    j = type(sys)("jinja2")
    class _Env:
        def __init__(self, *a, **kw):
            # Parità col contratto reale di jinja2.Environment: http_render fa
            # `_jinja_env.globals["msg"] = ...` a import-time → senza questo
            # attributo lo stub esplode (AttributeError) appena http_render è
            # importato. Mappa globals/filters reali (dict mutabili).
            self.globals = {}
            self.filters = {}
        def get_template(self, *a, **kw):
            class _T:
                def render(self, *a, **kw): return ""
            return _T()
    j.Environment = _Env
    j.FileSystemLoader = lambda *a, **kw: None
    j.select_autoescape = lambda *a, **kw: None
    sys.modules["jinja2"] = j

_stub_jinja2()


class _StubTurnLog:
    def __init__(self):
        self.turn_id = "turn-stub-1"
        self.final_message = "ok"
        self.final_kind = "answer"
        self.ts_start = 0.0
        self.ts_end = 0.1
        self.steps = []
        self.expandable_caps = []
        self.attachments = []


class _FakeFileField:
    """Mock di aiohttp.web.FileField (campo file in multipart)."""
    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(data)


class _FakeMultiDict:
    """Multidict-like con .get() e .items() tipici di aiohttp form."""
    def __init__(self, items: list[tuple[str, object]]):
        self._items = items
    def get(self, key, default=None):
        for k, v in self._items:
            if k == key:
                return v
        return default
    def items(self):
        return list(self._items)
    def getall(self, key):
        return [v for k, v in self._items if k == key]


class _FakeRequest:
    """Mock minimo di aiohttp.web.Request per testare il handler."""
    def __init__(self, *, content_type: str, form_items=None,
                  json_body=None, headers=None, app=None):
        self.content_type = content_type
        self._form_items = form_items
        self._json_body = json_body
        self.headers = headers or {}
        self.app = app or {"admin_key": "test"}
    async def post(self):
        return _FakeMultiDict(self._form_items or [])
    async def json(self):
        if self._json_body is None:
            raise ValueError("no json")
        return self._json_body


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class HttpMultipartUploadsTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        td = Path(self._tmpdir.name)
        self._upload_root = td / "uploads"
        # Patch UPLOAD_DIR per non poller /tmp reale
        import upload_cleanup
        self._orig_upload = upload_cleanup.UPLOAD_DIR
        upload_cleanup.UPLOAD_DIR = self._upload_root
        # event loop fresco per ogni test
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()
        import upload_cleanup
        upload_cleanup.UPLOAD_DIR = self._orig_upload
        self._tmpdir.cleanup()

    # --- 1. JSON branch (no upload) -----------------------------------------

    def test_turn_json_branch_calls_run_turn_without_refs(self):
        """JSON request → run_turn senza reference_images."""
        # Lazy import per beccare la patch a UPLOAD_DIR
        import http_routes_agent as hra
        captured = {}
        def fake_run_turn(query, **kw):
            captured["query"] = query
            captured["refs"] = kw.get("reference_images")
            return _StubTurnLog()
        req = _FakeRequest(
            content_type="application/json",
            json_body={"query": "ciao mondo"},
            headers={"Accept": "application/json"},
        )
        with mock.patch.object(hra, "_save_cap_pending_if_any"), \
             mock.patch.object(hra, "_resolve_actor", return_value="host"), \
             mock.patch.object(hra, "_apply_cap_pending",
                               return_value=("ciao mondo", None, None)), \
             mock.patch("agent_runtime.run_turn", side_effect=fake_run_turn), \
             mock.patch.object(hra, "_gallery_url_for", return_value=(None, 0)), \
             mock.patch.object(hra, "_enrich_attachments", return_value=[]):
            resp = _run(hra.turn(req))
        self.assertEqual(resp.status, 200, getattr(resp, "text", str(resp)))
        self.assertEqual(captured["query"], "ciao mondo")
        self.assertIsNone(captured["refs"])

    # --- 2. multipart con 1 image ------------------------------------------

    def test_turn_multipart_single_image_saves_and_propagates(self):
        import http_routes_agent as hra
        captured = {}
        def fake_run_turn(query, **kw):
            captured["query"] = query
            captured["refs"] = list(kw.get("reference_images") or [])
            return _StubTurnLog()
        png = b"\x89PNG\r\n\x1a\nFAKE_BYTES_FOR_TEST"
        ff = _FakeFileField("ref.png", "image/png", png)
        req = _FakeRequest(
            content_type="multipart/form-data",
            form_items=[("query", "trova foto simili"), ("image_0", ff)],
            headers={"Accept": "application/json"},
        )
        with mock.patch.object(hra, "_save_cap_pending_if_any"), \
             mock.patch.object(hra, "_resolve_actor", return_value="host"), \
             mock.patch.object(hra, "_apply_cap_pending",
                               return_value=("trova foto simili", None, None)), \
             mock.patch("agent_runtime.run_turn", side_effect=fake_run_turn), \
             mock.patch.object(hra, "_gallery_url_for", return_value=(None, 0)), \
             mock.patch.object(hra, "_enrich_attachments", return_value=[]):
            resp = _run(hra.turn(req))
        self.assertEqual(resp.status, 200)
        self.assertEqual(captured["query"], "trova foto simili")
        refs = captured["refs"]
        self.assertEqual(len(refs), 1)
        self.assertTrue(Path(refs[0]).exists())
        self.assertEqual(Path(refs[0]).read_bytes(), png)
        self.assertTrue(refs[0].endswith(".png"))

    # --- 3. multipart con 3 images ----------------------------------------

    def test_turn_multipart_multiple_images(self):
        import http_routes_agent as hra
        captured = {}
        def fake_run_turn(q, **kw):
            captured["refs"] = list(kw.get("reference_images") or [])
            return _StubTurnLog()
        items = [("query", "test")]
        for i in range(3):
            items.append((f"image_{i}",
                           _FakeFileField(f"p{i}.jpg", "image/jpeg",
                                           f"IMG_{i}".encode())))
        req = _FakeRequest(
            content_type="multipart/form-data",
            form_items=items,
            headers={"Accept": "application/json"},
        )
        with mock.patch.object(hra, "_save_cap_pending_if_any"), \
             mock.patch.object(hra, "_resolve_actor", return_value="host"), \
             mock.patch.object(hra, "_apply_cap_pending",
                               return_value=("test", None, None)), \
             mock.patch("agent_runtime.run_turn", side_effect=fake_run_turn), \
             mock.patch.object(hra, "_gallery_url_for", return_value=(None, 0)), \
             mock.patch.object(hra, "_enrich_attachments", return_value=[]):
            resp = _run(hra.turn(req))
        self.assertEqual(resp.status, 200)
        refs = captured["refs"]
        self.assertEqual(len(refs), 3)
        contents = sorted(Path(p).read_bytes() for p in refs)
        self.assertEqual(contents, [b"IMG_0", b"IMG_1", b"IMG_2"])

    # --- 4. multipart skips non-image fields ------------------------------

    def test_turn_multipart_skips_non_image_files(self):
        import http_routes_agent as hra
        captured = {}
        def fake_run_turn(q, **kw):
            captured["refs"] = list(kw.get("reference_images") or [])
            return _StubTurnLog()
        items = [
            ("query", "test"),
            ("image_0", _FakeFileField("doc.pdf", "application/pdf", b"PDF")),
            ("image_1", _FakeFileField("real.jpg", "image/jpeg", b"REAL")),
        ]
        req = _FakeRequest(
            content_type="multipart/form-data",
            form_items=items,
            headers={"Accept": "application/json"},
        )
        with mock.patch.object(hra, "_save_cap_pending_if_any"), \
             mock.patch.object(hra, "_resolve_actor", return_value="host"), \
             mock.patch.object(hra, "_apply_cap_pending",
                               return_value=("test", None, None)), \
             mock.patch("agent_runtime.run_turn", side_effect=fake_run_turn), \
             mock.patch.object(hra, "_gallery_url_for", return_value=(None, 0)), \
             mock.patch.object(hra, "_enrich_attachments", return_value=[]):
            resp = _run(hra.turn(req))
        self.assertEqual(resp.status, 200)
        refs = captured["refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(Path(refs[0]).read_bytes(), b"REAL")

    # --- 5. multipart con query mancante → 400 ----------------------------

    def test_turn_multipart_missing_query_rejected(self):
        import http_routes_agent as hra
        items = [
            ("image_0", _FakeFileField("p.jpg", "image/jpeg", b"x")),
        ]
        req = _FakeRequest(
            content_type="multipart/form-data",
            form_items=items,
            headers={"Accept": "application/json"},
        )
        resp = _run(hra.turn(req))
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()

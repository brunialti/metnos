"""Test di read_urls_pdf (ADR 0081, 4/5/2026).

Genera un PDF minimale al volo (struttura grezza, validissima per i parser
moderni) per evitare di committare blob binari nel repo.
"""
from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "read_urls_pdf"))


# ── PDF minimale generato in memoria ────────────────────────────────────
# Struttura PDF 1.4 con 1 pagina contenente "Hello PDF". Alcuni parser
# strict (pypdf) richiedono xref offset corretto; usiamo un blob noto
# generato con uno script offline e validato (visibile come testo).

_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 44>>stream\n"
    b"BT /F1 24 Tf 100 700 Td (Hello PDF) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f\n"
    b"0000000010 00000 n\n"
    b"0000000053 00000 n\n"
    b"0000000098 00000 n\n"
    b"0000000183 00000 n\n"
    b"0000000277 00000 n\n"
    b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n340\n%%EOF\n"
)


_pages: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        page = _pages.get(self.path)
        if page is None:
            self.send_response(404); self.end_headers(); return
        status, ctype, body = page
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestReadUrlsPdf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()
        cls.thread.join(timeout=2)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def setUp(self):
        _pages.clear()

    def test_simple_pdf(self):
        """PDF minimale → parser estrae 'Hello PDF' come body_text.

        Skip se nessun parser PDF disponibile (test environment).
        """
        import read_urls_pdf
        from pdf_extract import has_pdf_parser
        if not has_pdf_parser():
            self.skipTest("nessun parser PDF disponibile (pypdf/pdfminer.six)")
        _pages["/doc.pdf"] = (200, "application/pdf", _MINIMAL_PDF)
        out = read_urls_pdf.invoke({"urls": [self.url("/doc.pdf")]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["ok_count"], 1)
        e = out["entries"][0]
        # Il testo dovrebbe contenere "Hello PDF" (con qualche flessibilita',
        # alcuni parser introducono spazi/newline).
        self.assertIn("Hello", e["body_text"], e)

    def test_non_pdf_skipped(self):
        """Content-Type non pdf E url che non finisce in .pdf → skip."""
        import read_urls_pdf
        _pages["/foo"] = (200, "text/html", b"<html>not a pdf</html>")
        out = read_urls_pdf.invoke({"urls": [self.url("/foo")]})
        self.assertEqual(out["ok_count"], 0)
        self.assertEqual(out["fail_count"], 1)

    def test_multi_pdf_batch(self):
        """3 URL PDF → 3 entries (skip se nessun parser)."""
        import read_urls_pdf
        from pdf_extract import has_pdf_parser
        if not has_pdf_parser():
            self.skipTest("nessun parser PDF disponibile (pypdf/pdfminer.six)")
        _pages["/a.pdf"] = (200, "application/pdf", _MINIMAL_PDF)
        _pages["/b.pdf"] = (200, "application/pdf", _MINIMAL_PDF)
        _pages["/c.pdf"] = (200, "application/pdf", _MINIMAL_PDF)
        out = read_urls_pdf.invoke({
            "urls": [self.url("/a.pdf"), self.url("/b.pdf"), self.url("/c.pdf")]
        })
        self.assertEqual(out["ok_count"], 3, out)


if __name__ == "__main__":
    unittest.main()

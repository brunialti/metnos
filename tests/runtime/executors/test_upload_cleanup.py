"""Tests per upload_cleanup.sweep_old_uploads (ADR 0092, 5/5/2026).

Run: `python3 -m pytest tests/runtime/executors/test_upload_cleanup.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class UploadCleanupTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._td = Path(self._tmpdir.name)
        self._uploads = self._td / "uploads"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_file(self, rel: str, *, age_s: float = 0,
                    content: bytes = b"x") -> Path:
        p = self._uploads / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        if age_s > 0:
            t = time.time() - age_s
            os.utime(p, (t, t))
        return p

    def test_sweep_removes_old_files(self):
        from upload_cleanup import sweep_old_uploads
        old = self._make_file("sender1/old.jpg", age_s=7200)
        new = self._make_file("sender1/new.jpg", age_s=0)
        res = sweep_old_uploads(base_dir=self._uploads, ttl_s=3600)
        self.assertTrue(res["ok"])
        self.assertEqual(res["removed"], 1)
        self.assertEqual(res["kept"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_sweep_skips_pending_burst_buffer(self):
        """Il dir _pending_burst (media_group buffer del daemon) ha TTL
        diverso e non deve essere toccato dal sweep."""
        from upload_cleanup import sweep_old_uploads
        burst = self._make_file(
            "_pending_burst/sender1/group1.json", age_s=7200,
        )
        normal_old = self._make_file("sender1/old.jpg", age_s=7200)
        res = sweep_old_uploads(base_dir=self._uploads, ttl_s=3600)
        self.assertTrue(burst.exists(),
                        "_pending_burst file deve essere preservato")
        self.assertFalse(normal_old.exists())
        self.assertEqual(res["removed"], 1)

    def test_sweep_no_files(self):
        from upload_cleanup import sweep_old_uploads
        res = sweep_old_uploads(base_dir=self._uploads, ttl_s=3600)
        self.assertTrue(res["ok"])
        self.assertEqual(res["removed"], 0)
        self.assertEqual(res["kept"], 0)

    def test_sweep_missing_base_dir(self):
        from upload_cleanup import sweep_old_uploads
        res = sweep_old_uploads(base_dir=self._td / "nonexistent",
                                 ttl_s=3600)
        self.assertTrue(res["ok"])
        self.assertEqual(res["removed"], 0)


if __name__ == "__main__":
    unittest.main()

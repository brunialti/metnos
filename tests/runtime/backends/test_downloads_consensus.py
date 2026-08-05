"""Test install/downloads.py per-chunk CONSENSUS logic (deterministic, no net).

Mocks `_one_fetch` to verify `_fetch_chunk`:
  - consensus=False: one good fetch → written.
  - consensus=True: two AGREEING fetches → written.
  - consensus=True: disagreeing (non-deterministic corruption) → retries, only
    accepts when two agree; never writes mismatched bytes.
  - None (reset) → retried, not written.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = (Path(__file__).resolve().parents[3] / "runtime").parent

from install import downloads  # noqa: E402


class TestConsensus(unittest.TestCase):
    def setUp(self):
        self._orig = downloads._one_fetch
        self.tmp = Path(tempfile.mkdtemp()) / "f.bin"
        self.fd = os.open(self.tmp, os.O_RDWR | os.O_CREAT, 0o644)
        os.ftruncate(self.fd, 8)

    def tearDown(self):
        downloads._one_fetch = self._orig
        os.close(self.fd)

    def _written(self) -> bytes:
        return os.pread(self.fd, 8, 0)

    def test_single_fetch_writes(self):
        downloads._one_fetch = lambda *a, **k: b"AAAAAAAA"
        ok = downloads._fetch_chunk("u", self.fd, 0, 7, 1.0, consensus=False)
        self.assertTrue(ok)
        self.assertEqual(self._written(), b"AAAAAAAA")

    def test_consensus_two_agree_writes(self):
        downloads._one_fetch = lambda *a, **k: b"BBBBBBBB"
        ok = downloads._fetch_chunk("u", self.fd, 0, 7, 1.0, consensus=True)
        self.assertTrue(ok)
        self.assertEqual(self._written(), b"BBBBBBBB")

    def test_consensus_rejects_until_agreement(self):
        # Non-deterministic corruption: each pair of fetches differs, until the
        # 4th/5th call where two consecutive agree. Consensus must wait for it.
        seq = iter([b"XXXXXXXX", b"YYYYYYYY",   # pair 1: disagree
                    b"ZZZZZZZZ", b"11111111",   # pair 2: disagree
                    b"GOODGOOD", b"GOODGOOD"])   # pair 3: agree → accept
        downloads._one_fetch = lambda *a, **k: next(seq)
        ok = downloads._fetch_chunk("u", self.fd, 0, 7, 0.0, consensus=True)
        self.assertTrue(ok)
        self.assertEqual(self._written(), b"GOODGOOD")  # never a mismatched copy

    def test_none_is_retried_not_written(self):
        # Always-None (persistent reset) → exhausts attempts → False, nothing written.
        downloads._one_fetch = lambda *a, **k: None
        ok = downloads._fetch_chunk("u", self.fd, 0, 7, 0.0, consensus=False)
        self.assertFalse(ok)
        self.assertEqual(self._written(), b"\x00" * 8)


if __name__ == "__main__":
    unittest.main()

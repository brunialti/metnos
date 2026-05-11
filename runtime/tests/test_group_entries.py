"""Test di group_entries (4/5/2026)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "group_entries"))


class TestGroupEntries(unittest.TestCase):
    def test_merge_with_dedup(self):
        import group_entries
        a = [{"url": "x"}, {"url": "y"}]
        b = [{"url": "y"}, {"url": "z"}]
        out = group_entries.invoke({"entries_lists": [a, b]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 3)
        self.assertEqual(out["dedupes"], 1)
        urls = sorted(e["url"] for e in out["entries"])
        self.assertEqual(urls, ["x", "y", "z"])

    def test_no_dedup(self):
        import group_entries
        a = [{"url": "x"}, {"url": "x"}]
        out = group_entries.invoke({"entries_lists": [a], "dedup_key": None})
        self.assertEqual(out["ok_count"], 2)
        self.assertEqual(out["dedupes"], 0)


if __name__ == "__main__":
    unittest.main()

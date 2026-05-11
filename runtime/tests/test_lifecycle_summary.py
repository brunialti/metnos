"""Test deterministici per `runtime/lifecycle_summary.py` (ADR 0097).

Niente DB reale. Patch delle directory verso tmp_dir isolato.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


class TestCollectSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.aging_dir = Path(self.tmp) / "aging"
        self.intro_dir = Path(self.tmp) / "introvertiva"
        self.life_dir = Path(self.tmp) / "lifecycle"
        for d in (self.aging_dir, self.intro_dir, self.life_dir):
            d.mkdir()
        self._patches = [
            mock.patch("lifecycle_summary.AGING_DIR", self.aging_dir),
            mock.patch("lifecycle_summary.INTROVERTIVA_DIR", self.intro_dir),
            mock.patch("lifecycle_summary.LIFECYCLE_DIR", self.life_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp)

    def _mk(self, dir_path: Path, name: str, content: str, *,
             ts: int | None = None):
        p = dir_path / name
        p.write_text(content)
        if ts is not None:
            import os
            os.utime(p, (ts, ts))
        return p

    def test_empty_dirs_produce_empty_sections(self):
        from lifecycle_summary import collect_summary
        s = collect_summary()
        self.assertIn("executor_ager", s)
        self.assertIsNone(s["executor_ager"]["data"])
        self.assertEqual(s["introvertiva_apply"]["data"], None)

    def test_executor_ager_picked(self):
        from lifecycle_summary import collect_summary
        report = {"deprecated": ["a", "b"], "archived": ["c"],
                  "protected_skipped": 5, "total_seen": 50}
        self._mk(self.aging_dir, "executor_ager_999.jsonl",
                  json.dumps(report) + "\n")
        s = collect_summary(window_hours=999999)
        self.assertEqual(s["executor_ager"]["data"]["deprecated"], ["a", "b"])

    def test_introvertiva_apply_count(self):
        from lifecycle_summary import collect_summary
        # 3 record applicati
        records = [json.dumps({"name": "x"}) for _ in range(3)]
        self._mk(self.intro_dir, "auto_applied_888.jsonl",
                  "\n".join(records) + "\n")
        s = collect_summary(window_hours=999999)
        self.assertEqual(s["introvertiva_apply"]["data"]["applied_count"], 3)

    def test_propose_per_kind(self):
        from lifecycle_summary import collect_summary
        # 5 records specialize, 2 generalize, 1 dedupe
        self._mk(self.intro_dir, "candidates_specialize_111.jsonl",
                  "\n".join(['{"a":1}'] * 5) + "\n")
        self._mk(self.intro_dir, "candidates_generalize_222.jsonl",
                  "\n".join(['{"a":1}'] * 2) + "\n")
        self._mk(self.intro_dir, "candidates_dedupe_333.jsonl",
                  '{"a":1}\n')
        s = collect_summary(window_hours=999999)
        d = s["introvertiva_propose"]["data"]
        self.assertEqual(d["specialize"]["records"], 5)
        self.assertEqual(d["generalize"]["records"], 2)
        self.assertEqual(d["dedupe"]["records"], 1)

    def test_proposals_cleanup_picked(self):
        from lifecycle_summary import collect_summary
        report = {"synth_proposals": {"archived": 7},
                  "legacy_orphan_mnests": {"decayed": 1}}
        self._mk(self.life_dir, "proposals_cleanup_555.jsonl",
                  json.dumps(report) + "\n")
        s = collect_summary(window_hours=999999)
        self.assertEqual(s["proposals_cleanup"]["data"]["synth_proposals"]["archived"], 7)

    def test_window_filter_excludes_old(self):
        from lifecycle_summary import collect_summary
        old_ts = int(time.time()) - 30 * 3600  # 30h fa
        self._mk(self.aging_dir, "executor_ager_old.jsonl",
                  json.dumps({"deprecated": ["x"]}) + "\n", ts=old_ts)
        s = collect_summary(window_hours=24)
        self.assertIsNone(s["executor_ager"]["data"])


class TestFormatSummary(unittest.TestCase):
    def test_renders_all_sections(self):
        from lifecycle_summary import format_summary
        summary = {
            "executor_ager": {
                "data": {"deprecated": ["a"], "archived": [],
                         "protected_skipped": 23, "total_seen": 55},
            },
            "introvertiva_apply": {"data": {"applied_count": 1}},
            "introvertiva_propose": {
                "data": {
                    "dedupe": {"records": 1},
                    "specialize": {"records": 8},
                    "generalize": {"records": 1},
                },
            },
            "proposals_cleanup": {
                "data": {
                    "synth_proposals": {"archived": 56},
                    "introvertiva_dedup": {"removed_records": 13},
                    "introvertiva_snapshots": {"archived": 9},
                    "legacy_orphan_mnests": {"decayed": 1},
                },
            },
        }
        out = format_summary(summary, window_hours=24)
        self.assertIn("Lifecycle Summary", out)
        self.assertIn("**Executor ager**", out)
        self.assertIn("**Introvertiva apply**", out)
        self.assertIn("**Proposals cleanup**", out)

    def test_missing_section_shown_as_no_audit(self):
        from lifecycle_summary import format_summary
        summary = {
            "executor_ager": {"data": None, "note": "nessun audit recente"},
            "introvertiva_apply": {"data": None, "note": "nessun audit"},
            "introvertiva_propose": {"data": {}},
            "proposals_cleanup": {"data": None, "note": "nessun audit"},
        }
        out = format_summary(summary, window_hours=24)
        self.assertIn("nessun audit", out)


if __name__ == "__main__":
    unittest.main()

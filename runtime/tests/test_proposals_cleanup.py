"""Test deterministici per `runtime/proposals_cleanup.py` (ADR 0096).

Lavora su tmp_dir isolato, nessuna interazione con DB reale.
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


def _write_proposal(d: Path, ts: int, name: str, *,
                     final_state: str = "synthesized",
                     expected_name: str | None = None) -> Path:
    p = d / f"{ts}_{name}.json"
    doc = {
        "id": f"{ts}_{name}",
        "expected_name": expected_name or name,
        "name": name,
        "final_state": final_state,
        "ts_start": ts,
        "stages": [],
    }
    p.write_text(json.dumps(doc))
    import os
    os.utime(p, (ts, ts))
    return p


class TestArchiveSynthProposals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp) / "synt_proposals"
        self.dir.mkdir()
        # Patch SYNT_PROPOSALS_DIR per i test
        self._patcher = mock.patch(
            "proposals_cleanup.SYNT_PROPOSALS_DIR", self.dir,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp)

    def test_archives_synthesized_in_catalog(self):
        from proposals_cleanup import archive_aged_synth_proposals
        # Recente, executor X gia' nel catalog
        _write_proposal(self.dir, int(time.time()) - 100, "compress_files_gz",
                          final_state="synthesized")
        with mock.patch("proposals_cleanup._fallback_catalog_names",
                          return_value={"compress_files_gz"}), \
             mock.patch("loader.load_catalog", side_effect=Exception("force fallback")):
            res = archive_aged_synth_proposals(max_age_days=999)
        self.assertEqual(res["archived"], 1)
        self.assertEqual(res["kept"], 0)

    def test_archives_aged_regardless_of_state(self):
        from proposals_cleanup import archive_aged_synth_proposals
        old_ts = int(time.time()) - 60 * 86400  # 60 giorni
        _write_proposal(self.dir, old_ts, "x_old",
                          final_state="abandoned")
        with mock.patch("proposals_cleanup._fallback_catalog_names",
                          return_value=set()), \
             mock.patch("loader.load_catalog", side_effect=Exception("force fallback")):
            res = archive_aged_synth_proposals(max_age_days=30)
        self.assertEqual(res["archived"], 1)

    def test_keeps_recent_not_in_catalog(self):
        from proposals_cleanup import archive_aged_synth_proposals
        _write_proposal(self.dir, int(time.time()) - 100, "y_new",
                          final_state="synthesized")
        with mock.patch("proposals_cleanup._fallback_catalog_names",
                          return_value=set()), \
             mock.patch("loader.load_catalog", side_effect=Exception("force fallback")):
            res = archive_aged_synth_proposals(max_age_days=30)
        self.assertEqual(res["archived"], 0)
        self.assertEqual(res["kept"], 1)

    def test_dry_run_does_not_move(self):
        from proposals_cleanup import archive_aged_synth_proposals
        old_ts = int(time.time()) - 60 * 86400
        p = _write_proposal(self.dir, old_ts, "z_old")
        with mock.patch("proposals_cleanup._fallback_catalog_names",
                          return_value=set()), \
             mock.patch("loader.load_catalog", side_effect=Exception("force fallback")):
            res = archive_aged_synth_proposals(max_age_days=30, dry_run=True)
        self.assertEqual(res["archived"], 1)
        # File ancora al posto originale
        self.assertTrue(p.exists())


class TestDedupeIntrovertiva(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp) / "introvertiva"
        self.dir.mkdir()
        self._patcher = mock.patch(
            "proposals_cleanup.INTROVERTIVA_DIR", self.dir,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp)

    def test_dedup_keeps_highest_uses(self):
        from proposals_cleanup import dedupe_introvertiva_candidates
        f = self.dir / f"candidates_dedupe_{int(time.time())}.jsonl"
        f.write_text("\n".join([
            json.dumps({"kind": "legacy_orphan",
                          "src_executor": "fetch_urls",
                          "dst_executor": "write_files",
                          "uses": 10, "weight": 0.5}),
            json.dumps({"kind": "legacy_orphan",
                          "src_executor": "fetch_urls",
                          "dst_executor": "write_files",
                          "uses": 49, "weight": 0.876}),
            json.dumps({"kind": "legacy_orphan",
                          "src_executor": "fetch_urls",
                          "dst_executor": "write_files",
                          "uses": 30, "weight": 0.7}),
        ]))
        res = dedupe_introvertiva_candidates(retention_days=999)
        self.assertEqual(res["removed_records"], 2)
        # Il file deve contenere SOLO il record con uses=49
        lines = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["uses"], 49)

    def test_dedup_different_signatures_kept(self):
        from proposals_cleanup import dedupe_introvertiva_candidates
        f = self.dir / f"candidates_dedupe_{int(time.time())}.jsonl"
        f.write_text("\n".join([
            json.dumps({"kind": "legacy_orphan",
                          "src_executor": "a", "dst_executor": "b", "uses": 5}),
            json.dumps({"kind": "legacy_orphan",
                          "src_executor": "c", "dst_executor": "d", "uses": 5}),
        ]))
        res = dedupe_introvertiva_candidates(retention_days=999)
        self.assertEqual(res["removed_records"], 0)
        lines = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)

    def test_archive_aged_files(self):
        from proposals_cleanup import dedupe_introvertiva_candidates
        old_ts = int(time.time()) - 30 * 86400
        f = self.dir / f"candidates_dedupe_{old_ts}.jsonl"
        f.write_text(json.dumps({"kind": "x", "src_executor": "a",
                                    "dst_executor": "b", "uses": 1}) + "\n")
        import os
        os.utime(f, (old_ts, old_ts))
        res = dedupe_introvertiva_candidates(retention_days=7)
        self.assertEqual(res["archived"], 1)
        self.assertFalse(f.exists())
        # Esiste sotto _archived/<year>/<month>/
        archived = list((self.dir / "_archived").rglob("*.jsonl"))
        self.assertEqual(len(archived), 1)

    def test_tiebreaker_weight(self):
        from proposals_cleanup import dedupe_introvertiva_candidates
        f = self.dir / f"candidates_dedupe_{int(time.time())}.jsonl"
        f.write_text("\n".join([
            json.dumps({"kind": "x", "src_executor": "a",
                          "dst_executor": "b", "uses": 5, "weight": 0.5}),
            json.dumps({"kind": "x", "src_executor": "a",
                          "dst_executor": "b", "uses": 5, "weight": 0.8}),
        ]))
        dedupe_introvertiva_candidates(retention_days=999)
        lines = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["weight"], 0.8)


class TestCandidateSignature(unittest.TestCase):
    def test_legacy_orphan_signature(self):
        from proposals_cleanup import _candidate_signature
        s1 = _candidate_signature({
            "kind": "legacy_orphan", "src_executor": "fetch_urls",
            "dst_executor": "write_files",
        })
        s2 = _candidate_signature({
            "kind": "legacy_orphan", "src_executor": "fetch_urls",
            "dst_executor": "write_files",
        })
        self.assertEqual(s1, s2)

    def test_specialize_signature(self):
        from proposals_cleanup import _candidate_signature
        s = _candidate_signature({
            "executor": "list_dirs", "arg_name": "path",
            "dominant_value": '"/tmp"', "proposed_name": "list_dirs_tmp",
        }, kind_from_file="specialize")
        self.assertEqual(s[0], "specialize")
        self.assertIn("list_dirs", s)

    def test_generalize_signature_pattern_tuple(self):
        from proposals_cleanup import _candidate_signature
        s = _candidate_signature({
            "pattern": ["find_files", "get_files"],
            "uses": 4,
        }, kind_from_file="generalize")
        self.assertEqual(s[0], "generalize")
        self.assertEqual(s[1], ("find_files", "get_files"))


class TestKindFromFilename(unittest.TestCase):
    def test_dedupe(self):
        from proposals_cleanup import _kind_from_filename
        self.assertEqual(
            _kind_from_filename(Path("candidates_dedupe_1234567.jsonl")),
            "dedupe",
        )

    def test_specialize(self):
        from proposals_cleanup import _kind_from_filename
        self.assertEqual(
            _kind_from_filename(Path("candidates_specialize_999.jsonl")),
            "specialize",
        )

    def test_unknown(self):
        from proposals_cleanup import _kind_from_filename
        self.assertEqual(_kind_from_filename(Path("other.jsonl")), "")


class TestKeepLatestN(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp) / "introvertiva"
        self.dir.mkdir()
        self._patcher = mock.patch(
            "proposals_cleanup.INTROVERTIVA_DIR", self.dir,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp)

    def _mk(self, kind: str, ts: int):
        p = self.dir / f"candidates_{kind}_{ts}.jsonl"
        p.write_text("{}\n")
        import os
        os.utime(p, (ts, ts))
        return p

    def test_keeps_latest_n_per_kind(self):
        from proposals_cleanup import keep_latest_n_per_kind
        for k in ("specialize", "dedupe", "generalize"):
            for i in range(5):
                self._mk(k, 1000 + i * 100)
        res = keep_latest_n_per_kind(n=2)
        # 5 file × 3 kind = 15; tieni 2 × 3 = 6; archivia 9
        self.assertEqual(res["archived"], 9)
        self.assertEqual(res["kept"], 6)

    def test_n_larger_than_count(self):
        from proposals_cleanup import keep_latest_n_per_kind
        self._mk("dedupe", 1000)
        res = keep_latest_n_per_kind(n=10)
        self.assertEqual(res["archived"], 0)
        self.assertEqual(res["kept"], 1)


class TestCandidateCost(unittest.TestCase):
    def test_uses_takes_priority(self):
        from proposals_cleanup import _candidate_cost
        a = _candidate_cost({"uses": 50, "weight": 0.1})
        b = _candidate_cost({"uses": 30, "weight": 0.9})
        self.assertGreater(a, b)

    def test_total_uses_fallback(self):
        from proposals_cleanup import _candidate_cost
        c = _candidate_cost({"total_uses": 99, "dominance": 0.7})
        self.assertEqual(c[0], 99)


if __name__ == "__main__":
    unittest.main()

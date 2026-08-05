"""Test di build_orchestrator (ADR 0093, 6/5/2026).

Mock di subprocess.run per verificare la composizione del comando systemd-run
e il routing delle alternative (already_running, error, unit-naming).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class TestUnitNaming(unittest.TestCase):
    def test_unit_name_deterministic_per_path(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            n1 = bo._unit_name(base, "unified")
            n2 = bo._unit_name(base, "unified")
            self.assertEqual(n1, n2)
            self.assertTrue(n1.startswith("metnos-build-"))
            self.assertTrue(n1.endswith("-unified"))

    def test_unit_name_is_stable_for_unified_index(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(bo._unit_name(base, "unified"),
                             bo._unit_name(base, "unified"))

    def test_unit_name_differs_per_base_path(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            self.assertNotEqual(bo._unit_name(Path(tmp1), "unified"),
                                bo._unit_name(Path(tmp2), "unified"))


class TestStartAsyncBuild(unittest.TestCase):
    def test_start_invalid_idx(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            out = bo.start_async_build(tmp, "invalid_idx")
            self.assertFalse(out["ok"])
            self.assertIn("idx", out["error"])

    def test_start_missing_base_path(self):
        import build_orchestrator as bo
        out = bo.start_async_build("/nonexistent/path/zzz", "unified")
        self.assertFalse(out["ok"])

    def test_start_already_running_returns_progress(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(bo, "_is_unit_active", return_value=True):
                with patch.object(bo, "_read_progress",
                                    return_value={"state": "running",
                                                   "n_done": 100,
                                                   "n_total": 1000}):
                    out = bo.start_async_build(base, "unified")
            self.assertTrue(out["ok"])
            self.assertTrue(out.get("already_running"))
            self.assertEqual(out["progress"]["n_done"], 100)

    def test_start_systemd_run_invoked(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mock_proc = MagicMock(returncode=0, stdout="", stderr="")
            with patch.object(bo, "_is_unit_active", return_value=False):
                with patch.object(bo, "_is_unit_failed", return_value=False):
                    with patch("build_orchestrator.subprocess.run",
                                  return_value=mock_proc) as run_patch:
                        out = bo.start_async_build(base, "unified",
                                                     actor="alice",
                                                     channel="telegram",
                                                     chat_id="42")
            self.assertTrue(out["ok"])
            self.assertTrue(out.get("build_started"))
            cmd = run_patch.call_args[0][0]
            self.assertEqual(cmd[0], "systemd-run")
            self.assertIn("--user", cmd)
            self.assertIn("-m", cmd)
            self.assertIn("build_runner", cmd)
            self.assertIn("--idx", cmd)
            self.assertIn("unified", cmd)
            self.assertIn("--actor", cmd)
            self.assertIn("alice", cmd)
            self.assertIn("--channel", cmd)
            self.assertIn("telegram", cmd)

    def test_start_systemd_run_failed(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mock_proc = MagicMock(returncode=1, stdout="",
                                    stderr="systemd-run: error")
            with patch.object(bo, "_is_unit_active", return_value=False):
                with patch.object(bo, "_is_unit_failed", return_value=False):
                    with patch("build_orchestrator.subprocess.run",
                                  return_value=mock_proc):
                        out = bo.start_async_build(base, "unified")
            self.assertFalse(out["ok"])
            self.assertIn("systemd-run failed", out["error"])

    def test_start_already_exists_race(self):
        """systemd-run fallisce con 'already exists' → trattato come running."""
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mock_proc = MagicMock(returncode=1, stdout="",
                                    stderr="Unit metnos-build-x already exists")
            with patch.object(bo, "_is_unit_active",
                                side_effect=[False, True]):
                with patch.object(bo, "_is_unit_failed", return_value=False):
                    with patch.object(bo, "_read_progress",
                                       return_value={"state": "running"}):
                        with patch("build_orchestrator.subprocess.run",
                                       return_value=mock_proc):
                            out = bo.start_async_build(base, "unified")
            self.assertTrue(out["ok"])
            self.assertTrue(out.get("already_running"))


class TestGetBuildStatus(unittest.TestCase):
    def test_status_none_if_never_run(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bo, "_is_unit_active", return_value=False):
                out = bo.get_build_status(tmp, "unified")
            self.assertIsNone(out)

    def test_status_returns_progress(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(bo, "_is_unit_active", return_value=True):
                with patch.object(bo, "_read_progress",
                                    return_value={"state": "running",
                                                   "n_done": 50,
                                                   "n_total": 200,
                                                   "last_update": 0}):
                    out = bo.get_build_status(base, "unified")
            self.assertEqual(out["state"], "running")
            self.assertTrue(out["unit_active"])
            self.assertIn("unit_name", out)


class TestStopBuild(unittest.TestCase):
    def test_stop_invokes_systemctl(self):
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(bo, "_systemctl_user",
                                return_value=(0, "", "")) as sysctl:
                with patch.object(bo, "cleanup_orphan_tmp_dirs",
                                    return_value={"swept": [], "skipped": []}):
                    out = bo.stop_build(base, "unified")
            self.assertTrue(out["ok"])
            args = sysctl.call_args[0]
            self.assertEqual(args[0], "stop")


class TestCleanupOrphanTmpDirs(unittest.TestCase):
    def test_cleanup_recent_skipped(self):
        import build_orchestrator as bo
        # Default max_age_s 7 days: un temporaneo recente viene skippato.
        with tempfile.TemporaryDirectory() as tmp:
            # Mock _INDEX_BASE per non toccare la home reale
            orig = bo._INDEX_BASE
            try:
                bo._INDEX_BASE = Path(tmp)
                digest = "abc12345"
                d = Path(tmp) / digest / "unified"
                d.mkdir(parents=True)
                (d / "entries.tmp").write_text("partial")
                res = bo.cleanup_orphan_tmp_dirs(max_age_s=7 * 86400)
                self.assertEqual(len(res["swept"]), 0)
                self.assertEqual(len(res["skipped"]), 1)
            finally:
                bo._INDEX_BASE = orig

    def test_cleanup_old_swept(self):
        import build_orchestrator as bo
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            orig = bo._INDEX_BASE
            orig_pd = bo._PROGRESS_DIR
            try:
                bo._INDEX_BASE = Path(tmp)
                bo._PROGRESS_DIR = Path(tmp) / "progress"
                digest = "abc12345"
                d = Path(tmp) / digest / "unified"
                d.mkdir(parents=True)
                tmp_d = d / "entries.tmp"
                tmp_d.write_text("partial")
                # Backdate mtime 30 giorni
                old = 30 * 86400
                _os.utime(tmp_d, (0, _os.stat(tmp_d).st_mtime - old))
                res = bo.cleanup_orphan_tmp_dirs(max_age_s=7 * 86400)
                self.assertEqual(len(res["swept"]), 1)
            finally:
                bo._INDEX_BASE = orig
                bo._PROGRESS_DIR = orig_pd


if __name__ == "__main__":
    unittest.main()

"""Test di http_async_tasks (ADR 0093, 6/5/2026).

Verifica i 3 task async:
  - run_healthcheck_once: stale detection + abort marking
  - run_dispatcher_once: lettura marker + send_messages mock + archive
  - run_sweeper_once: cleanup orphan tmp + archive scaduti
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestHealthcheck(unittest.TestCase):
    def test_healthcheck_no_progress_dir(self):
        import http_async_tasks as hat
        with tempfile.TemporaryDirectory() as tmp:
            orig = hat._PROGRESS_DIR
            try:
                hat._PROGRESS_DIR = Path(tmp) / "nope"
                res = hat.run_healthcheck_once()
                self.assertEqual(res["checked"], 0)
            finally:
                hat._PROGRESS_DIR = orig

    def test_healthcheck_skips_done(self):
        import http_async_tasks as hat
        with tempfile.TemporaryDirectory() as tmp:
            orig = hat._PROGRESS_DIR
            try:
                hat._PROGRESS_DIR = Path(tmp)
                fp = Path(tmp) / "abc12345_scene.json"
                fp.write_text(json.dumps({
                    "state": "done",
                    "base_path": "/foo", "idx": "scene",
                    "last_update": time.time(),
                }), encoding="utf-8")
                res = hat.run_healthcheck_once()
                self.assertEqual(res["checked"], 1)
                self.assertEqual(res["stale_killed"], 0)
                self.assertEqual(res["aborted"], 0)
            finally:
                hat._PROGRESS_DIR = orig

    def test_healthcheck_stale_unit_active_kills(self):
        import http_async_tasks as hat
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            orig = hat._PROGRESS_DIR
            try:
                hat._PROGRESS_DIR = Path(tmp)
                fp = Path(tmp) / "abc12345_scene.json"
                fp.write_text(json.dumps({
                    "state": "running",
                    "base_path": "/foo", "idx": "scene",
                    "last_update": time.time() - 600.0,  # >5 min ago
                    "n_done": 100, "n_total": 1000,
                }), encoding="utf-8")
                with patch.object(bo, "_is_unit_active", return_value=True):
                    with patch.object(bo, "stop_build",
                                        return_value={"ok": True}) as stop:
                        res = hat.run_healthcheck_once()
                self.assertEqual(res["stale_killed"], 1)
                stop.assert_called_once()
                # Stato in progress.json marcato aborted
                data = json.loads(fp.read_text(encoding="utf-8"))
                self.assertEqual(data["state"], "aborted")
            finally:
                hat._PROGRESS_DIR = orig

    def test_healthcheck_orphan_unit_dead_aborted(self):
        import http_async_tasks as hat
        import build_orchestrator as bo
        with tempfile.TemporaryDirectory() as tmp:
            orig = hat._PROGRESS_DIR
            try:
                hat._PROGRESS_DIR = Path(tmp)
                fp = Path(tmp) / "abc12345_scene.json"
                fp.write_text(json.dumps({
                    "state": "running",
                    "base_path": "/foo", "idx": "scene",
                    "last_update": time.time(),
                }), encoding="utf-8")
                with patch.object(bo, "_is_unit_active", return_value=False):
                    res = hat.run_healthcheck_once()
                self.assertEqual(res["aborted"], 1)
                data = json.loads(fp.read_text(encoding="utf-8"))
                self.assertEqual(data["state"], "aborted")
            finally:
                hat._PROGRESS_DIR = orig


class TestDispatcher(unittest.TestCase):
    def test_dispatcher_no_dir(self):
        import http_async_tasks as hat
        with tempfile.TemporaryDirectory() as tmp:
            orig = hat._COMPLETE_DIR
            try:
                hat._COMPLETE_DIR = Path(tmp) / "nope"
                res = hat.run_dispatcher_once()
                self.assertEqual(res["dispatched"], 0)
            finally:
                hat._COMPLETE_DIR = orig

    def test_dispatcher_invokes_send_messages(self):
        import http_async_tasks as hat
        with tempfile.TemporaryDirectory() as tmp:
            orig_complete = hat._COMPLETE_DIR
            orig_archive = hat._COMPLETE_ARCHIVE
            try:
                hat._COMPLETE_DIR = Path(tmp) / "complete"
                hat._COMPLETE_ARCHIVE = Path(tmp) / "archive"
                hat._COMPLETE_DIR.mkdir()
                # Crea marker
                marker = hat._COMPLETE_DIR / "abc12345_scene.json"
                marker.write_text(json.dumps({
                    "ok": True,
                    "actor": "alice",
                    "channel": "telegram",
                    "n_entries": 500,
                    "duration_s": 60.0,
                    "errors_count": 0,
                    "base_path": "/foo",
                    "idx": "scene",
                }), encoding="utf-8")
                # Mock send_messages module
                fake_sm = MagicMock()
                fake_sm.invoke = MagicMock(return_value={
                    "ok": True, "ok_count": 1,
                })
                with patch.dict(sys.modules, {"send_messages": fake_sm}):
                    res = hat.run_dispatcher_once()
                self.assertEqual(res["dispatched"], 1)
                # Marker spostato in archivio
                self.assertFalse(marker.exists())
                archived = list(hat._COMPLETE_ARCHIVE.glob("*.json"))
                self.assertEqual(len(archived), 1)
                # send_messages chiamato con il body atteso
                fake_sm.invoke.assert_called_once()
                args = fake_sm.invoke.call_args[0][0]
                self.assertIn("messages", args)
                msg = args["messages"][0]
                self.assertEqual(msg["to_user"], "alice")
                self.assertIn("Indice immagini pronto", msg["body"])
            finally:
                hat._COMPLETE_DIR = orig_complete
                hat._COMPLETE_ARCHIVE = orig_archive


class TestSweeper(unittest.TestCase):
    def test_sweeper_archive_cleanup(self):
        import http_async_tasks as hat
        with tempfile.TemporaryDirectory() as tmp:
            orig = hat._COMPLETE_ARCHIVE
            try:
                hat._COMPLETE_ARCHIVE = Path(tmp)
                # File vecchio (>30 giorni)
                old_fp = Path(tmp) / "old.json"
                old_fp.write_text("{}", encoding="utf-8")
                old_age = 40 * 86400
                os.utime(old_fp, (0, old_fp.stat().st_mtime - old_age))
                # File recente
                new_fp = Path(tmp) / "new.json"
                new_fp.write_text("{}", encoding="utf-8")
                # Mock orchestrator cleanup (no-op)
                import build_orchestrator as bo
                with patch.object(bo, "cleanup_orphan_tmp_dirs",
                                    return_value={"swept": [], "skipped": []}):
                    res = hat.run_sweeper_once()
                self.assertEqual(res["archive_swept"], 1)
                self.assertFalse(old_fp.exists())
                self.assertTrue(new_fp.exists())
            finally:
                hat._COMPLETE_ARCHIVE = orig


if __name__ == "__main__":
    unittest.main()

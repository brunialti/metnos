"""A.0 (fase 7) — risultato TARDIVO di un'op remota abbandonata (§2.8).

Scenario: un'op mutante remota va in timeout nel turno (l'utente vede
«incerto»), MA il device la completa più tardi e fa il submit. Prima: il result
tardivo era muto e il pending undo restava orfano → op non annullabile. Ora:
alla submit tardiva di una op ABBANDONATA+mutante+ok, il record undo si chiude
(annullabilità ripristinata).

Run: `python3 -m pytest tests/runtime/remote/test_late_result_a0.py -xvs`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class UndoCloseForTurnTests(unittest.TestCase):
    def setUp(self):
        import undo
        self.tmp = tempfile.mkdtemp(prefix="metnos_undo_a0_")
        self.log = undo.UndoLog(Path(self.tmp) / "undo.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_closes_matching_open_pending(self):
        self.log.append_pending("op1", "turnA", "delete_files", {"paths": []},
                                plan={}, device="dev1")
        n = self.log.close_pending_for_turn("turnA", {"ok": True, "ok_count": 5},
                                            device="dev1")
        self.assertEqual(n, 1)
        # ora è chiuso: annullabile (done presente)
        ops = self.log._aggregate_ops()
        self.assertIn("done", ops["op1"])

    def test_idempotent_skips_already_done(self):
        self.log.append_pending("op1", "turnA", "delete_files", {}, plan={})
        self.log.append_done("op1", {"ok": True})
        n = self.log.close_pending_for_turn("turnA", {"ok": True})
        self.assertEqual(n, 0)

    def test_device_filter(self):
        self.log.append_pending("op1", "turnA", "delete_files", {}, plan={},
                                device="dev1")
        self.assertEqual(
            self.log.close_pending_for_turn("turnA", {"ok": True},
                                            device="dev2"), 0)

    def test_turn_mismatch_no_close(self):
        self.log.append_pending("op1", "turnA", "delete_files", {}, plan={})
        self.assertEqual(
            self.log.close_pending_for_turn("turnB", {"ok": True}), 0)


class InvocationsMigrationTests(unittest.TestCase):
    def test_schema_has_abandoned_column(self):
        import invocations
        tmp = tempfile.mkdtemp(prefix="metnos_inv_a0_")
        try:
            conn = invocations._open_db(Path(tmp) / "invocations.sqlite")
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(invocations)")}
            conn.close()
            self.assertIn("abandoned_by_turn", cols)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_migration_on_pre_a0_db(self):
        # DB pre-A.0 (senza la colonna): la migrazione additiva la aggiunge.
        import sqlite3
        import invocations
        tmp = tempfile.mkdtemp(prefix="metnos_inv_mig_")
        try:
            p = Path(tmp) / "old.sqlite"
            old = sqlite3.connect(p)
            old.execute("""CREATE TABLE invocations (
                invocation_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                payload_json TEXT NOT NULL, server_sig TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,
                delivered_at TEXT, delivered_epoch REAL,
                deadline_ms INTEGER NOT NULL, completed_at TEXT,
                result_json TEXT)""")
            old.commit()
            old.close()
            conn = invocations._open_db(p)
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(invocations)")}
            conn.close()
            self.assertIn("abandoned_by_turn", cols)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class LateSubmitClosesUndoTests(unittest.TestCase):
    """End-to-end del path A.0: enqueue → timeout(mark_abandoned) → submit
    tardivo → undo chiuso. DB isolati (§ feedback: mai store reali). La firma
    device è saltata passando raw_body/sig_b64=None (modo test documentato in
    complete_invocation)."""

    def setUp(self):
        import base64
        import devices
        import invocations
        import undo
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        from cryptography.hazmat.primitives import serialization
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_a0_e2e_"))
        self.db = self.tmp / "devices.sqlite"
        self.undo_log = undo.UndoLog(self.tmp / "undo.jsonl")
        # Pin PATH_EXECUTORS al repo: altri test lo ripuntano a temp dir e non
        # sempre lo ripristinano (§7.11 reload) → enqueue_invocation non
        # troverebbe delete_files. Order-independence.
        import config as _C
        self._orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"
        # Isola lo store notices (A.2): complete_invocation accoda avvisi —
        # MAI nello store reale (feedback: mai store reali nei test).
        import user_notices as _un
        self._orig_notices = _un.NOTICES_DIR
        _un.NOTICES_DIR = self.tmp / "notices"
        priv = Ed25519PrivateKey.generate()
        pub_raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()
        token = devices.generate_token("a0-test-dev", db_path=self.db)
        dev = devices.consume_token(token, pub_b64, db_path=self.db)
        self.dev_id = dev.id
        self.invocations = invocations

    def tearDown(self):
        import shutil
        import config as _C
        import user_notices as _un
        _C.PATH_EXECUTORS = self._orig_exec
        _un.NOTICES_DIR = self._orig_notices
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_late_success_closes_orphan_undo(self):
        turn_id = "turnLATE"
        # 1. pending undo scritto (come farebbe invoke_executor prima del remote)
        self.undo_log.append_pending("opX", turn_id, "delete_files",
                                     {"paths": ["/x"]}, plan={},
                                     device=self.dev_id)
        # 2. enqueue + timeout del turno → mark_abandoned
        inv_id = self.invocations.enqueue_invocation(
            self.dev_id, "delete_files", {"paths": ["/x"]}, turn_id=turn_id,
            reversibility="revertible", db_path=self.db)
        self.invocations.mark_abandoned(inv_id, db_path=self.db)
        # 3. submit TARDIVO (ok mutante); firma saltata (modo test)
        result = {"invocation_id": inv_id, "device_id": self.dev_id,
                  "ok": True, "n_processed": 1,
                  "payload": {"ok": True, "ok_count": 1,
                              "results": [{"path": "/x", "removed": True}]}}
        import undo as _undo_mod
        orig = _undo_mod.UndoLog
        _undo_mod.UndoLog = lambda *a, **k: self.undo_log
        try:
            ok = self.invocations.complete_invocation(result, db_path=self.db)
        finally:
            _undo_mod.UndoLog = orig
        self.assertTrue(ok)
        # 4. il pending undo orfano è stato CHIUSO → op annullabile
        ops = self.undo_log._aggregate_ops()
        self.assertIn("done", ops["opX"],
                      "undo tardivo non chiuso: op resta non annullabile")

    def test_non_abandoned_late_success_does_not_touch_undo(self):
        # Un submit NON abbandonato (turno normale) non deve toccare l'undo
        # qui (lo chiude il choke-point invoke_executor).
        turn_id = "turnNORMAL"
        self.undo_log.append_pending("opY", turn_id, "delete_files",
                                     {}, plan={}, device=self.dev_id)
        inv_id = self.invocations.enqueue_invocation(
            self.dev_id, "delete_files", {"paths": ["/x"]}, turn_id=turn_id,
            reversibility="revertible", db_path=self.db)
        # NIENTE mark_abandoned
        result = {"invocation_id": inv_id, "device_id": self.dev_id,
                  "ok": True, "payload": {"ok": True, "ok_count": 1}}
        import undo as _undo_mod
        orig = _undo_mod.UndoLog
        _undo_mod.UndoLog = lambda *a, **k: self.undo_log
        try:
            self.invocations.complete_invocation(result, db_path=self.db)
        finally:
            _undo_mod.UndoLog = orig
        ops = self.undo_log._aggregate_ops()
        self.assertNotIn("done", ops["opY"])


class BatchCopyRestoreTests(unittest.TestCase):
    """Reverse remoto restore_blob_backup: BATCH a chunk + COPY (blob dedup
    serve N path) + entry-passthrough {dst} (6/7)."""

    def test_builder_batches_with_copy_and_dst_template(self):
        import reverse_patterns as rp
        results = {"results": [
            {"path": f"C:\\D\\f{i}.txt", "blob_path": f"C:\\B\\{i}.bin"}
            for i in range(250)]}
        out = rp.build_remote_reverse_calls(
            ["restore_blob_backup"], {}, results)
        calls = out["calls"]
        self.assertEqual(len(calls), 3)  # 100+100+50
        c0 = calls[0]["args"]
        self.assertEqual(calls[0]["executor"], "move_files")
        self.assertIs(c0["copy"], True)
        self.assertEqual(c0["dst_template"], "{dst}")
        self.assertEqual(len(c0["entries"]), 100)
        self.assertEqual(c0["entries"][0],
                         {"src": "C:\\B\\0.bin", "dst": "C:\\D\\f0.txt"})
        self.assertEqual(out["unsupported"], [])

    def test_move_copy_mode_keeps_src(self):
        import shutil
        from backends.files import local
        tmp = Path(tempfile.mkdtemp(prefix="metnos_copy_"))
        try:
            src = tmp / "blob.bin"
            src.write_text("contenuto")
            dst = tmp / "sub" / "restored.txt"
            out = local.move({"entries": [{"src": str(src),
                                           "dst": str(dst)}],
                              "dst_template": "{dst}",
                              "parents": True, "copy": True})
            self.assertTrue(out["ok"], out)
            self.assertTrue(src.exists(), "copy=True deve LASCIARE il blob")
            self.assertEqual(dst.read_text(), "contenuto")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dedup_blob_restores_all_duplicates(self):
        # Un blob, due path (dedup sha256): con copy=True entrambi tornano.
        import shutil
        from backends.files import local
        tmp = Path(tempfile.mkdtemp(prefix="metnos_dedup_"))
        try:
            blob = tmp / "x.bin"
            blob.write_text("dup")
            d1, d2 = tmp / "a.txt", tmp / "b.txt"
            out = local.move({"entries": [{"src": str(blob), "dst": str(d1)},
                                          {"src": str(blob), "dst": str(d2)}],
                              "dst_template": "{dst}",
                              "parents": True, "copy": True})
            self.assertEqual(out["ok_count"], 2, out)
            self.assertTrue(d1.exists() and d2.exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class UserNoticesTests(unittest.TestCase):
    """A.2: coda avvisi per (channel, actor) drenata al turno successivo."""

    def setUp(self):
        import user_notices as un
        self._orig = un.NOTICES_DIR
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_notices_"))
        un.NOTICES_DIR = self.tmp
        self.un = un

    def tearDown(self):
        import shutil
        self.un.NOTICES_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_drain_roundtrip(self):
        self.un.append("http", "host", "avviso 1", owner_user_id="host")
        self.un.append("http", "host", "avviso 2", owner_user_id="host")
        self.un.append(
            "telegram", "host", "altro destinatario", owner_user_id="host")
        out = self.un.drain("http", "host", owner_user_id="host")
        self.assertEqual(out, ["avviso 1", "avviso 2"])
        self.assertEqual(
            self.un.drain("http", "host", owner_user_id="host"), [])
        self.assertEqual(
            self.un.drain("telegram", "host", owner_user_id="host"),
            ["altro destinatario"])

    def test_write_prepends_notice(self):
        import agent_runtime
        self.un.append(
            "http", "host", "📬 op completata in ritardo", owner_user_id="host")
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="q")
        log.actor = "host"
        log.channel = "http"
        log.owner_user_id = "host"
        sl = agent_runtime.StepLog(step_num=1)
        sl.chosen_tool = "get_now"
        sl.result = {"ok": True, "entries": [{"now": "x"}]}
        log.steps.append(sl)
        log.final_message = "Sono le 10:00."
        log.final_kind = "answer"
        # punta il modulo GIA' importato da agent_runtime alla dir di test
        import user_notices as un2
        un2.NOTICES_DIR = self.tmp
        log.write()
        self.assertTrue(log.final_message.startswith("📬"))
        self.assertIn("Sono le 10:00.", log.final_message)

    def test_late_submit_appends_notice(self):
        # complete_invocation di un'invocazione ABBANDONATA → notice accodata.
        import base64
        import devices
        import invocations
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        from cryptography.hazmat.primitives import serialization
        import config as _C
        orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"
        tmp = Path(tempfile.mkdtemp(prefix="metnos_a2_"))
        try:
            db = tmp / "devices.sqlite"
            priv = Ed25519PrivateKey.generate()
            pub_raw = priv.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw)
            pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()
            dev = devices.consume_token(
                devices.generate_token("a2-dev", db_path=db), pub_b64,
                db_path=db)
            inv_id = invocations.enqueue_invocation(
                dev.id, "delete_files", {"paths": ["/x"]}, turn_id="tA2",
                reversibility="revertible", origin_actor="host",
                origin_channel="http", db_path=db)
            invocations.mark_abandoned(inv_id, db_path=db)
            invocations.complete_invocation(
                {"invocation_id": inv_id, "device_id": dev.id, "ok": True,
                 "n_processed": 3, "payload": {"ok": True, "ok_count": 3}},
                db_path=db)
            out = self.un.drain("http", "host", owner_user_id=dev.owner_user_id)
            self.assertEqual(len(out), 1)
            self.assertIn("delete_files", out[0])
        finally:
            import shutil
            _C.PATH_EXECUTORS = orig_exec
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

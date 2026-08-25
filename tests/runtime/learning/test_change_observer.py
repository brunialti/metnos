"""change_observer + change_rollback — test verifier + rollback.

Run: `python3 -m pytest tests/runtime/learning/test_change_observer.py -v`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _reset_modules():
    for prefix in ("runtime.change_intents", "runtime.change_observer",
                    "runtime.change_rollback", "runtime.change_applier"):
        for m in list(sys.modules):
            if m == prefix or m.startswith(prefix + "."):
                del sys.modules[m]
    for m in ("change_intents", "change_observer", "change_rollback",
              "change_applier", "change_applier_extend"):
        if m in sys.modules:
            del sys.modules[m]


class TestObserver(unittest.TestCase):
    def setUp(self):
        os.environ["METNOS_CHANGE_GRACE_DAYS"] = "0"  # finalize subito
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        import config as C
        C.DB_CHANGE_INTENTS = self.tmpdir / "ci.sqlite"
        C.PATH_USER_DATA = self.tmpdir / "share"
        C.PATH_USER_STATE = self.tmpdir / "state"
        C.PATH_USER_DATA.mkdir(parents=True)
        C.PATH_USER_STATE.mkdir(parents=True)
        C.DB_MULTI_TOOL_PATHS = C.PATH_USER_DATA / "multi_tool_paths.sqlite"
        C.DB_MNESTOMA = C.PATH_USER_DATA / "mnest.sqlite"
        # Init multi_tool sqlite minimal
        cn = sqlite3.connect(str(C.DB_MULTI_TOOL_PATHS))
        cn.execute("""CREATE TABLE multi_tool_paths (
            id INTEGER PRIMARY KEY, canonical_query TEXT, tools_sequence TEXT,
            args_shape TEXT, path_shape_hash TEXT, uses INTEGER, ok_count INTEGER,
            fail_count INTEGER, ts_first TEXT, ts_last TEXT,
            last_used_active_day INTEGER, state TEXT
        )""")
        cn.execute("""INSERT INTO multi_tool_paths VALUES
            (1,'q1','["a","b"]','[]','active_hash',5,5,0,'2026-05-22','2026-05-22',1,'active')""")
        cn.execute("""INSERT INTO multi_tool_paths VALUES
            (2,'q2','["c","d"]','[]','demoted_hash',5,2,3,'2026-05-22','2026-05-22',1,'demoted')""")
        cn.commit()
        cn.close()
        # Init mnest minimal
        C.DB_MNESTOMA.parent.mkdir(parents=True, exist_ok=True)
        cn = sqlite3.connect(str(C.DB_MNESTOMA))
        cn.execute("""CREATE TABLE canonical_query_log (
            id INTEGER PRIMARY KEY, canonical_query TEXT, tool_name TEXT,
            args_shape TEXT, uses INTEGER, ok_count INTEGER, fail_count INTEGER,
            ts_first TEXT, ts_last TEXT, state TEXT
        )""")
        cn.execute("""INSERT INTO canonical_query_log VALUES
            (1,'q1','tool_a','{}',5,5,0,'2026-05-22','2026-05-22','active')""")
        cn.commit()
        cn.close()
        _reset_modules()
        import change_intents as ci_mod
        import change_observer as co
        import change_rollback as cr
        ci_mod.init_db()
        self.ci_mod = ci_mod
        self.co = co
        self.cr = cr

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("METNOS_CHANGE_GRACE_DAYS", None)

    def _make_applied(self, kind, target, body, effect):
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="u", origin_module="t",
            intent_kind=kind, intent_target=target,
            intent_summary="t", intent_body=body,
        )
        id_ = self.ci_mod.upsert_intent(ci)
        self.ci_mod.apply_decision(id_, action="accept", by="t")
        self.ci_mod.mark_applied(id_, effect=effect)
        return id_

    def test_verify_pipeline_active_finalize(self):
        id_ = self._make_applied(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE, "a→b",
            {"path_shape_hash": "active_hash"},
            {"shape_hash": "active_hash"},
        )
        report = self.co.task_change_observer()
        self.assertEqual(report["finalized"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "finalized")

    def test_verify_real_materialized_turn_without_legacy_row_finalizes(self):
        id_ = self._make_applied(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE, "a→b",
            {"suggested_query": "esegui a poi b"},
            {"turn_id": "turn-real", "final_kind": "answer",
             "steps": ["a", "b"]},
        )
        report = self.co.task_change_observer()
        self.assertEqual(report["finalized"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "finalized")
        self.assertEqual(ci.observed_metrics["turn_id"], "turn-real")

    def test_materialized_turn_waiting_for_jit_input_stays_observable(self):
        id_ = self._make_applied(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE, "a→b",
            {"suggested_query": "esegui a poi b"},
            {"turn_id": "turn-ask", "final_kind": "ask", "steps": ["a"]},
        )
        report = self.co.task_change_observer()
        self.assertEqual(report["observing"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "observed")

    def test_verify_pipeline_demoted_rollback(self):
        id_ = self._make_applied(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE, "c→d",
            {"path_shape_hash": "demoted_hash"},
            {"shape_hash": "demoted_hash"},
        )
        report = self.co.task_change_observer()
        self.assertEqual(report["rolled_back"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "rolled_back")
        self.assertIn("L2 auto-demoted", ci.rolled_back_reason)

    def test_verify_cache_pattern_active_finalize(self):
        id_ = self._make_applied(
            self.ci_mod.KIND_CACHE_PATTERN, "tool_a",
            {"canonical_query": "q1", "tool_name": "tool_a"},
            {"canonical_query": "q1"},
        )
        report = self.co.task_change_observer()
        self.assertEqual(report["finalized"], 1, msg=repr(report))

    def test_verify_reject_pattern_no_new_rejects_finalize(self):
        id_ = self._make_applied(
            self.ci_mod.KIND_REJECT_PATTERN, "x",
            {"canonical_query": "x", "tools_sequence": [], "n_rejections": 3},
            {"canonical_query": "x"},
        )
        report = self.co.task_change_observer()
        self.assertEqual(report["finalized"], 1, msg=repr(report))

    def test_verify_reject_pattern_new_rejects_rollback(self):
        import config as C
        # Inietta 2 nuovi feedback ✗ post applied
        id_ = self._make_applied(
            self.ci_mod.KIND_REJECT_PATTERN, "y",
            {"canonical_query": "y", "tools_sequence": [], "n_rejections": 3},
            {"canonical_query": "y"},
        )
        ci = self.ci_mod.get_intent(id_)
        import time
        applied_epoch = time.mktime(time.strptime(ci.applied_at, "%Y-%m-%dT%H:%M:%SZ"))
        tf = C.PATH_USER_DATA / "turn_feedback.jsonl"
        with tf.open("a") as fp:
            for i in range(2):
                fp.write(json.dumps({
                    "turn_id": f"t{i}", "action": "error",
                    "canonical": "y", "ts": applied_epoch + 60,
                }) + "\n")
        report = self.co.task_change_observer()
        self.assertEqual(report["rolled_back"], 1, msg=repr(report))


class TestRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        import config as C
        C.DB_CHANGE_INTENTS = self.tmpdir / "ci.sqlite"
        C.PATH_USER_DATA = self.tmpdir / "share"
        C.PATH_USER_STATE = self.tmpdir / "state"
        C.PATH_USER_DATA.mkdir(parents=True)
        C.PATH_USER_STATE.mkdir(parents=True)
        C.PATH_EXECUTORS = self.tmpdir / "executors"
        C.PATH_EXECUTORS.mkdir()
        C.PATH_SYNTH_EXECUTORS = C.PATH_USER_DATA / "executors"
        C.DB_MULTI_TOOL_PATHS = C.PATH_USER_DATA / "multi_tool_paths.sqlite"
        cn = sqlite3.connect(str(C.DB_MULTI_TOOL_PATHS))
        cn.execute("""CREATE TABLE multi_tool_paths (
            id INTEGER PRIMARY KEY, canonical_query TEXT, tools_sequence TEXT,
            args_shape TEXT, path_shape_hash TEXT, uses INTEGER, ok_count INTEGER,
            fail_count INTEGER, ts_first TEXT, ts_last TEXT,
            last_used_active_day INTEGER, state TEXT
        )""")
        cn.execute("""INSERT INTO multi_tool_paths VALUES
            (1,'q1','["a","b"]','[]','test_hash',5,5,0,'2026-05-22','2026-05-22',1,'active')""")
        cn.commit()
        cn.close()
        _reset_modules()
        import change_intents as ci_mod
        import change_rollback as cr
        ci_mod.init_db()
        self.ci_mod = ci_mod
        self.cr = cr

    def _extend_rollback_fixture(self):
        name = "find_things"
        mdir = __import__("config").PATH_EXECUTORS / name
        mdir.mkdir()
        manifest = mdir / "manifest.toml"
        changed = "name = 'changed'\n[code]\nfiles = ['tool.py']\n"
        original = "name = 'original'\n[code]\nfiles = ['tool.py']\n"
        manifest.write_text(changed, encoding="utf-8")
        (mdir / "manifest.lang_state.json").write_text("{}")
        (mdir / "tool.py").write_text("def invoke(): return {}\n")
        blob = self.tmpdir / "original.toml"
        blob.write_text(original, encoding="utf-8")
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="u", origin_module="t",
            intent_kind=self.ci_mod.KIND_EXTEND_EXECUTOR,
            intent_target=name, intent_summary="rollback", intent_body={},
        )
        ci.applied_effect = {
            "executor_name": name, "rollback_blob_path": str(blob),
        }
        from manifest_inventory import ContractId, ManifestOrigin
        contract_id = ContractId(
            ManifestOrigin.BUILTIN, f"{name}/manifest.toml",
        )
        return ci, manifest, contract_id, changed, original

    @staticmethod
    def _birth_result(contract_id, *, error=None):
        from contract_store import PublicationResult
        publication = None if error else PublicationResult(
            contract_id, "sha256:" + "4" * 64,
            "sha256:" + "5" * 64, "commit_birth_snapshot", False,
        )
        return mock.Mock(
            request_id="sha256:" + "3" * 64,
            publication=publication, error_code=error,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_rollback_dedupe_removes_alias(self):
        import config as C
        aliases_path = C.PATH_USER_DATA / "executor_aliases.json"
        aliases_path.write_text(json.dumps({"b": "a"}))
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="u", origin_module="t",
            intent_kind=self.ci_mod.KIND_DEDUPE_EXECUTORS,
            intent_target="b", intent_summary="t",
            intent_body={"a": "a", "b": "b"},
        )
        ci.applied_effect = {"alias_from": "b", "alias_to": "a"}
        effect = self.cr.rollback_for_kind(ci)
        self.assertTrue(effect["alias_removed"])
        aliases = json.loads(aliases_path.read_text())
        self.assertNotIn("b", aliases)

    def test_extend_rollback_fails_closed_before_restore_without_birth_request(self):
        ci, manifest, contract_id, changed, _original = self._extend_rollback_fixture()
        with mock.patch(
            "change_rollback.require_birth_intent_adapter",
            side_effect=RuntimeError("birth_intent_adapter_unavailable"),
        ):
            effect = self.cr.rollback_for_kind(ci)
        self.assertIn("birth intent unavailable", effect["error"])
        self.assertEqual(manifest.read_text(encoding="utf-8"), changed)

    def test_extend_rollback_publishes_only_through_birth(self):
        ci, manifest, contract_id, _changed, original = self._extend_rollback_fixture()
        def submit(intent):
            manifest.write_bytes(
                (intent.candidate_source_root / "manifest.toml").read_bytes())
            return self._birth_result(contract_id)
        with mock.patch("change_rollback.require_birth_intent_adapter"), \
                mock.patch("change_applier_extend._contract_id",
                           return_value=contract_id), \
                mock.patch("change_rollback.submit_birth_intent",
                           side_effect=submit) as birth:
            effect = self.cr.rollback_for_kind(ci)
        self.assertEqual(manifest.read_text(encoding="utf-8"), original)
        self.assertEqual(effect["birth_request_id"], "sha256:" + "3" * 64)
        self.assertIn("new_generation_id", effect)
        birth.assert_called_once()

    def test_extend_rollback_replay_is_rejected_without_false_success(self):
        ci, manifest, contract_id, changed, _original = self._extend_rollback_fixture()
        with mock.patch("change_rollback.require_birth_intent_adapter"), \
                mock.patch("change_applier_extend._contract_id",
                           return_value=contract_id), \
                mock.patch(
                    "change_rollback.submit_birth_intent",
                    return_value=self._birth_result(
                        contract_id, error="producer_receipt_replay"),
                ):
            effect = self.cr.rollback_for_kind(ci)
        self.assertIn("producer_receipt_replay", effect["re_sign_error"])
        self.assertNotIn("new_generation_id", effect)
        self.assertEqual(manifest.read_text(encoding="utf-8"), changed)

    def test_rollback_pipeline_sets_demoted(self):
        import config as C
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="u", origin_module="t",
            intent_kind=self.ci_mod.KIND_MATERIALIZE_PIPELINE,
            intent_target="a→b", intent_summary="t",
            intent_body={"path_shape_hash": "test_hash"},
        )
        ci.applied_effect = {"shape_hash": "test_hash"}
        effect = self.cr.rollback_for_kind(ci)
        self.assertEqual(effect["state_set_to"], "demoted")
        cn = sqlite3.connect(str(C.DB_MULTI_TOOL_PATHS))
        state = cn.execute(
            "SELECT state FROM multi_tool_paths WHERE path_shape_hash=?",
            ("test_hash",),
        ).fetchone()[0]
        cn.close()
        self.assertEqual(state, "demoted")

    def test_rollback_reject_pattern_removes_line(self):
        import config as C
        rp = C.PATH_USER_DATA / "rejected_patterns.jsonl"
        rp.write_text(
            json.dumps({"canonical_query": "y", "tools_sequence": ["t1"]}) + "\n"
            + json.dumps({"canonical_query": "z", "tools_sequence": []}) + "\n"
        )
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="u", origin_module="t",
            intent_kind=self.ci_mod.KIND_REJECT_PATTERN,
            intent_target="y", intent_summary="t",
            intent_body={"canonical_query": "y", "tools_sequence": ["t1"]},
        )
        effect = self.cr.rollback_for_kind(ci)
        self.assertEqual(effect["removed_lines"], 1)
        lines = [l for l in rp.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, "Solo z deve restare")
        self.assertIn("z", lines[0])


if __name__ == "__main__":
    unittest.main()

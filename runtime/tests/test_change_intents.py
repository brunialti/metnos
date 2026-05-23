"""change_intents — test schema, fingerprint, transitions, upsert merge.

Run: `python3 -m pytest runtime/tests/test_change_intents.py -v`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _setup_temp_db(tmp_dir: Path):
    """Patcha config.DB_CHANGE_INTENTS prima di importare change_intents."""
    import config as C
    C.DB_CHANGE_INTENTS = tmp_dir / "change_intents.sqlite"


class TestSchemaAndCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        _setup_temp_db(self.tmpdir)
        # Forza re-import dopo patch
        for m in list(sys.modules):
            if m.startswith("runtime.change_intents"):
                del sys.modules[m]
        global ci
        import change_intents as ci  # noqa
        ci.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_intent_and_upsert(self):
        ch = ci.ChangeIntent.new(
            origin_family="telos",
            origin_module="scamper",
            intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="find_files",
            intent_summary="Estendi find_files con kind=recipe",
            intent_body={"arg_name": "kind", "arg_value_example": "recipe"},
            score=0.52,
        )
        self.assertEqual(ch.state, "proposed")
        self.assertTrue(ch.fingerprint)
        rid = ci.upsert_intent(ch)
        self.assertEqual(rid, ch.id)
        got = ci.get_intent(rid)
        self.assertIsNotNone(got)
        self.assertEqual(got.intent_target, "find_files")
        self.assertEqual(got.score, 0.52)
        self.assertEqual(got.convergence, 1)

    def test_upsert_dedup_by_fingerprint_bumps_convergence(self):
        ch1 = ci.ChangeIntent.new(
            origin_family="telos",
            origin_module="scamper",
            intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="find_files",
            intent_summary="Estendi find_files con kind=recipe",
            intent_body={"arg_name": "kind"},
            score=0.52,
        )
        ch2 = ci.ChangeIntent.new(
            origin_family="introvertiva",   # diversa family
            origin_module="specialize",
            intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="find_files",
            intent_summary="(introvertiva variant)",
            intent_body={"arg_name": "kind"},   # stesso essential body
            score=0.61,
        )
        id1 = ci.upsert_intent(ch1)
        id2 = ci.upsert_intent(ch2)
        self.assertEqual(id1, id2, "should dedup to same id via fingerprint")
        got = ci.get_intent(id1)
        self.assertEqual(got.convergence, 2, "convergence bumps on diff family")
        self.assertEqual(got.score, 0.61, "score = max")

    def test_fingerprint_distinguishes_kinds(self):
        fp1 = ci.compute_fingerprint(
            origin_family="telos", intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="find_files",
            intent_body={"arg_name": "kind"},
        )
        fp2 = ci.compute_fingerprint(
            origin_family="telos", intent_kind=ci.KIND_CREATE_EXECUTOR,
            intent_target="find_files",
            intent_body={"name": "find_files", "action": "find", "object": "files"},
        )
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_independent_from_family(self):
        fp_t = ci.compute_fingerprint(
            origin_family="telos", intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="x", intent_body={"arg_name": "y"},
        )
        fp_i = ci.compute_fingerprint(
            origin_family="introvertiva", intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="x", intent_body={"arg_name": "y"},
        )
        self.assertEqual(fp_t, fp_i, "family non deve entrare nel fingerprint")


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        _setup_temp_db(self.tmpdir)
        for m in list(sys.modules):
            if m.startswith("runtime.change_intents"):
                del sys.modules[m]
        global ci
        import change_intents as ci  # noqa
        ci.init_db()
        self.id_ = ci.upsert_intent(ci.ChangeIntent.new(
            origin_family="telos", origin_module="scamper",
            intent_kind=ci.KIND_CREATE_EXECUTOR,
            intent_target="sign_files_pdf",
            intent_summary="Genera nuovo executor sign_files_pdf",
            intent_body={"name": "sign_files_pdf", "action": "sign",
                         "object": "files", "qualifier": "pdf"},
            score=0.45,
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def test_happy_path_lifecycle(self):
        got = ci.apply_decision(self.id_, action="accept", by="roberto",
                                reason="serve")
        self.assertEqual(got.state, "accepted")
        got = ci.mark_applied(self.id_, effect={"executor_name": "sign_files_pdf"})
        self.assertEqual(got.state, "applied")
        got = ci.mark_observed(self.id_, metrics={"calls": 3, "success_rate": 1.0})
        self.assertEqual(got.state, "observed")
        got = ci.mark_finalized(self.id_)
        self.assertEqual(got.state, "finalized")

    def test_reject_terminal_until_repropose(self):
        ci.apply_decision(self.id_, action="reject", by="roberto", reason="no")
        got = ci.get_intent(self.id_)
        self.assertEqual(got.state, "rejected")
        # reject → proposed e' ammesso (re-propose dopo dismissal)
        ci._transition(self.id_, to_state="proposed")
        self.assertEqual(ci.get_intent(self.id_).state, "proposed")

    def test_stage_then_accept(self):
        ci.apply_decision(self.id_, action="stage", by="roberto")
        ci.apply_decision(self.id_, action="accept", by="roberto")
        self.assertEqual(ci.get_intent(self.id_).state, "accepted")

    def test_apply_to_applied_then_rollback(self):
        ci.apply_decision(self.id_, action="accept", by="roberto")
        ci.mark_applied(self.id_, effect={"executor_name": "sign_files_pdf"})
        got = ci.mark_rolled_back(self.id_, reason="fail_rate 0.5")
        self.assertEqual(got.state, "rolled_back")
        self.assertEqual(got.rolled_back_reason, "fail_rate 0.5")

    def test_rollback_from_any_state(self):
        # da proposed direttamente
        ci.mark_rolled_back(self.id_, reason="audit")
        self.assertEqual(ci.get_intent(self.id_).state, "rolled_back")

    def test_invalid_transition_raises(self):
        # proposed → applied non e' ammesso (deve passare per accepted)
        with self.assertRaises(ci.TransitionError):
            ci._transition(self.id_, to_state="applied")


class TestListAndCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        _setup_temp_db(self.tmpdir)
        for m in list(sys.modules):
            if m.startswith("runtime.change_intents"):
                del sys.modules[m]
        global ci
        import change_intents as ci  # noqa
        ci.init_db()
        # Popola 3 intent: 2 proposed (telos + introvertiva), 1 applied
        a = ci.upsert_intent(ci.ChangeIntent.new(
            origin_family="telos", origin_module="scamper",
            intent_kind=ci.KIND_CREATE_EXECUTOR,
            intent_target="aaa", intent_summary="A",
            intent_body={"name": "aaa", "action": "find", "object": "files"},
            score=0.8,
        ))
        ci.upsert_intent(ci.ChangeIntent.new(
            origin_family="introvertiva", origin_module="specialize",
            intent_kind=ci.KIND_EXTEND_EXECUTOR,
            intent_target="bbb", intent_summary="B",
            intent_body={"arg_name": "kind"},
            score=0.4,
        ))
        c = ci.upsert_intent(ci.ChangeIntent.new(
            origin_family="telos", origin_module="oulipo",
            intent_kind=ci.KIND_CREATE_EXECUTOR,
            intent_target="ccc", intent_summary="C",
            intent_body={"name": "ccc", "action": "find", "object": "dirs"},
            score=0.6,
        ))
        ci.apply_decision(c, action="accept", by="r")
        ci.mark_applied(c, effect={"executor_name": "ccc"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_count_by_state(self):
        cnt = ci.count_by_state()
        self.assertEqual(cnt["proposed"], 2)
        self.assertEqual(cnt["applied"], 1)
        self.assertEqual(cnt["accepted"], 0)

    def test_list_proposed_only(self):
        rows = ci.list_intents(state="proposed")
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r.state, "proposed")

    def test_list_min_score(self):
        rows = ci.list_intents(state="proposed", min_score=0.5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].intent_target, "aaa")

    def test_list_filter_family(self):
        rows = ci.list_intents(origin_family="telos")
        self.assertEqual(len(rows), 2)

    def test_list_order_score_desc(self):
        rows = ci.list_intents()
        self.assertEqual([r.score for r in rows], sorted([r.score for r in rows], reverse=True))


if __name__ == "__main__":
    unittest.main()

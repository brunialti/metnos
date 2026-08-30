"""Test del form aggregator review (E3, 11/5/2026).

Copre:
1. build_review_dialog: 3 step-group con conteggi corretti.
2. build_review_dialog: cap N=10 per group + counter `more`.
3. apply_review_decisions: batch atomic con audit JSONL unico per sessione.
4. apply_review_decisions: confirm grace → mark_finalized.
5. apply_review_decisions: rollback grace → rollback_promotion invocato.
6. apply_review_decisions: resurrect archive → resurrect_from_archive.
7. Helper state mutation (mark_finalized, resurrect_from_archive,
   archive_review_needed): unit test isolati.

Determinismo §7.9: zero LLM. Test su sqlite tmpdir + mock di TelegramChannel.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class _BaseReviewTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="promoter_review_"))
        self._db = self._tmpdir / "promoter.sqlite"
        self._audit_dir = self._tmpdir / "audit"
        self._blob_dir = self._tmpdir / "blobs"
        self._synth_dir = self._tmpdir / "synth_execs"
        self._env = mock.patch.dict("os.environ", {
            "METNOS_PROMOTER_DB": str(self._db),
            "METNOS_PROMOTER_AUDIT_DIR": str(self._audit_dir),
            "METNOS_PROMOTER_BLOB_DIR": str(self._blob_dir),
            "METNOS_PROMOTER_SYNTH_DIR": str(self._synth_dir),
            "HOME": str(self._tmpdir),
        })
        self._env.start()
        for mod in (
            "jobs.promoter_state", "jobs.promoter_rollback",
            "jobs.promoter", "jobs.promoter_promote",
            "admin.promotions_review",
        ):
            sys.modules.pop(mod, None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _seed(self, *, proposal_id: str, name: str = "find_x",
              state: str = "promoted_grace",
              practical_example: str = "",
              archived_at: str | None = None,
              needs_review: int = 0) -> None:
        from jobs.promoter_state import ensure_schema
        conn = sqlite3.connect(str(self._db))
        ensure_schema(conn)
        promoted_at = (
            "2026-05-11T00:00:00Z" if state == "promoted_grace" else None
        )
        grace_until = (
            "2026-05-14T00:00:00Z" if state == "promoted_grace" else None
        )
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, promoted_at, grace_until, "
            " practical_example, archived_at, created_at, "
            " needs_human_review) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '2026-05-10T00:00:00Z', ?)",
            (proposal_id, name, state, promoted_at, grace_until,
             practical_example, archived_at, needs_review),
        )
        conn.commit()
        conn.close()


# ─── 1. build_review_dialog: 3 step-group ─────────────────────────────────


class TestBuildDialog3Groups(_BaseReviewTest):

    def test_three_groups_with_correct_steps(self):
        self._seed(proposal_id="g_001", name="find_a",
                   state="promoted_grace",
                   practical_example="esempio A")
        self._seed(proposal_id="g_002", name="find_b",
                   state="promoted_grace")
        self._seed(proposal_id="r_001", name="get_c",
                   state="review_needed", needs_review=1)
        # Archived recente (entro 7g).
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._seed(proposal_id="a_001", name="delete_d",
                   state="archived", archived_at=recent)
        # Archived vecchio (oltre 7g) → NON deve apparire.
        old = (datetime.now(timezone.utc) - timedelta(days=20)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._seed(proposal_id="a_002", name="delete_old",
                   state="archived", archived_at=old)

        from admin.promotions_review import build_review_dialog
        dlg = build_review_dialog()
        self.assertIn("dialog", dlg)
        self.assertIn("groups", dlg)
        # Count per group: 2 grace, 1 review, 1 archived recent (NON 2).
        self.assertEqual(dlg["groups"]["promoted_grace"]["count"], 2)
        self.assertEqual(dlg["groups"]["review_needed"]["count"], 1)
        self.assertEqual(dlg["groups"]["archived"]["count"], 1)
        # Totale step = 4.
        self.assertEqual(len(dlg["dialog"]), 4)
        # on_complete dichiarato.
        self.assertEqual(dlg["on_complete"]["type"], "apply_review_decisions")

    def test_step_vars_have_state_prefix(self):
        self._seed(proposal_id="g_001", name="find_a",
                   state="promoted_grace")
        self._seed(proposal_id="r_001", name="get_c",
                   state="review_needed", needs_review=1)
        from admin.promotions_review import build_review_dialog
        dlg = build_review_dialog()
        vars_set = {s["var"] for s in dlg["dialog"]}
        self.assertIn("promoted_grace__g_001", vars_set)
        self.assertIn("review_needed__r_001", vars_set)


# ─── 2. Cap N=10 per group + paginazione ──────────────────────────────────


class TestCapPagination(_BaseReviewTest):

    def test_cap_10_per_group_with_more_counter(self):
        # 15 items grace → cap 10 + more=5.
        for i in range(15):
            self._seed(proposal_id=f"g_{i:02d}", name=f"find_{i}",
                       state="promoted_grace")
        from admin.promotions_review import build_review_dialog
        dlg = build_review_dialog(max_per_group=10)
        self.assertEqual(dlg["groups"]["promoted_grace"]["count"], 10)
        self.assertEqual(dlg["groups"]["promoted_grace"]["more"], 5)
        self.assertEqual(dlg["groups"]["promoted_grace"]["total"], 15)


# ─── 3. apply_review_decisions: atomic + audit JSONL unico ────────────────


class TestApplyAuditUnique(_BaseReviewTest):

    def test_audit_jsonl_unique_per_session(self):
        self._seed(proposal_id="g_001", name="find_a",
                   state="promoted_grace")
        self._seed(proposal_id="r_001", name="get_c",
                   state="review_needed", needs_review=1)
        from admin.promotions_review import apply_review_decisions
        values = {
            "promoted_grace__g_001": "confirm",
            "review_needed__r_001": "skip",
        }
        result = apply_review_decisions(values)
        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)
        # UN solo audit file per la sessione.
        sid = result["session_id"]
        audit_files = list(self._audit_dir.glob(
            f"promoter_review_*_{sid}.jsonl"
        ))
        self.assertEqual(len(audit_files), 1,
                          f"expected 1 audit file, found {audit_files}")
        # Contenuto: summary + 1 evento applied.
        lines = audit_files[0].read_text(encoding="utf-8").strip().split("\n")
        self.assertGreaterEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["action"], "review_session_summary")
        self.assertEqual(first["applied"], 1)


# ─── 4. confirm grace → mark_finalized ────────────────────────────────────


class TestConfirmGrace(_BaseReviewTest):

    def test_confirm_grace_finalizes(self):
        self._seed(proposal_id="g_001", name="find_a",
                   state="promoted_grace")
        from admin.promotions_review import apply_review_decisions
        from jobs.promoter_state import load_proposal_state
        result = apply_review_decisions({
            "promoted_grace__g_001": "confirm",
        })
        self.assertTrue(result["ok"])
        row = load_proposal_state("g_001")
        self.assertEqual(row["state"], "promoted_finalized")
        self.assertTrue(row["finalized_at"])


# ─── 5. rollback grace → rollback_promotion invocato ──────────────────────


class TestRollbackGrace(_BaseReviewTest):

    def test_rollback_grace_invokes_rollback_promotion(self):
        # Setup: row + blob + exec dir (cosi' rollback_promotion ha lavoro
        # da fare; senza blob, fallirebbe con no_blob).
        blob_path = self._blob_dir / "g_002.tar.gz"
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        candidate = self._tmpdir / "candidate"
        candidate.mkdir()
        (candidate / "manifest.toml").write_text(
            'lifecycle = "synthesized"\n', encoding="utf-8")
        (candidate / "find_b.py").write_text("# candidate\n", encoding="utf-8")
        with tarfile.open(blob_path, "w:gz") as archive:
            for child in sorted(candidate.iterdir()):
                archive.add(child, arcname=child.name)
        from jobs.promoter_state import ensure_schema
        conn = sqlite3.connect(str(self._db))
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, promoted_at, grace_until, "
            " rollback_blob_path, created_at) "
            "VALUES (?, ?, 'promoted_grace', ?, ?, ?, ?)",
            ("g_002", "find_b", "2026-05-11T00:00:00Z",
             "2026-05-14T00:00:00Z", str(blob_path),
             "2026-05-11T00:00:00Z"),
        )
        conn.commit()
        conn.close()
        exec_dir = self._synth_dir / "find_b"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "find_b.py").write_text("# stub")

        from admin.promotions_review import apply_review_decisions
        from jobs.promoter_state import load_proposal_state
        result = apply_review_decisions({
            "promoted_grace__g_002": "rollback",
        })
        self.assertTrue(result["ok"])
        # state -> rolled_back
        row = load_proposal_state("g_002")
        self.assertEqual(row["state"], "rolled_back")
        # exec dir riportato allo stato candidato, non cancellato.
        self.assertTrue(exec_dir.exists())
        self.assertIn(
            'lifecycle = "synthesized"',
            (exec_dir / "manifest.toml").read_text(encoding="utf-8"),
        )


# ─── 6. resurrect archive → resurrect_from_archive ────────────────────────


class TestResurrect(_BaseReviewTest):

    def test_resurrect_archived(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._seed(proposal_id="a_001", name="delete_d",
                   state="archived", archived_at=recent)
        from admin.promotions_review import apply_review_decisions
        from jobs.promoter_state import load_proposal_state
        result = apply_review_decisions({
            "archived__a_001": "resurrect",
        })
        self.assertTrue(result["ok"])
        row = load_proposal_state("a_001")
        self.assertEqual(row["state"], "review_needed")
        self.assertEqual(row["needs_human_review"], 1)

    def test_localized_label_is_not_an_administrative_value(self):
        self._seed(proposal_id="a_002", name="delete_e",
                   state="archived")
        from admin.promotions_review import apply_review_decisions
        from jobs.promoter_state import load_proposal_state

        result = apply_review_decisions({
            "archived__a_002": "Resurrect a pending",
        })

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(load_proposal_state("a_002")["state"], "archived")


# ─── 7. State mutation helpers (unit) ─────────────────────────────────────


class TestStateMutations(_BaseReviewTest):

    def test_mark_finalized_only_from_grace(self):
        self._seed(proposal_id="m_001", name="x", state="promoted_grace")
        self._seed(proposal_id="m_002", name="y", state="review_needed",
                   needs_review=1)
        from jobs.promoter_state import (
            load_proposal_state, mark_finalized,
        )
        # grace → finalized OK.
        self.assertTrue(mark_finalized("m_001"))
        self.assertEqual(
            load_proposal_state("m_001")["state"], "promoted_finalized",
        )
        # review_needed → NOOP (False).
        self.assertFalse(mark_finalized("m_002"))
        self.assertEqual(
            load_proposal_state("m_002")["state"], "review_needed",
        )
        # Non esistente → False.
        self.assertFalse(mark_finalized("no_such_id"))

    def test_archive_review_needed(self):
        self._seed(proposal_id="m_001", name="x", state="review_needed",
                   needs_review=1)
        from jobs.promoter_state import (
            archive_review_needed, load_proposal_state,
        )
        self.assertTrue(archive_review_needed("m_001"))
        row = load_proposal_state("m_001")
        self.assertEqual(row["state"], "archived")
        self.assertTrue(row["archived_at"])
        self.assertEqual(row["needs_human_review"], 0)

    def test_resurrect_from_archive_helper(self):
        self._seed(proposal_id="m_001", name="x", state="archived",
                   archived_at="2026-05-09T00:00:00Z")
        from jobs.promoter_state import (
            load_proposal_state, resurrect_from_archive,
        )
        self.assertTrue(resurrect_from_archive("m_001"))
        row = load_proposal_state("m_001")
        self.assertEqual(row["state"], "review_needed")
        self.assertEqual(row["needs_human_review"], 1)
        # archived_at azzerato.
        self.assertFalse(row["archived_at"])


if __name__ == "__main__":
    unittest.main()

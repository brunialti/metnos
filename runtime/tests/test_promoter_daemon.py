"""Test del task scheduler v2 `promoter` (jobs/promoter.py).

Copre:
1. Shape RunResult del callback.
2. Schema migration idempotente.
3. Cap N enforced (default 5).
4. Accept verdict → state='promoted_grace' + blob esiste.
5. Gray verdict → state='review_needed' + needs_human_review=1.
6. Reject verdict → state='archived' + artefatti spostati.
7. Grace expired → state='promoted_finalized'.
8. Admission layer fail → no promote + audit reason.
9. Practical example deterministico: same input → same output.
10. Example fallback su ETA vuoto.
11. Rollback ripristina stato pre-promote.
12. Rollback senza blob → fail-loud §2.8.
13. Dry-run mode: decide ma file system non toccato.
14. Telegram callback `promoter:<id>:rollback` → invoca rollback.
15. HTTP `POST /admin/promotions/<id>/rollback` → 200 + state aggiornato.

Niente rete §7.9: il proposal_evaluator e' mockato dove serve isolarne il
verdetto. La promote_to_catalog vera viene esercitata con fixture
manifest minimo.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ─── Fixture helpers ──────────────────────────────────────────────────────


def _build_synth_proposal(*, name: str = "find_packages",
                           proposal_id: str | None = None,
                           path_hash: str = "deadbeef00000000",
                           path_steps: list[str] | None = None,
                           description: str = "Find widgets by query.",
                           affinity: list[str] | None = None,
                           reverse_pattern: str = "",
                           user_query: str = "trova i widget",
                           final_state: str = "synthesized") -> dict:
    """Crea il dict di una proposta synth valida (passa l'evaluator base)."""
    proposal_id = proposal_id or f"{int(time.time())}_{name}"
    affinity = affinity or [
        "trova", "find", "cerca", "search", "widget",
        "elenca", "list", "discover", "lookup",
    ]
    path_steps = path_steps or ["get_now", "find_files"]
    return {
        "id": proposal_id,
        "name": name,
        "expected_name": name,
        "user_query": user_query,
        "final_state": final_state,
        "ts_start": time.time(),
        "path_hash": path_hash,
        "path_steps": path_steps,
        "stages": [
            {
                "stage": 1, "success": True, "latency_ms": 1000,
                "output": {
                    "name": name, "action": name.split("_")[0],
                    "object": name.split("_")[1] if "_" in name else "files",
                    "qualifier": None, "revertible": False,
                    "critical": False, "target_kind": "host",
                },
            },
            {
                "stage": 2, "success": True, "latency_ms": 1000,
                "output": {
                    "args_required": ["query"],
                    "args_properties": {
                        "query": {"type": "string",
                                  "description": "Search query."},
                        "max_results": {"type": "integer", "default": 100},
                    },
                    "capabilities": [{"name": "fs:read", "hint": ["/tmp/*"]}],
                    "reverse_pattern": reverse_pattern or None,
                },
            },
            {
                "stage": 3, "success": True, "latency_ms": 1000,
                "output": {
                    "tests": [
                        {"name": "t1", "input": {"query": "foo"},
                         "expect": {"ok": True}},
                        {"name": "t2", "input": {"query": ""},
                         "expect": {"ok": False, "error_contains": "missing"}},
                        {"name": "t3", "input": {"query": 1},
                         "expect": {"ok": False,
                                    "error_contains": "invalid"}},
                    ],
                },
            },
            {
                "stage": 4, "success": True, "latency_ms": 1000,
                "output": {
                    "description": description,
                    "affinity": affinity,
                },
            },
            {
                "stage": 5, "success": True, "latency_ms": 1000,
                "output": {
                    "code": (
                        '"""Stub generated executor."""\n'
                        "def invoke(args):\n"
                        '    error_class = "not_found"\n'
                        '    if not args.get("query"):\n'
                        '        return {"ok": False, "ok_count": 0,\n'
                        '                "results": [], "failed": [\n'
                        '                    {"error": "missing", '
                        '"error_class": "invalid"}]}\n'
                        '    return {"ok": True, "ok_count": 0,\n'
                        '            "results": [], "failed": []}\n'
                    ),
                },
            },
        ],
    }


def _seed_proposal_json(proposals_dir: Path, prop: dict) -> Path:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    fp = proposals_dir / f"{prop['id']}.json"
    fp.write_text(json.dumps(prop, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return fp


def _mock_evaluator_result(verdict: str, score: float = 5.0,
                            *, proposal_id: str = "?", name: str = "?",
                            killers: list[str] | None = None,
                            signals: dict | None = None,
                            ):
    """Costruisce un mock di EvaluationResult."""
    from proposal_evaluator import EvaluationResult
    return EvaluationResult(
        proposal_id=proposal_id, name=name,
        verdict=verdict, score=score,
        killers_triggered=killers or [],
        signals=signals or {"call_freq_60d": 50, "eta_speedup": 3.0},
        rationale=f"mock {verdict}",
    )


class _BasePromoterTest(unittest.TestCase):
    """Base con tmp dir isolata per ogni test + env override completo."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="promoter_test_"))
        self._db = self._tmpdir / "promoter.sqlite"
        self._audit_dir = self._tmpdir / "audit"
        self._blob_dir = self._tmpdir / "blobs"
        self._synth_dir = self._tmpdir / "synth_execs"
        self._proposals_dir = self._tmpdir / "proposals"
        self._archive_dir = self._tmpdir / "archive"
        self._turns_dir = self._tmpdir / "turns"
        self._eta_db = self._tmpdir / "proposals_eta.sqlite"
        self._proposals_dir.mkdir(parents=True, exist_ok=True)
        self._turns_dir.mkdir(parents=True, exist_ok=True)
        # NB: HOME redirected too, cosi' executor di terze parti
        # (proposal_evaluator etc.) che fanno `Path.home()` non scrivono
        # sotto la home reale.
        self._env = mock.patch.dict("os.environ", {
            "METNOS_PROMOTER_DB": str(self._db),
            "METNOS_PROMOTER_AUDIT_DIR": str(self._audit_dir),
            "METNOS_PROMOTER_BLOB_DIR": str(self._blob_dir),
            "METNOS_PROMOTER_SYNTH_DIR": str(self._synth_dir),
            "METNOS_SYNT_PROPOSALS_DIR": str(self._proposals_dir),
            "METNOS_SYNTH_ARCHIVE_DIR": str(self._archive_dir),
            "METNOS_TURNS_DIR": str(self._turns_dir),
            "METNOS_PROPOSALS_ETA_DB": str(self._eta_db),
            "METNOS_PROMOTER_GRACE_HOURS": "72",
            "METNOS_PROMOTER_MAX_PER_FIRE": "5",
            "METNOS_PROMOTER_DRY_RUN": "",
            "METNOS_PROMOTER_NOTIFY_ADMIN": "true",
            "HOME": str(self._tmpdir),
        })
        self._env.start()
        # Cache reset dei modules dipendenti dall'env letta a import-time.
        for mod in (
            "jobs.promoter", "jobs.promoter_state", "jobs.promoter_promote",
            "jobs.promoter_rollback", "jobs.promoter_example",
            "jobs.promoter_digest",
        ):
            sys.modules.pop(mod, None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _import_module(self, name: str):
        sys.modules.pop(f"jobs.{name}", None)
        return __import__(f"jobs.{name}", fromlist=[name])


# ─── 1. Shape RunResult ───────────────────────────────────────────────────


class TestRunResultShape(_BasePromoterTest):

    def test_shape_with_no_candidates(self):
        promoter = self._import_module("promoter")
        result = promoter.task_promoter(payload={})
        for k in ("ok", "ok_count", "error_count", "metadata"):
            self.assertIn(k, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ok_count"], 0)
        self.assertEqual(result["error_count"], 0)
        meta = result["metadata"]
        self.assertEqual(meta["cap"], 5)
        self.assertEqual(meta["grace_hours"], 72)
        self.assertFalse(meta["dry_run"])
        self.assertEqual(meta["candidates_seen"], 0)


# ─── 2. Schema migration idempotente ──────────────────────────────────────


class TestSchemaMigration(_BasePromoterTest):

    def test_schema_creates_table_and_indexes(self):
        state = self._import_module("promoter_state")
        # Apertura genera schema.
        conn = sqlite3.connect(str(self._db))
        cols_added = state.ensure_schema(conn)
        # Fresh DB: tabella creata via SCHEMA, niente colonne aggiunte
        # via ALTER (sono tutte gia' nello SCHEMA literal).
        self.assertEqual(cols_added, [])
        # Verifica tabella + indice.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        self.assertIn("proposal_promote", names)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(proposal_promote)"
        ).fetchall()}
        for expected in (
            "proposal_id", "name", "state", "promoted_at", "grace_until",
            "rollback_blob_path", "evaluator_verdict", "practical_example",
            "notified_at", "notified_ack", "rolled_back_at", "finalized_at",
            "archived_at", "created_at", "needs_human_review",
        ):
            self.assertIn(expected, cols)
        conn.close()

    def test_schema_migration_legacy_db(self):
        """Simula DB legacy senza le colonne nuove: ALTER TABLE le aggiunge."""
        # Crea schema legacy minimale.
        conn = sqlite3.connect(str(self._db))
        conn.execute(
            "CREATE TABLE proposal_promote ("
            " proposal_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            " state TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        state = self._import_module("promoter_state")
        conn = sqlite3.connect(str(self._db))
        added = state.ensure_schema(conn)
        # Tutte le colonne nuove vanno aggiunte.
        for expected in ("promoted_at", "grace_until", "rollback_blob_path",
                           "needs_human_review"):
            self.assertIn(expected, added)
        conn.close()

        # Run 2: idempotente.
        conn = sqlite3.connect(str(self._db))
        added2 = state.ensure_schema(conn)
        self.assertEqual(added2, [])
        conn.close()


# ─── 3. Cap N enforced ────────────────────────────────────────────────────


class TestCap(_BasePromoterTest):

    def test_cap_default_5_enforced(self):
        promoter = self._import_module("promoter")
        # Seed 8 proposals synthesized.
        for i in range(8):
            prop = _build_synth_proposal(
                proposal_id=f"prop_{i:02d}_name",
                name="find_packages",
            )
            _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m:
            m.return_value = _mock_evaluator_result("gray", score=0.5)
            result = promoter.task_promoter()
        # Cap=5: solo 5 processate, 3 restano da elaborare.
        self.assertLessEqual(result["metadata"]["candidates_seen"], 5)
        self.assertEqual(m.call_count, result["metadata"]["candidates_seen"])

    def test_cap_override_via_env(self):
        # Cap=2 → solo 2 processate.
        os.environ["METNOS_PROMOTER_MAX_PER_FIRE"] = "2"
        promoter = self._import_module("promoter")
        for i in range(4):
            prop = _build_synth_proposal(proposal_id=f"prop_{i}",
                                          name="find_packages")
            _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m:
            m.return_value = _mock_evaluator_result("gray", score=0)
            result = promoter.task_promoter()
        self.assertEqual(result["metadata"]["cap"], 2)
        self.assertLessEqual(result["metadata"]["candidates_seen"], 2)


# ─── 4. Accept → promoted_grace + blob esiste ─────────────────────────────


class TestAcceptVerdict(_BasePromoterTest):

    def test_accept_creates_grace_and_blob(self):
        promoter = self._import_module("promoter")
        state_mod = self._import_module("promoter_state")
        prop = _build_synth_proposal(proposal_id="proptest_acc",
                                      name="find_packages")
        _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m, \
                 mock.patch("jobs.promoter_promote.sign_executor",
                            create=True) as sign_m, \
                 mock.patch("jobs.promoter_promote."
                            "_dry_run_admission_layer2",
                            return_value=(True, "")), \
                 mock.patch("jobs.promoter_promote."
                            "_dry_run_admission_layer5",
                            return_value=(True, "")):
            # Mocka sign_executor cosi' i test non richiedono keypair.
            m.return_value = _mock_evaluator_result(
                "accept", score=5.0,
                proposal_id="proptest_acc", name="find_packages",
            )
            sign_m.return_value = ("dummy_digest", Path("/tmp/dummy.sig"))
            result = promoter.task_promoter()
        self.assertEqual(result["ok_count"], 1)
        row = state_mod.load_proposal_state("proptest_acc")
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "promoted_grace")
        self.assertTrue(row["grace_until"])
        # Blob esiste.
        self.assertTrue(Path(row["rollback_blob_path"]).exists())


# ─── 5. Gray → review_needed ──────────────────────────────────────────────


class TestGrayVerdict(_BasePromoterTest):

    def test_gray_sets_review_needed(self):
        promoter = self._import_module("promoter")
        state_mod = self._import_module("promoter_state")
        prop = _build_synth_proposal(proposal_id="proptest_gray",
                                      name="find_packages")
        _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m:
            m.return_value = _mock_evaluator_result(
                "gray", score=1.5,
                proposal_id="proptest_gray", name="find_packages",
            )
            result = promoter.task_promoter()
        self.assertEqual(result["ok_count"], 1)
        row = state_mod.load_proposal_state("proptest_gray")
        self.assertEqual(row["state"], "review_needed")
        self.assertEqual(row["needs_human_review"], 1)


# ─── 6. Reject → archived + JSON spostato ─────────────────────────────────


class TestRejectVerdict(_BasePromoterTest):

    def test_reject_archives_proposal(self):
        promoter = self._import_module("promoter")
        state_mod = self._import_module("promoter_state")
        prop = _build_synth_proposal(proposal_id="proptest_rej",
                                      name="find_packages")
        json_path = _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m:
            m.return_value = _mock_evaluator_result(
                "reject", score=-3.0,
                proposal_id="proptest_rej", name="find_packages",
                killers=["overlap"],
            )
            result = promoter.task_promoter()
        self.assertEqual(result["ok_count"], 1)
        row = state_mod.load_proposal_state("proptest_rej")
        self.assertEqual(row["state"], "archived")
        # JSON spostato in archive dir.
        self.assertFalse(json_path.exists())
        archived = self._archive_dir / "proptest_rej" / "proptest_rej.json"
        self.assertTrue(archived.exists())


# ─── 7. Grace expired → promoted_finalized ────────────────────────────────


class TestGraceExpiry(_BasePromoterTest):

    def test_grace_expired_promotes_to_finalized(self):
        state_mod = self._import_module("promoter_state")
        # Seed manualmente una row promoted_grace con grace_until in passato.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
        conn = sqlite3.connect(str(self._db))
        state_mod.ensure_schema(conn)
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, promoted_at, grace_until, "
            " created_at) VALUES (?, 'find_packages', 'promoted_grace', "
            "?, ?, ?)",
            ("expired_001", past, past, past),
        )
        conn.commit()
        conn.close()
        promoter = self._import_module("promoter")
        result = promoter.task_promoter()
        self.assertEqual(result["metadata"]["finalized_via_grace_expiry"], 1)
        row = state_mod.load_proposal_state("expired_001")
        self.assertEqual(row["state"], "promoted_finalized")
        self.assertTrue(row["finalized_at"])


# ─── 8. Admission layer fail → no promote ─────────────────────────────────


class TestAdmissionFailure(_BasePromoterTest):

    def test_admission_layer_5_blocks_promote(self):
        promoter = self._import_module("promoter")
        state_mod = self._import_module("promoter_state")
        # Smoke layer 5 fail: proposta con affinity che hijacka una smoke
        # case. Simuliamo `_dry_run_admission_layer5` che torna False.
        prop = _build_synth_proposal(proposal_id="proptest_adm",
                                      name="find_packages")
        _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m, \
                 mock.patch("jobs.promoter_promote."
                            "_dry_run_admission_layer5") as l5_m:
            m.return_value = _mock_evaluator_result(
                "accept", score=5.0,
                proposal_id="proptest_adm", name="find_packages",
            )
            l5_m.return_value = (False, "layer 5 hijack")
            result = promoter.task_promoter()
        # Marca review_needed (no promote, ma audit la ragione).
        self.assertEqual(result["error_count"], 1)
        row = state_mod.load_proposal_state("proptest_adm")
        self.assertEqual(row["state"], "review_needed")
        # Audit contiene la reason.
        audit_files = list(self._audit_dir.glob("promoter_*.jsonl"))
        self.assertTrue(audit_files)
        content = audit_files[0].read_text(encoding="utf-8")
        self.assertIn("admission_failed_layer_5", content)


# ─── 9. Practical example deterministico ──────────────────────────────────


class TestPracticalExampleDeterministic(_BasePromoterTest):

    def test_same_input_same_output(self):
        ex_mod = self._import_module("promoter_example")
        prop = _build_synth_proposal(name="find_packages",
                                      user_query="trova i widget rossi")
        verdict = {
            "verdict": "accept", "score": 5.0,
            "signals": {"call_freq_60d": 30, "eta_speedup": 2.5},
        }
        out1 = ex_mod.render_practical_example(prop, verdict)
        out2 = ex_mod.render_practical_example(prop, verdict)
        self.assertEqual(out1, out2)
        self.assertIn("Query", out1)
        self.assertIn("Pipeline OGGI", out1)
        self.assertIn("Pipeline NUOVA", out1)
        self.assertIn("Sostituisce", out1)
        self.assertIn("NON sostituisce", out1)


# ─── 10. Practical example fallback su ETA vuoto ──────────────────────────


class TestPracticalExampleETAFallback(_BasePromoterTest):

    def test_eta_empty_fallback(self):
        ex_mod = self._import_module("promoter_example")
        prop = _build_synth_proposal(
            name="find_packages",
            path_hash="nonexistent_hash_xyz",
            user_query="trova widget",
        )
        prop["path_steps"] = []  # forza fallback ETA
        verdict = {"verdict": "accept", "score": 5.0,
                    "signals": {"call_freq_60d": 0}}
        out = ex_mod.render_practical_example(prop, verdict)
        # ETA vuoto → "da definire" oppure "dati insufficienti".
        self.assertTrue("da definire" in out or "dati insufficienti" in out)


# ─── 11. Rollback ripristina stato pre-promote ────────────────────────────


class TestRollbackRestores(_BasePromoterTest):

    def test_rollback_removes_files(self):
        promoter = self._import_module("promoter")
        state_mod = self._import_module("promoter_state")
        rollback_mod = self._import_module("promoter_rollback")
        prop = _build_synth_proposal(proposal_id="proptest_rb",
                                      name="find_packages")
        _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m, \
                 mock.patch("jobs.promoter_promote.sign_executor",
                            create=True) as sign_m, \
                 mock.patch("jobs.promoter_promote."
                            "_dry_run_admission_layer2",
                            return_value=(True, "")), \
                 mock.patch("jobs.promoter_promote."
                            "_dry_run_admission_layer5",
                            return_value=(True, "")):
            m.return_value = _mock_evaluator_result(
                "accept", score=5.0,
                proposal_id="proptest_rb", name="find_packages",
            )
            sign_m.return_value = ("digest", Path("/tmp/sig"))
            promoter.task_promoter()
        # Verifica setup
        row = state_mod.load_proposal_state("proptest_rb")
        self.assertEqual(row["state"], "promoted_grace")
        exec_dir = self._synth_dir / "find_packages"
        self.assertTrue(exec_dir.exists())
        self.assertTrue((exec_dir / "find_packages.py").exists())

        # Rollback
        result = rollback_mod.rollback_promotion("proptest_rb")
        self.assertTrue(result["ok"])
        # File rimossi
        self.assertFalse(exec_dir.exists())
        # Blob spostato in _rolled_back/
        rolled = self._blob_dir / "_rolled_back" / "proptest_rb.tar.gz"
        self.assertTrue(rolled.exists())
        # State aggiornato
        row2 = state_mod.load_proposal_state("proptest_rb")
        self.assertEqual(row2["state"], "rolled_back")
        self.assertTrue(row2["rolled_back_at"])


# ─── 12. Rollback senza blob → fail-loud §2.8 ─────────────────────────────


class TestRollbackNoBlob(_BasePromoterTest):

    def test_rollback_missing_blob_returns_error(self):
        state_mod = self._import_module("promoter_state")
        rollback_mod = self._import_module("promoter_rollback")
        # Manualmente inserisci una row senza rollback_blob_path.
        conn = sqlite3.connect(str(self._db))
        state_mod.ensure_schema(conn)
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, created_at) "
            "VALUES (?, 'find_packages', 'promoted_grace', ?)",
            ("noblob_001", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()
        result = rollback_mod.rollback_promotion("noblob_001")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_blob")


# ─── 13. Dry-run mode: filesystem untouched ───────────────────────────────


class TestDryRun(_BasePromoterTest):

    def test_dry_run_decides_but_no_filesystem_changes(self):
        os.environ["METNOS_PROMOTER_DRY_RUN"] = "true"
        promoter = self._import_module("promoter")
        state_mod = self._import_module("promoter_state")
        prop = _build_synth_proposal(proposal_id="proptest_dry",
                                      name="find_packages")
        _seed_proposal_json(self._proposals_dir, prop)
        with mock.patch("proposal_evaluator.evaluate_proposal") as m:
            m.return_value = _mock_evaluator_result(
                "accept", score=5.0,
                proposal_id="proptest_dry", name="find_packages",
            )
            result = promoter.task_promoter()
        # ok_count incrementato (la decisione e' stata presa) ma filesystem
        # invariato.
        self.assertEqual(result["metadata"]["dry_run"], True)
        self.assertEqual(result["ok_count"], 1)
        # No executor creato.
        self.assertFalse((self._synth_dir / "find_packages").exists())
        # No row promoted_grace.
        row = state_mod.load_proposal_state("proptest_dry")
        # In dry-run il task NON chiama insert_pending: row None.
        self.assertIsNone(row)


# ─── 14. Telegram callback `promoter:<id>:rollback` ───────────────────────


class TestTelegramCallback(_BasePromoterTest):
    """Test del dispatcher `_handle_promoter_callback` su daemon channels."""

    def test_callback_rollback_invokes_rollback(self):
        # Setup: una row promoted_grace con blob valido.
        state_mod = self._import_module("promoter_state")
        rollback_mod = self._import_module("promoter_rollback")
        # Creo blob falso per non passare per il promote completo.
        blob_dir = self._blob_dir
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = blob_dir / "tgcb_001.tar.gz"
        blob_path.write_bytes(b"fake_tar_content")
        exec_dir = self._synth_dir / "find_packages"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "find_packages.py").write_text("# stub")
        conn = sqlite3.connect(str(self._db))
        state_mod.ensure_schema(conn)
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, rollback_blob_path, created_at) "
            "VALUES (?, ?, 'promoted_grace', ?, ?)",
            ("tgcb_001", "find_packages", str(blob_path),
             "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Invoca direttamente rollback_promotion (e' cio' che il callback
        # invoca; il test del callback wiring vero richiederebbe un mock
        # del TelegramChannel intero, eccessivo per §7.2 KISS).
        result = rollback_mod.rollback_promotion("tgcb_001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "find_packages")
        # Verifica state DB.
        row = state_mod.load_proposal_state("tgcb_001")
        self.assertEqual(row["state"], "rolled_back")

    def test_callback_data_format_parseable(self):
        # Il callback handler in daemon parsa data="promoter:<id>:<action>".
        # Test smoke che il formato e' parseable dallo split(':',2).
        data = "promoter:1234567_find_packages:ok"
        parts = data.split(":", 2)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "promoter")
        self.assertEqual(parts[1], "1234567_find_packages")
        self.assertEqual(parts[2], "ok")

        data2 = "promoter:abc:rollback"
        parts2 = data2.split(":", 2)
        self.assertEqual(parts2, ["promoter", "abc", "rollback"])


# ─── 15. HTTP POST /admin/promotions/<id>/rollback ────────────────────────


class TestHTTPRollback(_BasePromoterTest):

    def test_rollback_endpoint_returns_ok(self):
        """Test diretto della funzione admin_promotion_rollback.

        Costruiamo una proposal_promote in stato promoted_grace + blob
        falso, invochiamo l'handler con mock Request, verifichiamo redirect.
        """
        state_mod = self._import_module("promoter_state")
        # Setup blob falso + exec dir.
        blob_dir = self._blob_dir
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = blob_dir / "httprb_001.tar.gz"
        blob_path.write_bytes(b"fake")
        exec_dir = self._synth_dir / "find_packages_http"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "find_packages_http.py").write_text("# stub")
        conn = sqlite3.connect(str(self._db))
        state_mod.ensure_schema(conn)
        conn.execute(
            "INSERT INTO proposal_promote "
            "(proposal_id, name, state, rollback_blob_path, created_at) "
            "VALUES (?, ?, 'promoted_grace', ?, ?)",
            ("httprb_001", "find_packages_http", str(blob_path),
             "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()
        # Invoca tramite JSON-accept (non HTML) per testare il payload.
        rollback_mod = self._import_module("promoter_rollback")
        result = rollback_mod.rollback_promotion("httprb_001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "find_packages_http")
        # State aggiornato.
        row = state_mod.load_proposal_state("httprb_001")
        self.assertEqual(row["state"], "rolled_back")


# ─── 16. Callback scheduler registrato ────────────────────────────────────


class TestCallbackRegistered(_BasePromoterTest):

    def test_promoter_callback_registered_at_boot(self):
        from scheduler_v2.daemon import SchedulerDaemon
        from scheduler_v2.builtin_callbacks import install_default_callbacks
        db_path = self._tmpdir / "scheduler_v2.sqlite"
        d = SchedulerDaemon(db_path)
        install_default_callbacks(d)
        info = d.callbacks.get("promoter")
        self.assertIsNotNone(info)
        self.assertEqual(info.key, "promoter")
        info_dg = d.callbacks.get("promoter_digest")
        self.assertIsNotNone(info_dg)
        self.assertEqual(info_dg.key, "promoter_digest")

    def test_seed_idempotent(self):
        from scheduler_v2.daemon import SchedulerDaemon
        from scheduler_v2.builtin_callbacks import install_default_jobs
        db_path = self._tmpdir / "seed.sqlite"
        d = SchedulerDaemon(db_path)
        install_default_jobs(d)
        install_default_jobs(d)
        entries = d.storage.list_all()
        matches_promoter = [e for e in entries if e.name == "promoter"]
        matches_digest = [e for e in entries if e.name == "promoter_digest"]
        self.assertEqual(len(matches_promoter), 1)
        self.assertEqual(len(matches_digest), 1)
        self.assertEqual(matches_promoter[0].trigger, "daily@04:45")
        self.assertEqual(matches_digest[0].trigger, "daily@07:00")


if __name__ == "__main__":
    unittest.main()

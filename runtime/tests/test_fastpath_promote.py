"""test_fastpath_promote.py — promozione fastpath L0 → executor synt (11/6/2026).

Copre il mandato promozione:
  1. PROVENIENZA — l'executor promosso porta i fp_id/hash di origine; la
     morte C2 diventa ESATTA per provenienza (non più solo name-based).
  2. DETECTION — cluster semantici (shape+intent) con gating conservativo:
     i candidati deboli (mono-step, <3 distinti, poco usati, giovani) sono
     RIFIUTATI (test anti-esplosione).
  3. TIER 1 — proposta emessa nel canale introvertiva (proposals_state),
     dedupe per sig_key + vs catalog + vs generalize pendenti.
  4. TIER 2 — auto-promozione dietro flag (OFF default), floor alto,
     hard cap 1/notte, attraverso la pipeline synt (handle_synth_request).

Mock everywhere — no live LLM, no live executor, no BGE-M3.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import Framework, Intent, StepSpec
from engine import fastpath as eng_fastpath


def _fw(*tools, args_map=None, final="fatto"):
    args_map = args_map or {}
    steps = [StepSpec(tool=t, args=dict(args_map.get(t, {}))) for t in tools]
    steps.append(StepSpec(tool="final_answer", args={}))
    return Framework(steps=steps, final_message=final)


_NOW = 1_780_000_000.0  # epoch fisso (§7.9)
_AGING = dict(stale_days=30, grace_days=14, max_rows=500, now_ts=_NOW)


def _iso_days_ago(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(_NOW - days * 86400))


class _FastpathDbCase(unittest.TestCase):
    """DB fastpath isolato per-test (stesso pattern di test_fastpath_lifecycle)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(self.tmp) / "fastpaths.sqlite"
        eng_fastpath._DB_INIT_DONE = False

    def tearDown(self):
        eng_fastpath._db_path = self._orig
        eng_fastpath._DB_INIT_DONE = False

    def _seed(self, query: str, fw: Framework, *, intent=None,
              created_days_ago: float = 0.0, n_uses: int = 0,
              used_days_ago: float | None = 1.0) -> tuple[int, str]:
        """record_success + retrodatazione/usi. Ritorna (fp_id, canonical_hash)."""
        fp_id = eng_fastpath.record_success(query, fw, intent=intent)
        self.assertGreater(fp_id, 0)
        c = eng_fastpath._conn()
        c.execute(
            "UPDATE fastpaths SET created_at = ?, n_uses = ?, last_used = ? "
            "WHERE id = ?",
            (_iso_days_ago(created_days_ago), n_uses,
             _iso_days_ago(used_days_ago) if used_days_ago is not None
             else None, fp_id))
        chash = c.execute("SELECT canonical_hash FROM fastpaths WHERE id = ?",
                          (fp_id,)).fetchone()[0]
        c.commit()
        c.close()
        return fp_id, chash


# ── 1. Provenienza → morte C2 esatta ───────────────────────────────────────

class TestProvenanceDeath(_FastpathDbCase):
    """L'executor promosso porta i fp_id di origine: quando entra nel
    catalog, i fastpath di provenienza muoiono ESATTAMENTE (anche dove il
    match name-based C2 era un falso-negativo)."""

    def test_promoted_executor_kills_member_exact(self):
        # Composizione: il piano USA find_images (famiglia dell'intent) →
        # name-C2 immune per costruzione. La provenienza chiude il buco.
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di silvia al mare",
            _fw("find_persons", "find_images"), intent=intent)
        n = eng_fastpath.record_promotion(
            "find_images_similar", [(fp_id, chash)], tier=2)
        self.assertEqual(n, 1)
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images",
                           "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        self.assertEqual(rep["dead_superseded"], 0)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_promotion_inert_until_executor_in_catalog(self):
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di silvia al mare",
            _fw("find_persons", "find_images"), intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_promoted_in_plan_immune(self):
        # Il fastpath NUOVO che usa l'executor promosso NON deve morire
        # (si ri-creerebbe in loop): immunità se il piano lo contiene.
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di silvia al mare", _fw("find_images_similar"),
            intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        rep = eng_fastpath.prune(
            catalog_names={"find_images", "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)

    def test_provenance_survives_recreation_via_hash(self):
        # Il fastpath muore (prune/delete) e si RI-CREA con fp_id nuovo:
        # stessa query → stesso canonical_hash → provenienza ancora valida.
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di silvia al mare",
            _fw("find_persons", "find_images"), intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        eng_fastpath.delete(fp_id)
        fp_id2, chash2 = self._seed(
            "foto di silvia al mare",
            _fw("find_persons", "find_images"), intent=intent)
        self.assertNotEqual(fp_id, fp_id2)
        self.assertEqual(chash, chash2)
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images",
                           "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        self.assertEqual(eng_fastpath.list_all(), [])

    def test_provenance_exact_only_members_die(self):
        # Esattezza: muore SOLO il membro registrato, non il vicino con
        # stesso shape ma fuori dalla provenienza (e intent diverso).
        intent = Intent(verb="find", object="images")
        member_id, member_hash = self._seed(
            "foto di silvia al mare",
            _fw("find_persons", "find_images"), intent=intent)
        outsider_id, _ = self._seed(
            "cerca documenti di marco",
            _fw("find_persons", "find_files"))  # intent assente
        eng_fastpath.record_promotion("find_images_similar",
                                      [(member_id, member_hash)])
        rep = eng_fastpath.prune(
            catalog_names={"find_persons", "find_images", "find_files",
                           "find_images_similar"}, **_AGING)
        self.assertEqual(rep["dead_promoted"], 1)
        ids = {r["id"] for r in eng_fastpath.list_all()}
        self.assertEqual(ids, {outsider_id})

    def test_record_promotion_idempotent(self):
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di silvia", _fw("find_persons", "find_images"),
            intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)], tier=1)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)], tier=2)
        rows = eng_fastpath.list_promotions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tier"], 2)  # refresh aggiorna il tier

    def test_no_catalog_no_provenance_death(self):
        # catalog_names=None → nessuna morte, nemmeno per provenienza (§2.8).
        intent = Intent(verb="find", object="images")
        fp_id, chash = self._seed(
            "foto di silvia", _fw("find_persons", "find_images"),
            intent=intent)
        eng_fastpath.record_promotion("find_images_similar",
                                      [(fp_id, chash)])
        rep = eng_fastpath.prune(catalog_names=None, **_AGING)
        self.assertEqual(rep["dead_promoted"], 0)
        self.assertEqual(len(eng_fastpath.list_all()), 1)


if __name__ == "__main__":
    unittest.main()

"""Test routing PLANNER unified post-ADR0117.

Verifica che il prompt IT non instrada piu' al pattern 2-step
(find_persons_indices → find_images_indices con paths_filter), e che
il blocco (W) IMMAGINI E FOTO contiene gli args attesi.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

_PLANNER_IT = _RUNTIME / "prompts" / "it" / "planner.j2"


class TestPlannerPromptUnified(unittest.TestCase):

    def setUp(self):
        self.text = _PLANNER_IT.read_text(encoding="utf-8")

    def test_prompt_file_exists(self):
        self.assertTrue(_PLANNER_IT.exists())
        self.assertGreater(len(self.text), 1000)

    def test_w_block_unified_present(self):
        # Il nuovo blocco (W) IMMAGINI E FOTO deve esserci
        self.assertIn("(W) IMMAGINI E FOTO", self.text)
        self.assertIn("indice unificato", self.text)
        self.assertIn("ADR 0117", self.text)

    def test_no_legacy_2step_paths_filter_pattern(self):
        # Non deve piu' raccomandare la pipeline a 2 step esplicita
        # con find_persons → find_images (idx="scene", paths_filter)
        # nei blocchi pertinenti. (W) e' stato sostituito.
        # Cerca pattern obsoleto: idx="scene" usato in DEVI/OK del block W
        # (assumendo che il block legacy contenesse "step1: find_persons_indices(name=...)")
        # Rimosso interamente.
        legacy_step1 = "step1: find_persons_indices(name="
        self.assertNotIn(legacy_step1, self.text)

    def test_w_block_documents_unified_args(self):
        # Tutti gli args principali devono apparire
        for arg in ("name=", "query_text=", "min_face_pixels", "min_face_count",
                     "max_face_count", "reference_images", "near_lat",
                     "time_window"):
            self.assertIn(
                arg, self.text,
                f"PLANNER (W) block missing arg reference: {arg}",
            )

    def test_w_alias_block_present(self):
        # Il blocco thin alias deve restare visibile
        self.assertIn("(W.alias) PERSONS THIN ALIAS", self.text)
        self.assertIn("find_persons_indices(name", self.text)

    def test_examples_combine_args_in_single_call(self):
        # Esempi devono mostrare combine in UNA call
        self.assertIn(
            'find_images_indices(name="silvia", query_text="mare")',
            self.text,
        )

    def test_idx_arg_marked_deprecated(self):
        self.assertIn("idx=", self.text)
        # Deve essere nel "NON DEVI"
        # (forma prescrittiva §6)
        # Verifica indiretta: la stringa "deprecato" o "ADR 0117" e' vicina
        idx_block_idx = self.text.find("NON DEVI: passare `idx=`")
        self.assertGreater(idx_block_idx, 0, "expected NON DEVI block per idx")


if __name__ == "__main__":
    unittest.main()

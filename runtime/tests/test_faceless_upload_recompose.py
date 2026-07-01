# SPDX-License-Identifier: AGPL-3.0-only
"""test_faceless_upload_recompose — separazione punteggi + descrizione VLM in
testa nel path upload-default faceless (dispatch, 1/7/2026).

La ricerca foto-senza-volto (upload → describe_images → find_images_indices
idx=scene) deve:
  (1) far PARTIRE la risposta dalla descrizione VLM (sempre utile);
  (2) mostrare «foto simili» SOLO se c'è separazione netta nei punteggi;
      banda piatta = rumore → entries/attachments/n_above_threshold azzerati.

Guard §7.9: la decisione è deterministica, sulla SEPARAZIONE relativa
(spread=(max-min)/max), non su una soglia assoluta (lo score composito
coseno+BM25 non ha scala interpretabile).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("METNOS_LANG", "it")

from engine.types import RunResult, StepRun
from engine import dispatch as D


def _step(idx, tool, result):
    return StepRun(step_idx=idx, tool=tool, args={}, result=result,
                   ok=True, latency_ms=0)


def _run_faceless(find_result, desc="uno screenshot di GitHub"):
    return RunResult(steps=[
        _step(1, "@uploaded", {"entries": [{"path": "/up.jpg"}]}),
        _step(2, "describe_images", {"query_text": desc,
                                     "entries": [{"path": "/up.jpg",
                                                  "description": desc}]}),
        _step(3, "find_images_indices", find_result),
    ], final_text="find_images_indices: completato", final_kind="answer")


class TestFlatDetection(unittest.TestCase):
    def test_flat_screenshot_band(self):
        # Caso reale: 13 foto punteggi ~1.76, spread 0.003 → piatto.
        xs = [1.7615, 1.7605, 1.7600, 1.7598, 1.7595, 1.7590, 1.7588]
        self.assertTrue(D._faceless_scores_are_flat(xs))

    def test_separated_venezia(self):
        # Foto-scena in indice: self=1.0 + coda → separato.
        xs = [1.0, 0.62, 0.55, 0.41, 0.30]
        self.assertFalse(D._faceless_scores_are_flat(xs))

    def test_single_result_not_flat(self):
        self.assertFalse(D._faceless_scores_are_flat([0.8]))

    def test_empty_not_flat(self):
        self.assertFalse(D._faceless_scores_are_flat([]))

    def test_all_zero_is_flat(self):
        self.assertTrue(D._faceless_scores_are_flat([0.0, 0.0, 0.0]))

    def test_strong_single_match_plus_noise(self):
        # Un match forte netto + rumore basso → separato (mostra).
        xs = [0.92, 0.28, 0.19, 0.10]
        self.assertFalse(D._faceless_scores_are_flat(xs))


class TestRecompose(unittest.TestCase):
    def test_flat_clears_results_and_leads_with_desc(self):
        entries = [{"path": f"/f{i}.jpg", "score": 1.76 + i * 0.0002}
                   for i in range(13)]
        find_res = {"entries": entries,
                    "attachments": [{"kind": "image", "path": e["path"]}
                                    for e in entries],
                    "n_above_threshold": 13}
        run = _run_faceless(find_res)
        D._recompose_faceless_upload(run)
        self.assertEqual(find_res["entries"], [])
        self.assertEqual(find_res["attachments"], [])
        self.assertEqual(find_res["n_above_threshold"], 0)
        self.assertIn("Nella foto:", run.final_text)
        self.assertIn("screenshot di GitHub", run.final_text)
        self.assertIn("Non ho trovato foto simili", run.final_text)

    def test_separated_keeps_results(self):
        entries = [{"path": "/self.jpg", "score": 1.0},
                   {"path": "/v1.jpg", "score": 0.6},
                   {"path": "/v2.jpg", "score": 0.4}]
        find_res = {"entries": entries,
                    "attachments": [{"kind": "image", "path": e["path"]}
                                    for e in entries],
                    "n_above_threshold": 3}
        run = _run_faceless(find_res, desc="una veduta di Venezia")
        D._recompose_faceless_upload(run)
        self.assertEqual(len(find_res["entries"]), 3)          # invariato
        self.assertEqual(len(find_res["attachments"]), 3)      # invariato
        self.assertIn("Nella foto:", run.final_text)
        self.assertIn("Venezia", run.final_text)
        self.assertIn("Foto simili nell'archivio: 3", run.final_text)

    def test_noop_without_describe_step(self):
        # Path a-volto: nessun describe_images → no-op (testo/entries intatti).
        run = RunResult(steps=[
            _step(1, "@uploaded", {"entries": [{"path": "/up.jpg"}]}),
            _step(2, "find_images_indices",
                  {"entries": [{"path": "/x.jpg", "score": 0.9}],
                   "attachments": [{"kind": "image", "path": "/x.jpg"}],
                   "n_above_threshold": 1}),
        ], final_text="ORIGINALE", final_kind="answer")
        D._recompose_faceless_upload(run)
        self.assertEqual(run.final_text, "ORIGINALE")
        self.assertEqual(len(run.steps[1].result["entries"]), 1)


if __name__ == "__main__":
    unittest.main()

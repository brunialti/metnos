"""alignment_engine (telos engine fase 2 — 22/5/2026).

Test deterministici della composizione formula + parser tollerante LLM
output. Niente chiamate LLM reali (mock).

Run: `python3 -m pytest tests/runtime/infra/test_alignment_engine.py -v`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _telos(telos_id, weight, threshold=0.20, phrase="phrase", notes=""):
    """Costruisce un Telos minimo per test (compatibile con dataclass frozen)."""
    from telos_loader import Telos
    return Telos(id=telos_id, phrase=phrase, weight=weight,
                 activation_threshold=threshold, notes=notes)


class ComposeFormulaTests(unittest.TestCase):
    """Math deterministica della formula alignment."""

    def test_single_telos_full_fit(self):
        from alignment_engine import compose, FitEstimate, _ALPHA
        t = [_telos("t.a", 1.0, threshold=0.2)]
        fits = [FitEstimate("t.a", 1.0)]
        # contrib=1.0, top=1.0, rest=0 → ea_base = ALPHA*1.0
        # ea = ALPHA * 1.0 urgency * 0.8 conf - 0
        self.assertAlmostEqual(compose(fits, t), _ALPHA * 0.8, places=4)

    def test_gate_blocks_subthreshold_fit(self):
        from alignment_engine import compose, FitEstimate
        t = [_telos("t.a", 1.0, threshold=0.5)]
        fits = [FitEstimate("t.a", 0.3)]  # sotto soglia 0.5
        # gate → 0, quindi expected_alignment = 0
        self.assertEqual(compose(fits, t), 0.0)

    def test_multi_telos_alpha_top_plus_gamma_rest(self):
        from alignment_engine import compose, FitEstimate, _ALPHA, _GAMMA
        t = [
            _telos("t.a", 0.6, threshold=0.2),
            _telos("t.b", 0.4, threshold=0.2),
        ]
        fits = [FitEstimate("t.a", 1.0), FitEstimate("t.b", 0.5)]
        # contribs = [0.6 * 1.0, 0.4 * 0.5] = [0.6, 0.2]
        # top=0.6, rest=0.2 → ea_base = ALPHA*0.6 + GAMMA*0.2
        # ea = ea_base * 1.0 * 0.8 - 0
        expected = (_ALPHA * 0.6 + _GAMMA * 0.2) * 0.8
        self.assertAlmostEqual(compose(fits, t), expected, places=4)

    def test_urgency_multiplies(self):
        from alignment_engine import compose, FitEstimate, _ALPHA
        t = [_telos("t.a", 1.0)]
        fits = [FitEstimate("t.a", 1.0)]
        base = _ALPHA * 0.8  # ea_base * confidence
        self.assertAlmostEqual(compose(fits, t, urgency=2.0), base * 2.0, places=4)
        self.assertAlmostEqual(compose(fits, t, urgency=0.5), base * 0.5, places=4)

    def test_bother_cost_subtracts(self):
        from alignment_engine import compose, FitEstimate, _ALPHA
        t = [_telos("t.a", 1.0)]
        fits = [FitEstimate("t.a", 1.0)]
        # base ALPHA*0.8, bother 0.3
        self.assertAlmostEqual(compose(fits, t, bother_cost=0.3),
                               _ALPHA * 0.8 - 0.3, places=4)

    def test_confidence_zero_zeros_alignment(self):
        from alignment_engine import compose, FitEstimate
        t = [_telos("t.a", 1.0)]
        fits = [FitEstimate("t.a", 1.0)]
        # confidence=0 → 0 - bother_cost
        self.assertEqual(compose(fits, t, confidence=0.0, bother_cost=0.1), -0.1)

    def test_urgency_clamped_to_range(self):
        from alignment_engine import compose, FitEstimate, _ALPHA
        t = [_telos("t.a", 1.0)]
        fits = [FitEstimate("t.a", 1.0)]
        base = _ALPHA * 0.8  # ea_base * confidence
        # 3.0 out of [0.5, 2.0] → clamp 2.0
        self.assertAlmostEqual(compose(fits, t, urgency=3.0), base * 2.0, places=4)
        # 0.1 → clamp 0.5
        self.assertAlmostEqual(compose(fits, t, urgency=0.1), base * 0.5, places=4)

    def test_missing_fit_for_telos_contributes_zero(self):
        from alignment_engine import compose, FitEstimate, _ALPHA
        t = [
            _telos("t.a", 0.5),
            _telos("t.b", 0.5),
        ]
        fits = [FitEstimate("t.a", 1.0)]  # nessun fit per t.b
        # contribs = [0.5, 0]; top=0.5, rest=0 → ea_base = ALPHA*0.5
        # ea = ALPHA*0.5 * 0.8
        self.assertAlmostEqual(compose(fits, t), _ALPHA * 0.5 * 0.8, places=4)

    def test_empty_telos_list_returns_zero(self):
        from alignment_engine import compose, FitEstimate
        self.assertEqual(compose([FitEstimate("t.x", 1.0)], []), 0.0)


class FormulaOrderingTests(unittest.TestCase):
    """Proprieta' qualitative della formula v1.3 α·top+γ·rest (22/5/2026)."""

    def _two_telos(self):
        return [
            _telos("t.heavy", 0.7, threshold=0.2),
            _telos("t.light", 0.3, threshold=0.2),
        ]

    def test_specialist_outperforms_diluted_generalist(self):
        """Specialista perfetto su telos pesante > tuttofare medio.
        Proprieta' centrale: la sum-product v1.0 la violava (tuttofare medio
        sommava molti fit moderati e batteva lo specialista). v1.3 ribalta
        grazie al boost ALPHA sul top1."""
        from alignment_engine import compose, FitEstimate
        t = self._two_telos()
        specialist = [FitEstimate("t.heavy", 1.0), FitEstimate("t.light", 0.0)]
        generalist = [FitEstimate("t.heavy", 0.5), FitEstimate("t.light", 0.5)]
        ea_spec = compose(specialist, t)
        ea_gen = compose(generalist, t)
        self.assertGreater(ea_spec, ea_gen,
                           f"specialista ({ea_spec:.3f}) deve battere tuttofare medio ({ea_gen:.3f})")

    def test_multi_strong_outperforms_specialist(self):
        """Multi-telos forte (fit alto su 2+ telos) > specialista perfetto su 1.
        La γ·rest deve dare credito alla copertura ampia."""
        from alignment_engine import compose, FitEstimate
        t = self._two_telos()
        specialist = [FitEstimate("t.heavy", 1.0), FitEstimate("t.light", 0.0)]
        multi_strong = [FitEstimate("t.heavy", 1.0), FitEstimate("t.light", 0.8)]
        ea_spec = compose(specialist, t)
        ea_multi = compose(multi_strong, t)
        self.assertGreater(ea_multi, ea_spec,
                           f"multi-forte ({ea_multi:.3f}) deve battere specialista ({ea_spec:.3f})")

    def test_alpha_above_three_gamma_invariant(self):
        """Vincolo critico ALPHA > 3*GAMMA: garantisce specialista perfetto
        su t.tempo (peso 0.25, fit=1) > tuttofare medio (fit=0.5 uniforme su
        tutti i telos). Derivato algebricamente in docstring modulo."""
        from alignment_engine import _ALPHA, _GAMMA
        self.assertGreater(_ALPHA, 3.0 * _GAMMA,
                           f"_ALPHA={_ALPHA} non > 3*_GAMMA={3*_GAMMA} — "
                           f"specialista perderebbe contro tuttofare medio")
        self.assertGreater(_GAMMA, 0.0,
                           "_GAMMA=0 azzererebbe il credito multi-telos")


class ParseFitsTests(unittest.TestCase):
    """Parser tollerante dell'output LLM."""

    def test_parse_well_formed_array(self):
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0), _telos("t.b", 1.0)]
        raw = '[{"telos_id":"t.a","fit":0.7,"why":"buono"},{"telos_id":"t.b","fit":0.3,"why":"meh"}]'
        fits = _parse_fits(raw, t)
        self.assertEqual(len(fits), 2)
        self.assertEqual(fits[0].telos_id, "t.a")
        self.assertAlmostEqual(fits[0].fit, 0.7)
        self.assertEqual(fits[0].why, "buono")

    def test_parse_with_markdown_fence(self):
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0)]
        raw = '```json\n[{"telos_id":"t.a","fit":1.0,"why":"x"}]\n```'
        fits = _parse_fits(raw, t)
        self.assertEqual(fits[0].fit, 1.0)

    def test_parse_with_surrounding_prose(self):
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0)]
        # LLM aggiunge prosa: il parser cerca il primo array JSON.
        raw = 'Ecco la risposta:\n[{"telos_id":"t.a","fit":0.5,"why":"ok"}]\n\nGrazie.'
        fits = _parse_fits(raw, t)
        self.assertEqual(fits[0].fit, 0.5)

    def test_parse_missing_telos_filled_with_zero(self):
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0), _telos("t.b", 1.0), _telos("t.c", 1.0)]
        raw = '[{"telos_id":"t.a","fit":0.8,"why":"x"}]'  # solo t.a
        fits = _parse_fits(raw, t)
        # 3 fits in output (uno per telos), gli altri 2 = 0 con why="(missing)"
        self.assertEqual(len(fits), 3)
        self.assertEqual(fits[0].fit, 0.8)
        self.assertEqual(fits[1].fit, 0.0)
        self.assertEqual(fits[1].why, "(missing)")

    def test_parse_invalid_telos_id_dropped(self):
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0)]
        raw = '[{"telos_id":"t.unknown","fit":1.0,"why":"x"},{"telos_id":"t.a","fit":0.4,"why":"y"}]'
        fits = _parse_fits(raw, t)
        # t.unknown ignorato, solo t.a presente
        self.assertEqual(len(fits), 1)
        self.assertEqual(fits[0].telos_id, "t.a")
        self.assertAlmostEqual(fits[0].fit, 0.4)

    def test_parse_fit_clamped_to_range(self):
        # v1.4: range [-1, 1] — il negativo è un CONFLITTO dichiarato dal
        # judge (penalità in compose), non più clampato a 0.
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0), _telos("t.b", 1.0), _telos("t.c", 1.0)]
        raw = ('[{"telos_id":"t.a","fit":1.5,"why":"x"},'
               '{"telos_id":"t.b","fit":-0.2,"why":"y"},'
               '{"telos_id":"t.c","fit":-3,"why":"z"}]')
        fits = _parse_fits(raw, t)
        self.assertEqual(fits[0].fit, 1.0)   # clamped da 1.5
        self.assertEqual(fits[1].fit, -0.2)  # preservato (conflitto)
        self.assertEqual(fits[2].fit, -1.0)  # clamped da -3

    def test_parse_garbage_returns_empty(self):
        from alignment_engine import _parse_fits
        t = [_telos("t.a", 1.0)]
        for raw in ("", "   ", "not json at all", "{not array}"):
            fits = _parse_fits(raw, t)
            # Garbage: array vuoto OPPURE fits con tutti 0 missing (entrambi accettabili)
            for f in fits:
                self.assertEqual(f.fit, 0.0)


class EstimateFitTests(unittest.TestCase):
    """End-to-end: estimate_fit con LLM mock."""

    def test_estimate_with_mock_llm(self):
        from alignment_engine import estimate_fit, _ALPHA, _GAMMA
        t = [_telos("t.a", 0.7, threshold=0.2),
             _telos("t.b", 0.3, threshold=0.2)]
        prop = {"lens": "scamper", "proposed_action": "x", "rationale": "y"}

        def fake_llm(prompt):
            self.assertIn("t.a", prompt)
            self.assertIn("scamper", prompt)
            return '[{"telos_id":"t.a","fit":1.0,"why":"a"},{"telos_id":"t.b","fit":0.5,"why":"b"}]'

        res = estimate_fit(prop, t, llm_invoke=fake_llm)
        # contribs=[0.7*1.0, 0.3*0.5]=[0.7, 0.15]; top=0.7, rest=0.15
        # ea = (ALPHA*0.7 + GAMMA*0.15) * 0.8
        expected = (_ALPHA * 0.7 + _GAMMA * 0.15) * 0.8
        self.assertAlmostEqual(res.expected_alignment, expected, places=4)
        self.assertEqual(len(res.per_telos), 2)

    def test_estimate_with_empty_telos_returns_zero(self):
        from alignment_engine import estimate_fit
        res = estimate_fit({"lens": "x"}, [], llm_invoke=lambda p: "[]")
        self.assertEqual(res.expected_alignment, 0.0)
        self.assertEqual(res.per_telos, [])

    def test_estimate_handles_llm_exception(self):
        from alignment_engine import estimate_fit
        t = [_telos("t.a", 1.0)]
        prop = {"lens": "x", "proposed_action": "y", "rationale": "z"}

        def bad_llm(prompt):
            raise ConnectionError("llama-server down")

        res = estimate_fit(prop, t, llm_invoke=bad_llm)
        # Exception NON propagata (degrade graceful). Tutti i fit = 0.
        self.assertEqual(res.expected_alignment, 0.0)


if __name__ == "__main__":
    unittest.main()

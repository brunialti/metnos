"""Test della stima % performance savings (E2, 11/5/2026).

Copre:
1. Time savings calcolo base ((old - new) / old * 100).
2. Fallback "n/a (dati insufficienti)" su input invalid (None / 0).
3. Token savings dai turn JSONL matching path_hash.
4. Token savings fallback su nessun turn matching.
5. Frequency: rispetta call_freq_60d.
6. Integration con render_practical_example: sezione "## Stima risparmio".

Determinismo §7.9: zero LLM nelle funzioni testate. Test isolati con tmpdir.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class _BasePerfTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="promoter_perf_"))
        self._turns_dir = self._tmpdir / "turns"
        self._turns_dir.mkdir(parents=True, exist_ok=True)
        self._env = mock.patch.dict("os.environ", {
            "HOME": str(self._tmpdir),
            "METNOS_TURNS_DIR": str(self._turns_dir),
        })
        self._env.start()
        for mod in (
            "jobs.promoter_example", "jobs.promoter_state",
        ):
            sys.modules.pop(mod, None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _seed_turn(self, *, path_hash: str, steps: list[dict],
                    user_query: str = "q",
                    file_name: str = "turns.jsonl") -> None:
        """Scrive UN turn record nel turns dir; usa path_shape per produrre
        un record che matcha path_hash quando interrogato.

        Nota: il path_shape e' calcolato dai chosen_tool degli steps, quindi
        passa step gia' costruiti col tool desiderato e usa path_shape_hash
        per ottenere path_hash. Per i test usiamo path_hash come prefisso
        del file e seed steps coerenti."""
        fp = self._turns_dir / file_name
        rec = {
            "ts_start": 1000.0, "ts_end": 1010.0,
            "user_query": user_query,
            "steps": steps,
        }
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


# ─── 1. Time savings calcolo base ─────────────────────────────────────────


class TestTimeSavings(_BasePerfTest):

    def test_basic_calculation(self):
        from jobs import promoter_example
        # old=5000ms, new=1700ms → (5000-1700)/5000 = 66.0%
        s = promoter_example._compute_perf_savings(
            5000, 1700, path_hash="empty_hash", call_freq_60d=10,
        )
        self.assertEqual(s["time_savings_pct"], 66.0)
        self.assertEqual(s["time_old_ms"], 5000)
        self.assertEqual(s["time_new_ms"], 1700)
        self.assertIsNone(s["time_fallback"])

    def test_no_speedup_or_slowdown(self):
        from jobs import promoter_example
        # new >= old → savings deve essere <=0 (onesto).
        s = promoter_example._compute_perf_savings(
            1000, 1500, path_hash="empty_hash", call_freq_60d=10,
        )
        # (1000 - 1500) / 1000 = -50.0%
        self.assertEqual(s["time_savings_pct"], -50.0)
        self.assertIsNone(s["time_fallback"])


# ─── 2. Fallback su input invalid ─────────────────────────────────────────


class TestTimeFallback(_BasePerfTest):

    def test_fallback_on_none(self):
        from jobs import promoter_example
        s = promoter_example._compute_perf_savings(
            None, 1700, path_hash="empty_hash", call_freq_60d=10,
        )
        self.assertIsNone(s["time_savings_pct"])
        self.assertEqual(s["time_fallback"], "n/a (dati insufficienti)")

    def test_fallback_on_zero(self):
        from jobs import promoter_example
        s = promoter_example._compute_perf_savings(
            0, 1700, path_hash="empty_hash", call_freq_60d=10,
        )
        self.assertIsNone(s["time_savings_pct"])
        self.assertEqual(s["time_fallback"], "n/a (dati insufficienti)")


# ─── 3+4. Token savings dai turn JSONL ────────────────────────────────────


class TestTokenSavings(_BasePerfTest):

    def test_aggregate_matching_path_hash(self):
        # Seed un turno con UN path_shape definito + uso il path_shape
        # calcolato per cercarlo.
        from jobs import promoter_example
        from path_shape import path_shape_hash
        steps = [
            {"chosen_tool": "find_files",
             "llm_in_tokens": 1500, "llm_out_tokens": 50, "error": None},
            {"chosen_tool": "filter_entries",
             "llm_in_tokens": 2500, "llm_out_tokens": 30, "error": None},
        ]
        ph = path_shape_hash(steps)
        self.assertTrue(ph, "path_shape_hash dovrebbe ritornare hash non-vuoto")
        self._seed_turn(path_hash=ph, steps=steps)
        self._seed_turn(path_hash=ph, steps=steps)  # 2 turni → mean
        mean, n = promoter_example._aggregate_token_in_for_path_hash(ph)
        # Mean = 1500 + 2500 = 4000 per ogni turno; due turni → 4000.
        self.assertEqual(mean, 4000)
        self.assertEqual(n, 2)

    def test_aggregate_no_match(self):
        from jobs import promoter_example
        mean, n = promoter_example._aggregate_token_in_for_path_hash(
            "no_such_hash"
        )
        self.assertEqual(mean, 0)
        self.assertEqual(n, 0)

    def test_token_savings_fallback_on_no_data(self):
        from jobs import promoter_example
        s = promoter_example._compute_perf_savings(
            5000, 1700, path_hash="no_match", call_freq_60d=10,
        )
        # tokens_in_old_mean = 0 → fallback su tokens.
        self.assertEqual(s["tokens_in_old_mean"], 0)
        self.assertEqual(s["tokens_fallback"], "n/a (dati insufficienti)")
        self.assertIsNone(s["tokens_savings_pct"])

    def test_token_savings_with_data(self):
        from jobs import promoter_example
        from path_shape import path_shape_hash
        steps = [
            {"chosen_tool": "find_files",
             "llm_in_tokens": 8000, "llm_out_tokens": 100, "error": None},
        ]
        ph = path_shape_hash(steps)
        self._seed_turn(path_hash=ph, steps=steps)
        s = promoter_example._compute_perf_savings(
            5000, 1700, path_hash=ph, call_freq_60d=10,
        )
        # tokens_in_new_estimated = 6KB * 0.25 = 1536
        # tokens_in_old_mean = 8000
        # savings = (8000 - 1536) / 8000 * 100 = 80.8%
        self.assertEqual(s["tokens_in_old_mean"], 8000)
        self.assertGreater(s["tokens_savings_pct"] or 0, 70.0)


# ─── 5. Markdown rendering della sezione perf savings ─────────────────────


class TestPerfMarkdown(_BasePerfTest):

    def test_format_with_data(self):
        from jobs import promoter_example
        savings = {
            "time_savings_pct": 66.0, "time_old_ms": 5000, "time_new_ms": 1700,
            "tokens_in_old_mean": 8000, "tokens_in_new_estimated": 1536,
            "tokens_savings_pct": 80.8, "tokens_sample_count": 3,
            "call_freq_60d": 42,
            "time_fallback": None, "tokens_fallback": None,
        }
        out = promoter_example._format_perf_savings_block(savings, lang="it")
        self.assertIn("## Stima del risparmio", out)
        self.assertIn("Tempo: 66.0%", out)
        self.assertIn("p50 corrente 5000 ms", out)
        self.assertIn("Token in ingresso: circa 8000 per turno (-80.8%)", out)
        self.assertIn("campione: 3 turni", out)
        self.assertIn("Frequenza: 42 chiamate in 60 giorni", out)

        # Una lingua non ancora presente nel catalogo usa il ripiego EN per
        # l'intero blocco, senza mescolare etichette italiane.
        fallback = promoter_example._format_perf_savings_block(
            savings, lang="fr")
        self.assertIn("## Estimated savings", fallback)
        self.assertIn("- Time:", fallback)
        self.assertIn("- Input tokens:", fallback)
        self.assertNotIn("Tempo:", fallback)

    def test_format_with_fallbacks(self):
        from jobs import promoter_example
        savings = {
            "time_savings_pct": None, "time_old_ms": None, "time_new_ms": None,
            "tokens_in_old_mean": 0, "tokens_in_new_estimated": 1536,
            "tokens_savings_pct": None, "tokens_sample_count": 0,
            "call_freq_60d": None,
            "time_fallback": "n/a (dati insufficienti)",
            "tokens_fallback": "n/a (dati insufficienti)",
        }
        out = promoter_example._format_perf_savings_block(savings, lang="it")
        self.assertIn("## Stima del risparmio", out)
        self.assertIn("Tempo: dati insufficienti", out)
        self.assertIn("Token in ingresso: dati insufficienti", out)
        self.assertIn("Frequenza: dati insufficienti", out)


# ─── 6. Integration con render_practical_example ──────────────────────────


class TestIntegrationMarkdown(_BasePerfTest):

    def test_render_includes_perf_section(self):
        from jobs import promoter_example
        prop = {
            "id": "p1", "name": "find_packages",
            "user_query": "trova pacchetti",
            "path_hash": "deadbeef", "path_steps": ["find_files"],
            "stages": [
                {"stage": 1, "success": True, "output": {
                    "name": "find_packages", "action": "find",
                }},
                {"stage": 2, "success": True, "output": {
                    "args_required": ["query"],
                    "args_properties": {"query": {"type": "string"}},
                }},
                {"stage": 3, "success": True, "output": {}},
                {"stage": 4, "success": True, "output": {
                    "description": "...", "affinity": [],
                }},
                {"stage": 5, "success": True, "output": {"code": "x"}},
            ],
        }
        verdict = {
            "verdict": "accept", "score": 4.0,
            "signals": {"call_freq_60d": 12},
        }
        out = promoter_example.render_practical_example(
            prop, verdict, skip_llm=True, lang="it",
        )
        self.assertIn("## Stima del risparmio", out)
        self.assertIn("Frequenza:", out)
        # 12 chiamate ci stanno.
        self.assertIn("12", out)


if __name__ == "__main__":
    unittest.main()

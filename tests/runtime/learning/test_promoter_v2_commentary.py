"""Test del commento LLM al practical_example (E1, 11/5/2026).

Copre:
1. _render_llm_commentary chiama call_llm con tier=creative e timeout 5s.
2. Fallback "(commento non disponibile)" su timeout/exception del LLM.
3. Integration in render_practical_example: produce 3 sezioni con marker.
4. Frontmatter prompt promoter_commentary.j2 esiste in IT.
5. Frontmatter prompt promoter_commentary.j2 esiste in EN (parity ADR 0092).

Determinismo: niente rete (mock di llm_helpers.call_llm). Test 5 isolati
in tmpdir + ENV override.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class _BaseCommentaryTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="promoter_commentary_"))
        self._env = mock.patch.dict("os.environ", {
            "HOME": str(self._tmpdir),
            "METNOS_TURNS_DIR": str(self._tmpdir / "turns"),
        })
        self._env.start()
        for mod in (
            "jobs.promoter_example", "jobs.promoter_state",
        ):
            sys.modules.pop(mod, None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


# ─── 1. _render_llm_commentary chiama call_llm tier=creative ──────────────


class TestCommentaryCall(_BaseCommentaryTest):

    def test_calls_llm_with_creative_tier(self):
        from jobs import promoter_example
        # Mock call_llm: ritorna un paragrafo finto.
        with mock.patch("llm_helpers.call_llm") as m:
            m.return_value = (
                "La proposta find_packages riduce p50 da 5000 a 1700ms su "
                "30 chiamate. No killer scattati, overlap basso. "
                "Rischio: args generici da monitorare in grace.",
                {"tier": "creative", "in_tokens": 800, "out_tokens": 60,
                 "latency_ms": 1200},
            )
            data = {
                "proposal_name": "find_packages",
                "sig_key": ["specialize", "find_files", "ext", "json"],
                "eta_p50_old_ms": 5000,
                "eta_p50_new_ms": 1700,
                "eta_count_60d": 30,
                "killers": [],
                "signals": {"eta_speedup": 2.9, "call_freq_60d": 30},
                "top_query": "trova i pacchetti json",
            }
            text = promoter_example._render_llm_commentary(data, lang="it")
        self.assertTrue(text)
        self.assertIn("find_packages", text)
        # Verifica che call_llm sia stato invocato con tier=creative.
        _, kwargs = m.call_args
        self.assertEqual(kwargs.get("tier"), "creative")
        # Cap di output ragionevole (<= 300 token).
        self.assertEqual(kwargs.get("max_tokens"), 300)


# ─── 2. Fallback su timeout / exception ───────────────────────────────────


class TestCommentaryFallback(_BaseCommentaryTest):

    def test_fallback_on_timeout(self):
        from jobs import promoter_example
        # Simula TimeoutError del Future.
        import concurrent.futures as _cf

        def _raise_timeout(*a, **kw):
            raise _cf.TimeoutError

        with mock.patch("llm_helpers.call_llm", side_effect=_raise_timeout):
            data = {
                "proposal_name": "x", "sig_key": None,
                "eta_p50_old_ms": None, "eta_p50_new_ms": None,
                "eta_count_60d": 0, "killers": [], "signals": {},
                "top_query": "",
            }
            text = promoter_example._render_llm_commentary(data, lang="it")
        self.assertEqual(text, "(commento non disponibile)")

    def test_fallback_on_provider_crash(self):
        from jobs import promoter_example
        with mock.patch("llm_helpers.call_llm",
                          side_effect=ConnectionError("llamacpp down")):
            data = {
                "proposal_name": "x", "sig_key": None,
                "eta_p50_old_ms": None, "eta_p50_new_ms": None,
                "eta_count_60d": 0, "killers": [], "signals": {},
                "top_query": "",
            }
            text = promoter_example._render_llm_commentary(data, lang="it")
        self.assertEqual(text, "(commento non disponibile)")

    def test_fallback_on_empty_response(self):
        from jobs import promoter_example
        with mock.patch("llm_helpers.call_llm") as m:
            m.return_value = ("", {"tier": "middle"})
            data = {
                "proposal_name": "x", "sig_key": None,
                "eta_p50_old_ms": None, "eta_p50_new_ms": None,
                "eta_count_60d": 0, "killers": [], "signals": {},
                "top_query": "",
            }
            text = promoter_example._render_llm_commentary(data, lang="it")
        self.assertEqual(text, "(commento non disponibile)")

    def test_fallback_en_locale(self):
        from jobs import promoter_example
        with mock.patch("llm_helpers.call_llm",
                          side_effect=RuntimeError("boom")):
            data = {
                "proposal_name": "x", "sig_key": None,
                "eta_p50_old_ms": None, "eta_p50_new_ms": None,
                "eta_count_60d": 0, "killers": [], "signals": {},
                "top_query": "",
            }
            text = promoter_example._render_llm_commentary(data, lang="en")
        self.assertEqual(text, "(commentary unavailable)")


# ─── 3. Integration con render_practical_example: 3 sezioni + marker ──────


class TestIntegration(_BaseCommentaryTest):

    def _make_proposal(self) -> dict:
        return {
            "id": "proptest_int",
            "name": "find_packages",
            "expected_name": "find_packages",
            "user_query": "trova pacchetti json",
            "path_hash": "abc123",
            "path_steps": ["find_files", "filter_entries"],
            "stages": [
                {"stage": 1, "success": True, "output": {
                    "name": "find_packages", "action": "find",
                    "object": "packages", "revertible": False,
                }},
                {"stage": 2, "success": True, "output": {
                    "args_required": ["query"],
                    "args_properties": {
                        "query": {"type": "string"},
                    },
                    "capabilities": [],
                    "reverse_pattern": None,
                }},
                {"stage": 3, "success": True, "output": {"tests": []}},
                {"stage": 4, "success": True, "output": {
                    "description": "Find packages by query.",
                    "affinity": ["find", "trova", "packages"],
                }},
                {"stage": 5, "success": True, "output": {
                    "code": "def invoke(args): return {'ok': True}",
                }},
            ],
        }

    def test_render_with_llm_produces_three_sections(self):
        from jobs import promoter_example
        prop = self._make_proposal()
        verdict = {
            "verdict": "accept", "score": 5.0,
            "signals": {"call_freq_60d": 30, "eta_speedup": 2.5},
            "killers_triggered": [],
        }
        with mock.patch("llm_helpers.call_llm") as m:
            m.return_value = (
                "La proposta find_packages riduce p50 e copre 30 chiamate "
                "negli ultimi 60 giorni. Nessun killer scattato. "
                "Rischio: monitorare in grace window.",
                {"tier": "middle"},
            )
            out = promoter_example.render_practical_example(
                prop, verdict, lang="it",
            )
        # Sezione 1 (deterministica).
        self.assertIn("**Query**", out)
        self.assertIn("**Pipeline corrente**", out)
        # Sezione 2 (perf savings).
        self.assertIn("## Stima del risparmio", out)
        # Sezione 3 (LLM marker + commento).
        self.assertIn(promoter_example._LLM_MARKER, out)
        self.assertIn("## Commento", out)
        self.assertIn("find_packages", out)

    def test_render_skip_llm_omits_commentary(self):
        from jobs import promoter_example
        prop = self._make_proposal()
        verdict = {
            "verdict": "accept", "score": 5.0,
            "signals": {"call_freq_60d": 30, "eta_speedup": 2.5},
            "killers_triggered": [],
        }
        out = promoter_example.render_practical_example(
            prop, verdict, lang="it", skip_llm=True,
        )
        self.assertNotIn(promoter_example._LLM_MARKER, out)
        self.assertNotIn("## Commento", out)
        # Le prime 2 sezioni ci sono ancora.
        self.assertIn("**Query**", out)
        self.assertIn("## Stima del risparmio", out)


# ─── 4+5. Frontmatter prompt IT + EN esistono ─────────────────────────────


class TestPromptFrontmatter(unittest.TestCase):

    def test_it_prompt_exists_with_frontmatter(self):
        p = _RUNTIME / "prompts" / "it" / "promoter_commentary.j2"
        self.assertTrue(p.exists(), f"manca {p}")
        body = p.read_text(encoding="utf-8")
        for key in ("role: promoter_commentary", "tier: creative",
                    "lang: it", "style: prescriptive"):
            self.assertIn(key, body, f"frontmatter chiave mancante: {key}")
        # Pattern §6 prescrittivo: DEVI / NON DEVI / OK / ERRORE.
        self.assertIn("DEVI:", body)
        self.assertIn("NON DEVI:", body)
        self.assertIn("OK:", body)
        self.assertIn("ERRORE:", body)

    def test_en_prompt_exists_with_frontmatter(self):
        p = _RUNTIME / "prompts" / "en" / "promoter_commentary.j2"
        self.assertTrue(p.exists(), f"manca {p}")
        body = p.read_text(encoding="utf-8")
        for key in ("role: promoter_commentary", "tier: creative",
                    "lang: en", "style: prescriptive"):
            self.assertIn(key, body, f"frontmatter chiave mancante: {key}")
        # Pattern EN: YOU MUST / YOU MUST NOT / OK / ERROR.
        self.assertIn("YOU MUST:", body)
        self.assertIn("YOU MUST NOT:", body)
        self.assertIn("OK:", body)
        self.assertIn("ERROR:", body)


if __name__ == "__main__":
    unittest.main()

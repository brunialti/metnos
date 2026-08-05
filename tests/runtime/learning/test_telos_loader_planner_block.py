"""Fase 4 wire-in TELOS.md → system prompt PLANNER (22/5/2026).

Test del rendering deterministico di `telos_loader.render_planner_block(lang)`:
- Render IT/EN con telos parsati dal default `workspace/TELOS.md`.
- Degrade graceful: stringa vuota se file mancante.
- Ordine per peso decrescente.
- Quartetto §6 DEVI/NON DEVI/OK/ERRORE presente nelle 2 lingue.

Run: `python3 -m pytest tests/runtime/learning/test_telos_loader_planner_block.py -v`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


_SAMPLE = """\
# TELOS

## t.alpha — fine alfa
peso: 0.40
soglia_attivazione: 0.20
note: alfa note.

## t.beta — fine beta
peso: 0.30
soglia_attivazione: 0.30
note: beta note.

## t.gamma — fine gamma
peso: 0.30
soglia_attivazione: 0.40
note: gamma note.
"""


class TelosPlannerBlockTests(unittest.TestCase):

    def _write_tmp(self, body: str) -> Path:
        d = Path(tempfile.mkdtemp(prefix="telos_test_"))
        p = d / "TELOS.md"
        p.write_text(body, encoding="utf-8")
        return p

    def setUp(self):
        # Reset cache per evitare bleed-through fra test (mtime-based hot reload).
        import telos_loader
        telos_loader._CACHE["telos"] = None
        telos_loader._CACHE["mtime"] = 0.0
        telos_loader._CACHE["version"] = ""

    def test_render_it_contains_telos_phrases_ordered_by_weight(self):
        import telos_loader
        p = self._write_tmp(_SAMPLE)
        out = telos_loader.render_planner_block(lang="it", path=p)
        self.assertIn("TELOS DELL'UTENTE", out)
        self.assertIn("t.alpha (peso 0.40): fine alfa", out)
        self.assertIn("t.beta (peso 0.30): fine beta", out)
        # Ordine: alpha (0.40) prima di beta (0.30) prima di gamma (0.30).
        self.assertLess(out.find("t.alpha"), out.find("t.beta"))
        self.assertLess(out.find("t.beta"), out.find("t.gamma"))

    def test_render_en_uses_english_rules(self):
        import telos_loader
        p = self._write_tmp(_SAMPLE)
        out = telos_loader.render_planner_block(lang="en", path=p)
        self.assertIn("USER TELOS", out)
        self.assertIn("MUST:", out)
        self.assertIn("MUST NOT:", out)
        self.assertNotIn("DEVI:", out)

    def test_unknown_lang_falls_back_to_english(self):
        import telos_loader
        p = self._write_tmp(_SAMPLE)
        out = telos_loader.render_planner_block(lang="fr", path=p)
        self.assertIn("USER TELOS", out)
        self.assertIn("MUST:", out)

    def test_empty_when_file_missing(self):
        import telos_loader
        out = telos_loader.render_planner_block(
            lang="it",
            path=Path("/tmp/nonexistent_telos_xyz_22-5-2026.md"),
        )
        self.assertEqual(out, "")

    def test_section_six_rules_present_in_it(self):
        import telos_loader
        p = self._write_tmp(_SAMPLE)
        out = telos_loader.render_planner_block(lang="it", path=p)
        # §6 obbligatorio CLAUDE.md: quartetto DEVI / NON DEVI / OK / ERRORE.
        self.assertIn("DEVI:", out)
        self.assertIn("NON DEVI:", out)
        self.assertIn("OK:", out)
        self.assertIn("ERRORE:", out)

    def test_compose_planner_includes_telos_block_end_to_end(self):
        """E2E: chiamata reale a prompt_loader.compose('planner', ...) con
        telos_block passato, verifica che il blocco TELOS sia presente nel
        rendering finale (protezione anti-regressione sul wire-in footer)."""
        import prompt_loader
        import telos_loader
        p = self._write_tmp(_SAMPLE)
        block = telos_loader.render_planner_block(lang="it", path=p)
        rendered = prompt_loader.compose(
            "planner", "it",
            sections=(),  # core only — minimo necessario
            vocab_actions="x", vocab_objects="x", vocab_qualifiers="x",
            project_paths="(test)", users_known="(test)",
            telos_block=block,
            tz="Europe/Rome", today_iso="2026-05-22",
            weekday_iso=5, now_hhmm="10:00",
        )
        self.assertIn("TELOS DELL'UTENTE", rendered)
        self.assertIn("t.alpha (peso 0.40): fine alfa", rendered)


if __name__ == "__main__":
    unittest.main()

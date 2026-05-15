"""Test del size del PLANNER targeted vs all-sections (asse A refactor, 12/5/2026).

Verifica che il refactor di split web/* + workspace/* riduca il prompt PLANNER
quando l'intent extractor identifica un object specifico (es. messages+events
per una query «appuntamenti + mail»), rispetto al fallback all-sections.

Verifica anche invarianti strutturali post-refactor:
- la lista sezioni include nested (web/*, workspace/*) con `/` separator;
- IT ed EN hanno lo stesso set di sezioni (simmetria ADR 0092);
- la composizione targeted < composizione all-sections.

Determinismo §7.9: zero LLM, zero network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


_NOW_VARS = dict(
    now_iso="2026-05-12T12:00:00+02:00",
    now_date="2026-05-12",
    now_year="2026",
    now_weekday="Tue",
    now_time="12:00",
)


class TestPlannerSizeTargeted(unittest.TestCase):
    """Asse A — riduzione size con sezioni mirate."""

    def test_planner_size_targeted_calendar_mail(self):
        """Compose con sections=["calendar","mail"] (typical mailbox+events).

        Soglia 40_000 char (~10K token): dopo il refactor il calendar.j2 e'
        ridotto (workspace/* separati), tutto il web/* non viene incluso.
        Il bulk del prompt resta nel _core.j2 (~20K) + variazioni vocab.
        """
        import prompt_loader
        out = prompt_loader.compose(
            "planner", "it",
            sections=["calendar", "mail"],
            vocab_actions="read,write",
            vocab_objects="events,messages",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        self.assertLess(
            len(out), 40_000,
            f"prompt PLANNER targeted too large: {len(out)} char "
            "(soglia 40_000 = ~10K token).",
        )

    def test_planner_targeted_less_than_all_sections(self):
        """Invariante: targeted < all-sections (la sezione web/* + workspace/*
        non incluse pesano significativamente)."""
        import prompt_loader
        out_targeted = prompt_loader.compose(
            "planner", "it",
            sections=["calendar", "mail"],
            vocab_actions="read,write",
            vocab_objects="events,messages",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        out_all = prompt_loader.compose(
            "planner", "it",
            sections=None,
            vocab_actions="read,write",
            vocab_objects="events,messages",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        self.assertLess(
            len(out_targeted), len(out_all),
            f"targeted ({len(out_targeted)}) >= all-sections ({len(out_all)})",
        )

    def test_list_planner_sections_symmetric_it_en(self):
        """Invariante ADR 0092: IT ed EN hanno lo stesso set di sezioni."""
        import prompt_loader
        secs_it = prompt_loader.list_planner_sections("it")
        secs_en = prompt_loader.list_planner_sections("en")
        self.assertEqual(secs_it, secs_en,
                          "IT/EN sections must be symmetric")

    def test_list_planner_sections_includes_nested(self):
        """Verifica che il walk ricorsivo includa nested con `/` separator."""
        import prompt_loader
        secs = prompt_loader.list_planner_sections("it")
        self.assertIn("web/search", secs)
        self.assertIn("web/crawl", secs)
        self.assertIn("web/content", secs)
        self.assertIn("workspace/drive", secs)
        self.assertIn("workspace/sheets", secs)
        self.assertIn("workspace/docs", secs)
        self.assertIn("workspace/contacts", secs)
        # Le sezioni flat top-level continuano a esistere:
        self.assertIn("calendar", secs)
        self.assertIn("mail", secs)
        self.assertIn("photos", secs)
        self.assertIn("system", secs)
        self.assertIn("admin_shell", secs)
        # Niente più "web" come top-level (sostituito da web/*):
        self.assertNotIn("web", secs)

    def test_compose_works_with_nested_sections(self):
        """compose() risolve `sections=["web/search"]` come
        `planner/sections/web/search.j2`."""
        import prompt_loader
        out = prompt_loader.compose(
            "planner", "it",
            sections=["web/search"],
            vocab_actions="find",
            vocab_objects="urls",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        # Deve contenere marker della sezione search (find_urls).
        self.assertIn("find_urls", out)
        # Non deve contenere marker di sezioni NON richieste.
        self.assertNotIn("read_events", out)


if __name__ == "__main__":
    unittest.main()

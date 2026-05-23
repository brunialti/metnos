"""Tests per il count corretto e il walk-back del producer in
`auto_final_on_duplicate` (UX HIGH item I, 6/5/2026).

Bug osservato 5/5: turn `9d6c71ac` mostrava
"describe_entries: completato (? elementi)" perche' `describe_entries`
era considerato `last_productive`, non risaliva al producer
upstream (`find_images_indices`) che aveva il count reale.

Fix: `describe_entries` aggiunto a `_AUTO_FINAL_SKIP_TOOLS`; aggiunti
fallback `n_entries`/`item_count` per `ok_count`; aggiunto
`final_message_hint` come fonte di detail; aggiunto suffisso
"su N sopra soglia" se `n_above_threshold > ok_count`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

# Path setup: i test girano da <install_root>, runtime/ e' importabile direttamente
_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def _step(tool: str, result: dict) -> SimpleNamespace:
    return SimpleNamespace(chosen_tool=tool, result=result)


class TestResolveAutoFinalFromSteps(unittest.TestCase):
    def test_skips_describe_entries_back_to_producer(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_images_indices", {
                "ok": True,
                "entries": [{"path": "/a.jpg"}, {"path": "/b.jpg"}, {"path": "/c.jpg"}],
                "n_entries": 3,
                "n_above_threshold": 17,
                "final_message_hint": "Top 3 risultati su 17 sopra soglia.",
            }),
            _step("describe_entries", {
                "ok": True, "summary": "tre foto generiche", "item_count": 3,
            }),
        ]
        lp_tool, lp_obs = _resolve_auto_final_from_steps(steps)
        self.assertEqual(lp_tool, "find_images_indices")
        self.assertEqual(lp_obs.get("n_entries"), 3)

    def test_skips_data_piping_helpers(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_files", {"ok": True, "entries": [{"path": "/x"}]}),
            _step("filter_entries", {"ok": True, "entries": [{"path": "/x"}]}),
            _step("classify_entries", {"ok": True, "entries": [{"path": "/x"}]}),
            _step("describe_entries", {"ok": True, "summary": "uno"}),
        ]
        lp_tool, _ = _resolve_auto_final_from_steps(steps)
        self.assertEqual(lp_tool, "find_files")

    def test_falls_back_to_last_step_when_no_producer(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("describe_entries", {"ok": True, "summary": "x"}),
        ]
        lp_tool, lp_obs = _resolve_auto_final_from_steps(steps)
        # Fallback: anche se in _SKIP, se e' l'unico step ok lo restituisce
        self.assertEqual(lp_tool, "describe_entries")
        self.assertEqual(lp_obs.get("summary"), "x")

    def test_skips_failed_steps(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_files", {"ok": True, "entries": [{"path": "/y"}]}),
            _step("read_files", {"ok": False, "error": "denied"}),
        ]
        lp_tool, _ = _resolve_auto_final_from_steps(steps)
        self.assertEqual(lp_tool, "find_files")

    def test_empty_steps(self):
        from agent_runtime import _resolve_auto_final_from_steps
        lp_tool, lp_obs = _resolve_auto_final_from_steps([])
        self.assertIsNone(lp_tool)
        self.assertEqual(lp_obs, {})


class TestExtractAutoFinalCount(unittest.TestCase):
    def test_explicit_ok_count(self):
        from agent_runtime import _extract_auto_final_count
        c, nat = _extract_auto_final_count({"ok_count": 5})
        self.assertEqual(c, 5)
        self.assertIsNone(nat)

    def test_entries_list_fallback(self):
        from agent_runtime import _extract_auto_final_count
        c, nat = _extract_auto_final_count({"entries": [1, 2, 3]})
        self.assertEqual(c, 3)
        self.assertIsNone(nat)

    def test_n_entries_field(self):
        from agent_runtime import _extract_auto_final_count
        # Niente entries-list, ma n_entries presente
        c, nat = _extract_auto_final_count({"n_entries": 7})
        self.assertEqual(c, 7)

    def test_item_count_from_describe_entries(self):
        from agent_runtime import _extract_auto_final_count
        c, _ = _extract_auto_final_count({"item_count": 4, "summary": "..."})
        self.assertEqual(c, 4)

    def test_n_above_threshold_when_greater(self):
        from agent_runtime import _extract_auto_final_count
        c, nat = _extract_auto_final_count({
            "entries": [1, 2, 3], "n_above_threshold": 17,
        })
        self.assertEqual(c, 3)
        self.assertEqual(nat, 17)

    def test_n_above_threshold_ignored_when_le_count(self):
        from agent_runtime import _extract_auto_final_count
        # Quando n_above_threshold == ok_count il suffisso e' rumore
        c, nat = _extract_auto_final_count({
            "entries": [1, 2, 3], "n_above_threshold": 3,
        })
        self.assertEqual(c, 3)
        self.assertIsNone(nat)

    def test_no_count_at_all(self):
        from agent_runtime import _extract_auto_final_count
        c, nat = _extract_auto_final_count({"summary": "..."})
        self.assertIsNone(c)
        self.assertIsNone(nat)


class TestFormatAutoFinalCount(unittest.TestCase):
    def test_basic_count(self):
        from agent_runtime import _format_auto_final_count
        self.assertEqual(_format_auto_final_count(3, None), "3 elementi")

    def test_with_threshold(self):
        from agent_runtime import _format_auto_final_count
        self.assertEqual(
            _format_auto_final_count(3, 17),
            "3 elementi su 17 sopra soglia",
        )

    def test_unknown_count(self):
        from agent_runtime import _format_auto_final_count
        self.assertEqual(_format_auto_final_count(None, None), "? elementi")

    def test_threshold_ignored_without_count(self):
        from agent_runtime import _format_auto_final_count
        self.assertEqual(_format_auto_final_count(None, 17), "? elementi")


class TestEndToEndScenario(unittest.TestCase):
    """Scenario coerente con il bug 9d6c71ac: find_images_indices →
    describe_entries → describe_entries (duplicato) deve risolvere a
    'find_images_indices: completato (3 elementi su 17 sopra soglia).'.
    """

    def test_bug_9d6c71ac_scenario(self):
        from agent_runtime import (
            _resolve_auto_final_from_steps,
            _extract_auto_final_count,
            _format_auto_final_count,
        )
        steps = [
            _step("find_images_indices", {
                "ok": True,
                "entries": [{"path": f"/img{i}.jpg"} for i in range(3)],
                "n_entries": 3,
                "n_above_threshold": 17,
                "final_message_hint": (
                    "Top 3 risultati ordinati per similarita' "
                    "(similarity 0.118-0.141). Sopra soglia 0.10 ce ne "
                    "sono 17 in tutto."
                ),
            }),
            _step("describe_entries", {
                "ok": True,
                "summary": "Tre foto di compleanno con torta e candele.",
                "item_count": 3,
            }),
        ]
        lp_tool, lp_obs = _resolve_auto_final_from_steps(steps)
        ok_count, nat = _extract_auto_final_count(lp_obs)
        count_str = _format_auto_final_count(ok_count, nat)

        # 1. tool name = producer, non describe_entries
        self.assertEqual(lp_tool, "find_images_indices")
        # 2. count = numero di entries del producer
        self.assertEqual(ok_count, 3)
        # 3. n_above_threshold preservato
        self.assertEqual(nat, 17)
        # 4. format finale corretto
        self.assertEqual(count_str, "3 elementi su 17 sopra soglia")
        # 5. final_message_hint disponibile su lp_obs (consumer-friendly)
        self.assertIn("Top 3 risultati", lp_obs.get("final_message_hint", ""))


class TestMessageOnlyVerbUnique(unittest.TestCase):
    """Bug live 7/5/2026 11:13: 'Cancella task monitoraggio sito ics' →
    'delete_tasks_scheduled: completato (? elementi). Esito gia' nei
    risultati precedenti.' — count irrilevante per verb-unique
    'singola azione'. Quando ok_count e' ignoto E lp_obs ha un
    'message' descrittivo, usare il message verbatim, senza wrapper
    '(? elementi)'.
    """

    def test_message_only_no_count_uses_message_verbatim(self):
        # Costruzione minimale di in-memory state simil run_turn flow
        from agent_runtime import (
            _resolve_auto_final_from_steps,
            _extract_auto_final_count,
        )
        steps = [
            _step("delete_tasks_scheduled", {
                "ok": True,
                "message": "Task 'monitoraggio sito ics' cancellato.",
            }),
        ]
        lp_tool, lp_obs = _resolve_auto_final_from_steps(steps)
        ok_count, _ = _extract_auto_final_count(lp_obs)
        self.assertEqual(lp_tool, "delete_tasks_scheduled")
        self.assertIsNone(ok_count)
        self.assertEqual(
            lp_obs.get("message"),
            "Task 'monitoraggio sito ics' cancellato.",
        )


class TestPreferReadOverDiscovery(unittest.TestCase):
    """Bug live federvolley 7/5/2026 14:25: PLANNER lancia find_urls 3x +
    read_urls_html 1x. cap_same → auto_final usa find_urls (last_productive)
    come fonte → final_message contiene URL+titoli, non dati.

    Fix (ADR 0098 §c3): se last_productive e' discovery (find_urls) e
    in history c'e' un read_urls_* con contenuto sostanziale, preferisci
    il read come fonte.
    """

    def test_prefers_read_when_discovery_is_last(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_urls", {"ok": True, "entries": [{"url": "/a"}]}),
            _step("read_urls_html", {
                "ok": True,
                "entries": [{
                    "url": "/calendario",
                    "text": "Girone F giornata 22: " + ("X" * 250),
                }],
            }),
            _step("find_urls", {"ok": True, "entries": [{"url": "/b"}]}),
            _step("find_urls", {"ok": True, "entries": [{"url": "/c"}]}),
        ]
        lp_tool, lp_obs = _resolve_auto_final_from_steps(steps)
        # find_urls last; read_urls_html con text sostanziale prima → preferisci read
        self.assertEqual(lp_tool, "read_urls_html")
        self.assertIn("Girone F", lp_obs["entries"][0]["text"])

    def test_keeps_discovery_when_read_has_no_content(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_urls", {"ok": True, "entries": [{"url": "/a"}]}),
            _step("read_urls_html", {
                "ok": True,
                "entries": [{"url": "/", "text": "x"}],  # text troppo corto
            }),
            _step("find_urls", {"ok": True, "entries": [{"url": "/b"}]}),
        ]
        lp_tool, _ = _resolve_auto_final_from_steps(steps)
        # read non ha contenuto sostanziale → resta find_urls
        self.assertEqual(lp_tool, "find_urls")

    def test_uses_summary_field_for_content_detection(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_urls", {"ok": True, "entries": [{"url": "/a"}]}),
            _step("read_urls_pdf", {
                "ok": True,
                "summary": "Calendario completo girone F. Giornata 22: " + ("..." * 50),
            }),
            _step("find_urls", {"ok": True, "entries": [{"url": "/b"}]}),
        ]
        lp_tool, _ = _resolve_auto_final_from_steps(steps)
        self.assertEqual(lp_tool, "read_urls_pdf")

    def test_no_read_in_history_keeps_discovery(self):
        from agent_runtime import _resolve_auto_final_from_steps
        steps = [
            _step("find_urls", {"ok": True, "entries": [{"url": "/a"}]}),
            _step("find_urls", {"ok": True, "entries": [{"url": "/b"}]}),
        ]
        lp_tool, _ = _resolve_auto_final_from_steps(steps)
        self.assertEqual(lp_tool, "find_urls")


if __name__ == "__main__":
    unittest.main()

"""Test del cap deterministico su describe_entries (PR mini 8/5/2026, Patch 3).

Cap interno _DESCRIBE_CAP=20: oltre 20 entries il prompt LLM riceve solo
le prime 20, l'output dichiara `truncated=True` + cap_field/cap_value e
il summary include la nota MSG_DESCRIBE_TRUNCATED.

Mock di `call_llm` (no ollama richiesto) — restano deterministici §7.9.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestDescribeEntriesCap(unittest.TestCase):

    def setUp(self):
        # Mock `call_llm`: riceve la lista entries effettivamente passata
        # al prompt. La salviamo per verifica + ritorniamo un summary
        # fissato senza chiamate di rete.
        self._captured_entries = []

        def _fake_call_llm(entries, prompt, *, tier="middle", max_tokens=600):
            self._captured_entries.append(entries)
            return ("Riassunto sintetico.",
                    {"in_tokens": 0, "out_tokens": 0, "latency_ms": 1})

        self._patch = mock.patch("describe_entries.call_llm",
                                 side_effect=_fake_call_llm)
        self._patch.start()
        # Mock prompt_loader.get per evitare dipendenza da .j2 file e
        # rendere il test idratante (KeyError su `n`/context/kind se non
        # mockato sarebbe un falso negativo del cap).
        self._patch_pl = mock.patch(
            "describe_entries.prompt_loader.get",
            return_value="STUB PROMPT",
        )
        self._patch_pl.start()

    def tearDown(self):
        self._patch.stop()
        self._patch_pl.stop()

    def _make_entries(self, n: int) -> list[dict]:
        return [{"path": f"/tmp/file_{i}.txt", "size": i * 100,
                 "kind": "file"} for i in range(n)]

    def test_describe_under_cap_no_truncation(self):
        import describe_entries as de
        entries = self._make_entries(5)
        out = de.handle_describe_entries({
            "entries": entries,
            "style": "by_importance",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["item_count"], 5)
        self.assertNotIn("truncated", out)
        # Il LLM ha visto tutte le 5 entries
        self.assertEqual(len(self._captured_entries[0]), 5)

    def test_describe_at_exact_cap_no_truncation(self):
        import describe_entries as de
        entries = self._make_entries(20)
        out = de.handle_describe_entries({
            "entries": entries,
            "style": "by_importance",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["item_count"], 20)
        self.assertNotIn("truncated", out)
        self.assertEqual(len(self._captured_entries[0]), 20)

    def test_describe_over_cap_truncates(self):
        import describe_entries as de
        entries = self._make_entries(25)
        out = de.handle_describe_entries({
            "entries": entries,
            "style": "by_importance",
        })
        self.assertTrue(out["ok"], out)
        # item_count riflette il TOTALE, non il cap
        self.assertEqual(out["item_count"], 25)
        # Pattern §2.7
        self.assertTrue(out["truncated"])
        self.assertEqual(out["truncated_what"], "describe")
        self.assertEqual(out["used"], 20)
        self.assertEqual(out["available_total"], 25)
        self.assertEqual(out["cap_field"], "describe_cap")
        self.assertEqual(out["cap_value"], 20)
        # LLM ha visto solo 20
        self.assertEqual(len(self._captured_entries[0]), 20)
        # Summary include la nota MSG_DESCRIBE_TRUNCATED
        self.assertIn("20", out["summary"])
        # 25 - 20 = 5 hidden
        self.assertIn("5", out["summary"])

    def test_describe_far_over_cap_only_first_20_to_llm(self):
        import describe_entries as de
        entries = self._make_entries(100)
        out = de.handle_describe_entries({
            "entries": entries,
            "style": "by_importance",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["item_count"], 100)
        self.assertEqual(out["used"], 20)
        self.assertEqual(out["available_total"], 100)
        self.assertEqual(len(self._captured_entries[0]), 20)
        # Le prime 20 sono ESATTAMENTE entries[:20] (deterministico)
        self.assertEqual(self._captured_entries[0][0]["path"],
                         "/tmp/file_0.txt")
        self.assertEqual(self._captured_entries[0][-1]["path"],
                         "/tmp/file_19.txt")


if __name__ == "__main__":
    unittest.main()

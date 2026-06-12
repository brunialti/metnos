"""Test del cap DINAMICO su describe_entries (12/6/2026, sostituisce il
fisso _DESCRIBE_CAP=20).

Il cap verso il prompt LLM e' a DIMENSIONE del bundle (budget caratteri),
non a conteggio: sotto budget passano TUTTE le entries (anche 100 mail
corte), si tronca solo quando la dimensione serializzata supera il budget
o si sfora il tetto di sicurezza sul conteggio. Razionale §7.3: un cap a
conteggio fisso tagliava per un solo elemento di troppo (es. 21 mail ->
"1 fuori"), rischiando di perdere roba importante per nulla.

Mock di `call_llm` (no llama-server richiesto) — restano deterministici §7.9.
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
        # Mock prompt_loader.get per evitare dipendenza da .j2 file.
        self._patch_pl = mock.patch(
            "describe_entries.prompt_loader.get",
            return_value="STUB PROMPT",
        )
        self._patch_pl.start()

    def tearDown(self):
        self._patch.stop()
        self._patch_pl.stop()

    def _small_entries(self, n: int) -> list[dict]:
        # ~50 char serializzati ciascuna: 100 di queste = ~5 KB, ben sotto
        # il budget default 24 KB.
        return [{"path": f"/tmp/file_{i}.txt", "size": i * 100,
                 "kind": "file"} for i in range(n)]

    def _big_entries(self, n: int, chars: int = 3000) -> list[dict]:
        return [{"text": "x" * chars, "id": i, "kind": "doc"}
                for i in range(n)]

    def test_under_budget_no_truncation(self):
        import describe_entries as de
        out = de.handle_describe_entries({
            "entries": self._small_entries(5), "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["item_count"], 5)
        self.assertNotIn("truncated", out)
        self.assertEqual(len(self._captured_entries[0]), 5)

    def test_many_small_entries_all_pass(self):
        """Il caso che il cap fisso=20 sbagliava: 21 (e 100) mail corte
        stanno larghe nel budget -> NESSUN troncamento, niente "1 fuori"."""
        import describe_entries as de
        for n in (21, 100):
            with self.subTest(n=n):
                self._captured_entries.clear()
                out = de.handle_describe_entries({
                    "entries": self._small_entries(n),
                    "style": "by_importance"})
                self.assertTrue(out["ok"], out)
                self.assertEqual(out["item_count"], n)
                self.assertNotIn("truncated", out)
                self.assertEqual(len(self._captured_entries[0]), n)

    def test_byte_budget_truncates(self):
        import describe_entries as de
        # 10 entries da ~3 KB = ~30 KB > budget 24 KB default.
        out = de.handle_describe_entries({
            "entries": self._big_entries(10), "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["item_count"], 10)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["truncated_what"], "describe")
        self.assertEqual(out["available_total"], 10)
        self.assertEqual(out["cap_field"], "describe_cap")
        used = out["used"]
        # cap dinamico: alcune passano, non tutte, e cap_value == used.
        self.assertTrue(0 < used < 10)
        self.assertEqual(out["cap_value"], used)
        self.assertEqual(len(self._captured_entries[0]), used)
        # Nota di troncamento con i numeri reali (visible + hidden).
        self.assertIn(str(used), out["summary"])
        self.assertIn(str(10 - used), out["summary"])

    def test_hard_max_count_ceiling(self):
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_HARD_MAX", 50):
            out = de.handle_describe_entries({
                "entries": self._small_entries(80), "style": "by_importance"})
        self.assertTrue(out["truncated"])
        self.assertEqual(out["used"], 50)
        self.assertEqual(out["available_total"], 80)
        self.assertEqual(len(self._captured_entries[0]), 50)

    def test_truncation_is_prefix_deterministic(self):
        import describe_entries as de
        out = de.handle_describe_entries({
            "entries": self._big_entries(10), "style": "by_importance"})
        used = out["used"]
        sent = self._captured_entries[0]
        # Le entries inviate sono ESATTAMENTE entries[:used], in ordine.
        self.assertEqual([e["id"] for e in sent], list(range(used)))

    def test_single_oversized_entry_always_passes(self):
        """Almeno 1 entry passa sempre, anche se da sola sfora il budget."""
        import describe_entries as de
        out = de.handle_describe_entries({
            "entries": self._big_entries(1, chars=50_000),
            "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(self._captured_entries[0]), 1)
        self.assertNotIn("truncated", out)

    def test_env_override_budget(self):
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 7000):
            out = de.handle_describe_entries({
                "entries": self._big_entries(10), "style": "by_importance"})
        # budget 7 KB / ~3 KB per entry -> 2 entries.
        self.assertEqual(out["used"], 2)
        self.assertTrue(out["truncated"])


if __name__ == "__main__":
    unittest.main()

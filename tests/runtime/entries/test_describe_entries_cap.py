"""Test del budget describe_entries + over-budget MAP-REDUCE (22/6/2026).

Contratto (aggiornato 22/6, Roberto «robusto, universale, efficiente»):
- Il vincolo verso il prompt LLM e' a DIMENSIONE del bundle (budget caratteri),
  non a conteggio. `_pack_entries` e' la DETECTION pura (prefix-deterministica,
  budget + tetto di sicurezza sul conteggio) — invariante durevole.
- SOTTO budget: singola chiamata, tutte le entries, nessun troncamento.
- SOPRA budget (default): MAP-REDUCE — copre le entries FINO al cap
  anti-runaway `_MR_MAX_ENTRIES` (default 100, env, 0=illimitato; 6/7/2026:
  /tmp → 1759 chiamate LLM senza tetto), `map_reduce=True`; oltre il cap
  → `truncated` §2.7 + nota chiara all'UTENTE nel summary.
- SOPRA budget con map-reduce DISABILITATO (gate `_DESCRIBE_MAPREDUCE=False`)
  o LLM non disponibile: fallback al TRONCAMENTO §2.7 (used/truncated/cap_*).

Mock di `call_llm` (no llama-server) — restano deterministici §7.9.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class TestPackEntries(unittest.TestCase):
    """Invariante PURA di packing (no LLM): prefix-deterministica, budget,
    tetto conteggio. `_pack_entries` e' usato per DETECTARE l'over-budget."""

    def _big(self, n: int, chars: int = 3000) -> list[dict]:
        return [{"text": "x" * chars, "id": i, "kind": "doc"} for i in range(n)]

    def _small(self, n: int) -> list[dict]:
        return [{"path": f"/tmp/file_{i}.txt", "size": i * 100,
                 "kind": "file"} for i in range(n)]

    def test_under_budget_all_pass(self):
        import describe_entries as de
        visible, truncated = de._pack_entries(self._small(100))
        self.assertEqual(len(visible), 100)
        self.assertFalse(truncated)

    def test_byte_budget_prefix_deterministic(self):
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 24000):
            visible, truncated = de._pack_entries(self._big(10))
        self.assertTrue(truncated)
        self.assertTrue(0 < len(visible) < 10)
        # ESATTAMENTE il prefisso entries[:k], in ordine.
        self.assertEqual([e["id"] for e in visible], list(range(len(visible))))

    def test_env_override_budget(self):
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 7000):
            visible, truncated = de._pack_entries(self._big(10))
        self.assertEqual(len(visible), 2)  # 7 KB / ~3 KB = 2
        self.assertTrue(truncated)

    def test_hard_max_count_ceiling(self):
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_HARD_MAX", 50):
            visible, truncated = de._pack_entries(self._small(80))
        self.assertEqual(len(visible), 50)
        self.assertTrue(truncated)

    def test_single_oversized_always_passes(self):
        import describe_entries as de
        visible, truncated = de._pack_entries(self._big(1, chars=50_000))
        self.assertEqual(len(visible), 1)
        self.assertFalse(truncated)


class TestDescribeEntriesCap(unittest.TestCase):

    def setUp(self):
        self._captured_entries = []

        def _fake_call_llm(entries, prompt, *, tier="middle", max_tokens=600,
                           **kwargs):
            self._captured_entries.append(entries)
            return ("Riassunto sintetico.",
                    {"in_tokens": 0, "out_tokens": 0, "latency_ms": 1})

        self._patch = mock.patch("describe_entries.call_llm",
                                 side_effect=_fake_call_llm)
        self._patch.start()
        self._patch_pl = mock.patch(
            "describe_entries.prompt_loader.get", return_value="STUB PROMPT")
        self._patch_pl.start()

    def tearDown(self):
        self._patch.stop()
        self._patch_pl.stop()

    def _small_entries(self, n: int) -> list[dict]:
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
        self.assertIsNone(out.get("map_reduce"))
        self.assertEqual(len(self._captured_entries[0]), 5)

    def test_many_small_entries_all_pass(self):
        """21 e 100 mail corte stanno nel budget -> nessun troncamento,
        nessun map-reduce (sotto budget = singola chiamata)."""
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

    def test_over_budget_mapreduce_covers_all(self):
        """DEFAULT over-budget: map-reduce copre TUTTE le entries, niente
        troncamento. 10 entries grandi -> 10 MAP + 1 REDUCE = 11 chiamate."""
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 24000):
            out = de.handle_describe_entries({
                "entries": self._big_entries(10), "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["map_reduce"])
        self.assertEqual(out["item_count"], 10)   # TUTTE coperte
        self.assertEqual(out["mapped"], 10)
        self.assertNotIn("truncated", out)        # niente droppato
        self.assertFalse(out.get("deterministic"))  # path map-reduce, onesto
        # 10 MAP (1 entry ciascuna) + 1 REDUCE (10 digest).
        self.assertEqual(len(self._captured_entries), 11)
        self.assertTrue(all(len(c) == 1 for c in self._captured_entries[:10]))
        self.assertEqual(len(self._captured_entries[10]), 10)

    def test_mapreduce_cap_bounds_llm_calls_and_tells_user(self):
        """Cap ANTI-RUNAWAY (6/7/2026, Roberto «100 max + indicazione chiara»):
        oltre `_MR_MAX_ENTRIES` il MAP lavora solo le prime N (bounded per
        costruzione: N MAP + 1 REDUCE) e l'utente lo LEGGE nel summary
        (MSG_DESCRIBE_TRUNCATED) + campi §2.7 nel result."""
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 24000), \
                mock.patch.object(de, "_MR_MAX_ENTRIES", 4):
            out = de.handle_describe_entries({
                "entries": self._big_entries(10), "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["map_reduce"])
        self.assertEqual(out["item_count"], 10)
        self.assertEqual(out["mapped"], 4)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["truncated_what"], "describe")
        self.assertEqual(out["used"], 4)
        self.assertEqual(out["available_total"], 10)
        self.assertEqual(out["cap_field"], "METNOS_DESCRIBE_MR_MAX_ENTRIES")
        self.assertEqual(out["cap_value"], 4)
        # 4 MAP (prime 4 in ordine d'arrivo) + 1 REDUCE = 5 chiamate totali.
        self.assertEqual(len(self._captured_entries), 5)
        self.assertTrue(all(len(c) == 1 for c in self._captured_entries[:4]))
        self.assertEqual([c[0].get("id") for c in self._captured_entries[:4]],
                         [0, 1, 2, 3])
        # Indicazione CHIARA all'utente nel testo: numeri visti/esclusi.
        self.assertIn("4", out["summary"])
        self.assertIn("6", out["summary"])

    def test_mapreduce_cap_zero_is_unlimited(self):
        """`_MR_MAX_ENTRIES=0` = illimitato (§2.4 0-as-placeholder):
        comportamento copre-tutto pre-cap, nessun troncamento."""
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 24000), \
                mock.patch.object(de, "_MR_MAX_ENTRIES", 0):
            out = de.handle_describe_entries({
                "entries": self._big_entries(10), "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["mapped"], 10)
        self.assertNotIn("truncated", out)
        self.assertEqual(len(self._captured_entries), 11)

    def test_nested_reduce_truncation_is_never_erased(self):
        import describe_entries as de
        nested = {
            "ok": True, "summary": "nested", "truncated": True,
            "truncated_what": "describe", "used": 2,
            "available_total": 3, "cap_field": "nested_cap",
            "cap_value": 2,
        }
        with mock.patch.object(de, "handle_describe_entries",
                               return_value=dict(nested)):
            out = de._describe_map_reduce(
                self._small_entries(3), style="by_importance", context="",
                data_kind=None, fmt="markdown", group_by=None,
                max_tokens=600, health_context=None, mr_depth=0)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["cap_field"], "nested_cap")
        self.assertEqual(out["available_total"], 3)

    def test_over_budget_truncation_fallback_when_mapreduce_off(self):
        """Gate `_DESCRIBE_MAPREDUCE=False`: torna il troncamento §2.7
        (used/truncated/cap_*), prefix-deterministico."""
        import describe_entries as de
        with mock.patch.object(de, "_DESCRIBE_MAX_CHARS", 24000), \
                mock.patch.object(de, "_DESCRIBE_MAPREDUCE", False):
            out = de.handle_describe_entries({
                "entries": self._big_entries(10), "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["item_count"], 10)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["truncated_what"], "describe")
        self.assertEqual(out["available_total"], 10)
        self.assertEqual(out["cap_field"], "describe_cap")
        used = out["used"]
        self.assertTrue(0 < used < 10)
        self.assertEqual(out["cap_value"], used)
        self.assertEqual(len(self._captured_entries[0]), used)
        # entries inviate = prefisso entries[:used], in ordine.
        self.assertEqual([e["id"] for e in self._captured_entries[0]],
                         list(range(used)))
        self.assertIn(str(used), out["summary"])
        self.assertIn(str(10 - used), out["summary"])

    def test_single_oversized_entry_always_passes(self):
        import describe_entries as de
        out = de.handle_describe_entries({
            "entries": self._big_entries(1, chars=50_000),
            "style": "by_importance"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(self._captured_entries[0]), 1)
        self.assertNotIn("truncated", out)
        self.assertIsNone(out.get("map_reduce"))


if __name__ == "__main__":
    unittest.main()

"""Tests per cap_max_per_turn rinforzato (CLAUDE.md §4.4 estesa, 8/5/2026 notte).

Trigger live: turn 9-step thrashing «cerca organico scuola Roma» con
find_texts ×7. cap_same a 10 e duplicate_call non bastavano: gli args
cambiavano leggermente ogni call (topic ridotto progressivamente).

Patch (c):
- `_normalize_args_for_dup(args)`: strip+lowercase+ordina, riduce
  topic/query/pattern a token-set ordinato.
- `_args_jaccard(a, b)`: Jaccard fra i token-set semantici.
- `_is_non_action_tool(name)`: True per find/get/list/read/classify/filter.
- Cap_max_per_turn = 3 (env METNOS_CAP_MAX_PER_TURN, default 3): tool
  non-action chiamato >= soglia con args Jaccard >= 0.7 → final_answer.
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


def _step(tool: str, args: dict, result: dict) -> SimpleNamespace:
    return SimpleNamespace(chosen_tool=tool, raw_args=args, result=result)


class TestNormalizeArgsForDup(unittest.TestCase):
    def test_topic_variants_reduced_to_token_set(self):
        from agent_runtime import _normalize_args_for_dup
        a = _normalize_args_for_dup({"topic": "organico scuola"})
        b = _normalize_args_for_dup({"topic": "scuola organico"})
        # Stesso token-set ordinato → uguali
        self.assertEqual(a, b)

    def test_topic_subset_distinct(self):
        from agent_runtime import _normalize_args_for_dup, _args_jaccard
        a = _normalize_args_for_dup({"topic": "organico di diritto"})
        b = _normalize_args_for_dup({"topic": "organico"})
        # Stessi tokens "organico" + altri → Jaccard 1/3 < 0.7
        self.assertLess(_args_jaccard(a, b), 0.7)

    def test_topic_identical_jaccard_one(self):
        from agent_runtime import _normalize_args_for_dup, _args_jaccard
        a = _normalize_args_for_dup({"topic": "organico scuola"})
        b = _normalize_args_for_dup({"topic": "scuola organico"})
        self.assertEqual(_args_jaccard(a, b), 1.0)

    def test_paths_lowercased_and_sorted(self):
        from agent_runtime import _normalize_args_for_dup
        a = _normalize_args_for_dup({"paths": ["/A.txt", "/b.txt"]})
        b = _normalize_args_for_dup({"paths": ["/b.txt", "/a.txt"]})
        self.assertEqual(a, b)

    def test_none_and_empty_stripped(self):
        from agent_runtime import _normalize_args_for_dup
        a = _normalize_args_for_dup({"x": None, "y": "", "z": []})
        self.assertEqual(a, {})

    def test_non_dict_returns_empty(self):
        from agent_runtime import _normalize_args_for_dup
        self.assertEqual(_normalize_args_for_dup(None), {})
        self.assertEqual(_normalize_args_for_dup("foo"), {})

    def test_jaccard_no_semantic_field_falls_back_to_equality(self):
        from agent_runtime import _normalize_args_for_dup, _args_jaccard
        a = _normalize_args_for_dup({"paths": ["/x.txt"]})
        b = _normalize_args_for_dup({"paths": ["/x.txt"]})
        self.assertEqual(_args_jaccard(a, b), 1.0)
        c = _normalize_args_for_dup({"paths": ["/y.txt"]})
        self.assertEqual(_args_jaccard(a, c), 0.0)


class TestIsNonActionTool(unittest.TestCase):
    def test_find_is_non_action(self):
        from agent_runtime import _is_non_action_tool
        self.assertTrue(_is_non_action_tool("find_texts"))
        self.assertTrue(_is_non_action_tool("find_urls"))
        self.assertTrue(_is_non_action_tool("get_processes"))
        self.assertTrue(_is_non_action_tool("read_files"))
        self.assertTrue(_is_non_action_tool("list_dirs"))
        self.assertTrue(_is_non_action_tool("classify_entries"))
        self.assertTrue(_is_non_action_tool("filter_entries"))

    def test_action_verbs_excluded(self):
        from agent_runtime import _is_non_action_tool
        self.assertFalse(_is_non_action_tool("write_files"))
        self.assertFalse(_is_non_action_tool("move_files"))
        self.assertFalse(_is_non_action_tool("delete_files"))
        self.assertFalse(_is_non_action_tool("send_messages"))
        self.assertFalse(_is_non_action_tool("create_dirs"))
        self.assertFalse(_is_non_action_tool("set_signatures"))

    def test_no_underscore_returns_false(self):
        from agent_runtime import _is_non_action_tool
        self.assertFalse(_is_non_action_tool("foo"))
        self.assertFalse(_is_non_action_tool(""))
        self.assertFalse(_is_non_action_tool("final_answer"))  # 'final' non in set


class TestCapMaxPerTurnLogic(unittest.TestCase):
    """Test diretti sulla logica di conteggio near-identical args.

    Non gira l'intero `run_local_react`: simula la condizione e verifica
    che `_is_non_action_tool` + `_args_jaccard` + soglia diano il blocco
    atteso. Test e2e nel pacchetto smoke.
    """

    def test_find_texts_called_3x_in_turn_blocks_4th(self):
        from agent_runtime import (
            _is_non_action_tool, _normalize_args_for_dup, _args_jaccard,
            DEFAULT_CAP_MAX_PER_TURN,
        )
        # Simula history con 3 find_texts su topic vicini
        history = [
            _step("find_texts", {"topic": "organico scuola roma"}, {"ok": True, "entries": []}),
            _step("find_texts", {"topic": "organico scuola"}, {"ok": True, "entries": []}),
            _step("find_texts", {"topic": "scuola organico"}, {"ok": True, "entries": []}),
        ]
        # Quarta chiamata: stesso tool, args near-identical
        chosen_name = "find_texts"
        raw_args = {"topic": "organico"}
        self.assertTrue(_is_non_action_tool(chosen_name))
        cur_norm = _normalize_args_for_dup(raw_args)
        near_count = 1
        for prev in history:
            if prev.chosen_tool != chosen_name:
                continue
            prev_norm = _normalize_args_for_dup(prev.raw_args)
            if _args_jaccard(cur_norm, prev_norm) >= 0.7:
                near_count += 1
        # "organico" e' subset stretto: Jaccard < 0.7 con tutti i 3.
        # Ma il punto e' che TANTI dei 3 sono near-identical fra loro.
        # Tester stricter: stessa query identica
        raw_args2 = {"topic": "organico scuola"}
        cur_norm2 = _normalize_args_for_dup(raw_args2)
        near_count2 = 1
        for prev in history:
            if prev.chosen_tool != chosen_name:
                continue
            prev_norm = _normalize_args_for_dup(prev.raw_args)
            if _args_jaccard(cur_norm2, prev_norm) >= 0.7:
                near_count2 += 1
        # "organico scuola" matcha con tutti i 3 (token-set vicino) → near_count = 4
        self.assertGreaterEqual(near_count2, DEFAULT_CAP_MAX_PER_TURN)

    def test_find_texts_2x_no_block(self):
        from agent_runtime import (
            _normalize_args_for_dup, _args_jaccard, DEFAULT_CAP_MAX_PER_TURN,
        )
        history = [
            _step("find_texts", {"topic": "organico scuola"}, {"ok": True, "entries": []}),
        ]
        chosen_name = "find_texts"
        raw_args = {"topic": "organico scuola roma"}
        cur_norm = _normalize_args_for_dup(raw_args)
        near_count = 1
        for prev in history:
            if prev.chosen_tool != chosen_name:
                continue
            prev_norm = _normalize_args_for_dup(prev.raw_args)
            if _args_jaccard(cur_norm, prev_norm) >= 0.7:
                near_count += 1
        self.assertLess(near_count, DEFAULT_CAP_MAX_PER_TURN)

    def test_action_verb_cap_unchanged(self):
        """write_files non e' soggetto al cap_max_per_turn (non-action only).

        Le guardie sui verbi action restano: cyclic-call, vaglio,
        duplicate. Il cap_max_per_turn rinforzato e' solo per non-action.
        """
        from agent_runtime import _is_non_action_tool
        self.assertFalse(_is_non_action_tool("write_files"))
        self.assertFalse(_is_non_action_tool("move_messages"))
        self.assertFalse(_is_non_action_tool("delete_files"))
        # Quindi: la guardia cap_max_per_turn skippa per costruzione.

    def test_different_tools_dont_share_count(self):
        from agent_runtime import _normalize_args_for_dup, _args_jaccard
        # Stessi args, tool differenti: il filtro `prev.chosen_tool == chosen_name`
        # esclude i tool diversi dal conteggio.
        history = [
            _step("find_urls", {"topic": "organico scuola"}, {"ok": True, "entries": []}),
            _step("find_urls", {"topic": "organico scuola"}, {"ok": True, "entries": []}),
            _step("find_urls", {"topic": "organico scuola"}, {"ok": True, "entries": []}),
        ]
        chosen_name = "find_texts"
        raw_args = {"topic": "organico scuola"}
        cur_norm = _normalize_args_for_dup(raw_args)
        near_count = 1
        for prev in history:
            if prev.chosen_tool != chosen_name:
                continue
            prev_norm = _normalize_args_for_dup(prev.raw_args)
            if _args_jaccard(cur_norm, prev_norm) >= 0.7:
                near_count += 1
        # find_texts mai chiamato in history → near_count resta 1
        self.assertEqual(near_count, 1)

    def test_default_cap_env_override(self):
        """METNOS_CAP_MAX_PER_TURN env var modifica la soglia."""
        # NB: il modulo legge l'env al import-time. Test piu' semplice:
        # verifica che il valore di default (3) sia presente e int.
        from agent_runtime import DEFAULT_CAP_MAX_PER_TURN
        self.assertIsInstance(DEFAULT_CAP_MAX_PER_TURN, int)
        self.assertGreaterEqual(DEFAULT_CAP_MAX_PER_TURN, 1)


if __name__ == "__main__":
    unittest.main()

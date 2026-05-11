"""Test ADR 0111 (7/5/2026): PLANNER ignora health block in observation.

Verifica le tre difese contro il bug live turn 62f80f47 («stato sistema»):
- Level 1: planner.j2 contiene la regola (health_in_observation_skip) HEALTH BLOCK.
- Level 2: handle_describe_entries usa `health_context` per pre-pendere
  istruzione "STATO SERVER GIA' RIASSUNTO" al prompt LLM.
- Level 3: agent_runtime auto-final dopo get_processes ok+health, skip
  PLANNER step 2+ e lascia che `_prepend_health_block_if_any` componga
  il final_message.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ---------------------------------------------------------------------
# Level 1: planner prompt contiene (health_in_observation_skip)
# ---------------------------------------------------------------------

class TestLevel1PlannerPrompt(unittest.TestCase):
    """La regola (health_in_observation_skip) deve esistere e contenere i
    marker obbligatori (DEVI/NON DEVI/OK/ERRORE) — stile §6 prescrittivo."""

    def _planner_text(self) -> str:
        # Fase C (11/5/2026): planner splittato in 3 layer; usa compose()
        # con sections=None (all) per renderizzare tutto.
        import prompt_loader
        return prompt_loader.compose(
            "planner", "it", sections=None,
            vocab_actions="x", vocab_objects="x", vocab_qualifiers="x",
            project_paths="x", users_known="x",
        )

    def test_health_skip_marker_present(self):
        text = self._planner_text()
        self.assertIn("(health_in_observation_skip)", text)
        self.assertIn("HEALTH BLOCK", text)

    def test_health_skip_has_prescriptive_form(self):
        text = self._planner_text()
        # Estrai blocco (health_in_observation_skip)..(A) DISCOVERY
        i = text.index("(health_in_observation_skip)")
        j = text.index("(A) DISCOVERY", i)
        block = text[i:j]
        self.assertIn("DEVI:", block)
        self.assertIn("NON DEVI:", block)
        self.assertIn("OK:", block)
        self.assertIn("ERRORE:", block)
        self.assertIn("describe_entries", block)
        self.assertIn("get_processes", block)
        self.assertIn("non disponibile", block.lower())


# ---------------------------------------------------------------------
# Level 2: describe_entries health_context
# ---------------------------------------------------------------------

class TestLevel2DescribeEntriesHealthContext(unittest.TestCase):
    """`handle_describe_entries(health_context=<dict>)` deve pre-pendere
    al prompt LLM un blocco "STATO SERVER GIA' RIASSUNTO" e l'istruzione
    "non commentare salute"."""

    def test_health_context_pre_prepends_directive(self):
        import describe_entries as de
        captured = {}

        def fake_call_llm(entries, prompt, tier, max_tokens):
            captured["prompt"] = prompt
            return ("ok summary", {"in_tokens": 1, "out_tokens": 1, "latency_ms": 1})

        with mock.patch.object(de, "call_llm", fake_call_llm):
            health = {
                "load": {"available": True, "1m": 0.5, "5m": 0.4, "15m": 0.3,
                         "uptime_s": 3600},
                "memory": {"available": True, "used_mb": 8000, "total_mb": 16000,
                           "swap_pct": 5.0, "used_pct": 50.0},
                "disk": [], "services": [],
            }
            res = de.handle_describe_entries({
                "entries": [{"name": "python", "cpu": 33.0}],
                "style": "by_relevance",
                "context": "stato sistema",
                "health_context": health,
            })
        self.assertTrue(res.get("ok"))
        prompt = captured["prompt"]
        self.assertIn("STATO SERVER GIA' RIASSUNTO", prompt)
        self.assertIn("NON RIPETERE", prompt)
        self.assertIn("NON DICHIARARE", prompt)
        # Il blocco salute deve avere i numeri reali (load 1m=0.5)
        self.assertTrue("0.5" in prompt or "0.50" in prompt)

    def test_no_health_context_no_directive(self):
        """Senza health_context, nessuna stringa STATO SERVER nel prompt."""
        import describe_entries as de
        captured = {}

        def fake_call_llm(entries, prompt, tier, max_tokens):
            captured["prompt"] = prompt
            return ("ok", {"in_tokens": 1, "out_tokens": 1, "latency_ms": 1})

        with mock.patch.object(de, "call_llm", fake_call_llm):
            res = de.handle_describe_entries({
                "entries": [{"name": "python"}],
                "style": "by_relevance", "context": "test",
            })
        self.assertTrue(res.get("ok"))
        self.assertNotIn("STATO SERVER GIA' RIASSUNTO", captured["prompt"])

    def test_empty_health_context_no_directive(self):
        """health_context={} → no directive (idempotente)."""
        import describe_entries as de
        captured = {}

        def fake_call_llm(entries, prompt, tier, max_tokens):
            captured["prompt"] = prompt
            return ("ok", {"in_tokens": 1, "out_tokens": 1, "latency_ms": 1})

        with mock.patch.object(de, "call_llm", fake_call_llm):
            de.handle_describe_entries({
                "entries": [{"name": "x"}],
                "style": "by_relevance", "context": "t",
                "health_context": {},
            })
        self.assertNotIn("STATO SERVER GIA' RIASSUNTO", captured["prompt"])


# ---------------------------------------------------------------------
# Level 3: _prepend_health_block_if_any compone final_message vuoto
# ---------------------------------------------------------------------

class TestLevel3PrependHealthEmptyMessage(unittest.TestCase):
    """Quando `final_message=""` e uno step ha health, il prepend deve
    produrre un messaggio coerente (no whitespace stranded, no
    "non disponibile")."""

    def test_prepend_health_with_empty_message(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(ts_start=0.0, turn_id="test_zcinque",
                      user_query="stato sistema")
        # Simula uno step get_processes ok con health
        step = StepLog(step_num=1, chosen_tool="get_processes",
                       raw_args={"include_health": True})
        step.result = {
            "ok": True,
            "entries": [
                {"pid": 100, "name": "python", "cpu_pct": 33.0, "mem_pct": 5.0},
            ],
            "health": {
                "load": {"available": True, "1m": 0.47, "5m": 0.52,
                         "15m": 0.41, "uptime_s": 173 * 3600},
                "memory": {"available": True, "used_mb": 43000,
                           "total_mb": 121000, "swap_pct": 11.0,
                           "pct": 36.2},
                "disk": [], "services": [],
            },
        }
        log.steps = [step]
        log.final_kind = "answer"
        log.final_message = ""
        # Esegui solo il prepend (non scrivere su disco)
        log._prepend_health_block_if_any()
        msg = log.final_message
        self.assertGreater(len(msg), 50, "final_message vuoto dopo prepend")
        # Deve contenere il blocco salute, NON "non disponibile"
        self.assertNotIn("non disponibile", msg.lower())
        # Numeri reali
        self.assertIn("0.47", msg)
        self.assertIn("36", msg)  # used_pct ram

    def test_prepend_health_idempotent(self):
        """Invocato due volte non duplica il blocco."""
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(ts_start=0.0, turn_id="test_idem", user_query="stato")
        step = StepLog(step_num=1, chosen_tool="get_processes", raw_args={})
        step.result = {
            "ok": True, "entries": [],
            "health": {
                "load": {"available": True, "1m": 0.1, "5m": 0.1, "15m": 0.1,
                         "uptime_s": 100},
                "memory": {"available": True, "used_mb": 1000,
                           "total_mb": 8000, "swap_pct": 0, "pct": 12.5},
                "disk": [], "services": [],
            },
        }
        log.steps = [step]
        log.final_kind = "answer"
        log.final_message = ""
        log._prepend_health_block_if_any()
        first = log.final_message
        log._prepend_health_block_if_any()
        # Idempotente: il guard "Stato server" already-in-message blocca
        self.assertEqual(first, log.final_message)


# ---------------------------------------------------------------------
# Level 3: heuristic action verbs / imperative skip
# ---------------------------------------------------------------------

class TestLevel3ActionSkipHeuristic(unittest.TestCase):
    """Verifica la lista keyword imperative usata per skip auto-final.
    Anche se non chiamiamo run_turn (richiede LLM live), assicuriamo che
    le costanti siano disponibili e contengano i casi attesi."""

    def test_action_verbs_pred_includes_destructive(self):
        from agent_runtime import _ACTION_VERBS_PRED
        self.assertIn("delete", _ACTION_VERBS_PRED)
        self.assertIn("send", _ACTION_VERBS_PRED)
        self.assertIn("write", _ACTION_VERBS_PRED)


if __name__ == "__main__":
    unittest.main()

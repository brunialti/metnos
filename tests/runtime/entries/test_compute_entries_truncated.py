"""compute_entries op=count truncation-aware (D.2, 22/5/2026).

Quando le entries provengono da from_step e l'upstream era truncated
(find_files con max_results piccolo su cartella grande), compute_entries
deve usare `_from_step_total_hint` invece di len(entries) materializzate.

Caso live: turn d1e649a7 22/5 — find_files su /tmp/nas_public/media/Immagini
(33578 file) con max_results=1000 → entries=1000, available_total=11000+
sondaggio. compute_entries op=count ritornava 1000; con questo fix ritorna
il `total_hint` reale (1000 vs 33578 ≠ 1 ordine di grandezza).

Run: python3 -m pytest tests/runtime/entries/test_compute_entries_truncated.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EXEC = (Path(__file__).resolve().parents[3] / "runtime").parent / "executors" / "compute_entries"
if str(_EXEC) not in sys.path:
    sys.path.insert(0, str(_EXEC))
_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _fake_entries(n):
    return [{"path": f"/fake/{i}.txt"} for i in range(n)]


class ComputeCountTruncatedTests(unittest.TestCase):

    def test_count_uses_total_hint_when_truncated(self):
        from compute_entries import invoke
        r = invoke({
            "entries": _fake_entries(1000),
            "op": "count",
            "_from_step_total_hint": 33578,
            "_from_step_truncated": True,
        })
        self.assertEqual(r["value"], 33578)
        self.assertTrue(r.get("truncated_upstream"))
        self.assertEqual(r.get("materialized_count"), 1000)

    def test_count_uses_len_when_no_hint(self):
        from compute_entries import invoke
        r = invoke({"entries": _fake_entries(50), "op": "count"})
        self.assertEqual(r["value"], 50)
        self.assertFalse(r.get("truncated_upstream"))

    def test_count_ignores_hint_if_lower_than_materialized(self):
        """Hint < count_input non ha senso (inconsistenza upstream): si fida
        del count materializzato, non del hint."""
        from compute_entries import invoke
        r = invoke({
            "entries": _fake_entries(100),
            "op": "count",
            "_from_step_total_hint": 50,  # incoerente
            "_from_step_truncated": True,
        })
        # value == count_input perche' hint < count_input
        self.assertEqual(r["value"], 100)

    def test_count_with_key_uses_materialized_but_annotates_truncated(self):
        """Con `key`, non possiamo proiettare hint: usiamo materialized ma
        annotiamo truncated_upstream così il caller sa che e' parziale."""
        from compute_entries import invoke
        entries = [{"path": f"/f/{i}", "size": i * 10} for i in range(100)]
        r = invoke({
            "entries": entries,
            "op": "count",
            "key": "size",
            "_from_step_total_hint": 33578,
            "_from_step_truncated": True,
        })
        # value e' il conteggio materializzato (entries con `size` non None)
        self.assertEqual(r["value"], 100)
        # ma annota che e' parziale
        self.assertTrue(r.get("truncated_upstream"))


class ResolveFromStepInjectsHintTests(unittest.TestCase):
    """`agent_runtime.resolve_from_step` inietta `_from_step_total_hint` se
    l'observation upstream ha `available_total` > len(entries)."""

    def test_resolve_injects_total_hint_when_truncated(self):
        from agent_runtime import resolve_from_step
        history = [{
            "tool": "find_files",
            "observation": {
                "ok": True,
                "entries": _fake_entries(1000),
                "available_total": 33578,
                "metadata": {"truncated": True},
            },
        }]
        args = {"from_step": 1, "op": "count"}
        out, errors = resolve_from_step(args, history)
        self.assertEqual(errors, [])
        self.assertEqual(out.get("_from_step_total_hint"), 33578)
        self.assertTrue(out.get("_from_step_truncated"))

    def test_resolve_no_hint_when_not_truncated(self):
        from agent_runtime import resolve_from_step
        history = [{
            "tool": "find_files",
            "observation": {
                "ok": True,
                "entries": _fake_entries(50),
                "metadata": {"truncated": False, "count": 50},
            },
        }]
        args = {"from_step": 1, "op": "count"}
        out, errors = resolve_from_step(args, history)
        self.assertEqual(errors, [])
        self.assertNotIn("_from_step_total_hint", out)
        self.assertNotIn("_from_step_truncated", out)

    def test_resolve_reads_available_total_from_metadata(self):
        """Fallback: available_total puo' essere in step_obs.metadata invece
        che root (compat varie versioni di find_files)."""
        from agent_runtime import resolve_from_step
        history = [{
            "tool": "find_files",
            "observation": {
                "ok": True,
                "entries": _fake_entries(500),
                "metadata": {"truncated": True, "available_total": 9999},
            },
        }]
        args = {"from_step": 1, "op": "count"}
        out, errors = resolve_from_step(args, history)
        self.assertEqual(out.get("_from_step_total_hint"), 9999)


if __name__ == "__main__":
    unittest.main()

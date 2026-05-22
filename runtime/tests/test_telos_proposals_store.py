"""telos_proposals_store — modulo dashboard /admin/proposals/telos.

Test deterministici su file JSONL fixture in tempdir.

Run: `python3 -m pytest runtime/tests/test_telos_proposals_store.py -v`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _sample_proposal(
    ts=1000.0, lens="scamper", telos_id="t.tempo", target="create_events",
    ea=0.5, fits=None, action="combina X+Y in pipeline", rationale="osservato 49 volte",
) -> dict:
    return {
        "ts": ts,
        "telos_id": telos_id,
        "telos_phrase": "Liberare il mio tempo",
        "lens": lens,
        "operator": "S",
        "executor_target": target,
        "proposed_action": action,
        "rationale": rationale,
        "paternalism_flag": False,
        "expected_alignment": ea,
        "alignment_per_telos": fits or [
            {"telos_id": "t.tempo", "fit": 0.8, "why": "frees time"},
            {"telos_id": "t.ordine", "fit": 0.3, "why": "tidier"},
        ],
    }


def _sample_turn(query="domani che ore sono", chosen_tools=None, latencies_ms=None) -> dict:
    chosen_tools = chosen_tools or ["get_now", "final_answer"]
    latencies_ms = latencies_ms or [5000, 1000]
    return {
        "ts_start": 1779000000.0,
        "ts_end": 1779000010.0,
        "user_query": query,
        "turn_id": "abc",
        "steps": [
            {
                "step_num": i + 1,
                "chosen_tool": tool,
                "llm_latency_ms": lat,
                "exec_ms": 100,
                "intent_ms": 200,
            }
            for i, (tool, lat) in enumerate(zip(chosen_tools, latencies_ms))
        ],
    }


class FixtureBase(unittest.TestCase):
    """Crea tempdir + redirige _DATA_DIR / _TURN_LOG_DIR del modulo."""

    def setUp(self):
        import telos_proposals_store as S
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.props_path = self.tmpdir / "telos_proposals.rescored.recomposed.jsonl"
        self.decisions_path = self.tmpdir / "telos_decisions.jsonl"
        self.turns_dir = self.tmpdir / "turns"
        self.turns_dir.mkdir()
        # Redirect le costanti del modulo (test-only override).
        self._orig_data_dir = S._DATA_DIR
        self._orig_proposals_candidates = S._PROPOSALS_CANDIDATES
        self._orig_decisions = S.DECISIONS_PATH
        self._orig_turn_dir = S._TURN_LOG_DIR
        S._DATA_DIR = self.tmpdir
        S._PROPOSALS_CANDIDATES = (self.props_path,)
        S.DECISIONS_PATH = self.decisions_path
        S._TURN_LOG_DIR = self.turns_dir
        S._TURN_LOG_CACHE.clear()
        S._TURN_LOG_CACHE_MTIME.clear()
        self.S = S

    def tearDown(self):
        S = self.S
        S._DATA_DIR = self._orig_data_dir
        S._PROPOSALS_CANDIDATES = self._orig_proposals_candidates
        S.DECISIONS_PATH = self._orig_decisions
        S._TURN_LOG_DIR = self._orig_turn_dir
        S._TURN_LOG_CACHE.clear()
        S._TURN_LOG_CACHE_MTIME.clear()
        self.tmp.cleanup()

    def _write_proposals(self, props: list[dict]):
        self.props_path.write_text(
            "\n".join(json.dumps(p) for p in props) + "\n", encoding="utf-8"
        )

    def _write_turn(self, fname: str, turn: dict):
        fp = self.turns_dir / fname
        fp.write_text(json.dumps(turn) + "\n", encoding="utf-8")


class LoadAllTests(FixtureBase):

    def test_empty_file_returns_empty(self):
        self.assertEqual(self.S.load_all(), [])

    def test_load_sorted_by_ea_desc(self):
        self._write_proposals([
            _sample_proposal(ts=1, ea=0.20),
            _sample_proposal(ts=2, ea=0.50),
            _sample_proposal(ts=3, ea=0.35),
        ])
        rows = self.S.load_all()
        self.assertEqual([r["expected_alignment"] for r in rows], [0.50, 0.35, 0.20])

    def test_filter_min_alignment(self):
        self._write_proposals([
            _sample_proposal(ts=1, ea=0.20),
            _sample_proposal(ts=2, ea=0.50),
            _sample_proposal(ts=3, ea=0.35),
        ])
        rows = self.S.load_all(min_alignment=0.30)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["expected_alignment"] >= 0.30 for r in rows))

    def test_filter_lens(self):
        self._write_proposals([
            _sample_proposal(ts=1, lens="scamper", ea=0.5),
            _sample_proposal(ts=2, lens="endgame_book", ea=0.4),
        ])
        rows = self.S.load_all(lens="endgame_book")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lens"], "endgame_book")

    def test_filter_telos_id(self):
        self._write_proposals([
            _sample_proposal(ts=1, telos_id="t.tempo", ea=0.5),
            _sample_proposal(ts=2, telos_id="t.parsimonia", ea=0.5),
        ])
        rows = self.S.load_all(telos_id="t.parsimonia")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["telos_id"], "t.parsimonia")

    def test_prop_id_is_stable_string_from_ts(self):
        self._write_proposals([_sample_proposal(ts=1779382036.195538)])
        row = self.S.load_all()[0]
        self.assertEqual(row["prop_id"], "1779382036.195538")
        # Lo stesso ts → stesso prop_id (idempotenza per decisioni).
        row2 = self.S.load_all()[0]
        self.assertEqual(row["prop_id"], row2["prop_id"])


class DecisionsTests(FixtureBase):

    def test_apply_decision_valid_actions(self):
        for action in ("accept", "reject", "stage"):
            self.S.apply_decision("123.456", action, by="test")
        idx = self.S.decisions_index()
        self.assertEqual(len(idx), 1)
        self.assertEqual(idx["123.456"]["action"], "stage")  # LWW: ultima vince

    def test_apply_decision_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            self.S.apply_decision("123", "approve")  # not in {accept,reject,stage}

    def test_decisions_lww_independent_keys(self):
        self.S.apply_decision("100.0", "accept")
        self.S.apply_decision("200.0", "reject")
        self.S.apply_decision("100.0", "stage")  # cambia idea su 100
        idx = self.S.decisions_index()
        self.assertEqual(idx["100.0"]["action"], "stage")
        self.assertEqual(idx["200.0"]["action"], "reject")

    def test_load_all_joins_decision(self):
        self._write_proposals([_sample_proposal(ts=10.0, ea=0.5)])
        self.S.apply_decision("10.000000", "accept", by="me")
        rows = self.S.load_all()
        self.assertEqual(rows[0]["decision"]["action"], "accept")
        self.assertEqual(rows[0]["decision"]["by"], "me")

    def test_load_all_include_decided_false_hides_accepted(self):
        self._write_proposals([
            _sample_proposal(ts=10.0, ea=0.5),
            _sample_proposal(ts=20.0, ea=0.4),
        ])
        self.S.apply_decision("10.000000", "accept")
        self.S.apply_decision("20.000000", "stage")  # stage NON nasconde
        rows = self.S.load_all(include_decided=False)
        # Solo 20 visibile (stage, non terminale)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prop_id"], "20.000000")


class EnrichTests(FixtureBase):

    def test_enrich_finds_example_turn_with_target(self):
        self._write_proposals([_sample_proposal(
            ts=1.0, target="create_events", action="schedula evento"
        )])
        self._write_turn("2026-05-22.jsonl", _sample_turn(
            query="domani alle 15 dentista",
            chosen_tools=["get_now", "create_events", "final_answer"],
        ))
        rows = self.S.load_all(enrich_rows=True)
        r = rows[0]
        self.assertEqual(r["example_query"], "domani alle 15 dentista")
        self.assertIn("create_events", r["current_path"])

    def test_enrich_pipeline_observed_when_all_tools_match(self):
        self._write_proposals([_sample_proposal(
            ts=1.0, target="create_events",
            action="combina get_files_metadata + create_events",
        )])
        self._write_turn("2026-05-22.jsonl", _sample_turn(
            query="leggi i metadati e crea l'evento",
            chosen_tools=["get_files_metadata", "create_events"],
        ))
        rows = self.S.load_all(enrich_rows=True)
        r = rows[0]
        self.assertTrue(r["pipeline_observed"], f"got {r}")

    def test_enrich_speculative_when_target_never_used(self):
        self._write_proposals([_sample_proposal(
            ts=1.0, target="non_existent_tool",
            action="sostituisci tutto con non_existent_tool",
        )])
        self._write_turn("2026-05-22.jsonl", _sample_turn(
            query="domani che ore",
            chosen_tools=["get_now", "final_answer"],
        ))
        rows = self.S.load_all(enrich_rows=True)
        r = rows[0]
        self.assertFalse(r["pipeline_observed"])
        self.assertIsNone(r["example_query"])
        self.assertEqual(r["current_path"], [])

    def test_enrich_estimates_latency_saved(self):
        self._write_proposals([_sample_proposal(
            ts=1.0, target="create_events",
            action="combina get_files_metadata + create_events",
        )])
        self._write_turn("2026-05-22.jsonl", _sample_turn(
            query="leggi metadati e schedula",
            chosen_tools=["get_files_metadata", "create_events", "final_answer"],
            latencies_ms=[5000, 5000, 1000],
        ))
        rows = self.S.load_all(enrich_rows=True)
        r = rows[0]
        # current 3 step → new 2 step (get_files_metadata+create_events collassati)
        self.assertEqual(len(r["current_path"]), 3)
        self.assertEqual(len(r["new_path_estimated"]), 2)
        self.assertGreater(r["latency_saved_ms_est"], 0)

    def test_extract_tool_mentions_filters_false_positives(self):
        # Le parole italiane snake_case sintetiche (es. del_sistema) sono blacklistate.
        mentions = self.S._extract_tool_mentions(
            "Combinare get_files_metadata + create_events nella lista_di file"
        )
        self.assertIn("get_files_metadata", mentions)
        self.assertIn("create_events", mentions)
        self.assertNotIn("lista_di", mentions)

    def test_parse_n_observed(self):
        self.assertEqual(self.S._parse_n_observed("co-attivati 49 volte"), 49)
        self.assertEqual(self.S._parse_n_observed("12 occorrenze nel mnestoma"), 12)
        self.assertIsNone(self.S._parse_n_observed("nessun numero qui"))


class StatsTests(FixtureBase):

    def test_stats_counts_decisions(self):
        self._write_proposals([
            _sample_proposal(ts=1.0, ea=0.5),
            _sample_proposal(ts=2.0, ea=0.5),
            _sample_proposal(ts=3.0, ea=0.5),
            _sample_proposal(ts=4.0, ea=0.5),
        ])
        self.S.apply_decision("1.000000", "accept")
        self.S.apply_decision("2.000000", "reject")
        self.S.apply_decision("3.000000", "stage")
        # 4 pending, no decision
        st = self.S.stats()
        self.assertEqual(st["total"], 4)
        self.assertEqual(st["accepted"], 1)
        self.assertEqual(st["rejected"], 1)
        self.assertEqual(st["staged"], 1)
        self.assertEqual(st["pending"], 2)  # 4 - accepted(1) - rejected(1) = 2

    def test_stats_empty(self):
        st = self.S.stats()
        self.assertEqual(st["total"], 0)
        self.assertEqual(st["pending"], 0)


if __name__ == "__main__":
    unittest.main()

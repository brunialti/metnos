"""test_decomposer_path — copre il PATH DETERMINISTICO `compound_decomposer.
decompose_query`, NON il path engine/dispatch.

Perché esiste (lezione 22/6): il turno reale «Leggi gli eventi..., estrai
titolo e orario, e crea un foglio» falliva «extract_entries missing 'fields'»,
ma il banco (`bench/compound_dryrun.plan_only`) e i test guard
(`test_ensure_extract_clause`) battono il path ENGINE (proposer→dispatch guard).
Prod prova PRIMA il decomposer deterministico (`agent_runtime` log «COMPOUND
DECOMPOSED»), che costruisce il framework diretto e NON passa per i guard di
dispatch. Quel path NON era coperto da test → blind-spot. Questo file lo chiude:
guida `decompose_query` come fa prod e blocca la classe di bug «step con arg
REQUIRED mancante» (qui `extract_entries.fields`).

Deterministico: avail-tools sintetico (no load_catalog), nessun LLM nel
decomposer (§7.9). Vedi [[project-compound-planning-refactor]] P0.
"""
from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("METNOS_ENGINE", "v3")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compound_decomposer import decompose_query  # noqa: E402
from engine.dispatch import finalize_decomposed_plan  # noqa: E402

# Tool disponibili come li vede prod per il decomposer (catalog ∪ builtin ∪
# final_answer). Sottoinsieme sufficiente per i casi eventi→extract→create.
_AVAIL = {
    "read_events", "find_events", "read_files", "find_files", "list_files",
    "extract_entries", "filter_entries", "describe_entries",
    "create_files_doc", "create_files_spreadsheet", "create_files_csv",
    "write_files_spreadsheet", "write_files", "final_answer",
}


def _plan(query: str):
    """Replica il PATH REALE di prod: decompose_query → finalize_decomposed_plan
    (i guard riempiono extract.fields). `decompose_query` da solo NON riempie più
    i fields (de-dup P1) → il test DEVE passare per la finalizzazione, come prod."""
    steps = decompose_query(query, _AVAIL, {t: None for t in _AVAIL})
    if steps is None:
        return None
    fw = finalize_decomposed_plan(steps, query, None)
    return [{"tool": s.tool, "args": s.args} for s in fw.steps]


# Query che prod manda al DECOMPOSER (produttore locale «eventi» → passa il
# confidence gate; le query mail spesso DEFERISCONO all'engine, path diverso).
_DECOMPOSER_CASES = [
    ("Leggi gli eventi di domani, estrai titolo e luogo, e crea un documento.",
     ["titolo", "luogo"]),
    ("Leggi gli eventi della settimana, estrai data e descrizione, e mettili in un foglio.",
     ["data", "descrizione"]),
    ("Leggi gli eventi di oggi, estrai ora e titolo, e salvali in un documento.",
     ["ora", "titolo"]),
]


class TestDecomposerExtractFields(unittest.TestCase):
    def test_extract_step_has_fields(self):
        for query, expected in _DECOMPOSER_CASES:
            with self.subTest(query=query):
                steps = _plan(query)
                self.assertIsNotNone(steps, f"decomposer ha deferito: {query!r}")
                ex = [s for s in steps if s["tool"] == "extract_entries"]
                self.assertEqual(len(ex), 1,
                                 f"atteso 1 extract_entries in {[s['tool'] for s in steps]}")
                self.assertEqual(ex[0]["args"].get("fields"), expected)

    def test_shape_producer_extract_consumer(self):
        # SHAPE: read_events → extract_entries → consumer (create/write_files_*)
        steps = _plan(_DECOMPOSER_CASES[0][0])
        tools = [s["tool"] for s in steps]
        self.assertIn("read_events", tools)
        ie = tools.index("extract_entries")
        self.assertLess(tools.index("read_events"), ie)
        self.assertTrue(any(t.startswith(("create_files", "write_files"))
                            for t in tools[ie + 1:]),
                        f"nessun consumer dopo extract in {tools}")

    def test_invariant_no_extract_without_fields(self):
        # LOCK della classe di bug: un extract_entries emesso dal decomposer NON
        # deve MAI uscire senza `fields` (arg REQUIRED → executor fallirebbe).
        for query, _ in _DECOMPOSER_CASES:
            steps = _plan(query) or []
            for s in steps:
                if s["tool"] == "extract_entries":
                    f = s["args"].get("fields")
                    self.assertTrue(
                        isinstance(f, list) and f and all(isinstance(x, str) for x in f),
                        f"extract_entries senza fields validi in {query!r}: {s['args']}")


if __name__ == "__main__":
    unittest.main()

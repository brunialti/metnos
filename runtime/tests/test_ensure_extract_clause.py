"""test_ensure_extract_clause — §7.9: la clausola «estrai» (transform intermedio)
deve essere INSERITA nella posizione giusta (dopo l'ultimo produttore prima del
consumer mutante) con rewiring from_step, quando il proposer la droppa. Bug live
21/6 (banco compound-extract-create: extract droppato in 8/8 query)."""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path
os.environ.setdefault("METNOS_ENGINE", "v3")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import dispatch as D  # noqa: E402
from engine.types import Framework, StepSpec, Intent  # noqa: E402

_CAT = [type("E", (), {"name": n})() for n in
        ("read_messages", "create_files_spreadsheet", "create_files_doc",
         "filter_entries", "extract_entries")]


def _intent(acts):
    return Intent(verb="find", object="messages", keywords=[], confidence=1.0,
                  lang="it", actions=acts)


_WITH_EXTRACT = [{"verb": "find", "object": "messages"},
                 {"verb": "extract", "object": "messages"},
                 {"verb": "create", "object": "files"}]


def _run(spec, intent, query="q"):
    fw = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in spec])
    out = D._ensure_extract_clause(fw, intent, query, _CAT)
    return [(s.tool, s.args.get("from_step")) for s in out.steps]


def _run_fw(spec, intent, query):
    fw = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in spec])
    return D._ensure_extract_clause(fw, intent, query, _CAT)


class TestEnsureExtract(unittest.TestCase):
    def test_insert_before_create(self):
        r = _run([("read_messages", {}),
                  ("create_files_spreadsheet", {"from_step": 1})],
                 _intent(_WITH_EXTRACT))
        self.assertEqual(r, [("read_messages", None),
                             ("extract_entries", 1),
                             ("create_files_spreadsheet", 2)])

    def test_insert_before_filter(self):
        r = _run([("read_messages", {}), ("filter_entries", {"from_step": 1}),
                  ("create_files_doc", {"from_step": 2})], _intent(_WITH_EXTRACT))
        self.assertEqual(r[1], ("extract_entries", 1))
        self.assertEqual(r[2], ("filter_entries", 2))
        self.assertEqual(r[3], ("create_files_doc", 3))

    def test_ignores_spurious_trailing_producer(self):
        r = _run([("read_messages", {}),
                  ("create_files_spreadsheet", {"from_step": 1}),
                  ("read_messages", {"from_step": 3})], _intent(_WITH_EXTRACT))
        # extract dopo il PRIMO read, create lo consuma
        self.assertEqual(r[0][0], "read_messages")
        self.assertEqual(r[1], ("extract_entries", 1))
        self.assertEqual(r[2], ("create_files_spreadsheet", 2))

    def test_noop_when_extract_present(self):
        r = _run([("read_messages", {}), ("extract_entries", {"from_step": 1}),
                  ("create_files_spreadsheet", {"from_step": 2})],
                 _intent(_WITH_EXTRACT))
        self.assertEqual(len([t for t, _ in r if t == "extract_entries"]), 1)

    def test_present_extract_missing_fields_gets_filled(self):
        # Bug live 22/6: il proposer emette extract_entries SENZA `fields` (req) →
        # il guard li RIEMPIE dalla clausola, senza inserire un secondo step.
        out = _run_fw(
            [("read_messages", {}), ("extract_entries", {"from_step": 1}),
             ("create_files_spreadsheet", {"from_step": 2})],
            _intent(_WITH_EXTRACT),
            "Cerca le email, estrai mittente e importo, e crea un foglio.")
        exs = [s for s in out.steps if s.tool == "extract_entries"]
        self.assertEqual(len(exs), 1)  # non ne aggiunge un secondo
        self.assertEqual(exs[0].args.get("fields"), ["mittente", "importo"])

    def test_present_extract_keeps_existing_fields(self):
        # Se `fields` c'e' gia' (proposer corretto), NON lo sovrascrive.
        out = _run_fw(
            [("read_messages", {}),
             ("extract_entries", {"from_step": 1, "fields": ["a", "b"]}),
             ("create_files_spreadsheet", {"from_step": 2})],
            _intent(_WITH_EXTRACT),
            "Cerca le email, estrai mittente e importo, e crea un foglio.")
        ex = next(s for s in out.steps if s.tool == "extract_entries")
        self.assertEqual(ex.args.get("fields"), ["a", "b"])

    def test_noop_when_no_extract_intent(self):
        r = _run([("read_messages", {}),
                  ("create_files_spreadsheet", {"from_step": 1})],
                 _intent([{"verb": "find", "object": "messages"},
                          {"verb": "create", "object": "files"}]))
        self.assertNotIn("extract_entries", [t for t, _ in r])

    def test_inserted_extract_has_derived_fields(self):
        # Bug live 22/6: l'extract inserito DEVE avere `fields` (required) derivati
        # dalla clausola «estrai X e Y», altrimenti l'executor fallisce.
        out = _run_fw(
            [("read_messages", {}),
             ("create_files_spreadsheet", {"from_step": 1})],
            _intent(_WITH_EXTRACT),
            "Cerca le email, estrai mittente e importo, e crea un foglio.")
        ex = next(s for s in out.steps if s.tool == "extract_entries")
        self.assertEqual(ex.args.get("fields"), ["mittente", "importo"])

    def test_inserted_extract_no_fields_when_query_opaque(self):
        # Query senza nomi-campo espliciti → nessun `fields` (degrado onesto:
        # l'executor dara' l'errore-guida; non inventiamo campi).
        out = _run_fw(
            [("read_messages", {}),
             ("create_files_spreadsheet", {"from_step": 1})],
            _intent(_WITH_EXTRACT), "estrai e crea un foglio")
        ex = next(s for s in out.steps if s.tool == "extract_entries")
        self.assertNotIn("fields", ex.args)


class TestDeriveExtractFields(unittest.TestCase):
    def _d(self, q):
        from compound_decomposer import derive_extract_fields
        return derive_extract_fields(q)

    def test_simple_two_fields_it(self):
        self.assertEqual(
            self._d("Leggi gli eventi, estrai titolo e orario, e crea un foglio."),
            ["titolo", "orario"])

    def test_three_fields_commas(self):
        self.assertEqual(
            self._d("Leggi i pdf, estrai mittente, oggetto e importo, e crea."),
            ["mittente", "oggetto", "importo"])

    def test_multiword_fields_strip_articles(self):
        self.assertEqual(
            self._d("Trova ordini, estrai il numero d'ordine e l'importo totale, crea."),
            ["numero ordine", "importo totale"])

    def test_cut_source_phrase_en(self):
        self.assertEqual(
            self._d("Extract the title and time from this week's events and create a sheet."),
            ["title", "time"])

    def test_empty_when_no_extract_clause(self):
        self.assertEqual(self._d("che ore sono?"), [])


if __name__ == "__main__":
    unittest.main()

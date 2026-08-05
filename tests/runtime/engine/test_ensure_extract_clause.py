"""test_ensure_extract_clause — §7.9: la clausola «estrai» (transform intermedio)
deve essere INSERITA nella posizione giusta (dopo l'ultimo produttore prima del
consumer mutante) con rewiring from_step, quando il proposer la droppa. Bug live
21/6 (banco compound-extract-create: extract droppato in 8/8 query)."""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path
os.environ.setdefault("METNOS_ENGINE", "v3")
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

    def test_mail_bulk_uses_bounded_sources_and_batching(self):
        out = _run_fw(
            [("read_messages", {"time_window": "last-45d"}),
             ("extract_entries", {"from_step": 1,
                                  "fields": ["entità", "origine"]}),
             ("create_files_spreadsheet", {
                 "from_step": 2, "columns": ["entità", "origine"]})],
            _intent(_WITH_EXTRACT),
            "Analizza le email, estrai entità e origine, crea un foglio.")
        args = out.steps[1].args
        self.assertEqual(args["max_per_text"], 20)
        self.assertEqual(args["max_total"], 500)
        self.assertEqual(args["max_sources"], 500)
        self.assertEqual(args["batch_size"], 8)
        self.assertIs(args["drill_down"], False)

    def test_explicit_mail_link_traversal_is_not_batched(self):
        out = _run_fw(
            [("read_messages", {}),
             ("extract_entries", {"from_step": 1, "fields": ["importo"]}),
             ("create_files_spreadsheet", {
                 "from_step": 2, "columns": ["importo"]})],
            _intent(_WITH_EXTRACT),
            "Leggi le email, apri i link, estrai importo e crea un foglio.")
        args = out.steps[1].args
        self.assertNotIn("batch_size", args)
        self.assertNotIn("drill_down", args)

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

    def test_natural_spreadsheet_assignment_it(self):
        self.assertEqual(
            self._d("crea uno spreadsheet e metti data e importo per ogni fattura"),
            ["data", "importo"])

    def test_natural_spreadsheet_assignment_en(self):
        self.assertEqual(
            self._d("create a spreadsheet and put date and amount for each invoice"),
            ["date", "amount"])

    def test_plain_file_destination_is_not_a_field_schema(self):
        self.assertEqual(self._d("crea un file e mettilo in /tmp"), [])


class TestEnsureExtractFromSites(unittest.TestCase):
    def test_site_text_to_spreadsheet_gets_extract_columns_and_scope(self):
        query = ("accedi a example.com e cerca fatture 2026, crea uno "
                 "spreadsheet e metti data e importo per ogni fattura")
        fw = Framework(steps=[
            StepSpec(tool="read_sites", args={"from_step": 1}),
            StepSpec(tool="create_files_spreadsheet", args={"from_step": 1}),
        ])
        intent = Intent(verb="open", object="sites", actions=[
            {"verb": "open", "object": "sites"},
            {"verb": "create", "object": "files"},
        ])

        out = D._ensure_extract_clause(fw, intent, query, _CAT)

        self.assertEqual([s.tool for s in out.steps], [
            "read_sites", "extract_entries", "create_files_spreadsheet"])
        self.assertEqual(out.steps[1].args["fields"], ["data", "importo"])
        self.assertEqual(out.steps[1].args["instruction"], query)
        self.assertEqual(out.steps[2].args["columns"], ["data", "importo"])
        self.assertEqual(out.steps[2].args["from_step"], 2)


class TestExtractedPeriodScope(unittest.TestCase):
    def test_explicit_year_filters_date_records_before_sink(self):
        query = "trova fatture 2026 e crea uno spreadsheet"
        fw = Framework(steps=[
            StepSpec(tool="read_sites", args={}),
            StepSpec(tool="extract_entries", args={
                "from_step": 1, "fields": ["data", "importo"]}),
            StepSpec(tool="create_files_spreadsheet", args={"from_step": 2}),
            StepSpec(tool="final_answer", args={}),
        ], final_message="${step3.@table}")

        out = D._ensure_extracted_period_scope(
            fw, _intent([]), query, _CAT)

        self.assertEqual([s.tool for s in out.steps], [
            "read_sites", "extract_entries", "filter_entries",
            "create_files_spreadsheet", "final_answer"])
        self.assertEqual(out.steps[2].args, {
            "from_step": 2, "where_field": "data",
            "where_regex": "^(?:2026)-"})
        self.assertEqual(out.steps[3].args["from_step"], 3)
        self.assertEqual(out.final_message, "${step4.@table}")
        self.assertEqual(
            D._ensure_extracted_period_scope(out, _intent([]), query, _CAT),
            out)

    def test_multiple_years_filter_year_field(self):
        fw = Framework(steps=[
            StepSpec(tool="extract_entries", args={
                "from_step": 1, "fields": ["anno", "totale"]}),
        ])
        out = D._ensure_extracted_period_scope(
            fw, _intent([]), "fatture 2025 e 2024", _CAT)
        self.assertEqual(out.steps[1].args["where_in"], ["2025", "2024"])

    def test_no_date_or_year_field_is_noop(self):
        fw = Framework(steps=[StepSpec(tool="extract_entries", args={
            "from_step": 1, "fields": ["titolo", "importo"]})])
        out = D._ensure_extracted_period_scope(
            fw, _intent([]), "elementi 2026", _CAT)
        self.assertIs(out, fw)


if __name__ == "__main__":
    unittest.main()

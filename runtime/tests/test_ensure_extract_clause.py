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


def _run(spec, intent):
    fw = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in spec])
    out = D._ensure_extract_clause(fw, intent, "q", _CAT)
    return [(s.tool, s.args.get("from_step")) for s in out.steps]


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

    def test_noop_when_no_extract_intent(self):
        r = _run([("read_messages", {}),
                  ("create_files_spreadsheet", {"from_step": 1})],
                 _intent([{"verb": "find", "object": "messages"},
                          {"verb": "create", "object": "files"}]))
        self.assertNotIn("extract_entries", [t for t, _ in r])


if __name__ == "__main__":
    unittest.main()

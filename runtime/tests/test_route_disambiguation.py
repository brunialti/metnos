"""test_route_disambiguation — §2.11 routing: form di disambiguazione su query
ambigua sull'oggetto, MAI sui compound (intent copre tutti gli oggetti)."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import route_disambiguation as rd  # noqa: E402


class _I:
    def __init__(self, acts, object=""): self.actions = acts; self.object = object


class TestDetect(unittest.TestCase):
    def test_compound_not_ambiguous(self):
        # «manda mail e crea evento» = 2 azioni distinte → NON ambiguo
        i = _I([{"verb": "send", "object": "messages"},
                {"verb": "create", "object": "events"}])
        self.assertIsNone(rd.detect_object_ambiguity(
            "manda una mail e crea un evento", i))

    def test_compound_read_both(self):
        i = _I([{"verb": "read", "object": "messages"},
                {"verb": "read", "object": "files"}])
        self.assertIsNone(rd.detect_object_ambiguity(
            "leggi le mie mail e i file pdf", i))

    def test_ambiguous_intent_dropped_object(self):
        i = _I([{"verb": "read", "object": "messages"}])
        r = rd.detect_object_ambiguity("controlla la posta e i documenti", i)
        self.assertIsNotNone(r)
        self.assertIn("files", r)
        self.assertIn("messages", r)

    def test_single_object_never(self):
        i = _I([{"verb": "find", "object": "messages"}])
        self.assertIsNone(rd.detect_object_ambiguity(
            "cerca le email da Anthropic", i))

    def test_destination_argument_not_ambiguous(self):
        # «sposta le email ... nella cartella Spam»: «cartella» = destinazione
        # (dirs, score lessicale debole), non oggetto in gara. L'intent
        # (move/messages, piu' forte: email+mailbox) ha gia' deciso la clausola
        # → NON ambiguo. Regola forza-relativa NLU-first, nessun hardcoding di
        # preposizioni. (bug live turno 2e7916f0, 23/6/2026)
        # FORMA PROD REALE: query mono-clausola → actions=[] (popolato solo sui
        # compound), object='messages' risolto. Il gate deve reggere QUI.
        i = _I([], object="messages")
        r = rd.detect_object_ambiguity(
            "sposta le email di spam della mailbox knowcastle "
            "nella cartella Spam", i)
        self.assertIsNone(r)

    def test_no_hints_never(self):
        self.assertIsNone(rd.detect_object_ambiguity("che ore sono", None))

    def test_form_shape(self):
        f = rd.build_disambiguation_form("q", ["messages", "files"])
        self.assertEqual(f["decision"], "needs_inputs")
        ni = f["needs_inputs"]
        self.assertEqual(ni["on_complete"]["type"], "rerun_query_disambiguated")
        self.assertEqual(ni["on_complete"]["query"], "q")
        choices = ni["dialog"][0]["schema"]["choices"]
        self.assertEqual([c["value"] for c in choices], ["messages", "files"])


if __name__ == "__main__":
    unittest.main()

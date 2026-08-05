"""test_route_disambiguation — §2.11 routing: form di disambiguazione su query
ambigua sull'oggetto, MAI sui compound (intent copre tutti gli oggetti)."""
from __future__ import annotations
import sys, unittest
from pathlib import Path
import route_disambiguation as rd  # noqa: E402


class _I:
    def __init__(self, acts, object=""): self.actions = acts; self.object = object


def _classify(value):
    return lambda *_args, **_kwargs: value


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
        r = rd.detect_object_ambiguity(
            "controlla la posta e i documenti", i,
            llm_call=_classify("AMBIGUOUS"))
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
            "nella cartella Spam", i,
            llm_call=_classify("SINGLE_TARGET"))
        self.assertIsNone(r)

    def test_multiple_operations_do_not_force_one_global_object(self):
        # Regressione turn:60c1e2d9: i nomi file/directory/immagini sono
        # bersagli di operazioni diverse, non alternative per un solo slot.
        i = _I([], object="images")
        r = rd.detect_object_ambiguity(
            "controlla la directory Immagini. sul server Metnos conta i file "
            "e le directory. trova immagini duplicate",
            i, llm_call=_classify("MULTI_TARGET"))
        self.assertIsNone(r)

    def test_classifier_failure_declines_instead_of_interrupting(self):
        i = _I([], object="messages")
        self.assertIsNone(rd.detect_object_ambiguity(
            "controlla la posta e i documenti", i,
            llm_call=lambda *_args, **_kwargs: "not-a-label"))

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

    def test_form_replays_original_query_after_structural_normalization(self):
        f = rd.build_disambiguation_form(
            "conta i file e trova immagini duplicate",
            ["files", "images"],
            replay_query=(
                "controlla la directory Immagini. sul server Metnos conta "
                "i file e le directory. trova immagini duplicate"
            ),
        )
        self.assertEqual(
            f["needs_inputs"]["on_complete"]["query"],
            "controlla la directory Immagini. sul server Metnos conta "
            "i file e le directory. trova immagini duplicate",
        )


if __name__ == "__main__":
    unittest.main()

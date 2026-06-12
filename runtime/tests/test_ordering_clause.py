"""Test clausola «ordina/raggruppa per X» end-to-end (12/6/2026).

Bug live T38/T39: «controlla le mailbox ... ORDINATE PER MAILBOX» produceva
lo stesso piano (e lo stesso output, raggruppato per tema) della query base.
Copertura sui due fronti:
  1. ordering_clause: detection deterministica IT+EN, risoluzione
     chiave-utente → campo reale, normalizzazione del Framework
     (iniezione sort_entries + group_by su describe, rinumerazione,
     idempotenza, no-op senza clausola).
  2. describe_entries: la direttiva group_by vince sul raggruppamento
     intrinseco (sezioni deterministiche nel prompt).
  3. executor.is_query_specific: i piani con marker _ordering_clause
     sono 0a-only.
  4. sort_entries (executor): risoluzione chiave-utente («mailbox» →
     account).

Deterministici §7.9, nessun LLM (call_llm mockato).
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from ordering_clause import (  # noqa: E402
    apply_to_framework, detect, resolve_field, ORDERING_MARKER,
)
from engine.types import Framework  # noqa: E402
from engine.executor import is_query_specific  # noqa: E402


_MAIL_ENTRIES = [
    {"uid": "1", "account": "metnos_system", "from": "a@x.it",
     "subject": "alpha", "date": "2026-06-12T08:00", "size": 100},
    {"uid": "2", "account": "knowcastle", "from": "b@y.it",
     "subject": "beta", "date": "2026-06-12T09:00", "size": 300},
    {"uid": "3", "account": "metnos_system", "from": "c@z.it",
     "subject": "gamma", "date": "2026-06-12T10:00", "size": 200},
]


class TestDetect(unittest.TestCase):

    def test_sort_clause_it(self):
        c = detect("controlla tutte le mie mailbox  ultime 24 ore "
                   "ordinate per mailbox")
        self.assertEqual(c, {"mode": "sort", "key_text": "mailbox",
                             "desc": False})

    def test_group_clause_it(self):
        c = detect("raggruppa le mail per mittente")
        self.assertEqual(c["mode"], "group")
        self.assertEqual(c["key_text"], "mittente")

    def test_gap_and_desc(self):
        c = detect("ordina i file per dimensione decrescente")
        self.assertEqual(c, {"mode": "sort", "key_text": "dimensione",
                             "desc": True})

    def test_en_sorted_by(self):
        c = detect("list my emails sorted by size descending")
        self.assertEqual(c, {"mode": "sort", "key_text": "size",
                             "desc": True})

    def test_en_grouped_by(self):
        c = detect("show events grouped by day")
        self.assertEqual(c["key_text"], "day")

    def test_in_ordine_di(self):
        c = detect("metti i contatti in ordine di nome")
        self.assertEqual(c["key_text"], "nome")

    def test_no_clause(self):
        self.assertIsNone(detect("controlla tutte le mie mailbox "
                                 "ultime 24 ore"))
        # «per favore/cortesia» non è una chiave
        self.assertIsNone(detect("ordina una pizza per favore"))
        self.assertIsNone(detect("controlla la posta per cortesia"))
        self.assertIsNone(detect(""))

    def test_key_stops_at_preposition(self):
        c = detect("mail ordinate per data di arrivo")
        self.assertEqual(c["key_text"], "data")


class TestResolveField(unittest.TestCase):

    def test_synonym_families(self):
        self.assertEqual(resolve_field("mailbox", _MAIL_ENTRIES), "account")
        self.assertEqual(resolve_field("mittente", _MAIL_ENTRIES), "from")
        self.assertEqual(resolve_field("dimensione", _MAIL_ENTRIES), "size")
        self.assertEqual(resolve_field("oggetto", _MAIL_ENTRIES), "subject")

    def test_exact_match_wins(self):
        self.assertEqual(resolve_field("size", _MAIL_ENTRIES), "size")
        self.assertEqual(resolve_field("Account", _MAIL_ENTRIES), "account")

    def test_unresolvable_concept(self):
        # «tema» è un concetto, non un campo: None (describe degrada a
        # raggruppamento concettuale, comportamento intrinseco corretto).
        self.assertIsNone(resolve_field("tema", _MAIL_ENTRIES))

    def test_empty_inputs(self):
        self.assertIsNone(resolve_field("", _MAIL_ENTRIES))
        self.assertIsNone(resolve_field("mailbox", []))
        self.assertIsNone(resolve_field("mailbox", ["not-a-dict"]))


def _mail_framework() -> Framework:
    return Framework.from_dict({
        "steps": [
            {"tool": "read_messages",
             "args": {"account": "all", "time_window": "last-24h"}},
            {"tool": "describe_entries",
             "args": {"from_step": 1, "style": "by_relevance"}},
            {"tool": "final_answer", "args": {}},
        ],
        "final_message": "${step2.summary}",
    })


_CAT = {"read_messages", "describe_entries", "sort_entries", "final_answer"}


class TestApplyToFramework(unittest.TestCase):

    def test_injects_sort_and_group_by(self):
        fw = apply_to_framework(
            _mail_framework(),
            "controlla tutte le mie mailbox ultime 24 ore ordinate "
            "per mailbox", catalog_names=_CAT)
        tools = [s.tool for s in fw.steps]
        self.assertEqual(tools, ["read_messages", "sort_entries",
                                 "describe_entries", "final_answer"])
        sort_step = fw.steps[1]
        self.assertEqual(sort_step.args["by"], "mailbox")
        self.assertTrue(sort_step.args[ORDERING_MARKER])
        self.assertEqual(sort_step.args["from_step"], 1)
        desc_step = fw.steps[2]
        self.assertEqual(desc_step.args["from_step"], 2)  # legge dal sort
        self.assertEqual(desc_step.args["group_by"], "mailbox")
        # final_message rinumerato: describe è ora lo step 3
        self.assertEqual(fw.final_message, "${step3.summary}")

    def test_idempotent(self):
        q = ("controlla tutte le mie mailbox ultime 24 ore ordinate "
             "per mailbox")
        fw1 = apply_to_framework(_mail_framework(), q, catalog_names=_CAT)
        fw2 = apply_to_framework(fw1, q, catalog_names=_CAT)
        self.assertIs(fw2, fw1)

    def test_noop_without_clause(self):
        fw = _mail_framework()
        out = apply_to_framework(
            fw, "controlla tutte le mie mailbox ultime 24 ore",
            catalog_names=_CAT)
        self.assertIs(out, fw)

    def test_respects_explicit_sort_by_proposer(self):
        fw = Framework.from_dict({
            "steps": [
                {"tool": "find_files", "args": {"glob": "*.log"}},
                {"tool": "sort_entries",
                 "args": {"from_step": 1, "by": "size", "desc": True}},
                {"tool": "final_answer", "args": {}},
            ],
            "final_message": "",
        })
        out = apply_to_framework(
            fw, "trova i log ordinati per dimensione", catalog_names=_CAT
            | {"find_files"})
        tools = [s.tool for s in out.steps]
        # nessuna doppia iniezione
        self.assertEqual(tools.count("sort_entries"), 1)

    def test_no_injection_without_sort_in_catalog(self):
        out = apply_to_framework(
            _mail_framework(),
            "mail ordinate per mailbox",
            catalog_names={"read_messages", "describe_entries",
                           "final_answer"})
        tools = [s.tool for s in out.steps]
        self.assertNotIn("sort_entries", tools)
        # ma describe riflette comunque la chiave
        self.assertEqual(out.steps[1].args["group_by"], "mailbox")

    def test_no_producer_no_injection(self):
        fw = Framework.from_dict({
            "steps": [{"tool": "final_answer", "args": {}}],
            "final_message": "ciao",
        })
        out = apply_to_framework(fw, "ordinati per data", catalog_names=_CAT)
        self.assertIs(out, fw)

    def test_inject_before_final_answer_without_describe(self):
        fw = Framework.from_dict({
            "steps": [
                {"tool": "find_files", "args": {"glob": "*.pdf"}},
                {"tool": "final_answer", "args": {}},
            ],
            "final_message": "${step1.entries}",
        })
        out = apply_to_framework(
            fw, "trova i pdf ordinati per dimensione decrescente",
            catalog_names=_CAT | {"find_files"})
        tools = [s.tool for s in out.steps]
        self.assertEqual(tools, ["find_files", "sort_entries",
                                 "final_answer"])
        sort_step = out.steps[1]
        self.assertTrue(sort_step.args["desc"])
        # senza from_step (auto-wire): condizionato a entries non vuote
        self.assertNotIn("from_step", sort_step.args)
        self.assertTrue(sort_step.if_prev_entries_nonempty)
        # ${step1.entries} resta step1 (il produttore non si sposta)
        self.assertEqual(out.final_message, "${step1.entries}")

    def test_marker_makes_plan_query_specific(self):
        fw = apply_to_framework(
            _mail_framework(), "mail ordinate per mailbox",
            catalog_names=_CAT)
        self.assertTrue(is_query_specific(json.dumps(fw.to_dict())))


class TestDescribeGroupDirective(unittest.TestCase):
    """describe_entries con group_by: la direttiva (sezioni deterministiche)
    entra nel prompt e vince sul raggruppamento intrinseco."""

    def setUp(self):
        self._prompts: list[str] = []

        def _fake_call_llm(entries, prompt, *, tier="middle",
                           max_tokens=600, **kwargs):
            self._prompts.append(prompt)
            return ("Riassunto.", {"in_tokens": 0, "out_tokens": 0,
                                   "latency_ms": 1})

        self._p1 = mock.patch("describe_entries.call_llm",
                              side_effect=_fake_call_llm)
        self._p1.start()
        self._p2 = mock.patch("describe_entries.prompt_loader.get",
                              return_value="STUB PROMPT")
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()

    def test_group_by_directive_sections_in_order(self):
        from describe_entries import handle_describe_entries
        res = handle_describe_entries({
            "entries": list(_MAIL_ENTRIES),
            "style": "by_importance",
            "group_by": "mailbox",
        })
        self.assertTrue(res["ok"])
        self.assertEqual(res["group_by"], "mailbox")
        prompt = self._prompts[-1]
        self.assertIn("RAGGRUPPAMENTO RICHIESTO DALL'UTENTE", prompt)
        self.assertIn("'account'", prompt)
        # sezioni nell'ordine delle entries, con conteggi
        self.assertIn("'metnos_system' (2)", prompt)
        self.assertIn("'knowcastle' (1)", prompt)
        self.assertLess(prompt.index("'metnos_system' (2)"),
                        prompt.index("'knowcastle' (1)"))

    def test_no_group_by_no_directive(self):
        from describe_entries import handle_describe_entries
        res = handle_describe_entries({
            "entries": list(_MAIL_ENTRIES),
            "style": "by_importance",
        })
        self.assertTrue(res["ok"])
        self.assertNotIn("group_by", res)
        self.assertNotIn("RAGGRUPPAMENTO RICHIESTO", self._prompts[-1])

    def test_unresolvable_key_conceptual_directive(self):
        from describe_entries import handle_describe_entries
        res = handle_describe_entries({
            "entries": list(_MAIL_ENTRIES),
            "style": "by_importance",
            "group_by": "tema",
        })
        self.assertTrue(res["ok"])
        self.assertIn("'tema'", self._prompts[-1])

    def test_high_cardinality_key_ordered_directive(self):
        from describe_entries import handle_describe_entries
        res = handle_describe_entries({
            "entries": list(_MAIL_ENTRIES),
            "style": "by_importance",
            "group_by": "oggetto",  # subject: un valore per entry
        })
        self.assertTrue(res["ok"])
        self.assertIn("ORDINAMENTO RICHIESTO DALL'UTENTE",
                      self._prompts[-1])


class TestSortEntriesKeyResolution(unittest.TestCase):
    """sort_entries (executor): «mailbox» risolto su `account` (§2.4)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = (_RUNTIME.parent / "executors" / "sort_entries"
                / "sort_entries.py")
        os.environ.setdefault("METNOS_RUNTIME", str(_RUNTIME))
        spec = importlib.util.spec_from_file_location("sort_entries_x", path)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_user_term_resolved(self):
        res = self.mod.invoke({"entries": list(_MAIL_ENTRIES),
                               "by": "mailbox"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["sorted_by"], "account")
        self.assertEqual(res["requested_by"], "mailbox")
        self.assertEqual([e["account"] for e in res["entries"]],
                         ["knowcastle", "metnos_system", "metnos_system"])

    def test_real_field_untouched(self):
        res = self.mod.invoke({"entries": list(_MAIL_ENTRIES),
                               "by": "size", "desc": True})
        self.assertTrue(res["ok"])
        self.assertEqual(res["sorted_by"], "size")
        self.assertNotIn("requested_by", res)
        self.assertEqual([e["size"] for e in res["entries"]],
                         [300, 200, 100])

    def test_unresolvable_key_keeps_order(self):
        res = self.mod.invoke({"entries": list(_MAIL_ENTRIES),
                               "by": "argomento_inesistente"})
        self.assertTrue(res["ok"])
        # nessun campo → tutte in coda nell'ordine originale (sort stabile)
        self.assertEqual([e["uid"] for e in res["entries"]],
                         ["1", "2", "3"])


if __name__ == "__main__":
    unittest.main()

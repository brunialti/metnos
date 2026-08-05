"""test_align_foreign_object — guard §7.9 _align_framework_objects, pass
"oggetto estraneo a tutto l'intent" (bug live 21/6: fatture Anthropic).

Il proposer-LLM a volte compone `read_urls_html` (inventando un URL) per una
clausola che l'intent ha decomposto come {find, messages} — verbo `read` non fra
i verbi-intent (find/extract/create) e oggetto `urls` MAI richiesto. Il guard
riallinea al produttore corretto (read_messages). Intent-driven: NON tocca le
query che chiedono davvero urls.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")
from engine import dispatch  # noqa: E402
from engine.types import Framework, StepSpec  # noqa: E402


class _Cat:
    def __init__(self, name): self.name = name


_CATALOG = [_Cat(n) for n in (
    "read_messages", "read_urls_html", "get_urls", "find_files",
    "create_files_spreadsheet", "filter_entries", "final_answer")]


def _align(actions, tools):
    class _I:
        pass
    _I.actions = actions
    fw = Framework(steps=[StepSpec(tool=t, args={}) for t in tools])
    out = dispatch._align_framework_objects(fw, _I(), _CATALOG)
    return [s.tool for s in out.steps]


class TestAlignForeignObject(unittest.TestCase):
    def test_read_urls_realigned_to_messages(self):
        # {find,messages}+{create,files} ma proposer sceglie read_urls_html.
        out = _align(
            [{"verb": "find", "object": "messages"},
             {"verb": "create", "object": "files"}],
            ["read_urls_html", "create_files_spreadsheet", "final_answer"])
        self.assertEqual(out[0], "read_messages")

    def test_legit_urls_query_untouched(self):
        # L'intent CHIEDE urls → read_urls_html deve restare (no falso positivo).
        out = _align(
            [{"verb": "read", "object": "urls"},
             {"verb": "create", "object": "files"}],
            ["read_urls_html", "create_files_spreadsheet", "final_answer"])
        self.assertEqual(out[0], "read_urls_html")

    def test_legit_producer_untouched(self):
        out = _align([{"verb": "find", "object": "files"}],
                     ["find_files", "final_answer"])
        self.assertEqual(out[0], "find_files")

    def test_noop_without_actions(self):
        class _I:
            actions = []
        fw = Framework(steps=[StepSpec(tool="read_urls_html", args={})])
        out = dispatch._align_framework_objects(fw, _I(), _CATALOG)
        self.assertEqual(out.steps[0].tool, "read_urls_html")


if __name__ == "__main__":
    unittest.main()

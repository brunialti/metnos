"""test_resolve_store_field_refs — resolver §7.9 dei riferimenti-campo store.

Bug live 20/6/2026 (FASE 3 publish): il proposer compone la pipeline corretta
`find_entries(store) -> filter -> send_messages_github -> write_entries` ma NON
vede lo schema dello store, quindi sbaglia i riferimenti-campo:
  - `target_template="issue:{number}"`  (la colonna è `issue_number`)
  - `body_template="<letterale>"`        (dovrebbe essere `{accepted_reply}`)
  - `repo="${FILLER:repo}"`              (la entry porta già `repo`)
  - `write_entries(key=["number"])`      (la chiave d'upsert è la PK)

`_resolve_store_field_refs` ricuce questi riferimenti contro lo SCHEMA REALE
dello store (registry), deterministico, no LLM, no-op se nessun produttore
store-read registrato a monte.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store as _store  # noqa: E402
from store import Schema, TEXT, INT  # noqa: E402
from backends.datastore.memory import MemoryBackend  # noqa: E402
from engine.dispatch import _resolve_store_field_refs  # noqa: E402

_SCHEMA = Schema(
    "issue_qa",
    {"repo": TEXT, "issue_number": INT, "title": TEXT, "status": TEXT,
     "draft_reply": TEXT, "accepted_reply": TEXT, "posted_at": INT},
    primary_key=("repo", "issue_number"),
)
_STORE = "test_issue_qa"


def _step(tool, **args):
    return NS(tool=tool, args=args)


class TestResolveStoreFieldRefs(unittest.TestCase):
    def setUp(self):
        _store.register(_SCHEMA, name=_STORE, backend=MemoryBackend())

    def tearDown(self):
        _store.unregister(_STORE)

    def _publish_fw(self):
        return NS(steps=[
            _step("find_entries", repo="${FILLER:repo}", state="open",
                  store=_STORE),
            _step("filter_entries", from_step=1, where_field="status",
                  where_value="answered"),
            _step("send_messages_github", repo="${FILLER:repo}",
                  target_template="issue:{number}",
                  body_template="Questa issue è stata processata.", from_step=2),
            _step("write_entries", store=_STORE, from_step=2, key=["number"],
                  set_fields={"status": "posted"}),
            _step("final_answer"),
        ])

    def test_publish_pipeline_field_refs_repaired(self):
        fw = self._publish_fw()
        _resolve_store_field_refs(fw)
        send = fw.steps[2].args
        write = fw.steps[3].args
        # target: number → issue_number (colonna reale, alias univoco _number)
        self.assertEqual(send["target_template"], "issue:{issue_number}")
        # body letterale → colonna-risposta accettata
        self.assertEqual(send["body_template"], "{accepted_reply}")
        # repo ${FILLER:..} su colonna → droppato (l'executor lo riempie per-entry)
        self.assertNotIn("repo", send)
        # write_entries.key → primary_key dello store
        self.assertEqual(write["key"], ["repo", "issue_number"])

    def test_body_phantom_placeholder_mapped_to_reply(self):
        """body_template con placeholder FANTASMA ({body}, campo inesistente) →
        colonna-risposta (altrimenti verrebbe postato il letterale '{body}')."""
        fw = NS(steps=[
            _step("find_entries", store=_STORE),
            _step("send_messages_github", target_template="issue:{number}",
                  body_template="{body}", from_step=1),
            _step("final_answer"),
        ])
        _resolve_store_field_refs(fw)
        self.assertEqual(fw.steps[1].args["body_template"], "{accepted_reply}")

    def test_noop_without_registered_store(self):
        """Pipeline senza produttore store-read registrato → invariata."""
        fw = NS(steps=[
            _step("find_files", path="/tmp"),
            _step("send_messages_github", target_template="issue:{number}",
                  body_template="hi", from_step=1),
            _step("final_answer"),
        ])
        _resolve_store_field_refs(fw)
        self.assertEqual(fw.steps[1].args["target_template"], "issue:{number}")
        self.assertEqual(fw.steps[1].args["body_template"], "hi")

    def test_valid_placeholder_untouched(self):
        """Un placeholder GIÀ valido (colonna reale) non viene riscritto."""
        fw = NS(steps=[
            _step("find_entries", store=_STORE),
            _step("send_messages_github",
                  target_template="issue:{issue_number}",
                  body_template="{accepted_reply}", from_step=1),
            _step("final_answer"),
        ])
        _resolve_store_field_refs(fw)
        send = fw.steps[1].args
        self.assertEqual(send["target_template"], "issue:{issue_number}")
        self.assertEqual(send["body_template"], "{accepted_reply}")

    def test_traces_through_passthrough_chain(self):
        """from_step risale filter/sort fino al produttore store-read."""
        fw = NS(steps=[
            _step("find_entries", store=_STORE),
            _step("filter_entries", from_step=1, where_field="status",
                  where_value="answered"),
            _step("sort_entries", from_step=2, by="issue_number"),
            _step("send_messages_github", target_template="issue:{number}",
                  body_template="x", from_step=3),
            _step("final_answer"),
        ])
        _resolve_store_field_refs(fw)
        self.assertEqual(fw.steps[3].args["target_template"],
                         "issue:{issue_number}")


if __name__ == "__main__":
    unittest.main()

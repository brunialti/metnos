"""test_mail_delete_to_trash — §5 (bug live 7c4390f1, 23/6): in Metnos NON
esiste `delete_messages`; cancellare mail = `move_messages(dst_folder="Trash")`.
Il proposer ripiega su `delete_entries(store="messages")` (store inesistente →
fallisce). Il guard `_route_mail_delete_to_trash` lo riscrive, propagando
from_step + account dal produttore mail.
"""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path

os.environ.setdefault("METNOS_ENGINE", "v3")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import dispatch as D            # noqa: E402
from engine.types import Framework, StepSpec  # noqa: E402

_CAT = [type("E", (), {"name": n})() for n in
        ("read_messages", "find_messages", "move_messages", "delete_entries",
         "filter_entries")]


def _fw(spec):
    return Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in spec])


class TestMailDeleteToTrash(unittest.TestCase):
    def test_store_messages_to_move_trash_propaga_account(self):
        fw = _fw([("read_messages", {"account": "knowcastle"}),
                  ("delete_entries", {"store": "messages", "from_step": 1})])
        s = D._route_mail_delete_to_trash(fw, _CAT).steps[1]
        self.assertEqual(s.tool, "move_messages")
        self.assertEqual(s.args["dst_folder"], "Trash")
        self.assertEqual(s.args["from_step"], 1)
        self.assertEqual(s.args["account"], "knowcastle")  # propagato dal read
        self.assertTrue(s.if_prev_entries_nonempty)        # mutating: mai su 0

    def test_consuma_mail_senza_store(self):
        fw = _fw([("read_messages", {}), ("delete_entries", {"from_step": 1})])
        s = D._route_mail_delete_to_trash(fw, _CAT).steps[1]
        self.assertEqual(s.tool, "move_messages")
        self.assertEqual(s.args["dst_folder"], "Trash")
        self.assertEqual(s.args["from_step"], 1)

    def test_idempotente(self):
        fw = _fw([("read_messages", {}),
                  ("delete_entries", {"store": "messages", "from_step": 1})])
        o1 = D._route_mail_delete_to_trash(fw, _CAT)
        o2 = D._route_mail_delete_to_trash(o1, _CAT)
        self.assertEqual(o2.steps[1].tool, "move_messages")
        self.assertEqual(o2.steps[1].args["dst_folder"], "Trash")

    def test_noop_store_reale_non_mail(self):
        # delete_entries su uno store VERO (github_issue_qa) → NON toccare
        fw = _fw([("delete_entries", {"store": "github_issue_qa"})])
        self.assertEqual(
            D._route_mail_delete_to_trash(fw, _CAT).steps[0].tool, "delete_entries")

    def test_noop_senza_move_messages_nel_catalogo(self):
        cat = [type("E", (), {"name": "read_messages"})()]
        fw = _fw([("read_messages", {}),
                  ("delete_entries", {"store": "messages", "from_step": 1})])
        self.assertEqual(
            D._route_mail_delete_to_trash(fw, cat).steps[1].tool, "delete_entries")


if __name__ == "__main__":
    unittest.main()

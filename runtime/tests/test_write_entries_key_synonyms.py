"""write_entries: key robusta al confine NL→determinismo (§2.4, bug live 6/7).

Il task schedulato github (every_30m) era rotto in 2 modi:
 1. piano AVVELENATO in cache L0/L1 (find_entries al posto di
    find_issues_github — mai interrogato GitHub) → cache purgata a mano;
 2. il replan fresco falliva su write_entries: il proposer sceglie
    key=["number"] ma la colonna è `issue_number` («no such column»), e
    comunque su sqlite l'upsert ON CONFLICT esige la PK ESATTA.

Fix deterministici in handle_write_entries:
 - key non-colonna → rimappata via FIELD_SYNONYMS (mappa chiusa §7.9;
   number ↔ issue_number documentati dal contratto ADR 0141);
 - key sottoinsieme proprio della PK → completata alla PK (unica ON
   CONFLICT possibile; stesso intento di dedup).

Run: `python3 -m pytest runtime/tests/test_write_entries_key_synonyms.py -xvs`.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class WriteEntriesKeySynonymTests(unittest.TestCase):
    def setUp(self):
        import store as S
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_keysyn_"))
        self.name = f"ks_test_{id(self)}"
        S.register(S.Schema(
            "ks", {"repo": "text", "issue_number": "int",
                   "title": "text", "status": "text"},
            primary_key=("repo", "issue_number")),
            name=self.name, path=self.tmp / "ks.sqlite")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, key, status="new"):
        from store_entries import handle_write_entries
        return handle_write_entries({
            "store": self.name, "key": key,
            "entries": [{"repo": "o/r", "issue_number": 53, "number": 53,
                         "title": "t", "status": status}]})

    def test_synonym_key_number_remapped_and_pk_completed(self):
        out = self._write(["number"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["n_new"], 1)
        # upsert, non duplicato: secondo write stesso record → n_new 0
        out2 = self._write(["number"], status="closed")
        self.assertTrue(out2["ok"], out2)
        self.assertEqual(out2["n_new"], 0)

    def test_partial_pk_key_completed(self):
        out = self._write(["issue_number"])
        self.assertTrue(out["ok"], out)

    def test_exact_pk_key_untouched(self):
        out = self._write(["repo", "issue_number"])
        self.assertTrue(out["ok"], out)

    def test_unknown_key_still_honest_error(self):
        # campo inesistente senza sinonimo-colonna → errore onesto, non magia
        from store_entries import handle_write_entries
        out = handle_write_entries({
            "store": self.name, "key": ["campo_fantasma"],
            "entries": [{"repo": "o/r", "issue_number": 1, "title": "x"}]})
        self.assertFalse(out.get("ok", True))


if __name__ == "__main__":
    unittest.main()

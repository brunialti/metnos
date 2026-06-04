"""Anti-regressione routing EXIF/foto-metadata (#1, deterministico §7.9).

get_files (azione_oggetto = get+files, §2.2) e' il tool EXIF/dates/place. Deve
essere (a) nei primary pool di `files` E `images`, e (b) il TOP candidate di
rank_with_intent per intent {verb:get, object:files} — davanti a find_files
(che e' verb=find, filesystem). La scelta finale resta del Proposer (guidata
dalla core-rule §5), ma il pool/ranking dev'essere corretto a monte.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prefilter import _OBJECT_PRIMARY_TOOLS, rank_with_intent
from loader import load_catalog


class TestGetFilesInPools(unittest.TestCase):
    def test_get_files_primary_for_files_and_images(self):
        self.assertIn("get_files", _OBJECT_PRIMARY_TOOLS["files"])
        self.assertIn("get_files", _OBJECT_PRIMARY_TOOLS["images"])
        # find_files resta (path-based) ma get_files c'e' accanto.
        self.assertIn("find_files", _OBJECT_PRIMARY_TOOLS["files"])


class TestRankGetFilesTop(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        names = {getattr(e, "name", None) for e in self.catalog}
        if "get_files" not in names or "find_files" not in names:
            self.skipTest("catalog privo di get_files/find_files")

    def test_get_files_is_top_for_get_files_intent(self):
        ranked = rank_with_intent(
            "quando e' stata scattata /tmp/x.jpg", self.catalog,
            {"verb": "get", "object": "files"}, k=3)
        self.assertIsNotNone(ranked)
        names = [e.name for e in ranked]
        self.assertEqual(names[0], "get_files",
                         f"get_files non e' top: {names}")

    def test_find_files_still_top_for_find_intent(self):
        # Non-regressione: una query di ricerca files resta su find_files.
        ranked = rank_with_intent(
            "trova i pdf in /tmp", self.catalog,
            {"verb": "find", "object": "files"}, k=3)
        self.assertIsNotNone(ranked)
        self.assertEqual(ranked[0].name, "find_files")


if __name__ == "__main__":
    unittest.main()

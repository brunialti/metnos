"""Test runtime/executor_helpers.py — normalizzazione paths/urls (§2.4).

Pattern §2.4 CLAUDE.md: il PLANNER LLM occasionalmente confonde `urls` con
`paths` su argomenti generici. `normalize_paths_urls` corregge sistematica-
mente PRIMA che l'executor consumi gli args. Idempotente §7.9.

Run: python3 -m pytest runtime/tests/test_executor_helpers.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


class TestNormalizePathsUrls(unittest.TestCase):
    def test_path_in_urls_moves_to_paths(self):
        from executor_helpers import normalize_paths_urls
        out = normalize_paths_urls({
            "urls": ["/home/user/foto.jpg"], "max_results": 10,
        })
        self.assertEqual(out["paths"], ["/home/user/foto.jpg"])
        self.assertEqual(out["urls"], [])
        self.assertEqual(out["max_results"], 10)

    def test_http_url_in_paths_moves_to_urls(self):
        from executor_helpers import normalize_paths_urls
        out = normalize_paths_urls({
            "paths": ["https://example.com/foo.jpg"], "urls": [],
        })
        self.assertEqual(out["urls"], ["https://example.com/foo.jpg"])
        self.assertEqual(out["paths"], [])

    def test_mixed_normalization(self):
        from executor_helpers import normalize_paths_urls
        out = normalize_paths_urls({
            "paths": ["/local/a.jpg", "https://b.com/img.png"],
            "urls": ["/local/c.jpg", "https://d.com/img.png"],
        })
        self.assertEqual(set(out["paths"]),
                          {"/local/a.jpg", "/local/c.jpg"})
        self.assertEqual(set(out["urls"]),
                          {"https://b.com/img.png", "https://d.com/img.png"})

    def test_already_normalized_idempotent(self):
        from executor_helpers import normalize_paths_urls
        args = {"paths": ["/x.jpg"], "urls": ["https://y/img"]}
        out1 = normalize_paths_urls(args)
        out2 = normalize_paths_urls(out1)
        self.assertEqual(out1, out2)
        self.assertEqual(out1["paths"], ["/x.jpg"])
        self.assertEqual(out1["urls"], ["https://y/img"])

    def test_no_paths_no_urls_passthrough(self):
        from executor_helpers import normalize_paths_urls
        args = {"query": "foo", "max_results": 5}
        out = normalize_paths_urls(args)
        self.assertEqual(out, args)

    def test_non_dict_args_passthrough(self):
        from executor_helpers import normalize_paths_urls
        self.assertEqual(normalize_paths_urls(None), None)
        self.assertEqual(normalize_paths_urls("foo"), "foo")
        self.assertEqual(normalize_paths_urls([]), [])

    def test_non_list_paths_passthrough(self):
        from executor_helpers import normalize_paths_urls
        args = {"paths": "single.jpg"}
        out = normalize_paths_urls(args)
        # paths non-list non viene normalizzato (executor lo gestira').
        self.assertEqual(out, args)

    def test_other_schemas_not_treated_as_paths(self):
        from executor_helpers import normalize_paths_urls
        # file:// e ftp:// non sono path-like (sono URL veri ma non http)
        # NON spostati in paths.
        out = normalize_paths_urls({
            "urls": ["file:///etc/hosts", "ftp://ftp.example.com/a.jpg"],
        })
        # entrambi restano in urls
        self.assertEqual(out["urls"],
                          ["file:///etc/hosts", "ftp://ftp.example.com/a.jpg"])

    def test_does_not_mutate_input(self):
        from executor_helpers import normalize_paths_urls
        args = {"urls": ["/local.jpg"]}
        _ = normalize_paths_urls(args)
        # input non mutato
        self.assertEqual(args, {"urls": ["/local.jpg"]})


if __name__ == "__main__":
    unittest.main()

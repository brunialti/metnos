"""test_mail_read_caps — guard anti-drift sui limiti di lettura mail.

Il default OPERATIVO di max_results/max_total nasce nel backend email_metnos
(FONTE UNICA: il runtime non inietta il default del manifest, read_messages passa
gli args grezzi a backend.read). Il manifest di read_messages DOCUMENTA gli stessi
valori per l'LLM. Questo test impedisce che le due dichiarazioni divergano
(richiesta Roberto 21/6: «istanze diverse dello stesso backend non devono avere
valori di default diversi»).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

_ROOT = Path(__file__).resolve().parents[3]
from backends.messages import email_metnos as _em  # noqa: E402

_MANIFEST = _ROOT / "executors" / "read_messages" / "manifest.toml"


class TestMailReadCaps(unittest.TestCase):
    def setUp(self):
        self.man = tomllib.loads(_MANIFEST.read_text(encoding="utf-8"))
        self.props = self.man["args"]["properties"]

    def test_max_results_default_matches_backend(self):
        self.assertEqual(self.props["max_results"]["default"],
                         _em._DEFAULT_MAX_RESULTS,
                         "manifest read_messages.max_results.default deve combaciare "
                         "con email_metnos._DEFAULT_MAX_RESULTS (fonte unica)")

    def test_max_total_default_matches_backend(self):
        self.assertEqual(self.props["max_total"]["default"],
                         _em._DEFAULT_MAX_TOTAL,
                         "manifest read_messages.max_total.default deve combaciare "
                         "con email_metnos._DEFAULT_MAX_TOTAL")

    def test_default_within_cap(self):
        self.assertLessEqual(_em._DEFAULT_MAX_RESULTS, _em._MAX_RESULTS_CAP)
        self.assertLessEqual(_em._DEFAULT_MAX_TOTAL, _em._MAX_TOTAL_CAP)

    def test_manifest_range_covers_default(self):
        # La description dichiara "Range 1..N": N deve coprire il default+cap.
        desc = self.props["max_results"]["description"]["it"]
        m = re.search(r"1\.\.(\d+)", desc)
        self.assertIsNotNone(m, "max_results.description deve dichiarare 'Range 1..N'")
        self.assertGreaterEqual(int(m.group(1)), _em._DEFAULT_MAX_RESULTS)


if __name__ == "__main__":
    unittest.main()

"""Sonda osservabilità ingresso PLANNER legacy (ADR 0177 M1, scadenza 2026-06-30).

Pin del meccanismo di conteggio persistente: record + count per-trigger, robusto
ai restart (file JSONL), privacy (query non in chiaro — solo len+hash).

NB: test a tempo — rimuovere con la sonda quando il legacy viene eliminato.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class LegacyPlannerProbeTests(unittest.TestCase):

    def setUp(self):
        self._probe = tempfile.mktemp()
        self._orig = os.environ.get("METNOS_LEGACY_PROBE_PATH")
        os.environ["METNOS_LEGACY_PROBE_PATH"] = self._probe
        import importlib
        import legacy_planner_probe
        importlib.reload(legacy_planner_probe)
        self.P = legacy_planner_probe

    def tearDown(self):
        if self._orig is not None:
            os.environ["METNOS_LEGACY_PROBE_PATH"] = self._orig
        else:
            os.environ.pop("METNOS_LEGACY_PROBE_PATH", None)
        try:
            os.unlink(self._probe)
        except OSError:
            pass

    def test_empty_initial(self):
        self.assertEqual(self.P.count_entries(), {"total": 0, "by_trigger": {}})

    def test_record_and_count_by_trigger(self):
        self.P.record_legacy_entry(turn_id="t1", trigger="upload_fallthrough",
                                   query="trova foto")
        self.P.record_legacy_entry(turn_id="t2", trigger="resume_fallthrough",
                                   query="prenota")
        self.P.record_legacy_entry(turn_id="t3", trigger="upload_fallthrough",
                                   query="altre")
        c = self.P.count_entries()
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["by_trigger"]["upload_fallthrough"], 2)
        self.assertEqual(c["by_trigger"]["resume_fallthrough"], 1)

    def test_persists_across_reload(self):
        # Robustezza ai restart: una nuova istanza vede le righe già scritte.
        self.P.record_legacy_entry(turn_id="t1", trigger="legacy_flag",
                                   query="x")
        import importlib
        importlib.reload(self.P)
        self.assertEqual(self.P.count_entries()["total"], 1)

    def test_query_not_stored_plaintext(self):
        # Privacy §7.5: la query NON finisce in chiaro nel file persistente.
        secret = "prenota la riunione segretissima con il dottor Rossi"
        self.P.record_legacy_entry(turn_id="t1", trigger="upload_fallthrough",
                                   query=secret)
        with open(self._probe, encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("segretissima", content)
        self.assertNotIn("Rossi", content)
        # ma la lunghezza è registrata (segnale utile, non sensibile)
        self.assertIn(str(len(secret)), content)

    def test_record_never_raises(self):
        # §2.8: l'osservabilità non deve mai far fallire il turno.
        os.environ["METNOS_LEGACY_PROBE_PATH"] = "/nonexistent_dir/x/y.jsonl"
        import importlib
        importlib.reload(self.P)
        try:
            self.P.record_legacy_entry(turn_id="t", trigger="x", query="q")
        except Exception as ex:  # noqa: BLE001
            self.fail(f"record_legacy_entry raised: {ex}")


if __name__ == "__main__":
    unittest.main()

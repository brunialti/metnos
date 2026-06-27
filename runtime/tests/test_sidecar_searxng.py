"""Test install/sidecar.py — parti deterministiche (no rete, no systemd).

Verifica il contratto del modulo sidecar SearXNG senza clonare nulla:
    - settings.yml: json format presente (path /search?format=json del runtime),
      limiter off (no redis), secret_key non vuoto
    - secret_key: riusato su re-run (non rigenerato → niente sessioni invalidate)
    - registro SIDECARS = SoT (searxng ready; photon/vlm honest not-ready)
    - install() su sidecar non-pronto → outcome onesto 'not_implemented' (§2.8)
    - unit template metnos-searxng: tutti i @PLACEHOLDER@ sostituibili (incl.
      TMPDIR privato, fix collisione cache /tmp)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from install import sidecar  # noqa: E402


class TestSearxngSettings(unittest.TestCase):
    def test_settings_enables_json_and_disables_limiter(self):
        s = sidecar._searxng_settings(8888, "deadbeef")
        # json REQUIRED: il runtime interroga /search?format=json (find_urls)
        self.assertIn("json", s)
        self.assertIn("formats:", s)
        # limiter off → nessuna dipendenza redis per un singolo utente
        self.assertIn("limiter: false", s)
        self.assertIn('secret_key: "deadbeef"', s)
        self.assertIn("port: 8888", s)
        self.assertIn('bind_address: "127.0.0.1"', s)
        self.assertIn("use_default_settings: true", s)

    def test_secret_reused_across_runs(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.yml"
            # nessun file → secret nuovo (64 hex)
            first = sidecar._searxng_secret(p)
            self.assertEqual(len(first), 64)
            p.write_text(sidecar._searxng_settings(8888, first))
            # file presente → STESSO secret (no invalidazione sessioni)
            self.assertEqual(sidecar._searxng_secret(p), first)

    def test_secret_is_random_per_fresh_install(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a = sidecar._searxng_secret(Path(d) / "a.yml")
            b = sidecar._searxng_secret(Path(d) / "b.yml")
            self.assertNotEqual(a, b)


class TestSidecarRegistry(unittest.TestCase):
    def test_registry_is_source_of_truth(self):
        self.assertEqual(set(sidecar.SIDECARS), {"searxng", "photon", "vlm"})
        self.assertTrue(sidecar.SIDECARS["searxng"]["ready"])
        # photon/vlm non ancora spediti: onesti
        self.assertFalse(sidecar.SIDECARS["photon"]["ready"])
        self.assertFalse(sidecar.SIDECARS["vlm"]["ready"])
        for e in sidecar.SIDECARS.values():
            for k in ("label", "size", "desc", "install", "ready"):
                self.assertIn(k, e)

    def test_install_not_ready_is_honest(self):
        # §2.8: un sidecar non implementato NON finge — ritorna not_implemented
        self.assertEqual(sidecar.install("photon"), {"photon": "not_implemented"})
        self.assertEqual(sidecar.install("vlm"), {"vlm": "not_implemented"})

    def test_install_unknown_name(self):
        self.assertEqual(sidecar.install("nope"), {"nope": "unknown"})


class TestUnitTemplate(unittest.TestCase):
    def test_searxng_template_placeholders_all_substituted(self):
        tmpl = (_REPO / "install" / "units" / "metnos-searxng.service.tmpl").read_text()
        repl = {
            "@SEARXNG_VENV@": "/data/sidecars/searxng/venv",
            "@SEARXNG_SRC@": "/data/sidecars/searxng/searxng",
            "@SEARXNG_SETTINGS@": "/cfg/searxng/settings.yml",
            "@SEARXNG_CACHE@": "/data/sidecars/searxng/cache",
        }
        for k, v in repl.items():
            tmpl = tmpl.replace(k, v)
        self.assertNotIn("@", tmpl, "placeholder non sostituito nel unit template")
        # TMPDIR privato presente (fix collisione /tmp/sxng_cache_*.db)
        self.assertIn("TMPDIR=/data/sidecars/searxng/cache", tmpl)
        self.assertIn("ExecStart=/data/sidecars/searxng/venv/bin/python -m searx.webapp", tmpl)


if __name__ == "__main__":
    unittest.main()

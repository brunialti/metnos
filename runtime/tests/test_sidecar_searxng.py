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
        # tutti e tre spediti
        for name in ("searxng", "vlm", "photon"):
            self.assertTrue(sidecar.SIDECARS[name]["ready"], name)
        for e in sidecar.SIDECARS.values():
            for k in ("label", "size", "desc", "install", "ready"):
                self.assertIn(k, e)

    def test_install_unknown_name(self):
        self.assertEqual(sidecar.install("nope"), {"nope": "unknown"})


class TestPhoton(unittest.TestCase):
    def test_country_url_matches_prod_catalog(self):
        # default it = lo stesso URL del photon-switch-country di produzione
        self.assertEqual(
            sidecar._photon_dump_url("it"),
            "https://download1.graphhopper.com/public/europe/italy/"
            "photon-dump-italy-1.0-latest.jsonl.zst")
        # fallback europeo per un codice non in catalogo (come prod)
        self.assertIn("europe/xx/photon-dump-xx", sidecar._photon_dump_url("xx"))

    def test_jar_is_pinned(self):
        self.assertEqual(sidecar._PHOTON_VERSION, "1.1.0")
        self.assertEqual(len(sidecar._PHOTON_JAR_SHA256), 64)
        self.assertIn("komoot/photon/releases", sidecar._PHOTON_JAR_URL)

    def test_photon_template_placeholders_all_substituted(self):
        tmpl = (_REPO / "install" / "units" / "metnos-photon.service.tmpl").read_text()
        for k, v in {"@JAVA@": "/usr/bin/java", "@PHOTON_XMX@": "4G",
                     "@PHOTON_JAR@": "/d/photon.jar", "@PHOTON_DATA@": "/d/current",
                     "@PHOTON_ROOT@": "/d", "@PHOTON_PORT@": "2322"}.items():
            tmpl = tmpl.replace(k, v)
        self.assertNotIn("@", tmpl)
        self.assertIn("-jar /d/photon.jar serve", tmpl)
        self.assertIn("-listen-port 2322", tmpl)


class TestVlm(unittest.TestCase):
    def test_vlm_models_dir_follows_install_root(self):
        import os
        old = os.environ.get("METNOS_MODELS_DIR")
        try:
            os.environ["METNOS_MODELS_DIR"] = "/data/models"
            self.assertEqual(str(sidecar._vlm_models_dir()), "/data/models/vlm")
        finally:
            if old is None:
                os.environ.pop("METNOS_MODELS_DIR", None)
            else:
                os.environ["METNOS_MODELS_DIR"] = old

    def test_vlm_server_paths_are_env_driven(self):
        # §7.11: vlm_server.sh deve onorare gli override env (default = prod)
        sh = (_REPO / "scripts" / "vlm_server.sh").read_text()
        self.assertIn("${METNOS_VLM_MODEL:-", sh)
        self.assertIn("${METNOS_VLM_MMPROJ:-", sh)
        self.assertIn("${METNOS_VLM_LLAMA_BIN:-", sh)

    def test_vlm_hf_source_is_official_qwen(self):
        self.assertEqual(sidecar._VLM_REPO, "Qwen/Qwen3-VL-2B-Instruct-GGUF")
        self.assertTrue(sidecar._VLM_MODEL.endswith(".gguf"))
        self.assertTrue(sidecar._VLM_MMPROJ.startswith("mmproj-"))


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

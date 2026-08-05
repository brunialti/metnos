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

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_REPO = (Path(__file__).resolve().parents[3] / "runtime").parent

from install import sidecar  # noqa: E402
from install.phases import phase2_infra  # noqa: E402


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
        self.assertEqual(set(sidecar.SIDECARS),
                         {"searxng", "photon", "vlm", "playwright"})
        for name in ("searxng", "vlm", "photon", "playwright"):
            self.assertTrue(sidecar.SIDECARS[name]["ready"], name)
        for e in sidecar.SIDECARS.values():
            for k in ("label", "size", "desc", "install", "ready"):
                self.assertIn(k, e)

    def test_install_unknown_name(self):
        self.assertEqual(sidecar.install("nope"), {"nope": "unknown"})

    def test_yes_explicit_enable_is_non_interactive(self):
        args = SimpleNamespace(yes=True, enable=["searxng"], skip=[])
        with patch.object(phase2_infra.ui, "confirm",
                          side_effect=AssertionError("unexpected prompt")), \
             patch.object(sidecar, "install",
                          return_value={"searxng": "running"}) as install:
            result = phase2_infra._offer_optionals(args)
        self.assertEqual(result, {
            "searxng": "running",
            "photon": "skipped",
            "vlm": "skipped",
            "playwright": "skipped",
        })
        install.assert_called_once_with("searxng", yes=True)


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

    def test_index_requires_success_receipt_not_just_directory(self):
        with tempfile.TemporaryDirectory() as d:
            country_dir = Path(d) / "it"
            segment = (country_dir / "photon_data" / "node_1" / "data" /
                       "nodes" / "0" / "indices" / "x" / "0" / "index" /
                       "segments_1")
            segment.parent.mkdir(parents=True)
            segment.write_bytes(b"lucene")
            url = sidecar._photon_dump_url("it")

            # This is the exact interrupted-import shape: a real Lucene tree
            # exists, but Java never returned successfully.
            self.assertFalse(sidecar._photon_index_complete(
                country_dir, "it", url))

            source = Path(d) / "source.jsonl"
            source.write_text('{"name":"Roma"}\n')
            sidecar._write_json_atomic(
                country_dir / sidecar._PHOTON_INDEX_COMPLETE,
                sidecar._photon_import_receipt("it", url, source))
            self.assertTrue(sidecar._photon_index_complete(
                country_dir, "it", url))
            self.assertFalse(sidecar._photon_index_complete(
                country_dir, "fr", sidecar._photon_dump_url("fr")))

    def test_nonzero_import_is_failure_even_if_index_directory_exists(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.dict("os.environ", {"METNOS_USER_DATA": d}), \
             patch("install.sidecar.shutil.which", return_value="/usr/bin/java"), \
             patch("install.downloads._sha256_file",
                   return_value=sidecar._PHOTON_JAR_SHA256):
            root = Path(d) / "sidecars" / "photon"
            root.mkdir(parents=True)
            (root / f"photon-{sidecar._PHOTON_VERSION}.jar").write_bytes(b"jar")
            dumps = root / "dumps"
            dumps.mkdir()
            dump = dumps / "photon-dump-it.jsonl.zst"
            dump.write_bytes(b"zstd")
            jsonl = dumps / "photon-dump-it.jsonl"
            jsonl.write_text('{"name":"Roma"}\n')
            sidecar._write_json_atomic(
                sidecar._photon_jsonl_marker(dumps, "it"),
                sidecar._photon_jsonl_receipt(
                    sidecar._photon_dump_url("it"), dump, jsonl))

            def failed_import(*_args, **_kwargs):
                segment = (root / "data" / "it" / "photon_data" / "node_1" /
                           "data" / "nodes" / "0" / "indices" / "x" / "0" /
                           "index" / "segments_1")
                segment.parent.mkdir(parents=True)
                segment.write_bytes(b"partial")
                return SimpleNamespace(returncode=137, stderr="killed", stdout="")

            with patch("install.sidecar._run", side_effect=failed_import):
                self.assertEqual(sidecar.install_photon(yes=True),
                                 {"photon": "import_failed"})
            country_dir = root / "data" / "it"
            self.assertFalse(
                (country_dir / sidecar._PHOTON_INDEX_COMPLETE).exists())
            self.assertTrue(
                (country_dir / sidecar._PHOTON_INDEX_BUILDING).exists())

    def test_expanded_dump_requires_a_matching_completion_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            dumps = Path(d)
            dump = dumps / "photon-dump-it.jsonl.zst"
            jsonl = dumps / "photon-dump-it.jsonl"
            marker = sidecar._photon_jsonl_marker(dumps, "it")
            dump.write_bytes(b"compressed")
            jsonl.write_text('{"name":"Roma"}\n')
            url = sidecar._photon_dump_url("it")

            self.assertFalse(sidecar._photon_jsonl_complete(
                marker, url, dump, jsonl))
            sidecar._write_json_atomic(
                marker, sidecar._photon_jsonl_receipt(url, dump, jsonl))
            self.assertTrue(sidecar._photon_jsonl_complete(
                marker, url, dump, jsonl))
            jsonl.write_text("truncated")
            self.assertFalse(sidecar._photon_jsonl_complete(
                marker, url, dump, jsonl))


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

    def test_vlm_ready_requires_successful_http_status(self):
        sh = (_REPO / "scripts" / "vlm_server.sh").read_text()
        self.assertGreaterEqual(sh.count("curl -fsS"), 2)

    def test_vlm_status_treats_absent_watchdog_as_normal(self):
        """``set -e`` must not abort status when no watchdog is running."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            pgrep = fake_bin / "pgrep"
            pgrep.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *llama-server*) echo \"$FAKE_SERVER_PID\"; exit 0 ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n"
            )
            pgrep.chmod(0o755)
            ps = fake_bin / "ps"
            ps.write_text("#!/bin/sh\necho 1024\n")
            ps.chmod(0o755)
            curl = fake_bin / "curl"
            curl.write_text("#!/bin/sh\nexit 0\n")
            curl.chmod(0o755)
            ss = fake_bin / "ss"
            ss.write_text("#!/bin/sh\nexit 0\n")
            ss.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "HOME": str(root),
                "FAKE_SERVER_PID": str(os.getpid()),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            })
            result = subprocess.run(
                ["bash", str(_REPO / "scripts" / "vlm_server.sh"), "status"],
                env=env, text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("watchdog=off", result.stdout)

    def test_vlm_hf_source_is_official_qwen(self):
        self.assertEqual(sidecar._VLM_REPO, "Qwen/Qwen3-VL-2B-Instruct-GGUF")
        self.assertTrue(sidecar._VLM_MODEL.endswith(".gguf"))
        self.assertTrue(sidecar._VLM_MMPROJ.startswith("mmproj-"))

    def test_vlm_acquires_llama_for_external_llm_install(self):
        manager = SimpleNamespace(
            _llama_dir=lambda: Path("/managed/llama"),
            _find_llama_bin=lambda *_: None,
            detect_hardware=lambda: object(),
            recommend=lambda _hw: SimpleNamespace(backend="vulkan"),
        )
        expected = Path("/managed/llama/llama-server")
        manager.acquire_llama = unittest.mock.Mock(return_value=expected)
        with patch("install.sidecar.shutil.which", return_value=None):
            self.assertEqual(sidecar._resolve_vlm_llama(manager), expected)
        manager.acquire_llama.assert_called_once_with(
            "vulkan", Path("/managed/llama"))

    def test_playwright_uses_common_sidecar_contract(self):
        with patch("install.playwright_sidecar.install",
                   return_value={"playwright": "running"}) as install:
            self.assertEqual(sidecar.install("playwright", yes=True),
                             {"playwright": "running"})
        install.assert_called_once_with(yes=True)


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

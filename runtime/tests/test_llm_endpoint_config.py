"""Endpoint llama-server NON hardcoded (tier pure-abstract, §7.11).

Flag E2E installer 12/6/2026: `runtime/llm_helpers.py` aveva la costante
`LLAMA_ENDPOINT=":8080"` e TUTTI i consumer di call_llm (provider HTTP,
describe deterministico /props + /apply-template) ignoravano la porta
configurata in `~/.config/metnos/llm_tiers.toml`. SoT unica ora:
`llm_router.tier_endpoint(tier)` — llm_tiers.toml (env
METNOS_LLM_TIERS_CONFIG inclusa) -> DEFAULT_TIERS -> LOCAL_DEFAULT_ENDPOINT.
Test hermetici: mock, nessuna rete.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import llm_helpers  # noqa: E402
import llm_router  # noqa: E402


def _tiers_toml(body: str) -> str:
    """Scrive un llm_tiers.toml temporaneo e ritorna il path."""
    tf = tempfile.NamedTemporaryFile(
        "w", suffix=".toml", delete=False, encoding="utf-8")
    tf.write(body)
    tf.close()
    return tf.name


class TestTierEndpointResolver(unittest.TestCase):
    """llm_router.tier_endpoint: config > default, alias, fallback."""

    def test_configured_endpoint_wins_over_default(self):
        path = _tiers_toml(
            '[middle]\nprovider = "llamacpp"\n'
            'endpoint = "http://127.0.0.1:9123"\n')
        with mock.patch.dict("os.environ",
                             {"METNOS_LLM_TIERS_CONFIG": path}):
            self.assertEqual(llm_router.tier_endpoint("middle"),
                             "http://127.0.0.1:9123")

    def test_base_url_alias_accepted(self):
        path = _tiers_toml(
            '[middle]\nprovider = "llamacpp"\n'
            'base_url = "http://127.0.0.1:9124/"\n')
        with mock.patch.dict("os.environ",
                             {"METNOS_LLM_TIERS_CONFIG": path}):
            # trailing slash normalizzato
            self.assertEqual(llm_router.tier_endpoint("middle"),
                             "http://127.0.0.1:9124")

    def test_middle_missing_aliases_to_wise(self):
        path = _tiers_toml(
            '[fast]\nprovider = "llamacpp"\n'
            'endpoint = "http://127.0.0.1:9001"\n'
            '[wise]\nprovider = "llamacpp"\n'
            'endpoint = "http://127.0.0.1:9002"\n')
        with mock.patch.dict("os.environ",
                             {"METNOS_LLM_TIERS_CONFIG": path}):
            self.assertEqual(llm_router.tier_endpoint("middle"),
                             "http://127.0.0.1:9002")

    def test_no_config_falls_back_to_local_default(self):
        with mock.patch.dict(
                "os.environ",
                {"METNOS_LLM_TIERS_CONFIG": "/nonexistent/llm_tiers.toml"}):
            self.assertEqual(llm_router.tier_endpoint("middle"),
                             llm_router.LOCAL_DEFAULT_ENDPOINT)

    def test_nested_legacy_format_supported(self):
        path = _tiers_toml(
            '[tiers.middle]\nprovider = "llamacpp"\n'
            'endpoint = "http://127.0.0.1:9125"\n')
        with mock.patch.dict("os.environ",
                             {"METNOS_LLM_TIERS_CONFIG": path}):
            self.assertEqual(llm_router.tier_endpoint("middle"),
                             "http://127.0.0.1:9125")


class TestCallLlmUsesConfiguredEndpoint(unittest.TestCase):
    """call_llm (provider HTTP) e il path deterministico (/props +
    /apply-template) puntano l'endpoint configurato, non :8080."""

    def _env(self, port: int) -> dict:
        path = _tiers_toml(
            f'[middle]\nprovider = "llamacpp"\n'
            f'endpoint = "http://127.0.0.1:{port}"\n')
        return {"METNOS_LLM_TIERS_CONFIG": path}

    def test_http_provider_gets_configured_endpoint(self):
        captured = {}

        class FakeProvider:
            def __init__(self, *, model, endpoint, id_slot=None):
                captured["endpoint"] = endpoint

            def chat(self, *a, **k):
                return mock.Mock(text="ok", in_tokens=1, out_tokens=1)

        with mock.patch.dict("os.environ", self._env(9123)), \
             mock.patch.object(llm_helpers, "LlamaCppProvider",
                               FakeProvider):
            text, _ = llm_helpers.call_llm("q", "P", tier="middle")
        self.assertEqual(text, "ok")
        self.assertEqual(captured["endpoint"], "http://127.0.0.1:9123")

    def test_proc_path_props_and_template_hit_configured_endpoint(self):
        urls = []

        def fake_urlopen(req, timeout=0):
            url = req if isinstance(req, str) else req.full_url
            urls.append(url)
            body = ({"model_path": "/models/m.gguf"} if "/props" in url
                    else {"prompt": "<rendered>"})
            r = mock.MagicMock()
            r.__enter__.return_value.read.return_value = \
                json.dumps(body).encode("utf-8")
            return r

        completed = mock.Mock(returncode=0, stdout="testo", stderr="")
        with mock.patch.dict("os.environ", self._env(9123)), \
             mock.patch.object(llm_helpers, "_completion_bin",
                               return_value="/usr/bin/llama-completion"), \
             mock.patch.object(llm_helpers.urllib.request, "urlopen",
                               side_effect=fake_urlopen), \
             mock.patch.object(llm_helpers.subprocess, "run",
                               return_value=completed):
            text, meta = llm_helpers.call_llm(
                "q", "P", tier="middle", deterministic=True)
        self.assertEqual(text, "testo")
        self.assertIs(meta["deterministic"], True)
        self.assertEqual(len(urls), 2)
        for url in urls:
            self.assertTrue(url.startswith("http://127.0.0.1:9123/"),
                            f"endpoint hardcoded ignorato: {url}")
        self.assertTrue(any(u.endswith("/props") for u in urls))
        self.assertTrue(any(u.endswith("/apply-template") for u in urls))


if __name__ == "__main__":
    unittest.main()

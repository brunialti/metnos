"""Installer: pinning di GGUF (revision HF) e tag release llama.cpp (14/6).

Flag E2E installer #4 «GGUF/tag non pinnati»: il default scaricava llama.cpp
`releases/latest` (mobile) col commento che mentiva («default a un tag
pinnato»), e il GGUF da `resolve/main/` (ref mobile → build non riproducibile,
rompe il describe deterministico §11). Fix: `_LLAMA_TAG_DEFAULT` pinnato +
opt-out esplicito `=latest`; campo `hf_revision` (commit-sha) nel catalogo,
threaded in download_model→URL `resolve/<rev>/` + verifica sha della revisione.

Test hermetici: mock, nessuna rete.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from install import llm_manager as LM  # noqa: E402


class TestCatalogPins(unittest.TestCase):
    def test_canonical_pinned_others_main(self):
        by_key = {m["key"]: m for m in LM.CATALOG}
        # il canonico (wise_capable) è pinnato a un commit-sha (40 hex)
        canon = by_key["qwen3-32b"]
        self.assertRegex(canon["hf_revision"], r"^[0-9a-f]{40}$",
                         "canonico non pinnato a un commit-sha")
        # gli altri restano main finché non validati (pinnabili a una riga)
        for k in ("qwen3-14b", "qwen3-8b", "qwen3-4b"):
            self.assertEqual(by_key[k]["hf_revision"], "main")

    def test_plan_default_revision_main(self):
        p = LM.Plan(backend="cpu", model_key=None, model_label=None)
        self.assertEqual(p.hf_revision, "main")

    def test_default_tag_is_pinned_tag(self):
        self.assertRegex(LM._LLAMA_TAG_DEFAULT, r"^b\d+$")


class TestGgufRevisionUrl(unittest.TestCase):
    def _capture_download_url(self, revision, tmp):
        captured = {}

        def fake_dl(url, dest, **kw):
            captured["url"] = url
            return True

        with mock.patch.object(LM, "_hf_expected_sha256", return_value=None), \
                mock.patch.object(LM, "_download", side_effect=fake_dl):
            ok = LM.download_model("Qwen/Qwen3-32B-GGUF",
                                   "Qwen3-32B-Q4_K_M.gguf",
                                   tmp / "missing.gguf", revision=revision)
        self.assertTrue(ok)
        return captured["url"]

    def test_pinned_revision_in_url(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            url = self._capture_download_url("a" * 40, Path(d))
        self.assertIn("/resolve/" + "a" * 40 + "/", url)

    def test_main_revision_in_url(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            url = self._capture_download_url("main", Path(d))
        self.assertIn("/resolve/main/", url)

    def test_sha_query_targets_revision(self):
        captured = {}

        def fake_post(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return [{"path": "f.gguf", "lfs": {"oid": "c" * 64}}]

        with mock.patch.object(LM, "_http_post_json", side_effect=fake_post):
            got = LM._hf_expected_sha256("repo/x", "f.gguf", revision="b" * 40)
        # paths-info per-file, revision-scoped
        self.assertIn("/paths-info/" + "b" * 40, captured["url"])
        self.assertEqual(captured["payload"], {"paths": ["f.gguf"]})
        self.assertEqual(got, "c" * 64)

    def test_sha_main_uses_paths_info(self):
        captured = {}

        def fake_post(url, payload):
            captured["url"] = url
            return [{"path": "f.gguf", "lfs": {"oid": "d" * 64}}]

        with mock.patch.object(LM, "_http_post_json", side_effect=fake_post):
            got = LM._hf_expected_sha256("repo/x", "f.gguf", revision="main")
        self.assertIn("/paths-info/main", captured["url"])
        self.assertEqual(got, "d" * 64)


class TestLlamaTagResolution(unittest.TestCase):
    def _capture_api(self, env_value, tmp):
        captured = {}

        def fake_json(url):
            captured["api"] = url
            return {"assets": []}        # → _pick_llama_asset None → bail pulito

        ctx = mock.patch.dict("os.environ",
                              {"METNOS_LLAMA_TAG": env_value} if env_value is not None else {},
                              clear=False)
        with ctx, \
                mock.patch.object(LM, "_find_llama_server", return_value=None), \
                mock.patch.object(LM, "_http_json", side_effect=fake_json):
            if env_value is None:
                import os
                os.environ.pop("METNOS_LLAMA_TAG", None)
            LM.acquire_llama("cpu", tmp)
        return captured.get("api", "")

    def test_default_uses_pinned_tag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            api = self._capture_api(None, Path(d))
        self.assertIn("releases/tags/" + LM._LLAMA_TAG_DEFAULT, api)

    def test_env_override(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            api = self._capture_api("b1234", Path(d))
        self.assertIn("releases/tags/b1234", api)

    def test_latest_optout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            api = self._capture_api("latest", Path(d))
        self.assertIn("releases/latest", api)
        self.assertNotIn("tags/", api)


if __name__ == "__main__":
    unittest.main()

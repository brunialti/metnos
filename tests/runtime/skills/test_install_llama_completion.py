"""Installer: llama-completion estratto + esposto al runtime (12/6/2026).

Il fix describe-determinism (runtime/llm_helpers) richiede un binario
`llama-completion` ALLINEATO DI VERSIONE col llama-server. Il managed
install estrae l'archivio release INTERO, quindi il binario c'e' gia':
qui si testa che (1) llm_manager lo localizzi e lo renda eseguibile,
(2) phase5 ne esponga il path nell'unit metnos-http via
METNOS_LLAMACPP_COMPLETION_BIN, (3) l'assenza degradi onestamente
(§2.8) senza far fallire l'install.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = (Path(__file__).resolve().parents[3] / "runtime").parent

from install import llm_manager  # noqa: E402
from install.phases import phase5_systemd as p5  # noqa: E402


def _mk(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"#!bin\n")
    return p


class TestFindLlamaBin(unittest.TestCase):
    """_find_llama_bin: helper generale per binari nell'albero estratto."""

    def test_finds_nested_binary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            want = _mk(root, "build/bin/llama-completion")
            got = llm_manager._find_llama_bin(root, "llama-completion")
            self.assertEqual(got, want)

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(
                llm_manager._find_llama_bin(Path(td), "llama-completion"))

    def test_find_llama_server_still_handles_old_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            want = _mk(root, "bin/server")  # release vecchie
            self.assertEqual(llm_manager._find_llama_server(root), want)


class TestEnsureCompletionBin(unittest.TestCase):
    """_ensure_completion_bin: chmod se presente, degrado onesto se no."""

    def test_present_is_made_executable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            comp = _mk(root, "build/bin/llama-completion")
            comp.chmod(0o644)
            got = llm_manager._ensure_completion_bin(root)
            self.assertEqual(got, comp)
            mode = stat.S_IMODE(comp.stat().st_mode)
            self.assertEqual(mode, 0o755)

    def test_absent_returns_none_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(llm_manager._ensure_completion_bin(Path(td)))


class TestFindCompletionBinInstallRoot(unittest.TestCase):
    """find_completion_bin guarda sotto <INSTALL_ROOT>/llm/llama.cpp."""

    def test_resolves_under_install_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            want = _mk(root, "llm/llama.cpp/build/bin/llama-completion")
            with mock.patch.dict(os.environ,
                                 {"METNOS_INSTALL_ROOT": str(root)}):
                self.assertEqual(llm_manager.find_completion_bin(), want)

    def test_no_managed_install_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ,
                                 {"METNOS_INSTALL_ROOT": td}):
                self.assertIsNone(llm_manager.find_completion_bin())


class TestPhase5CompletionEnv(unittest.TestCase):
    """phase5: @COMPLETION_ENV@ → env reale o commento onesto."""

    def test_env_line_when_binary_found(self):
        with mock.patch.object(llm_manager, "find_completion_bin",
                               return_value=Path("/x/llm/llama-completion")):
            line = p5._completion_env_line()
        self.assertEqual(
            line,
            "Environment=METNOS_LLAMACPP_COMPLETION_BIN=/x/llm/llama-completion")

    def test_honest_comment_when_absent(self):
        with mock.patch.object(llm_manager, "find_completion_bin",
                               return_value=None):
            line = p5._completion_env_line()
        self.assertTrue(line.startswith("#"))
        self.assertIn("deterministic=false", line)

    def test_template_renders_without_stray_placeholder(self):
        tmpl = (_REPO / "install" / "units" /
                "metnos-http.service.tmpl").read_text()
        self.assertIn("@COMPLETION_ENV@", tmpl)
        with mock.patch.object(llm_manager, "find_completion_bin",
                               return_value=Path("/x/bin/llama-completion")):
            rendered = p5._substitute(tmpl, 8770, "it")
        self.assertNotIn("@COMPLETION_ENV@", rendered)
        self.assertIn(
            "Environment=METNOS_LLAMACPP_COMPLETION_BIN=/x/bin/llama-completion",
            rendered)
        # nessun placeholder residuo di alcun tipo
        for ph in ("@VENV@", "@DATA_DIR@", "@CONFIG_DIR@", "@STATE_DIR@",
                   "@REPO_DIR@", "@PORT@", "@LANG@"):
            self.assertNotIn(ph, rendered)


if __name__ == "__main__":
    unittest.main()

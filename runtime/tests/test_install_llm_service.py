"""Installer: il llama-server locale viene AVVIATO, non solo scritto.

Flag E2E installer #2 (12/6/2026): `llm_manager.provision()` scriveva
`metnos-llm.service` ma non lo installava/abilitava/avviava — dopo un
install fresco il 1° turno falliva finche' l'utente non avviava il server
a mano. Fix: `install_user_unit` installa la USER unit (no sudo, coerente
con phase5), la abilita e la avvia, con esiti onesti §2.8 (guard se
l'endpoint serve gia', degrado se systemd manca). Test di ispezione del
path install: mock, NESSUN install reale, nessuna rete.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from install import llm_manager  # noqa: E402


def _unit_src(td: Path) -> Path:
    src = td / "metnos-llm.service"
    src.write_text("[Unit]\nDescription=test\n", encoding="utf-8")
    return src


class TestInstallUserUnit(unittest.TestCase):
    """install_user_unit: copia, abilita, avvia, esiti onesti."""

    def test_happy_path_installs_enables_starts(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            src = _unit_src(tdp)
            unit_dir = tdp / "systemd-user"
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.object(llm_manager.shutil, "which",
                                   return_value="/bin/systemctl"), \
                 mock.patch.object(llm_manager, "_user_unit_dir",
                                   return_value=unit_dir), \
                 mock.patch.object(llm_manager, "_endpoint_health",
                                   side_effect=[False, True]), \
                 mock.patch.object(llm_manager.subprocess, "run",
                                   side_effect=fake_run):
                out = llm_manager.install_user_unit(
                    src, endpoint="http://127.0.0.1:9080", wait_s=30)
            copied = (unit_dir / "metnos-llm.service").exists()
        self.assertTrue(copied)
        self.assertEqual(out, {"installed": True, "enabled": True,
                               "started": True, "healthy": True,
                               "reason": ""})
        self.assertIn(["systemctl", "--user", "daemon-reload"], calls)
        self.assertIn(["systemctl", "--user", "enable", "--now",
                       "metnos-llm.service"], calls)

    def test_endpoint_already_alive_does_not_start_second_server(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            src = _unit_src(tdp)
            unit_dir = tdp / "systemd-user"
            with mock.patch.object(llm_manager.shutil, "which",
                                   return_value="/bin/systemctl"), \
                 mock.patch.object(llm_manager, "_user_unit_dir",
                                   return_value=unit_dir), \
                 mock.patch.object(llm_manager, "_endpoint_health",
                                   return_value=True), \
                 mock.patch.object(llm_manager.subprocess, "run") as run:
                out = llm_manager.install_user_unit(
                    src, endpoint="http://127.0.0.1:9080", wait_s=30)
        run.assert_not_called()
        self.assertTrue(out["installed"])   # unit in place per i prossimi boot
        self.assertFalse(out["started"])
        self.assertTrue(out["healthy"])
        self.assertIn("gia' attivo", out["reason"])

    def test_no_systemd_degrades_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            src = _unit_src(Path(td))
            with mock.patch.object(llm_manager.shutil, "which",
                                   return_value=None):
                out = llm_manager.install_user_unit(
                    src, endpoint="http://127.0.0.1:9080", wait_s=0)
        self.assertFalse(out["installed"])
        self.assertFalse(out["healthy"])
        self.assertIn("systemctl assente", out["reason"])

    def test_enable_failure_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            src = _unit_src(tdp)

            def fake_run(cmd, **kw):
                rc = 1 if "enable" in cmd else 0
                return mock.Mock(returncode=rc, stdout="",
                                 stderr="Failed to connect to bus")

            with mock.patch.object(llm_manager.shutil, "which",
                                   return_value="/bin/systemctl"), \
                 mock.patch.object(llm_manager, "_user_unit_dir",
                                   return_value=tdp / "u"), \
                 mock.patch.object(llm_manager, "_endpoint_health",
                                   return_value=False), \
                 mock.patch.object(llm_manager.subprocess, "run",
                                   side_effect=fake_run):
                out = llm_manager.install_user_unit(
                    src, endpoint="http://127.0.0.1:9080", wait_s=0)
        self.assertTrue(out["installed"])
        self.assertFalse(out["started"])
        self.assertFalse(out["healthy"])
        self.assertIn("Failed to connect to bus", out["reason"])

    def test_started_but_never_healthy_is_honest(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            src = _unit_src(tdp)
            with mock.patch.object(llm_manager.shutil, "which",
                                   return_value="/bin/systemctl"), \
                 mock.patch.object(llm_manager, "_user_unit_dir",
                                   return_value=tdp / "u"), \
                 mock.patch.object(llm_manager, "_endpoint_health",
                                   return_value=False), \
                 mock.patch.object(llm_manager.subprocess, "run",
                                   return_value=mock.Mock(
                                       returncode=0, stdout="", stderr="")):
                out = llm_manager.install_user_unit(
                    src, endpoint="http://127.0.0.1:9080", wait_s=0)
        self.assertTrue(out["started"])
        self.assertFalse(out["healthy"])
        self.assertIn("non risponde", out["reason"])


class TestProvisionStartsService(unittest.TestCase):
    """provision() (esecuzione reale mockata): chiama install_user_unit
    sull'endpoint del piano e riporta lo stato del servizio."""

    def _provision(self, td: Path, svc: dict):
        env = {"METNOS_MODELS_DIR": str(td / "models"),
               "METNOS_INSTALL_ROOT": str(td / "root"),
               "METNOS_USER_CONFIG": str(td / "cfg"),
               "METNOS_LLM_START_TIMEOUT_S": "5"}
        plan = llm_manager.Plan(
            backend="cpu", model_key="k", model_label="Tiny",
            hf_repo="r/x", hf_file="m.gguf", feasible=True,
            endpoint="http://127.0.0.1:9080")
        binp = td / "root" / "llm" / "llama.cpp" / "bin" / "llama-server"
        binp.parent.mkdir(parents=True, exist_ok=True)
        binp.write_bytes(b"#!bin\n")

        def fake_download(repo, f, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"GGUF")
            return True

        captured = {}

        def fake_install(unit_src, *, endpoint, wait_s):
            captured["unit_src"] = Path(unit_src)
            captured["unit_written"] = Path(unit_src).exists()
            captured["endpoint"] = endpoint
            captured["wait_s"] = wait_s
            return dict(svc)

        with mock.patch.dict("os.environ", env), \
             mock.patch.object(llm_manager, "acquire_llama",
                               return_value=binp), \
             mock.patch.object(llm_manager, "find_completion_bin",
                               return_value=None), \
             mock.patch.object(llm_manager, "download_model",
                               side_effect=fake_download), \
             mock.patch.object(llm_manager, "install_user_unit",
                               side_effect=fake_install), \
             mock.patch.object(llm_manager, "health_check") as hc:
            out = llm_manager.provision(plan, dry_run=False, assume_yes=True)
        return out, captured, hc

    def test_provision_installs_and_reports_healthy_service(self):
        with tempfile.TemporaryDirectory() as td:
            out, cap, hc = self._provision(Path(td), {
                "installed": True, "enabled": True, "started": True,
                "healthy": True, "reason": ""})
        self.assertTrue(out["ok"])
        self.assertTrue(out["health"])
        self.assertTrue(out["service"]["healthy"])
        self.assertEqual(cap["endpoint"], "http://127.0.0.1:9080")
        self.assertEqual(cap["wait_s"], 5)  # env METNOS_LLM_START_TIMEOUT_S
        self.assertTrue(cap["unit_written"])  # unit scritta prima dell'avvio
        hc.assert_not_called()  # niente porta di prova se il servizio serve

    def test_provision_unhealthy_service_reported_with_fallback_check(self):
        with tempfile.TemporaryDirectory() as td:
            out, _, hc = self._provision(Path(td), {
                "installed": True, "enabled": True, "started": True,
                "healthy": False, "reason": "timeout"})
        self.assertFalse(out["service"]["healthy"])
        hc.assert_called_once()  # verifica di meccanismo su porta di prova


if __name__ == "__main__":
    unittest.main()

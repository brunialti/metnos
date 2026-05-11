"""Test del binario `metnos-cli credentials *` (Strato 3 ADR 0089).

Copertura via subprocess:
  - add (interattivo, stdin scripted) salva credenziali cifrate.
  - list le elenca.
  - remove le rimuove.
  - fingerprint ritorna sha256[:16] della pwd, niente plaintext.

Setup: monkeypatch della directory credentials in un tempdir tramite env
var (METNOS_CRED_DIR_OVERRIDE). Per evitare side-effect sulla home reale.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
_REPO = _RUNTIME.parent
sys.path.insert(0, str(_RUNTIME))

CLI = str(_REPO / "scripts" / "metnos-cli")


def _run_cli(args, *, stdin: str | None = None,
             env_extra: dict | None = None, timeout: int = 10) -> tuple[int, str, str]:
    """Esegue lo script CLI e ritorna (returncode, stdout, stderr)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr


class TestCliCredentials(unittest.TestCase):
    """Subprocess-based smoke test del CLI."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Override CRED_DIR via patch del modulo prima di lanciare il CLI:
        # il subprocess re-importa credentials da zero, quindi serve env var.
        # Strategia: il modulo credentials usa Path.home() / ".config" / "metnos".
        # Spostiamo HOME sul tempdir.
        self.fake_home = Path(self.tmp) / "home"
        self.fake_home.mkdir()
        (self.fake_home / ".config" / "metnos").mkdir(parents=True)
        # Genera admin.key fake (basta che sia hex valido per Fernet)
        import secrets
        admin_key = self.fake_home / ".config" / "metnos" / "admin.key"
        admin_key.write_text(secrets.token_hex(32))
        admin_key.chmod(0o600)
        self._env = {"HOME": str(self.fake_home)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_then_list_then_remove(self):
        # add interattivo: stdin = "user\npwd\npwd-confirm\n"
        rc, out, err = _run_cli(
            ["credentials", "add", "cifs_test.example.com",
             "--binding", "cifs", "--host", "test.example.com"],
            stdin="alice\nsecret123\nsecret123\n",
            env_extra=self._env,
        )
        self.assertEqual(rc, 0, f"add rc={rc} err={err}")
        self.assertIn("salvate", out)
        # list
        rc, out, err = _run_cli(["credentials", "list"], env_extra=self._env)
        self.assertEqual(rc, 0, f"list rc={rc} err={err}")
        self.assertIn("cifs_test.example.com", out)
        # fingerprint (16 char hex)
        rc, out, err = _run_cli(
            ["credentials", "fingerprint", "cifs_test.example.com"],
            env_extra=self._env,
        )
        self.assertEqual(rc, 0, f"fingerprint rc={rc} err={err}")
        fp = out.strip()
        self.assertEqual(len(fp), 16, f"fingerprint shape: {fp!r}")
        # password NEVER in stdout
        self.assertNotIn("secret123", out)
        self.assertNotIn("secret123", err)
        # remove
        rc, out, err = _run_cli(
            ["credentials", "remove", "cifs_test.example.com"],
            env_extra=self._env,
        )
        self.assertEqual(rc, 0, f"remove rc={rc} err={err}")
        # ora list e' vuoto
        rc, out, err = _run_cli(["credentials", "list"], env_extra=self._env)
        self.assertEqual(rc, 0)
        self.assertIn("nessun dominio", out.lower())

    def test_add_password_mismatch_rejected(self):
        rc, out, err = _run_cli(
            ["credentials", "add", "cifs_x.example.com"],
            stdin="user\nfoo\nbar\n",  # pwd != confirm
            env_extra=self._env,
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("non coincidono", err)

    def test_remove_missing_returns_nonzero(self):
        rc, out, err = _run_cli(
            ["credentials", "remove", "does_not_exist"],
            env_extra=self._env,
        )
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

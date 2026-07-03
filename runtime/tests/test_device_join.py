"""test_device_join — flusso install-at-the-fly dalla UI (§5 design doc).

Copre: join session lifecycle (stati monotoni, expiry), pagina join no-auth,
installer personalizzato (server+token baked, marca downloaded), aggancio
register→registered e heartbeat→heartbeat, boundary 404/410.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class JoinSessionModelTests(unittest.TestCase):
    """Lifecycle sqlite puro (senza HTTP)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["METNOS_DEVICES_DB"] = str(Path(self._tmp.name) / "devices.db")
        import importlib
        import devices
        importlib.reload(devices)
        self.devices = devices

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("METNOS_DEVICES_DB", None)

    def test_lifecycle_monotonic(self):
        s = self.devices.create_join_session("pc-1", server_url="http://x:8765")
        self.assertEqual(s["state"], "created")
        self.assertTrue(self.devices.mark_join_state(s["join_id"], "opened"))
        # regressione bloccata
        self.assertFalse(self.devices.mark_join_state(s["join_id"], "created"))
        # idempotenza: stesso stato due volte = secondo no-op
        self.assertFalse(self.devices.mark_join_state(s["join_id"], "opened"))
        self.assertTrue(self.devices.mark_join_state(s["join_id"], "downloaded"))

    def test_register_link_and_heartbeat(self):
        s = self.devices.create_join_session("pc-2")
        pub = _b64u(b"k" * 32)
        dev = self.devices.consume_token(s["token"], pub, os_family="linux")
        self.assertTrue(
            self.devices.mark_join_registered_by_token(s["token"], dev.id))
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "registered")
        self.devices.heartbeat(dev.id)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "heartbeat")

    def test_expiry_computed(self):
        s = self.devices.create_join_session("pc-3", ttl_seconds=1)
        self.assertEqual(s["state"], "created")
        time.sleep(1.2)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "expired")

    def test_repair_revoked_device_unrevokes_and_updates(self):
        # Bug live 3/7: client reinstallato con la STESSA chiave Ed25519 di
        # un device REVOCATO -> register 200 ma la riga restava revocata
        # (nome vecchio compreso): ogni poll/heartbeat respinto 403 in
        # silenzio, join fermo a 'registered'. Il token fresco one-shot
        # emesso dall'admin E' la ri-autorizzazione: la revoca DECADE.
        pub = _b64u(b"r" * 32)
        s1 = self.devices.create_join_session("vecchio-nome")
        dev = self.devices.consume_token(s1["token"], pub, os_family="windows")
        self.devices.revoke_device(dev.id)
        self.assertIsNotNone(self.devices.get_device(dev.id).revoked_at)

        s2 = self.devices.create_join_session("nuovo-nome")
        dev2 = self.devices.consume_token(s2["token"], pub, os_family="windows")
        self.assertEqual(dev2.id, dev.id)  # stessa identita', stessa riga
        self.assertIsNone(dev2.revoked_at)
        self.assertEqual(dev2.name, "nuovo-nome")
        fresh = self.devices.get_device(dev.id)
        self.assertIsNone(fresh.revoked_at)
        self.assertEqual(fresh.name, "nuovo-nome")

    def test_repair_same_token_stays_idempotent(self):
        # L'idempotenza (token GIA' consumato + stessa chiave) non deve
        # cambiare: ritorna il device cosi' com'e', senza ri-scriverlo.
        pub = _b64u(b"i" * 32)
        s = self.devices.create_join_session("pc-idem")
        d1 = self.devices.consume_token(s["token"], pub)
        d2 = self.devices.consume_token(s["token"], pub)
        self.assertEqual(d1.id, d2.id)

    def test_registered_never_expires(self):
        s = self.devices.create_join_session("pc-4", ttl_seconds=1)
        pub = _b64u(b"j" * 32)
        dev = self.devices.consume_token(s["token"], pub)
        self.devices.mark_join_registered_by_token(s["token"], dev.id)
        time.sleep(1.2)
        # una registrazione riuscita non torna 'expired' dopo il TTL token
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "registered")

    def test_unknown_join(self):
        self.assertIsNone(self.devices.get_join_session("deadbeef00000000"))

    def test_purge_join_sessions(self):
        # GC §12: via le sessioni col token scaduto oltre-retention; le
        # recenti (anche scadute da poco) restano osservabili.
        old = self.devices.create_join_session("pc-old", ttl_seconds=1)
        fresh = self.devices.create_join_session("pc-fresh")
        import sqlite3 as _sq
        conn = _sq.connect(os.environ["METNOS_DEVICES_DB"])
        conn.execute(
            "UPDATE device_join_sessions SET expires_at = ? WHERE join_id = ?",
            (int(time.time()) - 8 * 86400, old["join_id"]))
        conn.commit(); conn.close()
        removed = self.devices.purge_join_sessions(older_than_days=7)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.devices.get_join_session(old["join_id"]))
        self.assertIsNotNone(self.devices.get_join_session(fresh["join_id"]))

    def test_device_name_slug_enforced(self):
        # Dominio CHIUSO §2.4: il nome finisce in HTML/unit/log.
        for bad in ("<script>x</script>", "a'b", 'a"b', "a;b", "x" * 41, ""):
            with self.subTest(bad=bad):
                with self.assertRaises(self.devices.TokenError):
                    self.devices.generate_token(bad)
        # slug legittimi passano
        self.devices.generate_token("laptop-windows_2.OK")


class JoinHttpTests(AioHTTPTestCase):
    """Route /agent/client/join/* + aggancio register reale."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        td = Path(cls._tmp.name)
        cls._orig_db = os.environ.get("METNOS_DEVICES_DB")
        os.environ["METNOS_DEVICES_DB"] = str(td / "devices.db")
        # Mirror finto: installer minimi con i punti d'iniezione reali.
        cls._mirror = td / "client"
        cls._mirror.mkdir(parents=True)
        (cls._mirror / "install.sh").write_text(
            "#!/bin/sh\n: \"${METNOS_SERVER:?}\"\n: \"${METNOS_TOKEN:?}\"\n")
        (cls._mirror / "install.ps1").write_text(
            "if (-not $env:METNOS_SERVER) { throw 'x' }\n")
        (cls._mirror / "manifest.json").write_text(json.dumps({
            "latest": "9.9.9",
            "versions": {"9.9.9": {"x86_64-pc-windows-gnu": {
                "filename": "metnos-client.exe",
                "sha256": "cafe" * 16}}},
        }))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        if cls._orig_db is not None:
            os.environ["METNOS_DEVICES_DB"] = cls._orig_db
        else:
            os.environ.pop("METNOS_DEVICES_DB", None)

    async def get_application(self):
        import importlib
        import devices
        import invocations
        import agent_mirror
        import agent_server
        importlib.reload(devices)
        importlib.reload(invocations)
        importlib.reload(agent_server)
        self.devices = devices
        agent_mirror.MIRROR_CLIENT_DIR = self._mirror
        # Isola ANCHE la runtime dir: quella reale puo' ospitare il tarball
        # python-build-standalone e il pin verrebbe baked nei test.
        rt_empty = Path(self._tmp.name) / "runtime-empty"
        rt_empty.mkdir(exist_ok=True)
        agent_mirror.MIRROR_RUNTIME_DIR = rt_empty
        return agent_server.make_app()

    def _new_session(self, name="pc-http", **kw):
        return self.devices.create_join_session(name, **kw)

    async def test_join_page_marks_opened(self):
        s = self._new_session()
        resp = await self.client.get(f"/agent/client/join/{s['join_id']}")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn(s["join_id"], text)
        self.assertIn("pc-http", text)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "opened")

    async def test_join_page_unknown_404(self):
        resp = await self.client.get("/agent/client/join/ffffffffffffffff")
        self.assertEqual(resp.status, 404)

    async def test_installer_bakes_token_and_marks_downloaded(self):
        s = self._new_session("pc-sh")
        resp = await self.client.get(
            f"/agent/client/join/{s['join_id']}/installer?platform=linux")
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertIn(s["token"], body)
        self.assertIn("METNOS_SERVER=", body)
        self.assertTrue(body.startswith("#!/bin/sh"))
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "downloaded")

    async def test_installer_windows_cmd_polyglot(self):
        # §5.7 (rev 3/7): l'artefatto Windows e' un .cmd eseguibile con un
        # click dal browser — testa batch (env baked + bootstrap) + marker +
        # install.ps1 INTATTO in coda. Il .ps1 nudo non si esegue col doppio
        # click (selettore app, attrito osservato live).
        import agent_server as A
        s = self._new_session("pc-cmd")
        resp = await self.client.get(
            f"/agent/client/join/{s['join_id']}/installer?platform=windows")
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertTrue(body.startswith("@echo off"))
        # Env baked in testa batch (il .ps1 in coda li legge da $env:).
        self.assertIn(f'set "METNOS_TOKEN={s["token"]}"', body)
        self.assertIn('set "METNOS_SERVER=http://', body)
        # Pin version+sha256 baked (§5.7): il manifest scaricato non fa fede.
        self.assertIn('set "METNOS_CLIENT_VERSION=9.9.9"', body)
        self.assertIn('set "METNOS_CLIENT_SHA256=' + "cafe" * 16 + '"', body)
        # Il marker compare UNA volta sola (nel bootstrap e' spezzato,
        # altrimenti IndexOf troverebbe quello del comando, non il vero).
        self.assertEqual(body.count(A._CMD_MARKER), 1)
        head, _, tail = body.partition(A._CMD_MARKER)
        self.assertIn("Invoke-Expression", head)
        self.assertIn("-ExecutionPolicy Bypass", head)
        self.assertIn("pause", head)  # la finestra non sparisce mai
        # La coda e' install.ps1 INTATTO (stesso corpo del one-liner).
        self.assertIn("$env:METNOS_SERVER", tail)
        # Testa batch: solo ASCII (cmd.exe legge ANSI/OEM, mai UTF-8).
        head.encode("ascii")
        self.assertIn("MetnosClientSetup.cmd",
                      resp.headers.get("Content-Disposition", ""))
        # Mirror di test SENZA runtime dir: nessun pin runtime baked.
        self.assertNotIn("METNOS_PYTHON_RUNTIME_WIN", body)

    async def test_installer_windows_bakes_python_runtime_pin(self):
        # Col tarball python-build-standalone nel mirror runtime, il .cmd
        # baka il pin: il client lo scarica lazy alla prima invocazione
        # (fix live 3/7: «nessun interprete Python» sul device reale).
        import agent_mirror
        tarball = "cpython-3.12.13+20260623-x86_64-pc-windows-msvc-install_only.tar.gz"
        rt = Path(self._tmp.name) / "runtime"
        rt.mkdir(exist_ok=True)
        (rt / tarball).write_bytes(b"fake")
        orig = agent_mirror.MIRROR_RUNTIME_DIR
        agent_mirror.MIRROR_RUNTIME_DIR = rt
        try:
            s = self._new_session("pc-pyrt")
            resp = await self.client.get(
                f"/agent/client/join/{s['join_id']}/installer?platform=windows")
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            self.assertIn(f'set "METNOS_PYTHON_RUNTIME_WIN={tarball}"', body)
        finally:
            agent_mirror.MIRROR_RUNTIME_DIR = orig

    async def test_join_page_escapes_device_name(self):
        # Difesa in profondita': nome malevolo NON puo' piu' nascere da
        # generate_token (slug §2.4), ma la pagina e' no-auth e deve fare
        # escape comunque (riga legacy/manomessa nel DB).
        s = self._new_session("pc-xss")
        evil = "<script>alert(1)</script>"
        import sqlite3 as _sq
        conn = _sq.connect(os.environ["METNOS_DEVICES_DB"])
        conn.execute(
            "UPDATE device_join_sessions SET device_name = ? WHERE join_id = ?",
            (evil, s["join_id"]))
        conn.commit(); conn.close()
        resp = await self.client.get(f"/agent/client/join/{s['join_id']}")
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertNotIn(evil, body)
        self.assertIn("&lt;script&gt;", body)

    async def test_installer_windows_503_without_pin(self):
        # §5.7 fail-closed: manifest illeggibile → niente installer Windows
        # senza pin (mai fallback silenzioso al manifest runtime).
        import agent_mirror
        s = self._new_session("pc-nopin")
        empty = Path(self._tmp.name) / "empty-mirror"
        empty.mkdir(exist_ok=True)
        (empty / "install.ps1").write_text("x")  # sorgente c'è, manifest no
        orig = agent_mirror.MIRROR_CLIENT_DIR
        agent_mirror.MIRROR_CLIENT_DIR = empty
        try:
            resp = await self.client.get(
                f"/agent/client/join/{s['join_id']}/installer?platform=windows")
            self.assertEqual(resp.status, 503)
        finally:
            agent_mirror.MIRROR_CLIENT_DIR = orig

    async def test_installer_expired_410(self):
        s = self._new_session("pc-exp", ttl_seconds=1)
        time.sleep(1.2)
        resp = await self.client.get(
            f"/agent/client/join/{s['join_id']}/installer?platform=linux")
        self.assertEqual(resp.status, 410)

    async def test_register_advances_session(self):
        s = self._new_session("pc-reg")
        priv = Ed25519PrivateKey.generate()
        pub_b64 = _b64u(priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw))
        resp = await self.client.post("/agent/register", json={
            "token": s["token"], "public_key": pub_b64, "os_family": "linux"})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "registered")

        # heartbeat firmato → sessione completata
        body = {"device_id": data["device_id"]}
        raw = json.dumps(body).encode()
        resp = await self.client.post(
            "/agent/heartbeat", data=raw,
            headers={"X-Metnos-Device-Sig": _b64u(priv.sign(raw)),
                     "Content-Type": "application/json"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "heartbeat")

    async def test_status_endpoint(self):
        s = self._new_session("pc-status")
        resp = await self.client.get(f"/agent/client/join/{s['join_id']}/status")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["state"], "created")
        self.assertEqual(data["device_name"], "pc-status")


class InstallerQuotingTests(unittest.TestCase):
    """Quoting/charset robusti dei valori baked (server_url dall'header Host)."""

    def test_sh_squote_neutralizes_quote(self):
        import agent_server as A
        self.assertEqual(A._sh_squote("plain"), "'plain'")
        # un apice non chiude la stringa: resta UN solo token shell
        self.assertEqual(A._sh_squote("a'b"), "'a'\\''b'")

    def test_cmd_env_line_accepts_real_values(self):
        import agent_server as A
        self.assertEqual(
            A._cmd_env_line("METNOS_SERVER", "http://192.168.1.33:8765"),
            'set "METNOS_SERVER=http://192.168.1.33:8765"')
        A._cmd_env_line("METNOS_TOKEN", "DEV.eyJhbGc.sig-b64_url")
        A._cmd_env_line("METNOS_CLIENT_SHA256", "cafe" * 16)
        # Il nome tarball python-build-standalone contiene `+` (inerte in
        # batch): deve passare il charset fail-closed.
        A._cmd_env_line(
            "METNOS_PYTHON_RUNTIME_WIN",
            "cpython-3.12.13+20260623-x86_64-pc-windows-msvc-install_only.tar.gz")

    def test_cmd_env_line_rejects_batch_specials(self):
        # Fail-closed: %/"/spazi/^/! romperebbero cmd.exe o aprirebbero
        # injection nella testa batch — mai emetterli, 503 a monte.
        import agent_server as A
        for evil in ('a"b', "a%b", "a b", "a^b", "a!b", "a&b", ""):
            with self.assertRaises(ValueError, msg=repr(evil)):
                A._cmd_env_line("X", evil)


if __name__ == "__main__":
    unittest.main()

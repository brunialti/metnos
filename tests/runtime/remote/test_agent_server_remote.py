"""test_agent_server_remote — endpoint HTTP poll/result/heartbeat/executor.

Verifica firma sui bytes grezzi (§6.3), rifiuto firma assente/errata, bundle
executor verificato server-side, idempotenza via /agent/result.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_AUTHOR_KEYS = Path.home() / ".config" / "metnos" / "keys"

from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class AgentServerRemoteTests(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        # Isola SOLO il DB device (temp); HOME resta reale così la chiave
        # 'author' (firma invocazioni/bundle) è raggiungibile.
        cls._tmp = tempfile.TemporaryDirectory()
        td = Path(cls._tmp.name)
        cls._orig_db = os.environ.get("METNOS_DEVICES_DB")
        os.environ["METNOS_DEVICES_DB"] = str(td / "devices.db")
        # Pin la dir executor al repo (altri test ricaricano config, §7.11).
        import config as _C
        cls._orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        import config as _C
        _C.PATH_EXECUTORS = cls._orig_exec
        if cls._orig_db is not None:
            os.environ["METNOS_DEVICES_DB"] = cls._orig_db
        else:
            os.environ.pop("METNOS_DEVICES_DB", None)

    async def get_application(self):
        import importlib
        import sign
        import devices
        import invocations
        import agent_server
        # Other HTTP suites intentionally import runtime modules under an
        # isolated HOME. Restore the real author-key root captured at module
        # collection; test order must not select a signing identity.
        sign.KEYS_DIR = _AUTHOR_KEYS
        importlib.reload(devices)
        importlib.reload(invocations)
        importlib.reload(agent_server)
        self.devices = devices
        self.invocations = invocations
        # Device di test.
        self.priv = Ed25519PrivateKey.generate()
        pub_raw = self.priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.pub_b64 = _b64u(pub_raw)
        token = devices.generate_token("test-dev")
        self.device = devices.consume_token(token, self.pub_b64)
        return agent_server.make_app()

    def _sign(self, raw: bytes) -> str:
        return _b64u(self.priv.sign(raw))

    async def _signed_post(self, path: str, body: dict):
        raw = json.dumps(body).encode("utf-8")
        return await self.client.post(
            path, data=raw,
            headers={"X-Metnos-Device-Sig": self._sign(raw),
                     "Content-Type": "application/json"})

    async def test_health(self):
        resp = await self.client.get("/agent/health")
        self.assertEqual(resp.status, 200)

    async def test_poll_missing_signature_rejected(self):
        resp = await self.client.post("/agent/poll", json={"device_id": self.device.id})
        self.assertEqual(resp.status, 401)

    async def test_poll_bad_signature_rejected(self):
        raw = json.dumps({"device_id": self.device.id, "block_ms": 0}).encode()
        resp = await self.client.post(
            "/agent/poll", data=raw,
            headers={"X-Metnos-Device-Sig": _b64u(b"\x00" * 64),
                     "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403)

    async def test_poll_empty_then_invocation(self):
        # coda vuota → invocation null
        resp = await self._signed_post(
            "/agent/poll", {"device_id": self.device.id, "block_ms": 0})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIsNone(data["invocation"])
        dev = self.devices.get_device(self.device.id)
        self.assertIsNotNone(dev.last_poll)

        # accoda → poll la ritorna firmata
        inv_id = self.invocations.enqueue_invocation(
            self.device.id, "find_packages", {"package_name": "git"})
        resp = await self._signed_post(
            "/agent/poll", {"device_id": self.device.id, "block_ms": 0})
        data = await resp.json()
        self.assertIsNotNone(data["invocation"])
        self.assertEqual(data["invocation"]["invocation_id"], inv_id)
        self.assertIn("server_sig", data["invocation"])

    async def test_result_roundtrip_and_idempotency(self):
        inv_id = self.invocations.enqueue_invocation(
            self.device.id, "find_packages", {"package_name": "git"})
        self.invocations.next_invocation(self.device.id)
        result = {
            "invocation_id": inv_id, "device_id": self.device.id, "ok": True,
            "entries": [{"path": "/usr/bin/git"}], "n_processed": 1,
            "elapsed_ms": 3, "sandbox": "none",
        }
        resp = await self._signed_post("/agent/result", result)
        self.assertEqual(resp.status, 200)
        info = self.invocations.get_invocation(inv_id)
        self.assertEqual(info["state"], "done")

        # secondo result (ok:false) NON sovrascrive (§6.4)
        result2 = dict(result, ok=False, entries=[])
        resp = await self._signed_post("/agent/result", result2)
        self.assertEqual(resp.status, 200)
        info = self.invocations.get_invocation(inv_id)
        self.assertEqual(info["state"], "done")
        self.assertEqual(len(info["result"]["entries"]), 1)

    async def test_result_bad_signature_rejected(self):
        inv_id = self.invocations.enqueue_invocation(
            self.device.id, "find_packages", {"package_name": "git"})
        result = {
            "invocation_id": inv_id, "device_id": self.device.id, "ok": True,
            "entries": [], "n_processed": 0, "elapsed_ms": 1, "sandbox": "none",
        }
        raw = json.dumps(result).encode()
        resp = await self.client.post(
            "/agent/result", data=raw,
            headers={"X-Metnos-Device-Sig": _b64u(b"\x00" * 64),
                     "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403)

    async def test_heartbeat_updates_profile(self):
        resp = await self._signed_post(
            "/agent/heartbeat",
            {"device_id": self.device.id, "profile": {"cpu_count": 8}})
        self.assertEqual(resp.status, 200)
        dev = self.devices.get_device(self.device.id)
        self.assertIsNotNone(dev.last_heartbeat)
        # Il task heartbeat non millanta che il worker abbia effettuato poll.
        self.assertIsNone(dev.last_poll)
        self.assertIn("cpu_count", dev.profile_json or "")

    async def test_signed_heartbeat_network_observation_cannot_be_substituted(self):
        observation = {"address": "192.0.2.7", "observed_at": 1000}
        body = {"device_id": self.device.id, "profile": {"network_observation": observation}}
        resp = await self._signed_post("/agent/heartbeat", body)
        self.assertEqual(resp.status, 200)
        signed = json.dumps(body).encode()
        tampered = signed.replace(b"192.0.2.7", b"192.0.2.9")
        resp = await self.client.post(
            "/agent/heartbeat", data=tampered,
            headers={"X-Metnos-Device-Sig": self._sign(signed), "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403)
        dev = self.devices.get_device(self.device.id)
        self.assertEqual(json.loads(dev.profile_json)["network_observation"], observation)

    async def test_executor_bundle_verified(self):
        resp = await self.client.get("/agent/executor/find_packages")
        self.assertEqual(resp.status, 200)
        bundle = await resp.json()
        self.assertEqual(bundle["name"], "find_packages")
        self.assertIn("find_packages.py", bundle["files"])
        # digest del codice = quello atteso.
        expected_manifest_sha, expected_code_sha = self.invocations.executor_shas(
            "find_packages")
        manifest_bytes = base64.b64decode(bundle["manifest_toml"])
        import hashlib
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(),
                         expected_manifest_sha)

    async def test_executor_bundle_unknown_404(self):
        resp = await self.client.get("/agent/executor/no_such_xyz")
        self.assertEqual(resp.status, 404)

    async def test_shim_bundle_signed(self):
        resp = await self.client.get("/agent/shim")
        self.assertEqual(resp.status, 200)
        bundle = await resp.json()
        self.assertIn("executor_helpers.py", bundle["files"])
        self.assertIn("messages.py", bundle["files"])
        # C7 read-only: path_alias spedito nello shim → sblocca list_dirs
        # (e i futuri find/read) sul device.
        self.assertIn("path_alias.py", bundle["files"])
        self.assertIn("parallel_walk.py", bundle["files"])
        self.assertIn("tabular_projection.py", bundle["files"])
        self.assertIn("executor_workers.py", bundle["files"])
        # C7 Area-2 CP1: CHIUSURA ad albero per gli executor files — il
        # dispatcher importa `backends.files.local` (+ platform_policy +
        # config a module-load). Chiavi col separatore '/' (formato wire).
        for key in ("backends/__init__.py", "backends/files/__init__.py",
                    "backends/files/local.py", "platform_policy.py",
                    "config.py"):
            self.assertIn(key, bundle["files"])
        # La chiusura resta stdlib-only a module-load: il local.py spedito
        # NON deve importare top-level moduli fuori bundle (§2.8 sul device:
        # meglio scoprirlo qui che con un ModuleNotFoundError remoto).
        import base64 as _b64
        local_src = _b64.b64decode(
            bundle["files"]["backends/files/local.py"]).decode("utf-8")
        for _line in local_src.splitlines():
            if _line.startswith(("import ", "from ")) and "__future__" not in _line:
                _mod = _line.split()[1].split(".")[0].rstrip(",")
                self.assertIn(_mod, {
                    "base64", "datetime", "fnmatch", "hashlib", "html", "io", "json",
                    "mimetypes", "os", "re", "shutil", "stat", "sys",
                    "tempfile", "pathlib", "typing", "xml", "zipfile", "zlib",
                    "platform_policy", "messages", "executor_helpers",
                    "config", "path_alias", "parallel_walk",
                    "tabular_projection",
                }, f"import a module-load fuori chiusura: {_line}")
        self.assertIn("sig", bundle)

    async def test_runtime_descriptor_is_signed_over_actual_archive(self):
        import agent_mirror
        import hashlib
        old_root = agent_mirror.MIRROR_RUNTIME_DIR
        runtime_root = Path(self._tmp.name) / "runtime"
        runtime_root.mkdir(exist_ok=True)
        archive = runtime_root / "cpython-test-install_only.tar.gz"
        archive.write_bytes(b"signed runtime test bytes")
        agent_mirror.MIRROR_RUNTIME_DIR = runtime_root
        try:
            resp = await self.client.get(
                "/agent/runtime/descriptor/cpython-test-install_only.tar.gz")
            self.assertEqual(resp.status, 200)
            envelope = await resp.json()
            desc = envelope["descriptor"]
            self.assertEqual(desc["archive_sha256"],
                             hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertTrue(self.invocations.verify_payload(
                self.invocations.server_public_key_b64(),
                envelope["sig"], desc))
        finally:
            agent_mirror.MIRROR_RUNTIME_DIR = old_root


class InvokeRemotePayloadTests(unittest.TestCase):
    """Il round-trip remoto deve consegnare l'output COMPLETO dell'executor
    (§2.6), non solo `entries`: bug live 3/7 (compute_files_loc → n_processed
    5 ma entries [] e nessun dato LOC, perché total_lines/by_path/summary
    venivano scartati). `invoke_remote` espone `payload` come result locale."""

    def _fake_executor(self):
        class _E:
            name = "compute_files_loc"
            revertible = False
        return _E()

    def test_payload_exposed_as_local_result(self):
        import remote_exec
        from unittest import mock
        # Un client 0.2.6+: il wire result porta `payload` con l'output pieno.
        wire = {
            "invocation_id": "inv-x", "device_id": "dev-abc", "ok": True,
            "entries": [], "n_processed": 5, "elapsed_ms": 42,
            "sandbox": "job-object",
            "payload": {
                "ok": True, "total_files": 5, "total_lines": 900,
                "by_path": [{"path": "a.py", "lines": 180}], "summary": "5 file",
            },
        }
        with mock.patch.object(remote_exec.invocations, "enqueue_invocation",
                               return_value="inv-x"), \
             mock.patch.object(remote_exec.invocations, "wait_result",
                               return_value=wire), \
             mock.patch.object(remote_exec.invocations, "get_invocation",
                               return_value={
                                   "created_epoch": 100.0,
                                   "delivered_epoch": 100.1,
                                   "completed_epoch": 100.2,
                               }), \
             mock.patch("devices.get_device", return_value=None):
            out = remote_exec.invoke_remote(
                self._fake_executor(), {"paths": ["/x"]}, "dev-abc", timeout_s=1)
        # Le chiavi di dominio sopravvivono (prima perse).
        self.assertEqual(out["total_lines"], 900)
        self.assertEqual(out["summary"], "5 file")
        self.assertEqual(out["by_path"][0]["lines"], 180)
        self.assertTrue(out["ok"])
        # Metadati di trasporto namespaced, non collidono con l'executor.
        self.assertEqual(out["_remote"]["sandbox"], "job-object")
        self.assertEqual(out["_remote"]["device_id"], "dev-abc")
        self.assertEqual(out["_remote"]["queue_ms"], 100)
        self.assertEqual(out["_remote"]["delivered_to_completed_ms"], 100)
        self.assertEqual(out["_remote"]["non_executor_ms"], 58)
        self.assertEqual(out["_remote"]["roundtrip_ms"], 200)

    def test_thin_body_fallback_when_no_payload(self):
        import remote_exec
        from unittest import mock
        # Client pre-payload (0.2.5): nessun `payload` → thin body invariato.
        wire = {
            "invocation_id": "inv-y", "device_id": "dev-abc", "ok": True,
            "entries": [{"path": "/usr/bin/git"}], "n_processed": 1,
            "elapsed_ms": 3, "sandbox": "none",
        }
        with mock.patch.object(remote_exec.invocations, "enqueue_invocation",
                               return_value="inv-y"), \
             mock.patch.object(remote_exec.invocations, "wait_result",
                               return_value=wire), \
             mock.patch("devices.get_device", return_value=None):
            out = remote_exec.invoke_remote(
                self._fake_executor(), {}, "dev-abc", timeout_s=1)
        self.assertEqual(out["entries"], [{"path": "/usr/bin/git"}])
        self.assertNotIn("_remote", out)


class InvokeRemoteEnvAndDeadlineTests(unittest.TestCase):
    """turn_id nell'env del sandbox device + deadline scalata per mutanti di
    massa (bug live 1ba8e2c4, 6/7). Cattura i kwargs dell'enqueue via mock."""

    def _revertible_exec(self, name="delete_files"):
        class _E:
            revertible = True
        _E.name = name
        return _E()

    def _capture_enqueue(self, executor, args, **kw):
        import remote_exec
        from unittest import mock
        captured = {}

        def _fake_enqueue(device_id, ex_name, ex_args, **kwargs):
            captured.update(kwargs)
            captured["args"] = ex_args
            return "inv-cap"
        with mock.patch.object(remote_exec.invocations, "enqueue_invocation",
                               side_effect=_fake_enqueue), \
             mock.patch.object(remote_exec.invocations, "wait_result",
                               return_value={"invocation_id": "inv-cap",
                                             "device_id": "d", "ok": True}), \
             mock.patch("devices.get_device", return_value=None):
            remote_exec.invoke_remote(executor, args, "d", **kw)
        return captured

    def test_turn_id_injected_into_env(self):
        cap = self._capture_enqueue(
            self._revertible_exec(), {"paths": ["/x"]},
            timeout_s=30, turn_id="abc123")
        self.assertEqual(cap["env_injections"]["METNOS_TURN_ID"], "abc123")

    def test_explicit_env_injection_wins(self):
        cap = self._capture_enqueue(
            self._revertible_exec(), {"paths": ["/x"]},
            timeout_s=30, turn_id="abc123",
            env_injections={"METNOS_TURN_ID": "explicit"})
        self.assertEqual(cap["env_injections"]["METNOS_TURN_ID"], "explicit")

    def test_no_turn_id_only_lang_injected(self):
        # §7.13 (7/7): METNOS_LANG (lingua istanza) e' SEMPRE iniettato — il
        # device non ha il DB i18n e rende i messaggi user-facing nel repertorio
        # bundleato; senza, cadrebbe su 'en'. METNOS_TURN_ID solo con turn_id.
        cap = self._capture_enqueue(
            self._revertible_exec(), {"paths": ["/x"]}, timeout_s=30)
        env = cap["env_injections"] or {}
        self.assertIn("METNOS_LANG", env)
        self.assertNotIn("METNOS_TURN_ID", env)

    def test_mass_delete_deadline_scaled(self):
        cap = self._capture_enqueue(
            self._revertible_exec(), {"paths": ["/x"] * 681, "client": "local"},
            timeout_s=30, turn_id="t")
        self.assertEqual(cap["deadline_ms"], 600_000)

    def test_readonly_deadline_untouched(self):
        cap = self._capture_enqueue(
            self._revertible_exec("find_files"), {"paths": ["/x"] * 681},
            timeout_s=30, reversibility="read_only")
        self.assertEqual(cap["deadline_ms"], 30_000)


if __name__ == "__main__":
    unittest.main()

"""test_invocations — coda invocazioni firmate + canonical JSON (executor remoti).

Copre: canonical bytes deterministici (contratto col client Rust), firma
server (server_sig), claim atomico, idempotenza §6.4, verifica firma device
sui bytes GREZZI (float-safe §6.3), redelivery orfane, cursor.
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import devices  # noqa: E402
import invocations  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class DeviceKey:
    """Coppia di chiavi device + registrazione in un DB temporaneo."""

    def __init__(self, db_path: Path, name: str = "test-dev"):
        self.priv = Ed25519PrivateKey.generate()
        pub_raw = self.priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.pub_b64 = _b64u(pub_raw)
        token = devices.generate_token(name, db_path=db_path)
        self.device = devices.consume_token(token, self.pub_b64, db_path=db_path)

    def sign(self, raw: bytes) -> str:
        return _b64u(self.priv.sign(raw))


class CanonicalTests(unittest.TestCase):
    def test_deterministic_sorted_compact(self):
        b = invocations.canonical_bytes({"b": 1, "a": {"z": True, "e": ["x", 2]}})
        self.assertEqual(b, b'{"a":{"e":["x",2],"z":true},"b":1}')

    def test_non_ascii_not_escaped(self):
        b = invocations.canonical_bytes({"k": "città"})
        self.assertEqual(b, "{\"k\":\"città\"}".encode("utf-8"))

    def test_rejects_float(self):
        with self.assertRaises(invocations.InvocationError):
            invocations.canonical_bytes({"x": 1.5})


class InvocationQueueTests(unittest.TestCase):
    def setUp(self):
        # Pin la dir executor al repo: altri test nel suite ricaricano `config`
        # con una root temporanea (§7.11), lasciando PATH_EXECUTORS altrove.
        import config as _C
        self._orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "devices.db"
        self.key = DeviceKey(self.db)
        self.device_id = self.key.device.id

    def tearDown(self):
        import config as _C
        _C.PATH_EXECUTORS = self._orig_exec
        self._tmp.cleanup()

    def test_enqueue_and_server_sig_verifies(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"},
            db_path=self.db)
        wire = invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertIsNotNone(wire)
        self.assertEqual(wire["invocation_id"], inv_id)
        # server_sig verifica sui bytes canonici del payload senza il campo sig.
        sig = wire.pop("server_sig")
        server_pub = invocations.server_public_key_b64()
        self.assertTrue(invocations.verify_payload(server_pub, sig, wire))

    def test_next_invocation_empty(self):
        self.assertIsNone(invocations.next_invocation(self.device_id, db_path=self.db))

    def test_cursor_excludes_seen(self):
        a = invocations.enqueue_invocation(self.device_id, "find_packages",
                                           {"package_name": "git"}, db_path=self.db)
        b = invocations.enqueue_invocation(self.device_id, "find_packages",
                                           {"package_name": "curl"}, db_path=self.db)
        first = invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertEqual(first["invocation_id"], a)
        # con cursor = a, la prossima e' b (a e' delivered e non ancora scaduta)
        second = invocations.next_invocation(self.device_id, cursor=a, db_path=self.db)
        self.assertEqual(second["invocation_id"], b)

    def _complete(self, inv_id, *, ok=True, entries=None, tamper=False):
        result = {
            "invocation_id": inv_id,
            "device_id": self.device_id,
            "ok": ok,
            "entries": entries if entries is not None else [{"path": "/usr/bin/git"}],
            "n_processed": 1,
            "elapsed_ms": 5,
            "sandbox": "bwrap",
        }
        raw = json.dumps(result).encode("utf-8")
        sig = self.key.sign(raw)
        if tamper:
            raw = raw + b" "  # bytes diversi da quelli firmati
        return invocations.complete_invocation(
            result, raw_body=raw, sig_b64=sig, db_path=self.db)

    def test_complete_valid_sig(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertTrue(self._complete(inv_id))
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "done")
        self.assertEqual(info["result"]["entries"][0]["path"], "/usr/bin/git")

    def test_complete_bad_sig_rejected(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        with self.assertRaises(invocations.SignatureError):
            self._complete(inv_id, tamper=True)
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "delivered")  # non registrato

    def test_idempotent_first_result_wins(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertTrue(self._complete(inv_id, ok=True))
        # secondo result (anche con ok diverso) non sovrascrive (§6.4)
        self.assertTrue(self._complete(inv_id, ok=False, entries=[]))
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "done")
        self.assertEqual(len(info["result"]["entries"]), 1)

    def test_float_entries_survive_raw_verification(self):
        """entries con float (punteggi) NON rompono la firma (§6.3 raw)."""
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_files", {"q": "x"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertTrue(self._complete(inv_id, entries=[{"score": 1.5, "id": "a"}]))
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "done")
        self.assertEqual(info["result"]["entries"][0]["score"], 1.5)

    def test_result_from_other_device_rejected(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        other = DeviceKey(self.db, name="intruder")
        result = {
            "invocation_id": inv_id, "device_id": other.device.id,
            "ok": True, "entries": [], "n_processed": 0,
            "elapsed_ms": 1, "sandbox": "none",
        }
        raw = json.dumps(result).encode("utf-8")
        sig = other.sign(raw)
        with self.assertRaises(invocations.SignatureError):
            invocations.complete_invocation(result, raw_body=raw, sig_b64=sig,
                                            db_path=self.db)

    def test_unknown_executor_rejected(self):
        with self.assertRaises(invocations.InvocationError):
            invocations.enqueue_invocation(
                self.device_id, "no_such_executor_xyz", {}, db_path=self.db)


class _InvocationsFixture(unittest.TestCase):
    """Fixture condivisa purge/expire: device appaiato su DB isolato
    (§feedback: mai store reali nei test) + helper di manipolazione righe."""

    def setUp(self):
        import config as _C
        self._orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "devices.db"
        self.key = DeviceKey(self.db)
        self.device_id = self.key.device.id

    def tearDown(self):
        import config as _C
        _C.PATH_EXECUTORS = self._orig_exec
        self._tmp.cleanup()

    def _iso_days_ago(self, days):
        import time as _t
        return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(_t.time() - days * 86400))

    def _epoch_days_ago(self, days):
        import time as _t
        return _t.time() - days * 86400

    def _mk(self, executor="find_packages", args=None):
        return invocations.enqueue_invocation(
            self.device_id, executor, args or {"package_name": "git"}, db_path=self.db)

    def _set(self, inv_id, **cols):
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        try:
            sets = ", ".join(f"{k}=?" for k in cols)
            conn.execute(f"UPDATE invocations SET {sets} WHERE invocation_id=?",
                         (*cols.values(), inv_id))
            conn.commit()
        finally:
            conn.close()

    def _states(self):
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        try:
            return {r[0] for r in conn.execute(
                "SELECT invocation_id FROM invocations")}
        finally:
            conn.close()

    def _state_of(self, inv_id):
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        try:
            return conn.execute(
                "SELECT state, completed_at FROM invocations "
                "WHERE invocation_id=?", (inv_id,)).fetchone()
        finally:
            conn.close()


class PurgeInvocationsTests(_InvocationsFixture):
    """Rilievo #2 (2026-07-04): purge_invocations basata sul COMPLETAMENTO
    (`completed_at`), non sulla consegna. Un terminale senza delivered_epoch
    (es. result da spool su un 'queued') non deve restare orfano per sempre;
    queued/delivered in volo non vanno mai toccati."""

    def test_old_terminal_completed_is_purged(self):
        done = self._mk(); self._set(done, state="done", completed_at=self._iso_days_ago(40))
        failed = self._mk(); self._set(failed, state="failed", completed_at=self._iso_days_ago(31))
        n = invocations.purge_invocations(older_than_days=30, db_path=self.db)
        self.assertEqual(n, 2)
        self.assertNotIn(done, self._states())
        self.assertNotIn(failed, self._states())

    def test_recent_terminal_kept(self):
        done = self._mk(); self._set(done, state="done", completed_at=self._iso_days_ago(5))
        n = invocations.purge_invocations(older_than_days=30, db_path=self.db)
        self.assertEqual(n, 0)
        self.assertIn(done, self._states())

    def test_inflight_never_purged(self):
        # queued vecchio + delivered vecchio (non terminali) NON si toccano.
        q = self._mk(); self._set(q, state="queued", created_at=self._iso_days_ago(90))
        d = self._mk(); self._set(d, state="delivered",
                                  delivered_epoch=self._epoch_days_ago(90),
                                  delivered_at=self._iso_days_ago(90))
        n = invocations.purge_invocations(older_than_days=30, db_path=self.db)
        self.assertEqual(n, 0)
        self.assertEqual(self._states(), {q, d})

    def test_terminal_without_completed_at_fallback(self):
        # IL CASO DEL RILIEVO #2: terminale con completed_at NULL ma
        # delivered_epoch vecchio → purgato via fallback (prima restava orfano).
        orphan = self._mk()
        self._set(orphan, state="done", completed_at=None,
                  delivered_epoch=self._epoch_days_ago(40))
        n = invocations.purge_invocations(older_than_days=30, db_path=self.db)
        self.assertEqual(n, 1)
        self.assertNotIn(orphan, self._states())

    def test_old_expired_is_purged(self):
        # B.4: `expired` è terminale a pieno titolo → retention standard.
        exp = self._mk()
        self._set(exp, state="expired", completed_at=self._iso_days_ago(40))
        n = invocations.purge_invocations(older_than_days=30, db_path=self.db)
        self.assertEqual(n, 1)
        self.assertNotIn(exp, self._states())


class ExpireStaleInvocationsTests(_InvocationsFixture):
    """B.4 (fase 7): il reaper chiude come `expired` le in-volo mai concluse
    oltre TTL; niente più consegna; result reale tardivo accettato comunque;
    notifica onesta per le abbandonate (A.0)."""

    def test_old_queued_expires_fresh_kept(self):
        old = self._mk(); self._set(old, created_at=self._iso_days_ago(2))
        fresh = self._mk()
        n = invocations.expire_stale_invocations(ttl_h=24, db_path=self.db)
        self.assertEqual(n, 1)
        state, completed = self._state_of(old)
        self.assertEqual(state, "expired")
        self.assertTrue(completed, "expired senza completed_at: la retention "
                                   "della purge non lo raccoglierebbe mai")
        self.assertEqual(self._state_of(fresh)[0], "queued")

    def test_delivered_stale_expires_inflight_kept(self):
        # delivered STANTIA (ri-eleggibile alla redelivery) → expired;
        # delivered RECENTE (device al lavoro ora) → intoccabile anche se
        # creata prima del TTL.
        import time as _t
        stale = self._mk()
        self._set(stale, state="delivered",
                  created_at=self._iso_days_ago(2),
                  delivered_epoch=self._epoch_days_ago(2))
        inflight = self._mk()
        self._set(inflight, state="delivered",
                  created_at=self._iso_days_ago(2),
                  delivered_epoch=_t.time())
        n = invocations.expire_stale_invocations(ttl_h=24, db_path=self.db)
        self.assertEqual(n, 1)
        self.assertEqual(self._state_of(stale)[0], "expired")
        self.assertEqual(self._state_of(inflight)[0], "delivered")

    def test_expired_never_redelivered(self):
        old = self._mk(); self._set(old, created_at=self._iso_days_ago(2))
        invocations.expire_stale_invocations(ttl_h=24, db_path=self.db)
        self.assertIsNone(
            invocations.next_invocation(self.device_id, db_path=self.db))

    def test_ttl_zero_disables(self):
        old = self._mk(); self._set(old, created_at=self._iso_days_ago(30))
        n = invocations.expire_stale_invocations(ttl_h=0, db_path=self.db)
        self.assertEqual(n, 0)
        self.assertEqual(self._state_of(old)[0], "queued")

    def test_idempotent(self):
        old = self._mk(); self._set(old, created_at=self._iso_days_ago(2))
        self.assertEqual(
            invocations.expire_stale_invocations(ttl_h=24, db_path=self.db), 1)
        self.assertEqual(
            invocations.expire_stale_invocations(ttl_h=24, db_path=self.db), 0)

    def test_late_result_on_expired_accepted(self):
        # Il device aveva ricevuto l'invocazione PRIMA della scadenza e il suo
        # result arriva dopo: l'evento reale vince (§2.8) → done.
        inv_id = self._mk()
        wire = invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertEqual(wire["invocation_id"], inv_id)
        self._set(inv_id, created_at=self._iso_days_ago(2),
                  delivered_epoch=self._epoch_days_ago(2))
        invocations.expire_stale_invocations(ttl_h=24, db_path=self.db)
        self.assertEqual(self._state_of(inv_id)[0], "expired")
        result = {"invocation_id": inv_id, "device_id": self.device_id,
                  "ok": True, "n_processed": 1,
                  "payload": {"ok": True, "ok_count": 1}}
        raw = json.dumps(result).encode("utf-8")
        self.assertTrue(invocations.complete_invocation(
            result, raw_body=raw, sig_b64=self.key.sign(raw),
            db_path=self.db))
        self.assertEqual(self._state_of(inv_id)[0], "done")

    def test_abandoned_expiry_notifies_origin(self):
        # A.0+B.4: il turno aveva risposto «esito incerto, ti avviso» → alla
        # scadenza l'utente riceve la chiusura onesta (op MAI eseguita).
        import tempfile as _tf
        import user_notices as _un
        orig_dir = _un.NOTICES_DIR
        _un.NOTICES_DIR = Path(_tf.mkdtemp(prefix="metnos_b4_notices_"))
        try:
            inv_id = invocations.enqueue_invocation(
                self.device_id, "delete_files", {"paths": ["/x"]},
                turn_id="tB4", reversibility="revertible",
                origin_actor="host", origin_channel="http", db_path=self.db)
            invocations.mark_abandoned(inv_id, db_path=self.db)
            self._set(inv_id, created_at=self._iso_days_ago(2))
            n = invocations.expire_stale_invocations(ttl_h=24, db_path=self.db)
            self.assertEqual(n, 1)
            out = _un.drain("http", "host")
            self.assertEqual(len(out), 1)
            self.assertIn("delete_files", out[0])
        finally:
            import shutil
            shutil.rmtree(_un.NOTICES_DIR, ignore_errors=True)
            _un.NOTICES_DIR = orig_dir

    def test_non_abandoned_expiry_no_notice(self):
        # Invocazione mai abbandonata (nessun turno in ascolto) → scade in
        # silenzio: nessun destinatario aveva ricevuto «esito incerto».
        import tempfile as _tf
        import user_notices as _un
        orig_dir = _un.NOTICES_DIR
        _un.NOTICES_DIR = Path(_tf.mkdtemp(prefix="metnos_b4_nonotice_"))
        try:
            inv_id = invocations.enqueue_invocation(
                self.device_id, "delete_files", {"paths": ["/x"]},
                origin_actor="host", origin_channel="http", db_path=self.db)
            self._set(inv_id, created_at=self._iso_days_ago(2))
            n = invocations.expire_stale_invocations(ttl_h=24, db_path=self.db)
            self.assertEqual(n, 1)
            self.assertEqual(_un.drain("http", "host"), [])
        finally:
            import shutil
            shutil.rmtree(_un.NOTICES_DIR, ignore_errors=True)
            _un.NOTICES_DIR = orig_dir


if __name__ == "__main__":
    unittest.main()

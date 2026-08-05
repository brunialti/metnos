"""Predisposizione multi-utente (2026-07-04): ogni device appaiato deve
essere associato a un VERO utente del registro (`users.id`), non al sentinel
legacy 'host'. Copre: default owner = host reale, migrazione legacy,
resolver di identificazione, list_by_owner, e la validazione dell'owner al
pairing.

DB isolati via env (devices + users in tempdir dedicata)."""
import importlib
import os
import sys
import tempfile
import unittest



class DevicesOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["METNOS_DEVICES_DB"] = os.path.join(self.tmp, "devices.db")
        os.environ["METNOS_USERS_DB"] = os.path.join(self.tmp, "users.db")
        import users
        import devices
        importlib.reload(users)
        importlib.reload(devices)
        self.users = users
        self.devices = devices
        self.users.init_db()  # bootstrap host
        self.host = self.users.list_users(role="host")[0]

    def tearDown(self):
        os.environ.pop("METNOS_DEVICES_DB", None)
        os.environ.pop("METNOS_USERS_DB", None)

    def test_host_user_id_is_real_registry_id(self):
        # Non piu' il sentinel 'host': un id uuid del registro.
        hid = self.devices.host_user_id()
        self.assertEqual(hid, self.host["id"])
        self.assertNotEqual(hid, "host")

    def test_generate_token_stores_real_owner(self):
        hid = self.host["id"]
        self.devices.generate_token("laptop-x", owner_user_id=hid)
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_DEVICES_DB"])
        row = conn.execute(
            "SELECT owner_user_id FROM device_tokens WHERE name='laptop-x'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], hid)

    def test_legacy_host_rows_migrated(self):
        # Inserisci una riga legacy owner='host' a mano, poi riapri: migrata.
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_DEVICES_DB"])
        conn.executescript(self.devices.SCHEMA)
        conn.execute(
            "INSERT INTO devices (id, name, owner_user_id, public_key_b64, "
            "public_key_fingerprint, paired_at) VALUES "
            "('d1','pc','host','k','fp1','2026-01-01T00:00:00Z')")
        conn.commit()
        conn.close()
        # Riapri via API pubblica → _migrate rimappa 'host' → host reale.
        d = self.devices.get_device("d1")
        self.assertEqual(d.owner_user_id, self.host["id"])

    def test_owner_user_resolves_identity(self):
        u = self.devices.owner_user(self.host["id"])
        self.assertIsNotNone(u)
        self.assertEqual(u["id"], self.host["id"])
        # Tolleranza sentinel legacy → utente host reale.
        u2 = self.devices.owner_user("host")
        self.assertEqual(u2["id"], self.host["id"])

    def _insert_device(self, dev_id, owner_id):
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_DEVICES_DB"])
        conn.executescript(self.devices.SCHEMA)
        conn.execute(
            "INSERT INTO devices (id, name, owner_user_id, public_key_b64, "
            "public_key_fingerprint, paired_at) VALUES (?,?,?,?,?,?)",
            (dev_id, dev_id, owner_id, "k", "fp-" + dev_id,
             "2026-01-01T00:00:00Z"))
        conn.commit()
        conn.close()

    def test_list_by_owner(self):
        guest = self.users.create_user("guest_a", role="guest")
        self._insert_device("d-host", self.host["id"])
        self._insert_device("d-guest", guest["id"])
        host_devs = self.devices.list_by_owner(self.host["id"])
        self.assertEqual([d.id for d in host_devs], ["d-host"])
        guest_devs = self.devices.list_by_owner(guest["id"])
        self.assertEqual([d.id for d in guest_devs], ["d-guest"])

    def test_owner_id_for_actor(self):
        # sentinel/vuoto → host reale (non piu' la stringa 'host').
        self.assertEqual(self.devices.owner_id_for_actor("host"), self.host["id"])
        self.assertEqual(self.devices.owner_id_for_actor(None), self.host["id"])
        # actor = device_id pairato → owner di QUEL device (identificazione).
        guest = self.users.create_user("guest_b", role="guest")
        self._insert_device("dev-g", guest["id"])
        self.assertEqual(self.devices.owner_id_for_actor("dev-g"), guest["id"])
        # actor = name/id utente → quell'utente.
        self.assertEqual(self.devices.owner_id_for_actor("guest_b"), guest["id"])

    def test_two_owners_two_homonym_devices_isolated(self):
        # A3 review: due owner, due device omonimi → ognuno vede solo il suo.
        guest = self.users.create_user("guest_c", role="guest")
        self._insert_device("host-pc", self.host["id"])   # name non conta qui
        self._insert_device("guest-pc", guest["id"])
        who_host = self.devices.owner_id_for_actor("host")
        who_guest = self.devices.owner_id_for_actor("guest_c")
        host_view = [d.id for d in self.devices.list_devices()
                     if d.owner_user_id == who_host]
        guest_view = [d.id for d in self.devices.list_devices()
                      if d.owner_user_id == who_guest]
        self.assertEqual(host_view, ["host-pc"])
        self.assertEqual(guest_view, ["guest-pc"])


if __name__ == "__main__":
    unittest.main()

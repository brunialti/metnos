"""Test del resolver chat-driven placement (runtime/target_device.py) e dello
store appiccicoso (runtime/chat_target_store.py)."""
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import target_device as td  # noqa: E402


@dataclass
class FakeDev:
    id: str
    name: str
    online: bool = True


def _avail(dev, now=None):
    return getattr(dev, "online", True)


def R(query, devices, last=None):
    return td.resolve_target(query, devices, last_target=last, is_available=_avail)


class ResolveTargetTests(unittest.TestCase):
    def setUp(self):
        self.pc = FakeDev("id-ufficio", "PORTATILE-UFFICIO")
        self.casa = FakeDev("id-casa", "FISSO-CASA")

    def test_no_devices_is_server(self):
        r = R("elenca la cartella documenti", [])
        self.assertEqual(r.target, td.SERVER)
        self.assertFalse(r.explicit)

    def test_no_reference_no_sticky_is_server(self):
        r = R("quante righe di codice ci sono", [self.pc])
        self.assertEqual(r.target, td.SERVER)

    def test_named_device_anchored_routes(self):
        r = R("elenca la cartella Documenti sul portatile-ufficio", [self.pc, self.casa])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.target, "id-ufficio")
        self.assertEqual(r.device_name, "PORTATILE-UFFICIO")
        self.assertTrue(r.explicit)
        # l'adjunct di destinazione è stato rimosso
        self.assertNotIn("portatile-ufficio", r.cleaned_query.lower())
        self.assertIn("documenti", r.cleaned_query.lower())

    def test_bare_name_without_preposition_does_not_route(self):
        # nome device presente ma SENZA preposizione locativa → niente routing
        casa = FakeDev("id-casa", "casa")
        r = R("trova le foto di casa", [casa])
        self.assertEqual(r.target, td.SERVER)
        self.assertFalse(r.explicit)

    def test_named_device_offline_is_unreachable(self):
        self.pc.online = False
        r = R("comprimi documenti sul portatile-ufficio", [self.pc])
        self.assertEqual(r.status, "unreachable")
        self.assertEqual(r.unreachable_name, "PORTATILE-UFFICIO")

    def test_local_marker_single_device(self):
        r = R("elenca i file su questo pc", [self.pc])
        self.assertEqual(r.target, "id-ufficio")
        self.assertTrue(r.explicit)

    def test_local_marker_multi_device_ambiguous(self):
        r = R("comprimi la cartella sul mio pc", [self.pc, self.casa])
        self.assertEqual(r.status, "ambiguous")
        self.assertEqual(len(r.candidates), 2)

    def test_server_marker_resets(self):
        r = R("quanti processi girano qui sul server", [self.pc])
        self.assertEqual(r.target, td.SERVER)
        self.assertTrue(r.explicit)

    def test_sticky_reused_when_no_reference(self):
        r = R("comprimila in zip", [self.pc], last="id-ufficio")
        self.assertEqual(r.target, "id-ufficio")
        self.assertFalse(r.explicit)   # riuso, non nuovo esplicito

    def test_sticky_offline_decays_to_server(self):
        # #1 assessor: appiccicoso offline (implicito) → server, NON errore
        # («che ore sono» non deve fallire solo perché l'ultimo PC è spento).
        self.pc.online = False
        r = R("comprimila in zip", [self.pc], last="id-ufficio")
        self.assertEqual(r.target, td.SERVER)
        self.assertEqual(r.status, "ok")
        self.assertFalse(r.explicit)

    def test_explicit_offline_still_unreachable(self):
        # un riferimento ESPLICITO a un PC offline resta «non connesso» (§2.8).
        self.pc.online = False
        r = R("elenca documenti sul portatile-ufficio", [self.pc])
        self.assertEqual(r.status, "unreachable")

    def test_duplicate_names_ambiguous(self):
        # #5 assessor: due device con lo STESSO nome → ambiguo, non arbitrario.
        a = FakeDev("id-a", "fisso-casa")
        b = FakeDev("id-b", "fisso-casa")
        r = R("elenca la cartella sul fisso-casa", [a, b])
        self.assertEqual(r.status, "ambiguous")
        self.assertEqual(len(r.candidates), 2)

    def test_sticky_device_gone_decays_to_server(self):
        r = R("comprimila in zip", [self.pc], last="id-sparito")
        self.assertEqual(r.target, td.SERVER)

    def test_longest_name_wins(self):
        a = FakeDev("a", "mac")
        b = FakeDev("b", "mac-studio")
        r = R("apri la cartella sul mac-studio", [a, b])
        self.assertEqual(r.target, "b")


class ReferencesDeviceTests(unittest.TestCase):
    """Guardia fast_path: la query che cita un device deve saltare il fast_path."""
    def setUp(self):
        self.pc = FakeDev("id-ufficio", "PORTATILE-UFFICIO")

    def test_named_device_is_reference(self):
        self.assertTrue(td.references_device("elenca documenti sul portatile-ufficio", [self.pc]))

    def test_local_marker_is_reference(self):
        self.assertTrue(td.references_device("che file ci sono su questo pc", [self.pc]))

    def test_server_marker_is_reference(self):
        self.assertTrue(td.references_device("quanti processi qui sul server", [self.pc]))

    def test_plain_query_is_not_reference(self):
        self.assertFalse(td.references_device("quante righe di codice ci sono", [self.pc]))

    def test_bare_name_is_not_reference(self):
        casa = FakeDev("c", "casa")
        self.assertFalse(td.references_device("trova le foto di casa", [casa]))


class DeviceEligibleManifestTests(unittest.TestCase):
    """F1/#4 (review 2026-07-04): l'eleggibilità al device è PURO manifest-driven
    (`[placement] device_ok=true`), la whitelist hardcoded è stata rimossa.
    Invariante GENERALE, catalog-driven: OGNI executor che dichiara device_ok
    DEVE dichiarare anche 'windows' in platforms — altrimenti choose_placement lo
    rifiuta sul device reale (Windows). Guard di regressione."""
    def test_device_ok_executors_declare_windows(self):
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__)))) + "/runtime")
        import loader
        cat = list(loader.load_catalog())
        device_ok = [e for e in cat
                     if (getattr(e, "placement", None) or {}).get("device_ok")]
        names = {e.name for e in device_ok}
        # sanity: i 3 read-only C7 non devono perdere device_ok nel manifest
        for expected in ("get_files", "compute_files_loc", "list_dirs"):
            self.assertIn(expected, names, f"{expected}: perso device_ok nel manifest")
        # invariante generale: device_ok ⇒ windows in platforms
        for e in device_ok:
            plats = getattr(e, "platforms", None) or ["linux"]
            self.assertIn("windows", plats,
                          f"{e.name}: device_ok=true ma manca 'windows' in platforms (F1)")


class StickyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["METNOS_CHAT_TARGET_DB"] = os.path.join(self.tmp, "ct.db")
        # ricarica il modulo per rileggere l'env
        import importlib
        import chat_target_store
        importlib.reload(chat_target_store)
        self.store = chat_target_store

    def tearDown(self):
        os.environ.pop("METNOS_CHAT_TARGET_DB", None)

    def test_get_missing_is_none(self):
        self.assertIsNone(self.store.get_last_target("tg:roberto"))

    def test_set_then_get_roundtrip(self):
        self.store.set_last_target("tg:roberto", "id-ufficio", "PORTATILE-UFFICIO")
        self.assertEqual(self.store.get_last_target("tg:roberto"), "id-ufficio")

    def test_upsert_overwrites(self):
        self.store.set_last_target("tg:roberto", "id-ufficio", "X")
        self.store.set_last_target("tg:roberto", "server", None)
        self.assertEqual(self.store.get_last_target("tg:roberto"), "server")

    def test_empty_sender_noop(self):
        self.store.set_last_target("", "id-x")
        self.assertIsNone(self.store.get_last_target(""))


if __name__ == "__main__":
    unittest.main()

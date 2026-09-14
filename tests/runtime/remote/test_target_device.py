"""Test del resolver chat-driven placement (runtime/target_device.py) e dello
store appiccicoso (runtime/chat_target_store.py)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


import target_device as td  # noqa: E402


@dataclass
class FakeDev:
    id: str
    name: str
    online: bool = True


def _avail(dev, now=None):
    return getattr(dev, "online", True)


def R(query, devices, last=None, *, server_aliases=None):
    return td.resolve_target(
        query, devices, last_target=last, is_available=_avail,
        server_aliases=server_aliases,
    )


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

    def test_bare_technical_identity_is_preserved_as_request_data(self):
        for query in (
            "fa ping a portatile-ufficio",
            "fai ping a portatile-ufficio",
            "ping portatile-ufficio",
            "check connectivity to portatile-ufficio",
            "confronta portatile-ufficio con il risultato precedente",
        ):
            with self.subTest(query=query):
                r = R(query, [self.pc])
                self.assertEqual(r.status, "ok")
                self.assertEqual(r.target, self.pc.id)
                self.assertEqual(r.cleaned_query, query)

    def test_bare_identity_is_preserved_beside_explicit_execution_adjunct(self):
        query = "ping portatile-ufficio sul fisso-casa"
        r = R(query, [self.pc, self.casa])
        self.assertEqual(r.target, self.casa.id)
        self.assertEqual(r.cleaned_query, "ping portatile-ufficio")

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

    def test_local_marker_selects_the_only_available_device(self):
        self.casa.online = False
        r = R("comprimi la cartella sul mio pc", [self.pc, self.casa])
        self.assertEqual(r.target, "id-ufficio")
        self.assertTrue(r.explicit)

    def test_server_marker_resets(self):
        r = R("quanti processi girano qui sul server", [self.pc])
        self.assertEqual(r.target, td.SERVER)
        self.assertTrue(r.explicit)

    def test_server_identity_resets_sticky_for_machine_question(self):
        r = R(
            "temperatura cpu metnos", [self.pc], last="id-ufficio",
            server_aliases=["metnos"],
        )
        self.assertEqual(r.target, td.SERVER)
        self.assertTrue(r.explicit)

    def test_explicit_device_wins_over_weak_server_identity(self):
        r = R(
            "installa Metnos sul portatile-ufficio", [self.pc],
            server_aliases=["metnos"],
        )
        self.assertEqual(r.target, "id-ufficio")
        self.assertTrue(r.explicit)

    def test_weak_server_alias_never_overrides_strong_device_target(self):
        for query in (
            "machine status on portatile-ufficio for metnos",
            "temperatura cpu sul portatile-ufficio con metnos",
            "on portatile-ufficio check system status in metnos",
        ):
            r = R(query, [self.pc], server_aliases=["metnos"])
            self.assertEqual(r.target, "id-ufficio")
            self.assertTrue(r.explicit)

    def test_later_weak_alias_can_revoke_the_same_server_identity(self):
        for query in (
            "controlla temperatura cpu sul server, ma non metnos",
            "inspect cpu temperature on the server, but not metnos",
        ):
            r = R(
                query, [self.pc], last="id-ufficio",
                server_aliases=["metnos"],
            )
            self.assertEqual(r.target, "id-ufficio")
            self.assertFalse(r.explicit)

    def test_overlapping_weak_alias_does_not_erase_strong_marker_strip(self):
        r = R(
            "temperatura cpu sul metnos", [self.pc],
            server_aliases=["metnos"],
        )
        self.assertEqual(r.target, td.SERVER)
        self.assertTrue(r.explicit)
        self.assertEqual(r.cleaned_query, "temperatura cpu")

    def test_server_identity_is_not_a_generic_placement_marker(self):
        r = R(
            "trova il file metnos", [self.pc], last="id-ufficio",
            server_aliases=["metnos"],
        )
        self.assertEqual(r.target, "id-ufficio")
        self.assertFalse(r.explicit)

    def test_negated_server_does_not_override_italian_local_target(self):
        r = R(
            "Sul mio computer crea /tmp/prova.txt, senza ripiegare sul server.",
            [self.pc],
        )
        self.assertEqual(r.target, "id-ufficio")
        self.assertTrue(r.explicit)

    def test_negated_server_does_not_override_english_local_target(self):
        r = R(
            "On my computer create /tmp/test.txt without falling back to the server.",
            [self.pc],
        )
        self.assertEqual(r.target, "id-ufficio")
        self.assertTrue(r.explicit)

    def test_coordinated_negated_server_does_not_override_sticky_italian(self):
        r = R(
            "non eseguire sul mio computer, o sul server",
            [self.pc], last="id-ufficio",
        )
        self.assertEqual(r.status, "ambiguous")

    def test_coordinated_negated_server_does_not_override_sticky_english(self):
        r = R(
            "do not execute on my computer, or on the server",
            [self.pc], last="id-ufficio",
        )
        self.assertEqual(r.status, "ambiguous")

    def test_colon_target_list_remains_negated(self):
        for query in (
            "non eseguire su nessuno di questi: sul mio computer o sul server",
            "do not execute on either of these: on my computer or on the server",
        ):
            r = R(query, [self.pc], last="id-ufficio")
            self.assertEqual(r.status, "ambiguous")

    def test_sequence_after_comma_can_assert_server_target(self):
        for query in (
            "non eseguire sul mio computer, e poi esegui sul server",
            "do not execute on my computer, and then execute on the server",
        ):
            r = R(query, [self.pc], last="id-ufficio")
            self.assertEqual(r.target, td.SERVER)
            self.assertTrue(r.explicit)

    def test_strong_sequence_can_assert_a_new_named_target(self):
        for query in (
            "non sul server, e poi esegui sul portatile-ufficio",
            "not on the server, and then execute on portatile-ufficio",
        ):
            r = R(query, [self.pc])
            self.assertEqual(r.target, "id-ufficio")
            self.assertTrue(r.explicit)

    def test_negated_local_target_is_not_reused_from_sticky(self):
        for query in (
            "non eseguire sul mio pc",
            "do not execute on my computer",
        ):
            r = R(query, [self.pc], last="id-ufficio")
            self.assertEqual(r.target, td.SERVER)
            self.assertFalse(r.explicit)

    def test_negated_server_cannot_be_selected_as_default(self):
        for query in (
            "non eseguire sul server",
            "do not execute on the server",
        ):
            r = R(query, [self.pc])
            self.assertEqual(r.status, "ambiguous")

    def test_negated_server_alias_cannot_be_selected(self):
        for query in (
            "non controllare temperatura cpu metnos",
            "do not inspect cpu temperature on metnos",
        ):
            r = R(query, [], server_aliases=["metnos"])
            self.assertEqual(r.status, "ambiguous")

    def test_unavailable_polarity_cannot_select_a_fallback_target(self):
        with patch(
            "detection_lexicon.polarity_state_at",
            return_value="unavailable",
        ):
            r = R("esegui sul mio pc", [self.pc], last="id-ufficio")
        self.assertEqual(r.status, "ambiguous")

    def test_negated_named_device_is_not_an_explicit_target(self):
        for query in (
            "non eseguire sul portatile-ufficio",
            "do not execute on portatile-ufficio",
        ):
            r = R(query, [self.pc])
            self.assertEqual(r.target, td.SERVER)
            self.assertFalse(r.explicit)
            sticky = R(query, [self.pc], last="id-ufficio")
            self.assertEqual(sticky.target, td.SERVER)
            self.assertFalse(sticky.explicit)

    def test_later_asserted_named_device_is_not_hidden_by_negated_mention(self):
        for query in (
            "non sul portatile-ufficio, ma sul portatile-ufficio",
            "not on portatile-ufficio, but on portatile-ufficio",
        ):
            r = R(query, [self.pc])
            self.assertEqual(r.target, "id-ufficio")
            self.assertTrue(r.explicit)

    def test_later_revocation_wins_for_each_target_form(self):
        cases = (
            "esegui sul server, ma non eseguire sul server",
            "execute on the server, but do not execute on the server",
            "esegui sul portatile-ufficio, ma non eseguire sul portatile-ufficio",
            "execute on portatile-ufficio, but do not execute on portatile-ufficio",
            "esegui sul mio computer, ma non eseguire sul mio computer",
            "execute on my computer, but do not execute on my computer",
        )
        for query in cases:
            r = R(query, [self.pc])
            self.assertFalse(r.explicit)
            if "server" in query:
                self.assertEqual(r.status, "ambiguous")
            else:
                self.assertEqual(r.target, td.SERVER)

    def test_later_target_correction_wins_across_identities(self):
        casa = FakeDev("id-casa", "PC-CASA")
        r = R(
            "esegui sul server, ma sul portatile-ufficio",
            [self.pc, casa],
        )
        self.assertEqual(r.target, "id-ufficio")
        self.assertTrue(r.explicit)
        r = R(
            "esegui sul portatile-ufficio, ma sul pc-casa",
            [self.pc, casa],
        )
        self.assertEqual(r.target, "id-casa")
        self.assertTrue(r.explicit)

    def test_later_local_assertion_overrides_earlier_named_negation(self):
        for query in (
            "non sul portatile-ufficio, ma sul mio computer",
            "not on portatile-ufficio, but on my computer",
        ):
            r = R(query, [self.pc])
            self.assertEqual(r.target, "id-ufficio")
            self.assertTrue(r.explicit)

    def test_sticky_reused_when_no_reference(self):
        r = R("comprimila in zip", [self.pc], last="id-ufficio")
        self.assertEqual(r.target, "id-ufficio")
        self.assertFalse(r.explicit)   # riuso, non nuovo esplicito

    def test_sticky_windows_posix_path_goes_server(self):
        r"""Hint forma-path→host (5/7): query con path POSIX assoluto (= fs
        del server) + sticky su device WINDOWS -> SERVER, non il PC
        (/opt/metnos/x diventava C:/opt/x not-found sul PC)."""
        self.pc.os_family = "windows"
        r = R("elenca i file in /opt/metnos/internal/reports",
              [self.pc], last="id-ufficio")
        self.assertEqual(r.target, td.SERVER)

    def test_sticky_windows_winpath_stays_device(self):
        self.pc.os_family = "windows"
        r = R("elenca C:\\Windows\\System32", [self.pc], last="id-ufficio")
        self.assertEqual(r.target, "id-ufficio")

    def test_explicit_name_wins_over_path_hint(self):
        """RESTRIZIONE-only (ADR 0179): il nome esplicito vince sull'hint."""
        self.pc.os_family = "windows"
        r = R("elenca /opt/dati sul portatile-ufficio", [self.pc], last=None)
        self.assertEqual(r.target, "id-ufficio")

    def test_server_nominal_marker_overrides_sticky(self):
        """«stato DEL server» = riferimento nominale al server: vince sullo
        sticky (visto live 5/7: finiva sul PC)."""
        r = R("stato del server e primi 3 processi", [self.pc],
              last="id-ufficio")
        self.assertEqual(r.target, td.SERVER)

    def test_sticky_no_path_unchanged(self):
        self.pc.os_family = "windows"
        r = R("comprimila in zip", [self.pc], last="id-ufficio")
        self.assertEqual(r.target, "id-ufficio")

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

    def test_server_identity_machine_question_is_reference(self):
        self.assertTrue(td.references_device(
            "temperatura cpu metnos", [self.pc], server_aliases=["metnos"],
        ))

    def test_plain_query_is_not_reference(self):
        self.assertFalse(td.references_device("quante righe di codice ci sono", [self.pc]))

    def test_bare_name_is_not_reference(self):
        casa = FakeDev("c", "casa")
        self.assertFalse(td.references_device("trova le foto di casa", [casa]))

    def test_negated_named_device_is_still_a_reference_constraint(self):
        self.assertTrue(td.references_device(
            "do not execute on portatile-ufficio", [self.pc],
        ))

    def test_negated_server_alias_is_still_a_reference_constraint(self):
        self.assertTrue(td.references_device(
            "do not inspect cpu temperature on metnos", [],
            server_aliases=["metnos"],
        ))


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

    def test_expired_target_is_not_reused(self):
        self.store.set_last_target("tg:roberto", "id-ufficio", "PORTATILE-UFFICIO")
        future = datetime.now(timezone.utc) + timedelta(minutes=16)
        self.assertIsNone(self.store.get_last_target(
            "tg:roberto", max_age_s=15 * 60, now=future,
        ))

    def test_scope_key_isolates_conversations_and_owners(self):
        base = dict(actor="host", channel="http")
        first = self.store.scope_key(
            owner_user_id="owner-a", conversation_id="one", **base,
        )
        second = self.store.scope_key(
            owner_user_id="owner-a", conversation_id="two", **base,
        )
        other = self.store.scope_key(
            owner_user_id="owner-b", conversation_id="one", **base,
        )
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(first, self.store.scope_key(
            owner_user_id="owner-a", conversation_id="one", **base,
        ))

    def test_upsert_overwrites(self):
        self.store.set_last_target("tg:roberto", "id-ufficio", "X")
        self.store.set_last_target("tg:roberto", "server", None)
        self.assertEqual(self.store.get_last_target("tg:roberto"), "server")

    def test_empty_sender_noop(self):
        self.store.set_last_target("", "id-x")
        self.assertIsNone(self.store.get_last_target(""))


if __name__ == "__main__":
    unittest.main()

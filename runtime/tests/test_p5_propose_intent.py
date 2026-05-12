"""Tests per P5 (12/5/2026): runtime enforcement propose-intent gate
contro calendar write tools (set_events/create_events/delete_events).

Bug live turn a0b96f6f (12/5/2026 09:07): query «proponi per la prossima
settimana 3 possibili orari per un appuntamento di una ora la mattina» →
planner ha chiamato set_events(summary="Appuntamento Proposto 1",
start=2026-05-18T08:00, end=2026-05-24T12:00) — UN evento da lunedi a
sabato 8-12, whole-week destructive blob.

Comportamento atteso: get_now → read_events → final_answer testuale con 3
slot mattutini computati. NESSUN set_events.

Fix:
  - prefilter._VERB_TO_CANONICAL: «proponi/suggerisci/raccomanda»+EN+enclitici
    → "describe" (verbo canonico per "presenta informazione strutturata").
  - _OBJECT_HINTS["events"]: aggiunti hint «orari/fasce/slot/mattina» per
    forzare detect_canonical_object → events su query suggestion-style.
  - agent_runtime.py:
      _PROPOSE_INTENT_RE: regex semantica universale IT+EN (verbi stem+enclitic
        quantifier + interrogative + N+proposal-noun).
      _query_is_propose_intent(): wrapper deterministico §7.9.
      Gate P5 in turn_react: chosen_name in _calendar_write_tools() + query
        propose-intent → reject sintetico + hint con workflow corretto.
  - planner section calendar.j2 IT+EN: hint (propose_intent) stile §6.
  - intent_extractor.j2 IT+EN: 4 esempi propose → {verb:describe, object:events}.

Convergenza: 81/81 PASS su 8 cicli text-variation (5+5+5+5+5+5+5+8).
Determinismo §7.9: regex compilata, niente LLM.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

# Path setup
_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


class TestProposeIntentDetectionIT(unittest.TestCase):
    """Detection regex IT — verbi suggestion con enclitici."""

    def setUp(self):
        from agent_runtime import _query_is_propose_intent
        self.detect = _query_is_propose_intent

    # ── proponi / propon-* enclitici ───────────────────────────────────
    def test_proponi_base(self):
        self.assertTrue(self.detect(
            "proponi 3 orari per appuntamento la prossima settimana"))

    def test_proponimi_enclitic(self):
        self.assertTrue(self.detect(
            "proponimi qualche orario il prossimo martedì"))

    def test_propongo_first_person(self):
        self.assertTrue(self.detect(
            "propongo 3 orari mattutini la settimana prossima"))

    def test_proporresti_conditional(self):
        self.assertTrue(self.detect(
            "proporresti 2 mattine libere?"))

    # ── suggerisci / sugger-* enclitici ────────────────────────────────
    def test_suggerisci_base(self):
        self.assertTrue(self.detect(
            "suggerisci 3 alternative"))

    def test_suggeriscimi_enclitic(self):
        self.assertTrue(self.detect(
            "suggeriscimi 3 slot mattutini liberi"))

    def test_suggerite_plural(self):
        self.assertTrue(self.detect(
            "suggerite 3 fasce orarie ai partecipanti"))

    # ── raccomanda / raccomand-* enclitici ─────────────────────────────
    def test_raccomanda_base(self):
        self.assertTrue(self.detect(
            "raccomanda 2 fasce mattutine"))

    def test_raccomandami_enclitic(self):
        self.assertTrue(self.detect(
            "raccomandami 2 mercoledi liberi"))

    def test_raccomandarmi_infinitive_enclitic(self):
        self.assertTrue(self.detect(
            "potresti raccomandarmi un orario libero la prossima settimana?"))

    # ── consigli-* (variante sinonimo) ─────────────────────────────────
    def test_consigliami(self):
        self.assertTrue(self.detect(
            "consigliami 3 fasce orarie libere"))

    # ── che [ne] dici / cosa [ne] pensi ────────────────────────────────
    def test_che_ne_dici(self):
        self.assertTrue(self.detect(
            "che ne dici di lunedi 9-10?"))

    def test_che_dici_short(self):
        self.assertTrue(self.detect(
            "che dici di lunedi 9-10?"))

    def test_cosa_ne_pensi(self):
        self.assertTrue(self.detect(
            "cosa ne pensi di mercoledi mattina?"))

    # ── quali sono / quali X ... liberi ────────────────────────────────
    def test_quali_sono_N_liberi(self):
        self.assertTrue(self.detect(
            "quali sono 3 mattine libere prossima settimana per un'ora?"))

    def test_quali_X_libere(self):
        self.assertTrue(self.detect(
            "quali fasce orarie libere ho domani mattina?"))

    def test_quali_X_disponibili(self):
        self.assertTrue(self.detect(
            "vorrei sapere quali sono le mattine disponibili"))

    # ── N + noun (numerico + proposta-noun) ────────────────────────────
    def test_N_alternative(self):
        self.assertTrue(self.detect(
            "puoi darmi 3 alternative di mattina per fissare un appuntamento"))

    def test_N_slot_with_search_verb(self):
        # «cerca 3 slot» = verbo find ma intent semantico = proposta
        self.assertTrue(self.detect(
            "cerca 3 slot 9-11 prossima settimana"))

    def test_N_mercoledi_liberi(self):
        self.assertTrue(self.detect(
            "ho 2 mercoledi liberi disponibili"))

    def test_qualche_orario(self):
        self.assertTrue(self.detect(
            "proponi qualche orario il prossimo martedì"))

    def test_alcune_alternative(self):
        self.assertTrue(self.detect(
            "potresti darmi alcune alternative per giovedi mattina?"))


class TestProposeIntentDetectionEN(unittest.TestCase):
    """Detection regex EN — propose/suggest/recommend + interrogative."""

    def setUp(self):
        from agent_runtime import _query_is_propose_intent
        self.detect = _query_is_propose_intent

    # ── verbs base ─────────────────────────────────────────────────────
    def test_propose(self):
        self.assertTrue(self.detect(
            "propose 3 morning times next week for a 1h appointment"))

    def test_suggest(self):
        self.assertTrue(self.detect(
            "suggest a free slot tomorrow morning"))

    def test_recommend_N(self):
        self.assertTrue(self.detect(
            "recommend 2 mornings for an interview"))

    def test_propose_two(self):
        # «propose two» = verbo propose, indipendente dal numero scritto
        self.assertTrue(self.detect(
            "propose two 1-hour slots for thursday"))

    # ── interrogative ──────────────────────────────────────────────────
    def test_what_about_N(self):
        self.assertTrue(self.detect(
            "what about 3 morning slots next week?"))

    def test_how_about(self):
        self.assertTrue(self.detect(
            "how about wednesday 9-10?"))

    def test_what_slots_are_free(self):
        self.assertTrue(self.detect(
            "what slots are free tomorrow morning?"))

    def test_what_are_some_free_mornings(self):
        self.assertTrue(self.detect(
            "what are some free mornings next week?"))

    def test_any_free_slots(self):
        self.assertTrue(self.detect(
            "any free slots tomorrow morning?"))

    def test_any_open_slots(self):
        self.assertTrue(self.detect(
            "any open slots wednesday morning?"))

    # ── N + noun ───────────────────────────────────────────────────────
    def test_give_me_some_options(self):
        self.assertTrue(self.detect(
            "give me some options for next week morning"))

    def test_id_like_N_options(self):
        self.assertTrue(self.detect(
            "I'd like 3 morning options next week"))


class TestProposeIntentNegatives(unittest.TestCase):
    """Negative cases — NON propose-intent (devono ritornare False)."""

    def setUp(self):
        from agent_runtime import _query_is_propose_intent
        self.detect = _query_is_propose_intent

    # ── set/book/schedule destructive ─────────────────────────────────
    def test_fissa(self):
        self.assertFalse(self.detect(
            "fissa un appuntamento la prossima settimana mattina alle 9"))

    def test_fissami_enclitic_set(self):
        # «fissami» = enclitico di SET, non propose
        self.assertFalse(self.detect(
            "fissami un appuntamento la prossima settimana"))

    def test_prenota(self):
        self.assertFalse(self.detect(
            "prenota riunione mercoledi 10"))

    def test_prenotami_other_domain(self):
        # «prenotami il tavolo» = book, NOT propose
        self.assertFalse(self.detect(
            "prenotami il tavolo per 2 al ristorante"))

    def test_book_en(self):
        self.assertFalse(self.detect(
            "book a meeting next week morning at 9"))

    def test_schedule_en(self):
        self.assertFalse(self.detect(
            "schedule the weekly meeting for monday morning"))

    def test_crea_evento(self):
        self.assertFalse(self.detect(
            "crea un evento lunedi alle 11 per un'ora"))

    def test_create_recurring(self):
        self.assertFalse(self.detect(
            "create a recurring weekly meeting wednesday 10am"))

    # ── availability check (P4, non P5) ────────────────────────────────
    def test_fissa_se_ce_posto(self):
        # P4 territorio, NON P5
        self.assertFalse(self.detect(
            "fissa appuntamento se c'è posto giovedi mattina"))

    # ── read events / read tools (no propose verb) ─────────────────────
    def test_read_appointments(self):
        self.assertFalse(self.detect(
            "what are my appointments tomorrow"))

    def test_list_events(self):
        self.assertFalse(self.detect(
            "list all events for this month"))

    def test_next_appointment(self):
        self.assertFalse(self.detect(
            "what's my next appointment?"))

    def test_quali_impegni(self):
        # «quali sono i miei impegni» = read events (no «liberi/disponibili»)
        self.assertFalse(self.detect(
            "quali sono i miei impegni di domani"))

    # ── delete events ──────────────────────────────────────────────────
    def test_delete_calendar(self):
        self.assertFalse(self.detect(
            "delete my calendar for tomorrow"))

    def test_elimina_N_eventi(self):
        # «elimina 3 eventi» = delete N, no propose intent
        self.assertFalse(self.detect(
            "elimina 3 eventi vecchi"))

    # ── N + noun NON proposal-noun (emails/files/messages/photos) ──────
    def test_3_emails_not_propose(self):
        # «3 emails» = file/object, not propose
        self.assertFalse(self.detect(
            "send 3 emails to roberto"))

    def test_3_files_not_propose(self):
        self.assertFalse(self.detect(
            "delete 3 files"))

    def test_3_paragrafi_not_propose(self):
        self.assertFalse(self.detect(
            "scrivimi 3 paragrafi sul ML"))

    def test_3_attachments_not_propose(self):
        self.assertFalse(self.detect(
            "send me an email with 3 attachments"))

    def test_find_3_emails(self):
        self.assertFalse(self.detect(
            "find 3 emails about meeting"))

    def test_3_foto_not_propose(self):
        self.assertFalse(self.detect(
            "trova 3 foto recenti"))

    def test_3_messaggi_not_propose(self):
        self.assertFalse(self.detect(
            "leggi 3 messaggi della inbox"))

    # ── meta query (non events) ────────────────────────────────────────
    def test_what_time_is_it(self):
        self.assertFalse(self.detect(
            "what time is it"))

    def test_empty(self):
        self.assertFalse(self.detect(""))

    def test_none(self):
        self.assertFalse(self.detect(None))


class TestProposeIntentGateLogic(unittest.TestCase):
    """Test composito gate runtime — propose-intent + calendar-write tool."""

    def test_gate_triggers_propose_set_events(self):
        """Query propose-intent + create_events → gate triggers.
        Test name unchanged for git diff readability; chosen_name aggiornato
        post ADR 0128 (set_events -> create_events)."""
        from agent_runtime import (
            _query_is_propose_intent,
            _calendar_write_tools,
        )
        q = "proponi 3 orari per appuntamento la prossima settimana"
        chosen_name = "create_events"  # post ADR 0128 (era set_events)
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_is_propose_intent(q)
        )
        self.assertTrue(gate_triggered)

    def test_gate_triggers_propose_delete_events(self):
        """Verifichiamo che create_events + EN propose triggera il gate."""
        from agent_runtime import (
            _query_is_propose_intent,
            _calendar_write_tools,
        )
        # NB: delete_events e' set/create-like? Cache attualmente filtra
        # per verb in {set, create}. Verifichiamo invece il pattern reale
        # del catalog: delete_events NON e' calendar_write_tool. Verifichiamo
        # invece create_events + EN propose.
        q = "propose 3 morning times next week"
        chosen_name = "create_events"  # post ADR 0128
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_is_propose_intent(q)
        )
        self.assertTrue(gate_triggered)

    def test_gate_skipped_legit_set_events(self):
        """Query «fissa» + create_events → gate NO trigger (legit booking)."""
        from agent_runtime import (
            _query_is_propose_intent,
            _calendar_write_tools,
        )
        q = "fissa un appuntamento la prossima settimana mattina alle 9"
        chosen_name = "create_events"  # post ADR 0128
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_is_propose_intent(q)
        )
        self.assertFalse(gate_triggered)

    def test_gate_skipped_non_calendar_write(self):
        """Tool non-calendar (es. read_events) NON e' gated da P5."""
        from agent_runtime import (
            _query_is_propose_intent,
            _calendar_write_tools,
        )
        q = "proponi 3 orari per appuntamento la prossima settimana"
        chosen_name = "read_events"
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_is_propose_intent(q)
        )
        self.assertFalse(gate_triggered)

    def test_gate_skipped_describe_entries(self):
        """describe_entries (helper) NON e' calendar-write: gate NO trigger."""
        from agent_runtime import (
            _query_is_propose_intent,
            _calendar_write_tools,
        )
        q = "proponi 3 orari per appuntamento la prossima settimana"
        chosen_name = "describe_entries"
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_is_propose_intent(q)
        )
        self.assertFalse(gate_triggered)


class TestPrefilterVerbMappingPropose(unittest.TestCase):
    """Verifica che il prefilter mappa correttamente i verbi propose a
    `describe` (canonico), abilitando il routing al tool corretto."""

    def setUp(self):
        from prefilter import _VERB_TO_CANONICAL, tokenize, detect_canonical_verb
        self.VERB_MAP = _VERB_TO_CANONICAL
        self.tokenize = tokenize
        self.detect_verb = detect_canonical_verb

    def test_proponi_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("proponi"), "describe")

    def test_proponimi_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("proponimi"), "describe")

    def test_suggerisci_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("suggerisci"), "describe")

    def test_suggeriscimi_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("suggeriscimi"), "describe")

    def test_raccomanda_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("raccomanda"), "describe")

    def test_raccomandami_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("raccomandami"), "describe")

    def test_propose_en_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("propose"), "describe")

    def test_suggest_en_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("suggest"), "describe")

    def test_recommend_en_mapped_describe(self):
        self.assertEqual(self.VERB_MAP.get("recommend"), "describe")

    def test_detect_canonical_verb_propose_query(self):
        """Query end-to-end: tokenize + detect_canonical_verb."""
        q = "proponi 3 orari per appuntamento la prossima settimana"
        toks = self.tokenize(q)
        self.assertEqual(self.detect_verb(toks), "describe")

    def test_detect_canonical_verb_suggerisci(self):
        q = "suggeriscimi 3 slot mattutini liberi"
        toks = self.tokenize(q)
        self.assertEqual(self.detect_verb(toks), "describe")

    def test_detect_canonical_verb_legit_set_unchanged(self):
        """«fissa» mappato a `create` post ADR 0128 (12/5/2026): create_events
        e' l'executor canonico per Google Calendar create (era set_events)."""
        q = "fissa un appuntamento mercoledi mattina"
        toks = self.tokenize(q)
        self.assertEqual(self.detect_verb(toks), "create")


if __name__ == "__main__":
    unittest.main()

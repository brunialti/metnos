"""Test del parser tollerante di extract_entries (recall §2.8, deterministico §7.9).

Il bug "0 record da contenuto con date" era spesso un array JSON TRONCATO dal
token-cap: `json.loads` falliva sull'intero array → 0 record. Il parser ora
(a) scala il budget output col numero di record attesi e (b) salva i record
gia' completi anche da output troncato/malformato/shape-alternativo.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


from extract_entries import (_parse_records, _extract_max_tokens,
                             _salvage_objects, _build_prompt, _pick_text,
                             _pick_model_text, _normalize_extracted_date,
                             handle_extract_entries)

F = ["summary", "start", "end"]


class TestDateGranularity(unittest.TestCase):
    """Le date sono normalizzate senza inventare componenti assenti."""

    def test_date_only_field_no_time(self):
        p = _build_prompt(["data", "importo"], "", 20)
        self.assertIn("YYYY-MM-DD", p)
        self.assertIn("SENZA orario", p)
        # nessuna istruzione di datetime con orario per un campo pura-data
        self.assertNotIn("T09:00:00", p)

    def test_datetime_field_keeps_time(self):
        p = _build_prompt(["summary", "start", "end"], "", 20)
        self.assertIn("ISO 8601", p)
        self.assertNotIn("T00:00", p)
        self.assertIn("NON inventare componenti mancanti", p)

    def test_mixed_date_and_datetime(self):
        p = _build_prompt(["start", "scadenza"], "", 20)
        self.assertIn("ISO 8601", p)              # start → datetime
        self.assertIn("YYYY-MM-DD", p)            # scadenza → date-only

    def test_missing_year_must_remain_literal(self):
        p = _build_prompt(["data_inizio", "data_fine"], "", 20)
        self.assertIn("conserva esattamente il valore letterale", p)
        self.assertIn("NON inventare", p)

    def test_observed_complete_date_is_normalized_without_inventing_year(self):
        self.assertEqual(
            _normalize_extracted_date("scadenza", "22/07/2026"),
            "2026-07-22")
        self.assertEqual(
            _normalize_extracted_date("data_inizio", "22/07/2026 14:30"),
            "2026-07-22T14:30:00")
        self.assertEqual(
            _normalize_extracted_date("scadenza", "22 luglio"),
            "22 luglio")


class TestParseRecords(unittest.TestCase):
    def test_wellformed_array(self):
        r = _parse_records('[{"summary":"A","start":"2026-01-01","end":""}]', F)
        self.assertEqual(r, [{"summary": "A", "start": "2026-01-01", "end": ""}])

    def test_truncated_array_salvages_complete(self):
        # Array tagliato a meta' dal token-cap: l'ultimo oggetto e' incompleto.
        # DEVE salvare i 2 completi invece di azzerare tutto.
        raw = ('[{"summary":"A","start":"x","end":"y"},'
               '{"summary":"B","start":"z","end":"w"},{"summary":"C","sta')
        r = _parse_records(raw, F)
        self.assertEqual(len(r), 2)
        self.assertEqual([x["summary"] for x in r], ["A", "B"])

    def test_wrapper_object(self):
        r = _parse_records('{"records":[{"summary":"A"},{"summary":"B"}]}', F)
        self.assertEqual([x["summary"] for x in r], ["A", "B"])

    def test_single_object(self):
        r = _parse_records('{"summary":"A","start":"t"}', F)
        self.assertEqual(r, [{"summary": "A", "start": "t"}])

    def test_fenced_json(self):
        self.assertEqual(_parse_records('```json\n[{"summary":"A"}]\n```', F),
                         [{"summary": "A"}])

    def test_prose_plus_array(self):
        self.assertEqual(_parse_records('Ecco:\n[{"summary":"A"}]\nfine', F),
                         [{"summary": "A"}])

    def test_garbage_returns_empty(self):
        self.assertEqual(_parse_records("nessun evento trovato", F), [])
        self.assertEqual(_parse_records("", F), [])

    def test_brace_inside_string_value(self):
        # Le parentesi dentro le stringhe NON devono rompere il bilanciamento.
        r = _parse_records('[{"summary":"a } b {","start":"x"}]', F)
        self.assertEqual(r, [{"summary": "a } b {", "start": "x"}])

    def test_salvage_filters_objects_without_fields(self):
        # Un oggetto top-level senza alcun field richiesto (es. wrapper spurio)
        # non viene tenuto dal recupero tollerante.
        raw = '{"meta":"x"} garbage {"summary":"A","start":"t","end":"u"} tail {'
        r = [o for o in _salvage_objects(raw) if any(f in o for f in F)]
        self.assertEqual(r, [{"summary": "A", "start": "t", "end": "u"}])


class TestMaxTokens(unittest.TestCase):
    def test_scaling_floor_and_cap(self):
        self.assertEqual(_extract_max_tokens(1), 1200)      # floor
        self.assertEqual(_extract_max_tokens(20), 2912)     # 512 + 20*120
        self.assertEqual(_extract_max_tokens(100), 8192)    # cap


def _mail(index: int) -> dict:
    return {
        "from": f"Person {index} <p{index}@example.test>",
        "subject": f"Subject {index}",
        "date": "Mon, 20 Jul 2026 11:52:47 +0000",
        "message_id": f"<m{index}@example.test>",
        "body_preview": f"Commitment {index}",
        "account": "work",
        "uid": str(index),
    }


def test_structured_calendar_text_keeps_dates_and_location():
    text = _pick_text({
        "id": "e1", "summary": "Review",
        "start": "2026-07-21T10:00:00+02:00",
        "end": "2026-07-21T11:00:00+02:00",
        "location": "Roma", "description": "Budget",
        "_calendar_id": "primary",
    })
    assert "summary: Review" in text
    assert "start: 2026-07-21T10:00:00+02:00" in text
    assert "end: 2026-07-21T11:00:00+02:00" in text
    assert "location: Roma" in text


def test_model_text_omits_transport_ids_but_keeps_semantic_mail_facts():
    source = _mail(7)
    source["folder"] = "INBOX"
    text = _pick_model_text(source)

    assert "from: Person 7 <p7@example.test>" in text
    assert "subject: Subject 7" in text
    assert "date: Mon, 20 Jul 2026 11:52:47 +0000" in text
    assert "body_preview: Commitment 7" in text
    assert "account:" not in text
    assert "folder:" not in text
    assert "uid:" not in text
    assert "message_id:" not in text


def test_model_text_omits_calendar_id_but_keeps_occurrence_facts():
    text = _pick_model_text({
        "id": "opaque-event-id", "summary": "Review",
        "start": "2026-07-21T10:00:00+02:00",
        "end": "2026-07-21T11:00:00+02:00",
        "location": "Roma", "_calendar_id": "primary",
    })
    assert "summary: Review" in text
    assert "start: 2026-07-21T10:00:00+02:00" in text
    assert "location: Roma" in text
    assert "opaque-event-id" not in text


def test_canonical_value_triple_and_responsible_are_completed_from_source():
    from extract_entries import _attach_source_provenance

    out = _attach_source_provenance(
        {"entità": "Mario Rossi", "valore normalizzato": "",
         "valore originale": "", "responsabile": ""},
        {"from": "Mario Rossi <MARIO@example.test>", "subject": "Conferma",
         "message_id": "m1", "account": "work"},
        ["entità", "valore normalizzato", "valore originale",
         "responsabile"])
    assert out["valore normalizzato"] == "Mario Rossi"
    assert out["valore originale"] == "Mario Rossi"
    assert out["responsabile"].startswith("Mario Rossi")


def test_batch_extract_preserves_order_and_structured_provenance(monkeypatch):
    calls = []

    def fake_call(query, _prompt, **_kwargs):
        calls.append(query)
        rows = [{"source_index": item["source_index"],
                 "entità": "persona",
                 "valore normalizzato": f"Person {item['source_index']}"}
                for item in query]
        return (str(rows).replace("'", '"'),
                {"in_tokens": 20, "out_tokens": 20, "latency_ms": 10})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    out = handle_extract_entries({
        "entries": [_mail(i) for i in range(4)],
        "fields": ["entità", "valore normalizzato", "origine", "dominio",
                   "sender", "date", "confidenza"],
        "instruction": "estrai persone",
        "max_sources": 10, "batch_size": 4, "drill_down": False,
    })

    assert out["ok"] is True
    assert out["batch_calls"] == 1
    assert out["batch_fallback_sources"] == 0
    assert len(calls) == 1
    assert [row["valore normalizzato"] for row in out["entries"]] == [
        "Person 0", "Person 1", "Person 2", "Person 3"]
    assert out["entries"][0]["sender"].startswith("Person 0")
    assert out["entries"][0]["date"] == "2026-07-20T11:52:47+00:00"
    assert out["entries"][0]["origine"] == "email:work:<m0@example.test>"
    assert out["entries"][0]["dominio"] == "email"


def test_batch_missing_source_uses_serial_fallback_in_place(monkeypatch):
    responses = iter([
        ('[{"source_index":0,"value":"A"}]',
         {"in_tokens": 10, "out_tokens": 10, "latency_ms": 10}),
        ('[{"value":"B"}]',
         {"in_tokens": 5, "out_tokens": 5, "latency_ms": 5}),
    ])
    monkeypatch.setattr(
        "extract_entries.call_llm", lambda *_args, **_kwargs: next(responses))
    out = handle_extract_entries({
        "entries": [_mail(0), _mail(1)], "fields": ["value"],
        "instruction": "extract", "batch_size": 2, "drill_down": False,
    })
    assert [row["value"] for row in out["entries"]] == ["A", "B"]
    assert out["batch_calls"] == 1
    assert out["batch_fallback_sources"] == 1


def test_parallel_batches_are_equivalent_and_recompose_in_source_order(
        monkeypatch):
    import threading
    import time

    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake_call(query, _prompt, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        rows = [[item["source_index"], f"V{item['source_index']}"]
                for item in query]
        with lock:
            active -= 1
        return (json.dumps(rows),
                {"in_tokens": 10, "out_tokens": 10, "latency_ms": 20})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    request = {
        "entries": [_mail(i) for i in range(6)],
        "fields": ["value"], "instruction": "extract",
        "batch_size": 2, "drill_down": False,
    }
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "1")
    serial = handle_extract_entries(request)
    maximum = 0
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "3")
    parallel = handle_extract_entries(request)

    assert maximum == 3
    assert parallel == serial
    assert [row["value"] for row in parallel["entries"]] == [
        "V0", "V1", "V2", "V3", "V4", "V5"]


def test_calendar_deadline_is_deterministically_the_event_start(monkeypatch):
    def fake_call(*_args, **_kwargs):
        return (json.dumps([{
            "entità": "Evento lungo", "valore normalizzato": "evento",
            "valore originale": "evento", "scadenza": "2026-06-16",
        }]), {"in_tokens": 1, "out_tokens": 1, "latency_ms": 1})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    out = handle_extract_entries({
        "entries": [{
            "id": "event-1", "summary": "Evento lungo",
            "start": "2026-06-10T14:00:00+02:00",
            "end": "2026-06-16T15:00:00+02:00",
            "status": "confirmed", "_calendar_id": "primary",
        }],
        "fields": ["entità", "valore normalizzato", "valore originale",
                   "scadenza", "stato", "dominio"],
        "drill_down": False,
    })

    assert out["entries"][0]["scadenza"] == "2026-06-10"
    assert out["entries"][0]["valore normalizzato"] == "2026-06-10"
    assert out["entries"][0]["valore originale"] == \
        "2026-06-10T14:00:00+02:00"
    assert out["entries"][0]["stato"] == "confirmed"
    assert out["entries"][0]["dominio"] == "calendar"


def test_structured_map_projects_contacts_without_llm(monkeypatch):
    monkeypatch.setattr(
        "extract_entries.call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("structured mapping must not call the LLM")))
    fields = [
        "entità", "tipo", "valore normalizzato", "valore originale",
        "email", "telefono", "origine", "confidenza", "dominio",
    ]
    out = handle_extract_entries({
        "entries": [{
            "id": "giulia-ferri", "name": "Giulia Ferri",
            "emails": ["giulia@example.test", "g.ferri@example.test"],
            "phones": ["+39 333 123"],
        }],
        "fields": fields,
        "structured_map": {
            "entità": ["name", "id"],
            "valore normalizzato": ["name", "id"],
            "valore originale": "name",
            "email": "emails",
            "telefono": "phones",
        },
        "structured_defaults": {"tipo": "contatto"},
        "max_sources": 1000,
    })

    assert out["ok"] is True
    assert out["source"] == "structured_map"
    assert out["in_tokens"] == out["out_tokens"] == 0
    assert out["entries"] == [{
        "entità": "Giulia Ferri",
        "tipo": "contatto",
        "valore normalizzato": "Giulia Ferri",
        "valore originale": "Giulia Ferri",
        "email": "giulia@example.test; g.ferri@example.test",
        "telefono": "+39 333 123",
        "origine": "contact:giulia-ferri",
        "confidenza": 0.95,
        "dominio": "contacts",
        "_source_name": "Giulia Ferri",
        "_source_domain": "contacts",
    }]


def test_file_provenance_exposes_duplicates_and_parse_diagnostic(monkeypatch):
    monkeypatch.setattr(
        "extract_entries.call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unreadable files must not call the LLM")))
    out = handle_extract_entries({
        "entries": [{
            "path": "/tmp/rotto.pdf", "name": "rotto.pdf",
            "file_type": "pdf", "readable": False, "content": "",
            "duplicate_paths": ["/tmp/copia.pdf"],
            "parse_diagnostic": "invalid_pdf",
        }],
        "fields": [
            "entità", "origine", "dominio", "leggibile", "duplicati",
            "diagnostica",
        ],
        "drill_down": False,
    })
    row = out["entries"][0]
    assert row["origine"] == "/tmp/rotto.pdf"
    assert row["dominio"] == "files"
    assert row["leggibile"] is False
    assert row["duplicati"] == ["/tmp/copia.pdf"]
    assert row["diagnostica"] == "invalid_pdf"


def test_relevance_prefilter_skips_unrelated_sources_before_llm(monkeypatch):
    calls = []

    def fake_call(query, *_args, **_kwargs):
        calls.append(query)
        return ('[{"entità":"Progetto Atlas","tipo":"progetto"}]', {
            "in_tokens": 10, "out_tokens": 10, "latency_ms": 1})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    out = handle_extract_entries({
        "entries": [
            {"message_id": "m1", "account": "mail",
             "subject": "Aggiornamento Atlas",
             "body_preview": "Decisione sul progetto Atlas"},
            {"message_id": "m2", "account": "mail",
             "subject": "Newsletter sportiva",
             "body_preview": "Risultati della giornata"},
        ],
        "fields": ["entità", "tipo", "origine", "dominio"],
        "relevance_entries": [{
            "entità": "Progetto Atlas", "tipo": "progetto"}],
        "relevance_fields": ["entità", "tipo"],
        "relevance_terms": ["Atlas"],
        "batch_size": 1,
        "drill_down": False,
    })

    assert len(calls) == 1
    assert out["input_source_total"] == 2
    assert out["selected_source_total"] == 1
    assert out["filtered_source_total"] == 1
    assert out["relevance_filter"] is True
    assert out["entries"][0]["origine"] == "email:mail:m1"


def test_relevance_prefilter_keeps_explicit_short_acronym(monkeypatch):
    calls = []

    def fake_call(query, *_args, **_kwargs):
        calls.append(query)
        return ('[{"entità":"TSA"}]', {
            "in_tokens": 5, "out_tokens": 5, "latency_ms": 1})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    out = handle_extract_entries({
        "entries": [
            {"message_id": "m1", "account": "mail",
             "subject": "Controllo TSA", "body_preview": "Confermato"},
            {"message_id": "m2", "account": "mail",
             "subject": "Riunione", "body_preview": "Aggiornamento"},
        ],
        "fields": ["entità", "origine", "dominio"],
        "relevance_terms": ["TSA"],
        "batch_size": 1,
        "drill_down": False,
    })

    assert len(calls) == 1
    assert out["selected_source_total"] == 1
    assert out["filtered_source_total"] == 1
    assert out["entries"][0]["entità"] == "TSA"


def test_reconciliation_evidence_and_explicit_state_are_deterministic(monkeypatch):
    def fake_call(_query, *_args, **_kwargs):
        return ('[{"entità":"BRUNIALTI ROBERTO",'
                '"tipo impegno":"ECOCOLORDOPPLERGRAFIA",'
                '"data normalizzata":"2026-07-14",'
                '"ora normalizzata":"17:10",'
                '"valore originale":"appuntamento alle 17:10 è stato annullato",'
                '"stato":""}]', {
                    "in_tokens": 10, "out_tokens": 10, "latency_ms": 1})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    out = handle_extract_entries({
        "entries": [{
            "message_id": "m1", "account": "mail",
            "subject": "POLICLINICO GEMELLI - ANNULLAMENTO PRENOTAZIONE",
            "body_preview": (
                "Appuntamento il 14/07 alle 17:10 presso il Policlinico "
                "Gemelli è stato annullato."),
        }],
        "fields": [
            "entità", "tipo impegno", "data normalizzata",
            "ora normalizzata", "valore originale", "stato",
        ],
        "relevance_terms": ["Policlinico Gemelli", "TSA"],
        "state_markers": {
            "annullato": ["annullamento prenotazione", "e stato annullato"],
        },
        "drill_down": False,
    })

    row = out["entries"][0]
    assert row["stato"] == "annullato"
    assert "policlinico gemelli" in row["_relevance_anchors"]
    assert row["_source_time_mentions"] == ["17:10"]


def test_multi_record_container_does_not_share_anchors_or_times(monkeypatch):
    def fake_call(_query, *_args, **_kwargs):
        return ('[{"entità":"Standup","ora":"10:00"},'
                '{"entità":"Esercizio","ora":"14:00"}]', {
                    "in_tokens": 10, "out_tokens": 10, "latency_ms": 1})

    monkeypatch.setattr("extract_entries.call_llm", fake_call)
    out = handle_extract_entries({
        "entries": [{
            "message_id": "m1", "account": "mail",
            "subject": "Standup ed Esercizio",
            "body_preview": "Standup 10:00. Esercizio 14:00.",
        }],
        "fields": ["entità", "ora"],
        "relevance_terms": ["Standup", "Esercizio"],
        "drill_down": False,
    })

    standup, exercise = out["entries"]
    assert standup["_relevance_anchors"] == ["standup"]
    assert standup["_source_time_mentions"] == ["10:00"]
    assert exercise["_relevance_anchors"] == ["esercizio"]
    assert exercise["_source_time_mentions"] == ["14:00"]


def test_relevance_multiword_focus_does_not_expand_on_generic_token():
    from extract_entries import _matches_relevance, _relevance_terms

    terms = _relevance_terms(
        ["Policlinico Gemelli", "visita diabetologica"], None, [])
    assert "policlinico gemelli" in terms
    assert "gemelli" in terms
    assert "visita diabetologica" in terms
    assert "diabetologica" in terms
    assert "policlinico" not in terms
    assert "visita" not in terms
    assert not any(_matches_relevance(
        "IACOPO - TORVERGATA POLICLINICO X PATENTE", term)
                   for term in terms)


def test_web_audit_projection_preserves_observed_domain_origin_and_confidence(
        monkeypatch):
    def must_not_call_llm(*_args, **_kwargs):
        raise AssertionError("structured web metadata must not call the LLM")

    monkeypatch.setattr("extract_entries.call_llm", must_not_call_llm)
    fields = [
        "origin", "final_url", "domain", "title", "language", "date",
        "text_length", "status", "confidence", "redirected",
        "iframe_count", "js_required", "error",
    ]
    source = {
        "url": "https://example.test/final",
        "origin": "https://example.test/start",
        "final_url": "https://example.test/final",
        "domain": "example.test",
        "title": "Example", "language": "en", "date": "2026-07-21",
        "text_length": 123, "status": "ok", "confidence": 0.99,
        "redirected": True, "iframe_count": 0, "js_required": False,
        "error": "", "body_text": "observed text",
    }
    out = handle_extract_entries({"entries": [source], "fields": fields})
    assert out["source"] == "structured_projection"
    assert out["in_tokens"] == out["out_tokens"] == 0
    row = out["entries"][0]
    assert row["origin"] == "https://example.test/start"
    assert row["final_url"] == "https://example.test/final"
    assert row["domain"] == "example.test"
    assert row["confidence"] == 0.99


def test_web_audit_projection_derives_transport_flags_without_llm(monkeypatch):
    monkeypatch.setattr(
        "extract_entries.call_llm",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("web transport audit is already structured")),
    )
    source = {
        "url": "https://example.test/final",
        "origin": "https://example.test/start",
        "final_url": "https://example.test/final",
        "domain": "example.test", "title": "Example", "language": "en",
        "date": "", "text_length": 123, "status": "ok",
        "confidence": 0.99, "redirected": True, "iframe_count": 1,
        "js_required": False, "error": "", "body_text": "observed",
    }
    fields = [
        "url", "final_url", "domain", "title", "language", "date",
        "text_length", "status", "confidence", "is_redirect",
        "is_timeout", "is_unreadable", "is_empty", "has_iframe",
        "needs_js_render", "Origine", "URL Finale", "Titolo", "Lingua",
        "Caratteri Estratti", "Stato", "Confidenza",
    ]
    out = handle_extract_entries({"entries": [source], "fields": fields})
    assert out["source"] == "structured_projection"
    assert out["used"] == 1
    assert out["in_tokens"] == out["out_tokens"] == 0
    row = out["entries"][0]
    assert row["is_redirect"] is True
    assert row["is_timeout"] is False
    assert row["is_unreadable"] is False
    assert row["is_empty"] is False
    assert row["has_iframe"] is True
    assert row["needs_js_render"] is False


def test_web_audit_projection_resolves_localized_sink_columns(monkeypatch):
    monkeypatch.setattr(
        "extract_entries.call_llm",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("localized structured fields must not call LLM")),
    )
    source = {
        "url": "https://example.test/final",
        "origin": "https://example.test/start",
        "final_url": "https://example.test/final",
        # Empty optional facts are observed absences, not unstructured input.
        "domain": "example.test", "title": "Titolo", "language": "",
        "date": "", "text_length": 321, "status": "ok",
        "confidence": 0.99, "redirected": True, "body_text": "testo",
    }
    fields = ["origine", "URL finale", "titolo", "lingua",
              "caratteri estratti", "stato", "confidenza"]
    out = handle_extract_entries({"entries": [source], "fields": fields})
    assert out["source"] == "structured_projection"
    assert out["in_tokens"] == 0
    row = out["entries"][0]
    assert {field: row[field] for field in fields} == {
        "origine": "https://example.test/start",
        "URL finale": "https://example.test/final",
        "titolo": "Titolo", "lingua": "",
        "caratteri estratti": 321, "stato": "ok", "confidenza": 0.99,
    }


def test_structured_projection_declares_source_and_record_caps(monkeypatch):
    monkeypatch.setattr(
        "extract_entries.call_llm",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("structured projection must not call LLM")),
    )
    sources = [
        {"summary": f"event-{index}", "start": f"2026-08-{index + 1:02d}"}
        for index in range(4)
    ]

    source_cap = handle_extract_entries({
        "entries": sources, "fields": ["summary", "start"],
        "max_sources": 2,
    })
    assert source_cap["source"] == "structured_projection"
    assert source_cap["truncated"] is True
    assert source_cap["available_input_total"] == 4
    assert source_cap["cap_field"] == "n_sources"
    assert source_cap["cap_value"] == 2

    record_cap = handle_extract_entries({
        "entries": sources, "fields": ["summary", "start"],
        "max_sources": 4, "max_total": 2,
    })
    assert record_cap["source"] == "structured_projection"
    assert record_cap["truncated"] is True
    assert record_cap["truncated_intentional"] is True
    assert record_cap["cap_field"] == "max_total"
    assert record_cap["cap_value"] == 2
    assert len(record_cap["entries"]) == 2

    both = handle_extract_entries({
        "entries": sources, "fields": ["summary", "start"],
        "max_sources": 3, "max_total": 2,
    })
    assert [item["cap_field"] for item in both["truncations"]] == [
        "n_sources", "max_total"]
    assert both["truncations"][0]["available_input_total"] == 4
    assert both["truncations"][1]["truncated_intentional"] is True


if __name__ == "__main__":
    unittest.main()

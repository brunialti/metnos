"""Test di group_entries (4/5/2026)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "group_entries"))


class TestGroupEntries(unittest.TestCase):
    def test_merge_with_dedup(self):
        import group_entries
        a = [{"url": "x"}, {"url": "y"}]
        b = [{"url": "y"}, {"url": "z"}]
        out = group_entries.invoke({"entries_lists": [a, b]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 3)
        self.assertEqual(out["dedupes"], 1)
        urls = sorted(e["url"] for e in out["entries"])
        self.assertEqual(urls, ["x", "y", "z"])

    def test_no_dedup(self):
        import group_entries
        a = [{"url": "x"}, {"url": "x"}]
        out = group_entries.invoke({"entries_lists": [a], "dedup_key": None})
        self.assertEqual(out["ok_count"], 2)
        self.assertEqual(out["dedupes"], 0)

    def test_merge_origins_and_surface_conflicts(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[{
                "valore normalizzato": "Atlas",
                "origine": "email:m1", "scadenza": "2026-09-15",
                "stato": "proposta",
            }], [{
                "valore normalizzato": "atlas",
                "origine": "calendar:e1", "scadenza": "2026-09-30",
                "stato": "confermato",
            }]],
            "dedup_key": "valore normalizzato",
            "merge_fields": ["origine"],
            "conflict_fields": ["scadenza", "stato"],
            "conflict_field": "conflitto",
        })
        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 1)
        self.assertEqual(out["dedupes"], 1)
        self.assertEqual(out["conflicts"], 2)
        row = out["entries"][0]
        self.assertEqual(row["origine"], "email:m1; calendar:e1")
        self.assertIn("scadenza: 2026-09-15 ↔ 2026-09-30",
                      row["conflitto"])
        self.assertIn("stato: proposta ↔ confermato", row["conflitto"])
        self.assertEqual(row["_conflict_count"], 2)

    def test_conflict_severity_survives_more_than_two_merges(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [
                [{"entità": "Atlas", "stato": "bozza", "importo": "10"}],
                [{"entità": "Atlas", "stato": "approvato", "importo": "20"}],
                [{"entità": "Atlas", "stato": "annullato", "importo": "30"}],
            ],
            "dedup_key": "entità",
            "conflict_fields": ["stato", "importo"],
        })
        self.assertTrue(out["ok"])
        row = out["entries"][0]
        self.assertEqual(row["_conflict_count"], 4)
        self.assertEqual(row["conflitto"].count("↔"), 4)

    def test_blank_dedup_keys_are_not_collapsed(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[{"key": ""}, {"key": ""}]],
            "dedup_key": "key",
        })
        self.assertEqual(out["ok_count"], 2)
        self.assertEqual(out["dedupes"], 0)

    def test_composite_key_keeps_distinct_same_domain_occurrences(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "Dentista", "valore": "2026-06-12",
                 "dominio": "calendar"},
                {"entità": "DENTISTA", "valore": "2026-07-02",
                 "dominio": "calendar"},
            ]],
            "dedup_key": ["entità", "valore"],
        })
        self.assertEqual(out["ok_count"], 2)
        self.assertEqual(out["dedupes"], 0)

    def test_cross_domain_key_merges_only_across_domains(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[{
                "entità": "Standup", "valore normalizzato": "2026-06-05",
                "scadenza": "2026-06-05", "origine": "email:m1",
                "dominio": "email",
            }], [{
                "entità": "standup", "valore normalizzato": "2026-06-06",
                "scadenza": "2026-06-06", "origine": "calendar:e1",
                "dominio": "calendar",
            }]],
            "dedup_key": ["entità", "valore normalizzato"],
            "cross_domain_key": "entità", "domain_field": "dominio",
            "cross_match_fields": ["valore normalizzato", "scadenza"],
            "merge_fields": ["origine", "dominio"],
            "conflict_fields": ["scadenza"],
        })
        self.assertEqual(out["ok_count"], 1)
        self.assertEqual(out["dedupes"], 1)
        self.assertEqual(out["entries"][0]["dominio"], "email; calendar")
        self.assertIn("2026-06-05 ↔ 2026-06-06",
                      out["entries"][0]["conflitto"])

    def test_composite_reconciliation_classifies_without_merging_dates(self):
        import group_entries
        labels = {
            "exact": "corrispondenza esatta",
            "probable": "corrispondenza probabile",
            "email_only": "solo email",
            "calendar_only": "solo calendario",
            "cancelled": "cancellazione senza evento",
            "unmatched": "non riconciliato",
        }
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "Standup", "tipo impegno": "standup",
                 "data normalizzata": "2026-06-05",
                 "ora normalizzata": "10:00", "origine": "email:m1",
                 "dominio": "email", "stato": "confermato"},
                {"entità": "Standup", "tipo impegno": "standup",
                 "data normalizzata": "2026-06-05",
                 "ora normalizzata": "10:00", "origine": "email:m2",
                 "dominio": "email", "stato": "confermato"},
                {"entità": "TSA", "tipo impegno": "ecodoppler TSA",
                 "data normalizzata": "2026-07-14",
                 "ora normalizzata": "17:10", "origine": "email:m3",
                 "dominio": "email", "stato": "annullato"},
                {"entità": "Esercizio", "tipo impegno": "esercizio",
                 "data normalizzata": "2026-06-10",
                 "ora normalizzata": "14:00", "origine": "email:m4",
                 "dominio": "email", "stato": "confermato"},
                {"entità": "Gemelli", "tipo impegno": "ecodoppler TSA",
                 "data normalizzata": "2026-07-07",
                 "ora normalizzata": "", "origine": "email:m5",
                 "dominio": "email", "stato": "confermato"},
            ], [
                {"entità": "standup", "tipo impegno": "standup",
                 "data normalizzata": "2026-06-05",
                 "ora normalizzata": "", "origine": "calendar:e1",
                 "dominio": "calendar", "stato": "confermato"},
                {"entità": "Standup", "tipo impegno": "standup",
                 "data normalizzata": "2026-06-06",
                 "ora normalizzata": "", "origine": "calendar:e2",
                 "dominio": "calendar", "stato": "confermato"},
                {"entità": "ESERCIZIO", "tipo impegno": "esercizio",
                 "data normalizzata": "2026-06-10",
                 "ora normalizzata": "14:00", "origine": "calendar:e3",
                 "dominio": "calendar", "stato": "confermato"},
                {"entità": "gemelli", "tipo impegno": "ecodoppler TSA",
                 "data normalizzata": "2026-07-08",
                 "ora normalizzata": "09:00", "origine": "calendar:e4",
                 "dominio": "calendar", "stato": "confermato"},
            ]],
            "dedup_key": ["entità", "tipo impegno", "data normalizzata",
                          "ora normalizzata"],
            "cross_domain_key": [
                "entità", "tipo impegno", "data normalizzata"],
            "domain_field": "dominio",
            "cross_match_fields": ["ora normalizzata"],
            "merge_fields": ["origine", "dominio"],
            "conflict_fields": ["ora normalizzata", "stato"],
            "missing_conflict_fields": ["ora normalizzata"],
            "missing_value_label": "mancante",
            "required_fields_by_domain": {
                "calendar": ["ora normalizzata"]},
            "unmatched_conflict_key": ["entità", "tipo impegno"],
            "unmatched_conflict_fields": ["data normalizzata"],
            "match_field": "corrispondenza",
            "match_labels": labels,
            "cancellation_states": ["annullato", "cancellato"],
        })

        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 6)
        standup = next(row for row in out["entries"]
                       if row.get("entità") == "Standup"
                       and row.get("data normalizzata") == "2026-06-05")
        self.assertEqual(
            standup["origine"], "email:m1; email:m2; calendar:e1")
        self.assertIn("ora normalizzata: 10:00 ↔ [mancante]",
                      standup["conflitto"])
        self.assertEqual(standup["corrispondenza"],
                         "corrispondenza probabile")
        exact = next(row for row in out["entries"]
                     if row.get("entità") == "Esercizio")
        self.assertEqual(exact["corrispondenza"], "corrispondenza esatta")
        calendar_only = next(row for row in out["entries"]
                             if row.get("data normalizzata") == "2026-06-06")
        self.assertEqual(calendar_only["corrispondenza"], "solo calendario")
        self.assertIn("ora normalizzata: [mancante]",
                      calendar_only["conflitto"])
        cancelled = next(row for row in out["entries"]
                         if row.get("data normalizzata") == "2026-07-14")
        self.assertEqual(cancelled["corrispondenza"],
                         "cancellazione senza evento")
        gemelli = [row for row in out["entries"]
                   if row.get("entità", "").casefold() == "gemelli"]
        self.assertEqual(len(gemelli), 2)
        self.assertEqual(
            {row["data normalizzata"] for row in gemelli},
            {"2026-07-07", "2026-07-08"})
        self.assertTrue(all("possibile divergenza, non unificati" in
                            row["conflitto"] for row in gemelli))
        self.assertEqual(out["match_counts"], {
            "corrispondenza probabile": 1,
            "cancellazione senza evento": 1,
            "corrispondenza esatta": 1,
            "solo calendario": 2,
            "solo email": 1,
        })

    def test_unmatched_conflict_refuses_ambiguous_one_to_many_pairing(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "Standup", "tipo": "riunione",
                 "data": "2026-06-01", "dominio": "email"},
            ], [
                {"entità": "Standup", "tipo": "riunione",
                 "data": "2026-06-02", "dominio": "calendar"},
                {"entità": "Standup", "tipo": "riunione",
                 "data": "2026-06-03", "dominio": "calendar"},
            ]],
            "dedup_key": ["entità", "tipo", "data"],
            "domain_field": "dominio",
            "unmatched_conflict_key": ["entità", "tipo"],
            "unmatched_conflict_fields": ["data"],
        })

        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 3)
        self.assertTrue(all("conflitto" not in row for row in out["entries"]))

    def test_anchor_reconciliation_uses_unique_observed_evidence(self):
        """Identity drift must not hide the real Gemelli time conflict."""
        import group_entries
        policy = {
            "dedup_key": ["entità", "tipo impegno", "data", "ora"],
            "cross_domain_key": ["entità", "tipo impegno", "data"],
            "domain_field": "dominio",
            "cross_match_fields": ["ora", "organizzazione", "persona"],
            "merge_fields": ["origine", "dominio"],
            "conflict_fields": ["ora", "organizzazione", "stato"],
            "anchor_field": "_relevance_anchors",
            "anchor_equal_fields": ["data"],
            "anchor_match_fields": [
                "_source_time_mentions", "organizzazione",
                "tipo impegno", "persona", "entità",
            ],
            "anchor_within_domains": ["email"],
            "match_field": "corrispondenza",
            "match_labels": {
                "exact": "esatta", "probable": "probabile",
                "email_only": "solo email",
                "calendar_only": "solo calendario",
                "cancelled": "cancellazione senza evento",
                "unmatched": "non riconciliato",
            },
            "cancellation_states": ["annullato"],
        }
        emails = [
            {"entità": "roberto.brunialti", "tipo impegno": "Standup",
             "data": "2026-06-05", "ora": "10:00",
             "dominio": "email", "origine": "email:standup",
             "_relevance_anchors": ["standup"],
             "_source_time_mentions": ["10:00"]},
            {"entità": "roberto.brunialti", "tipo impegno": "ESERCIZIO",
             "data": "2026-06-10", "ora": "14:00",
             "dominio": "email", "origine": "email:digest",
             "_relevance_anchors": ["esercizio"],
             "_source_time_mentions": ["14:00"]},
            {"entità": "ESERCIZIO", "tipo impegno": "Esercizio",
             "data": "2026-06-10", "ora": "14:00",
             "dominio": "email", "origine": "email:single",
             "_relevance_anchors": ["esercizio"],
             "_source_time_mentions": ["14:00"]},
            {"entità": "Cassandra Morciano",
             "tipo impegno": "ecodoppler TSA",
             "data": "2026-07-07", "ora": "13:45",
             "persona": "Cassandra Morciano", "organizzazione": "",
             "dominio": "email", "origine": "email:morciano",
             "_relevance_anchors": ["morciano", "tsa", "ecodoppler"],
             "_source_time_mentions": ["13:45"]},
            {"entità": "BRUNIALTI ROBERTO",
             "tipo impegno": "ECOCOLORDOPPLERGRAFIA DEI TRONCHI SOVRAAORTICI",
             "data": "2026-07-07", "ora": "17:10",
             "organizzazione": "Policlinico Agostino Gemelli",
             "dominio": "email", "origine": "email:gemelli",
             "_relevance_anchors": ["gemelli"],
             "_source_time_mentions": ["12:30", "17:10"]},
            {"entità": "BRUNIALTI ROBERTO",
             "tipo impegno": "ECOCOLORDOPPLERGRAFIA DEI TRONCHI SOVRAAORTICI",
             "data": "2026-07-14", "ora": "17:10", "stato": "annullato",
             "dominio": "email", "origine": "email:cancel",
             "_relevance_anchors": ["gemelli"],
             "_source_time_mentions": ["17:10"]},
        ]
        calendar = [
            {"entità": "Standup", "tipo impegno": "Standup",
             "data": "2026-06-05", "ora": "10:00", "stato": "confirmed",
             "dominio": "calendar", "origine": "calendar:standup",
             "_relevance_anchors": ["standup"],
             "_source_time_mentions": ["10:00"]},
            {"entità": "Esercizio", "tipo impegno": "Esercizio",
             "data": "2026-06-10", "ora": "14:00", "stato": "confirmed",
             "dominio": "calendar", "origine": "calendar:exercise",
             "_relevance_anchors": ["esercizio"],
             "_source_time_mentions": ["14:00"]},
            {"entità": "TSA", "tipo impegno": "TSA",
             "data": "2026-07-07", "ora": "13:30", "stato": "confirmed",
             "organizzazione": "Policlinico Gemelli",
             "dominio": "calendar", "origine": "calendar:tsa",
             "_relevance_anchors": ["tsa", "gemelli"],
             "_source_time_mentions": ["13:30", "17:10"]},
        ]

        out = group_entries.invoke({
            **policy, "entries_lists": [emails, calendar]})

        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 5)
        # Three fallback joins; the calendar exercise then uses the exact
        # alias key registered by the preceding within-email reconciliation.
        self.assertEqual(out["anchor_reconciliations"], 3)
        exercise = next(row for row in out["entries"]
                        if "calendar:exercise" in row.get("origine", ""))
        self.assertEqual(exercise["origine"],
                         "email:digest; email:single; calendar:exercise")
        tsa = next(row for row in out["entries"]
                   if "calendar:tsa" in row.get("origine", ""))
        self.assertIn("email:gemelli", tsa["origine"])
        self.assertNotIn("email:morciano", tsa["origine"])
        self.assertIn("ora: 17:10 ↔ 13:30", tsa["conflitto"])
        self.assertEqual(tsa["corrispondenza"], "probabile")
        cancellation = next(row for row in out["entries"]
                            if row.get("data") == "2026-07-14")
        self.assertEqual(cancellation["corrispondenza"],
                         "cancellazione senza evento")

    def test_anchor_reconciliation_refuses_tied_candidates(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "A", "tipo": "visita", "data": "2026-07-01",
                 "dominio": "email", "_anchors": ["visita"],
                 "_times": ["10:00"]},
                {"entità": "B", "tipo": "visita", "data": "2026-07-01",
                 "dominio": "email", "_anchors": ["visita"],
                 "_times": ["10:00"]},
            ], [{"entità": "C", "tipo": "visita",
                 "data": "2026-07-01", "dominio": "calendar",
                 "_anchors": ["visita"], "_times": ["10:00"]}]],
            "dedup_key": ["entità", "tipo", "data"],
            "domain_field": "dominio",
            "anchor_field": "_anchors",
            "anchor_equal_fields": ["data"],
            "anchor_match_fields": ["_times", "tipo"],
        })
        self.assertEqual(out["ok_count"], 3)
        self.assertEqual(out["anchor_reconciliations"], 0)

    def test_opt_in_within_domain_conflict_and_reference_pruning(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "Progetto Atlas", "tipo": "progetto",
                 "valore normalizzato": "2026-09-30",
                 "scadenza": "2026-09-30", "importo": "120000",
                 "origine": "file:a.pdf", "dominio": "files"},
                {"entità": "PROGETTO ATLAS", "tipo": "progetto",
                 "valore normalizzato": "2026-09-15",
                 "scadenza": "2026-09-15", "importo": "135000",
                 "origine": "file:b.docx", "dominio": "files"},
            ], [
                {"entità": "Progetto Atlas", "tipo": "contatto",
                 "valore normalizzato": "Progetto Atlas",
                 "origine": "contact:atlas", "dominio": "contacts"},
                {"entità": "Contatto estraneo", "tipo": "contatto",
                 "valore normalizzato": "Contatto estraneo",
                 "origine": "contact:other", "dominio": "contacts"},
            ]],
            "dedup_key": ["entità", "tipo", "valore normalizzato"],
            "cross_domain_key": "entità",
            "domain_field": "dominio",
            "merge_fields": ["origine", "dominio"],
            "conflict_fields": ["scadenza", "importo"],
            "reconcile_within_domains": ["files"],
            "drop_unmatched_domains": ["contacts"],
        })
        self.assertEqual(out["ok_count"], 1)
        self.assertEqual(out["dropped_unmatched"], 1)
        self.assertEqual(out["conflicts"], 2)
        row = out["entries"][0]
        self.assertEqual(row["dominio"], "files; contacts")
        self.assertIn("2026-09-30 ↔ 2026-09-15", row["conflitto"])

    def test_coalesce_unique_source_subject_before_reconciliation(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "Progetto Atlas", "tipo": "progetto",
                 "valore normalizzato": "Atlas", "dominio": "files",
                 "origine": "approved.pdf", "importo": "120000",
                 "scadenza": "2026-09-30", "stato": "approvato"},
                {"entità": "Progetto Atlas", "tipo": "progetto",
                 "valore normalizzato": "Atlas", "dominio": "files",
                 "origine": "revision.docx"},
                {"entità": "135000 EUR", "tipo": "importo",
                 "importo": "135000", "dominio": "files",
                 "origine": "revision.docx"},
                {"entità": "2026-09-15", "tipo": "scadenza",
                 "scadenza": "2026-09-15", "dominio": "files",
                 "origine": "revision.docx"},
                {"entità": "bozza", "tipo": "stato", "stato": "bozza",
                 "dominio": "files", "origine": "revision.docx"},
            ]],
            "dedup_key": ["entità", "tipo", "valore normalizzato"],
            "cross_domain_key": "entità", "domain_field": "dominio",
            "reconcile_within_domains": ["files"],
            "merge_fields": ["origine", "dominio"],
            "conflict_fields": ["importo", "scadenza", "stato"],
            "coalesce_source_facts": True,
            "source_field": "origine", "type_field": "tipo",
            "subject_types": ["progetto"],
            "coalesce_fields": ["importo", "scadenza", "stato"],
        })
        self.assertTrue(out["ok"])
        self.assertEqual(out["coalesced_source_facts"], 3)
        project = next(row for row in out["entries"]
                       if row.get("tipo") == "progetto")
        self.assertEqual(project["_conflict_count"], 3)
        self.assertIn("importo: 120000 ↔ 135000", project["conflitto"])

    def test_coalesce_refuses_ambiguous_multi_subject_source(self):
        import group_entries
        out = group_entries.invoke({
            "entries_lists": [[
                {"entità": "Atlas", "tipo": "progetto", "origine": "rows.xlsx"},
                {"entità": "Beta", "tipo": "progetto", "origine": "rows.xlsx"},
                {"entità": "10", "tipo": "importo", "importo": "10",
                 "origine": "rows.xlsx"},
            ]],
            "dedup_key": None,
            "coalesce_source_facts": True,
        })
        self.assertEqual(out["coalesced_source_facts"], 0)
        projects = [row for row in out["entries"]
                    if row.get("tipo") == "progetto"]
        self.assertTrue(all(not row.get("importo") for row in projects))


if __name__ == "__main__":
    unittest.main()

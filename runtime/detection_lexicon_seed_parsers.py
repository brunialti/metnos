#!/usr/bin/env python3
"""Versioned natural-language resources for deterministic runtime parsers.

The consumers keep ownership of grammar, captures, bounds and canonical
identifiers.  This module owns only surface forms.  A parser family is exposed
only when every resource in that family is ready for the active language; this
prevents a partially materialized translation from assembling a hybrid grammar.

Italian and English remain additive baselines, matching the historical parser
behaviour.  A third language must first materialize the complete family.
"""
from __future__ import annotations

import detection_lexicon as _dl


ORDERING_CONCEPTS = (
    "parser.ordering.mode_verb",
    "parser.ordering.group_connector",
    "parser.ordering.sort_connector",
    "parser.ordering.sort_prefix",
    "parser.ordering.article",
    "parser.ordering.key_stop",
    "parser.ordering.descending",
    "parser.ordering.field_alias",
)

TIME_RESOLVER_CONCEPTS = (
    "parser.time.past_determiner",
    "parser.time.unit",
    "parser.time.singular_unit",
    "parser.time.past_postfix.h",
    "parser.time.past_postfix.d",
    "parser.time.past_postfix.w",
    "parser.time.past_postfix.m",
    "parser.time.past_postfix.y",
    "parser.time.relative_day",
    "parser.time.absolute_year_prefix",
    "parser.time.absolute_year_suffix",
)

TIME_PARSER_CONCEPTS = (
    "parser.time.normalizer_past_determiner",
    "parser.time.future_offset_prefix",
    "parser.time.future_determiner",
    "parser.time.day_word",
    "parser.time.past_offset_suffix",
    "parser.time.range_connector",
)

RECURRENCE_CONCEPTS = (
    "parser.recurrence.interrogative",
    "parser.recurrence.quantifier",
    "parser.recurrence.unit",
    "parser.recurrence.at",
    "parser.recurrence.daily",
    "parser.recurrence.hourly",
    "parser.recurrence.task_noun",
    "parser.recurrence.article",
    "parser.recurrence.relative",
    "parser.recurrence.edge_connector",
)

COMPOUND_CONCEPTS = (
    "parser.compound.format_hint",
    "parser.compound.field_stop",
    "parser.compound.field_cut",
    "parser.compound.schema_marker",
    "parser.compound.tabular_noun",
    "parser.compound.with_connector",
    "parser.compound.list_connector",
    "parser.compound.artifact_boundary",
    "parser.compound.row_noun",
    "parser.compound.total_quantifier",
    "parser.compound.article",
    "parser.compound.recipient_preposition",
)

FAMILY_CONCEPTS = {
    "ordering": ORDERING_CONCEPTS,
    "time_resolver": TIME_RESOLVER_CONCEPTS,
    "time_parser": TIME_PARSER_CONCEPTS,
    "recurrence": RECURRENCE_CONCEPTS,
    "compound": COMPOUND_CONCEPTS,
}

_KINDS = {
    "parser.ordering.mode_verb": "mapping",
    "parser.ordering.group_connector": "phrases",
    "parser.ordering.sort_connector": "phrases",
    "parser.ordering.sort_prefix": "phrases",
    "parser.ordering.article": "phrases",
    "parser.ordering.key_stop": "phrases",
    "parser.ordering.descending": "phrases",
    "parser.ordering.field_alias": "mapping",
    "parser.time.past_determiner": "phrases",
    "parser.time.normalizer_past_determiner": "phrases",
    "parser.time.unit": "mapping",
    "parser.time.singular_unit": "mapping",
    "parser.time.past_postfix.h": "phrases",
    "parser.time.past_postfix.d": "phrases",
    "parser.time.past_postfix.w": "phrases",
    "parser.time.past_postfix.m": "phrases",
    "parser.time.past_postfix.y": "phrases",
    "parser.time.relative_day": "mapping",
    "parser.time.absolute_year_prefix": "phrases",
    "parser.time.absolute_year_suffix": "phrases",
    "parser.time.range_connector": "mapping",
    "parser.time.future_offset_prefix": "phrases",
    "parser.time.future_determiner": "phrases",
    "parser.time.day_word": "phrases",
    "parser.time.past_offset_suffix": "phrases",
    "parser.recurrence.interrogative": "phrases",
    "parser.recurrence.quantifier": "phrases",
    "parser.recurrence.unit": "mapping",
    "parser.recurrence.at": "phrases",
    "parser.recurrence.daily": "phrases",
    "parser.recurrence.hourly": "phrases",
    "parser.recurrence.task_noun": "phrases",
    "parser.recurrence.article": "phrases",
    "parser.recurrence.relative": "phrases",
    "parser.recurrence.edge_connector": "phrases",
    "parser.compound.format_hint": "mapping",
    "parser.compound.field_stop": "phrases",
    "parser.compound.field_cut": "phrases",
    "parser.compound.schema_marker": "phrases",
    "parser.compound.tabular_noun": "phrases",
    "parser.compound.with_connector": "phrases",
    "parser.compound.list_connector": "phrases",
    "parser.compound.artifact_boundary": "phrases",
    "parser.compound.row_noun": "phrases",
    "parser.compound.total_quantifier": "phrases",
    "parser.compound.article": "phrases",
    "parser.compound.recipient_preposition": "phrases",
}

_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Register the complete IT/EN parser corpus idempotently."""

    R = _dl.register

    R("parser.ordering.mode_verb", "mapping", match_mode="word",
      it={
          "group": [
              "raggruppata", "raggruppate", "raggruppati", "raggruppato",
              "raggruppa", "raggruppare", "raggruppando", "raggruppale",
              "raggruppali", "suddivisa", "suddivise", "suddivisi",
              "suddiviso", "suddividi", "suddividile", "suddividili",
              "divise", "divisi",
          ],
          "sort": [
              "ordinata", "ordinate", "ordinati", "ordinato", "ordina",
              "ordinare", "ordinando", "ordinale", "ordinali",
              "riordinata", "riordinate", "riordinati", "riordinato",
              "riordina", "riordinare", "riordinando", "riordinale",
              "riordinali",
          ],
      },
      en={
          "group": ["group", "grouped"],
          "sort": ["sort", "sorted", "order", "ordered", "arrange",
                   "arranged"],
      })
    R("parser.ordering.group_connector", "phrases", match_mode="word",
      it=["per"], en=["by"])
    R("parser.ordering.sort_connector", "phrases", match_mode="word",
      it=["per"], en=["by"])
    # The historical English/Italian additive grammar admitted this one
    # standalone prefix.  Duplicating it in both baseline rows preserves that
    # exact union without adding an unmeasured English paraphrase.
    R("parser.ordering.sort_prefix", "phrases", match_mode="word",
      it=["in ordine di"], en=["in ordine di"])
    R("parser.ordering.article", "phrases", match_mode="word",
      it=["il", "lo", "la", "i", "gli", "le", "l", "un", "uno",
          "una", "mio", "mia", "miei", "mie", "loro"],
      en=["the", "a", "an", "my", "their"])
    R("parser.ordering.key_stop", "phrases", match_mode="word",
      it=[
          "di", "del", "della", "dei", "delle", "da", "in", "con",
          "su", "per", "tra", "fra", "e", "ed", "o", "od", "poi",
          "che", "quindi", "favore", "cortesia", "piacere", "me",
          "esempio", "prima", "crescente", "decrescente", "ascendente",
          "discendente",
      ],
      en=["and", "or", "first", "ascending", "descending", "asc", "desc"])
    R("parser.ordering.descending", "phrases", match_mode="word",
      it=[
          "decrescente", "decrescenti", "discendente", "discendenti",
          "inversa", "inverso", "inverse", "inversi",
          "dal piu recente", "dal più recente", "dal piu grande",
          "dal più grande", "dal piu nuovo", "dal più nuovo",
          "piu recenti prima", "più recenti prima", "piu grandi prima",
          "più grandi prima", "piu nuovi prima", "più nuovi prima",
          "piu nuove prima", "più nuove prima",
      ],
      en=["descending", "desc", "newest first", "largest first",
          "biggest first", "reverse", "reversed"])
    R("parser.ordering.field_alias", "mapping", match_mode="word",
      it={
          "domain": ["dominio", "domini"],
          "account": ["casella", "caselle", "cassetta"],
          "sender": ["mittente", "mittenti", "da"],
          "recipient": ["destinatario", "destinatari"],
          "date": ["data", "giorno", "ora", "orario", "quando"],
          "size": ["dimensione", "dimensioni", "grandezza", "peso"],
          "subject": ["oggetto", "titolo"],
          "name": ["nome"],
          "type": ["tipo", "formato", "estensione"],
          "folder": ["cartella", "cartelle"],
          "status": ["stato"],
          "author": ["autore", "autori"],
          "category": ["categoria", "categorie", "classe", "etichetta",
                       "importanza"],
      },
      en={
          "domain": ["domain", "domains", "host", "hostname"],
          "account": ["mailbox", "mailboxes", "account", "accounts", "mail"],
          "sender": ["sender", "senders", "from"],
          "recipient": ["recipient", "recipients", "to"],
          "date": ["date", "day", "time", "when"],
          "size": ["size"],
          "subject": ["subject", "title"],
          "name": ["name", "filename"],
          "type": ["type", "format", "extension"],
          "folder": ["folder", "directory"],
          "status": ["state", "status"],
          "author": ["author", "authors"],
          "category": ["category", "class", "label", "importance"],
      })

    R("parser.time.past_determiner", "phrases", match_mode="word",
      it=[
          "ultima", "ultime", "ultimi", "ultimo", "scorsa", "scorse",
          "scorsi", "scorso", "passata", "passate", "passati", "passato",
      ],
      en=["last", "past"])
    R("parser.time.unit", "mapping", match_mode="word",
      it={
          "h": ["ora", "ore", "h"],
          "d": ["giorno", "giorni", "gg", "d"],
          "w": ["settimana", "settimane", "sett", "w"],
          "m": ["mese", "mesi", "m"],
          "y": ["anno", "anni", "y"],
      },
      en={
          "h": ["hour", "hours", "hr", "hrs", "h"],
          "d": ["day", "days", "d"],
          "w": ["week", "weeks", "w"],
          "m": ["month", "months", "m"],
          "y": ["year", "years", "y"],
      })
    R("parser.time.singular_unit", "mapping", match_mode="word",
      it={"h": ["ora"], "d": ["giorno"], "m": ["mese"], "y": ["anno"]},
      en={"h": ["hour"], "d": ["day"], "m": ["month"], "y": ["year"]})
    # Separate resources let a translation legitimately reuse one postfix for
    # several units without becoming an ambiguous canonical mapping.
    for unit, forms in {
        "h": ["scorsa", "scorse", "passata", "passate"],
        "d": ["scorso", "scorsi", "passato", "passati"],
        "w": ["fa", "scorsa", "scorse", "passata", "passate"],
        "m": ["fa", "scorsi", "passati"],
        "y": ["fa", "scorsi", "passati"],
    }.items():
        R(f"parser.time.past_postfix.{unit}", "phrases", match_mode="word",
          it=forms, en=forms)
    R("parser.time.relative_day", "mapping", match_mode="word",
      it={"today": ["oggi"], "yesterday": ["ieri"]},
      en={"today": ["today"], "yesterday": ["yesterday"]})
    R("parser.time.absolute_year_prefix", "phrases", match_mode="word",
      it=["dell'anno", "dell anno", "nell'anno", "nell anno", "anno",
          "del", "dal", "nel"],
      en=["of", "in", "year"])
    R("parser.time.absolute_year_suffix", "phrases", match_mode="word",
      it=["year"], en=["year"])
    R("parser.time.range_connector", "mapping", match_mode="word",
      it={"from": ["dal"], "to": ["al"]},
      en={"from": ["dal"], "to": ["al"]})
    R("parser.time.normalizer_past_determiner", "phrases", match_mode="word",
      it=["ultimi", "ultime"], en=["ultimi", "ultime"])
    R("parser.time.future_offset_prefix", "phrases", match_mode="word",
      it=["in", "fra", "tra"], en=["in"])
    R("parser.time.future_determiner", "phrases", match_mode="word",
      it=["prossimi", "prossime"], en=["prossimi", "prossime"])
    R("parser.time.day_word", "phrases", match_mode="word",
      it=["giorni"], en=["day", "days"])
    R("parser.time.past_offset_suffix", "phrases", match_mode="word",
      it=["fa"], en=["ago"])

    R("parser.recurrence.interrogative", "phrases", match_mode="word",
      it=["quanti", "quante", "quanto", "quanta", "qual", "quali", "quale", "chi",
          "che", "cosa", "come", "perche", "perché", "quando", "dove"],
      en=["how", "what", "which", "who", "why", "when", "where", "do",
          "does", "did", "is", "are", "can", "could"])
    R("parser.recurrence.quantifier", "phrases", match_mode="word",
      it=["ogni"], en=["every"])
    R("parser.recurrence.unit", "mapping", match_mode="word",
      it={
          "half_hour": ["mezz'ora", "mezz ora", "mezzora"],
          "minute": ["minuto", "minuti"],
          "hour": ["ora", "ore"],
          "day": ["giorno", "giorni", "di", "dì"],
      },
      en={
          "half_hour": ["half hour", "half an hour"],
          "minute": ["minute", "minutes", "min", "mins"],
          "hour": ["hour", "hours", "hr", "hrs"],
          "day": ["day", "days"],
      })
    R("parser.recurrence.at", "phrases", match_mode="word",
      it=["all", "alle"], en=["at"])
    R("parser.recurrence.daily", "phrases", match_mode="word",
      it=["daily"], en=["daily"])
    R("parser.recurrence.hourly", "phrases", match_mode="word",
      it=["hourly"], en=["hourly"])
    R("parser.recurrence.task_noun", "phrases", match_mode="word",
      it=["task", "attivita", "attività", "lavoro", "promemoria", "cron",
          "routine"],
      en=["task", "job", "reminder", "cron", "routine"])
    R("parser.recurrence.article", "phrases", match_mode="word",
      it=["un", "uno", "una", "un'", "il", "lo", "la"],
      en=["a", "an", "the"])
    R("parser.recurrence.relative", "phrases", match_mode="word",
      it=["che", "per"], en=["that", "which", "to"])
    R("parser.recurrence.edge_connector", "phrases", match_mode="word",
      it=["e", "ed", "poi"], en=["and", "then"])

    R("parser.compound.format_hint", "mapping", match_mode="word",
      it={
          "files:spreadsheet": ["foglio di calcolo", "foglio elettronico",
                                "foglio", "fogli", "excel", "spreadsheet"],
          "files:xlsx": ["xlsx", "xls"],
          "files:csv": ["csv"],
          "files:pdf": ["pdf"],
          "files:doc": ["doc", "documento"],
          "files:json": ["json"],
          "files:xml": ["xml"],
          "files:html": ["html"],
          "files:md": ["markdown", "md"],
          "files:txt": ["txt", "testo"],
      },
      en={
          "files:spreadsheet": ["spreadsheet", "excel"],
          "files:xlsx": ["xlsx", "xls"],
          "files:csv": ["csv"],
          "files:pdf": ["pdf"],
          "files:doc": ["doc", "document"],
          "files:json": ["json"],
          "files:xml": ["xml"],
          "files:html": ["html"],
          "files:md": ["markdown", "md"],
          "files:txt": ["txt", "text"],
      })
    R("parser.compound.field_stop", "phrases", match_mode="word",
      it=["il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "dei",
          "degli", "delle", "del", "dello", "della", "di", "da", "d", "l",
          "a", "ad", "ogni"],
      en=["the", "an", "of", "each", "every", "its", "their"])
    R("parser.compound.field_cut", "phrases", match_mode="word",
      it=["da", "dal", "dalla", "dallo", "dai", "dagli", "dalle", "nel",
          "nella", "nello", "nei", "negli", "su", "sul", "sulla", "sui",
          "sulle", "per", "con", "tra", "fra", "presso", "in"],
      en=["from", "in", "into", "about", "regarding", "for"])
    R("parser.compound.schema_marker", "phrases", match_mode="word",
      it=["colonne", "campi", "intestazioni", "voci", "dati"],
      en=["columns", "fields", "headers", "data"])
    R("parser.compound.tabular_noun", "phrases", match_mode="word",
      it=["foglio di calcolo", "foglio elettronico", "foglio", "tabella"],
      en=["spreadsheet", "sheet", "table"])
    R("parser.compound.with_connector", "phrases", match_mode="word",
      it=["con"], en=["with"])
    R("parser.compound.list_connector", "phrases", match_mode="word",
      it=["e", "ed"], en=["and"])
    R("parser.compound.artifact_boundary", "phrases", match_mode="word",
      it=["archivio", "rapporto", "cartella"],
      en=["archive", "zip", "report", "folder", "directory"])
    R("parser.compound.row_noun", "phrases", match_mode="word",
      it=["righe"], en=["rows", "records"])
    R("parser.compound.total_quantifier", "phrases", match_mode="word",
      it=["tutti i", "tutte le"], en=["all", "all the"])
    R("parser.compound.article", "phrases", match_mode="word",
      it=["il", "lo", "la", "i", "gli", "le", "un", "uno", "una"],
      en=["the", "a", "an"])
    R("parser.compound.recipient_preposition", "phrases", match_mode="word",
      it=["a", "ad"], en=["to"])


def _all_seed_rows_ready() -> bool:
    for concept, kind in _KINDS.items():
        for lang in ("it", "en"):
            if not _dl.native_resource_status(concept, lang)["ok"]:
                return False
            resource = _dl.resource_for_language(
                concept, lang, fallback=False, ready_only=True,
            )
            if not resource or resource.get("kind") != kind:
                return False
    return True


def _ensure_registered() -> None:
    """Register lazily and retry after tests/installations swap the DB."""

    global _registered_target
    target = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    if _registered_target == target:
        return
    register_all()
    refreshed = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    _registered_target = refreshed if _all_seed_rows_ready() else None


def load_family(name: str) -> dict[str, object] | None:
    """Return one complete active parser family, or ``None`` fail-safe.

    Readiness is checked against the exact active language.  Once complete,
    one atomic family snapshot adds the exact reviewed baselines without ever
    reopening the fallback-aware resolver.  This preserves the historical
    IT+EN union and adds the ready target locale in the same database epoch.
    """

    concepts = FAMILY_CONCEPTS.get(name)
    if not concepts:
        raise KeyError(f"unknown parser lexicon family: {name}")
    _ensure_registered()
    snapshot = _dl.native_ready_family_resources(
        {concept: _KINDS[concept] for concept in concepts},
        include_reviewed_baselines=True,
    )
    if snapshot is None:
        return None
    result: dict[str, object] = {}
    for concept in concepts:
        resources = snapshot[concept]
        if _KINDS[concept] == "mapping":
            merged: dict[str, list[str]] = {}
            for resource in resources:
                payload = resource.get("payload")
                if not isinstance(payload, dict):
                    return None
                for canonical, forms in payload.items():
                    if not isinstance(forms, list):
                        return None
                    bucket = merged.setdefault(canonical, [])
                    bucket.extend(form for form in forms if form not in bucket)
            result[concept] = merged
            continue
        merged_forms: list[str] = []
        for resource in resources:
            payload = resource.get("payload")
            if not isinstance(payload, list):
                return None
            merged_forms.extend(form for form in payload if form not in merged_forms)
        result[concept] = merged_forms
    return result


__all__ = [
    "COMPOUND_CONCEPTS",
    "FAMILY_CONCEPTS",
    "ORDERING_CONCEPTS",
    "RECURRENCE_CONCEPTS",
    "TIME_PARSER_CONCEPTS",
    "TIME_RESOLVER_CONCEPTS",
    "load_family",
    "register_all",
]

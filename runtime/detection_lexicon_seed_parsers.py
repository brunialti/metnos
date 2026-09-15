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
    "parser.time.number",
    "parser.time.weekday",
    "parser.time.direction",
    "parser.time.calendar_period",
    "parser.time.month",
    "parser.time.future_offset_prefix",
    "parser.time.future_determiner",
    "parser.time.past_offset_suffix",
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
    "parser.time.number": "mapping",
    "parser.time.weekday": "mapping",
    "parser.time.direction": "mapping",
    "parser.time.calendar_period": "mapping",
    "parser.time.month": "mapping",
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


def register_temporal_messages() -> None:
    """Versioned UI sources for the common temporal selection form."""
    import i18n
    i18n.register_key_if_missing("MSG_TEMPORAL_TITLE",
                                "Chiarisci data o intervallo", "Clarify date or interval")
    i18n.register_key_if_missing("MSG_TEMPORAL_CHOOSE",
                                "Quale data o intervallo intendi per {field}?",
                                "Which date or interval do you mean for {field}?")
    for kind, text_it, text_en in (
        ("DATE", "la data", "the date"),
        ("DATE_TIME", "la data e l'ora", "the date and time"),
        ("WINDOW", "il periodo", "the period"),
        ("TIMEZONE", "il fuso orario", "the timezone"),
    ):
        i18n.register_key_if_missing("MSG_TEMPORAL_FIELD_" + kind, text_it, text_en)


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
          "s": ["secondo", "secondi", "s"],
          "min": ["minuto", "minuti", "min"],
          "h": ["ora", "ore", "h"],
          "d": ["giorno", "giorni", "gg", "d"],
          "w": ["settimana", "settimane", "sett", "w"],
          "m": ["mese", "mesi", "m"],
          "y": ["anno", "anni", "y"],
      },
      en={
          "s": ["second", "seconds", "sec", "s"],
          "min": ["minute", "minutes", "mins", "min"],
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
      it={"today": ["oggi"], "yesterday": ["ieri"],
          "tomorrow": ["domani"], "today+2d": ["dopodomani", "dopo domani"],
          "today-2d": ["avantieri", "l'altro ieri", "l’altro ieri"]},
      en={"today": ["today"], "yesterday": ["yesterday"],
          "tomorrow": ["tomorrow"], "today+2d": ["the day after tomorrow", "day after tomorrow"],
          "today-2d": ["the day before yesterday", "day before yesterday"]})
    # Cardinal surfaces are localization data, not branches in a parser.
    R("parser.time.number", "mapping", match_mode="word",
      it={"0": ["zero"], "1": ["uno", "una", "un", "un'", "un’"],
          "2": ["due"], "3": ["tre"], "4": ["quattro"], "5": ["cinque"],
          "6": ["sei"], "7": ["sette"], "8": ["otto"], "9": ["nove"],
          "10": ["dieci"], "11": ["undici"], "12": ["dodici"],
          "13": ["tredici"], "14": ["quattordici"], "15": ["quindici"],
          "16": ["sedici"], "17": ["diciassette"], "18": ["diciotto"],
          "19": ["diciannove"], "20": ["venti"]},
      en={"0": ["zero"], "1": ["one", "a", "an"], "2": ["two"],
          "3": ["three"], "4": ["four"], "5": ["five"], "6": ["six"],
          "7": ["seven"], "8": ["eight"], "9": ["nine"], "10": ["ten"],
          "11": ["eleven"], "12": ["twelve"], "13": ["thirteen"],
          "14": ["fourteen"], "15": ["fifteen"], "16": ["sixteen"],
          "17": ["seventeen"], "18": ["eighteen"], "19": ["nineteen"],
          "20": ["twenty"]})
    R("parser.time.weekday", "mapping", match_mode="word",
      it={"0": ["lunedì", "lunedi", "lunedi'"],
          "1": ["martedì", "martedi", "martedi'"],
          "2": ["mercoledì", "mercoledi", "mercoledi'"],
          "3": ["giovedì", "giovedi", "giovedi'"],
          "4": ["venerdì", "venerdi", "venerdi'"],
          "5": ["sabato"], "6": ["domenica"]},
      en={"0": ["monday"], "1": ["tuesday"], "2": ["wednesday"],
          "3": ["thursday"], "4": ["friday"], "5": ["saturday"],
          "6": ["sunday"]})
    R("parser.time.direction", "mapping", match_mode="word",
      it={"last": ["scorso", "scorsa", "passato", "passata"],
          "next": ["prossimo", "prossima"], "this": ["questo", "questa"]},
      en={"last": ["last", "previous"], "next": ["next"], "this": ["this"]})
    R("parser.time.calendar_period", "mapping", match_mode="word",
      it={"week": ["settimana"], "month": ["mese"], "year": ["anno"]},
      en={"week": ["week"], "month": ["month"], "year": ["year"]})
    R("parser.time.month", "mapping", match_mode="word",
      it={"1": ["gennaio", "gen"], "2": ["febbraio", "feb"],
          "3": ["marzo", "mar"], "4": ["aprile", "apr"],
          "5": ["maggio", "mag"], "6": ["giugno", "giu"],
          "7": ["luglio", "lug"], "8": ["agosto", "ago"],
          "9": ["settembre", "set"], "10": ["ottobre", "ott"],
          "11": ["novembre", "nov"], "12": ["dicembre", "dic"]},
      en={"1": ["january", "jan"], "2": ["february", "feb"],
          "3": ["march", "mar"], "4": ["april", "apr"],
          "5": ["may"], "6": ["june", "jun"], "7": ["july", "jul"],
          "8": ["august", "aug"], "9": ["september", "sep", "sept"],
          "10": ["october", "oct"], "11": ["november", "nov"],
          "12": ["december", "dec"]})
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
      it=["prossimi", "prossime"], en=["next", "coming"])
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

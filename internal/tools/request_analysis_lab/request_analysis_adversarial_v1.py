#!/usr/bin/env python3
"""Frozen multilingual adversarial oracle for structured RequestAnalysis.

This file is deliberately outside the repository.  It contains a candidate-
independent fixture and a sequential runner for the experimental structured
analyzer in /tmp/metnos_unified_query_bench.py.  Expected values are separated
into four gates:

G0 intrinsic structure/schema invariants
G1 predicate inventory, anchors, roles and dependency graph
G2 morphology and semantic frame
G3 product/catalog projection and requested signature

Novel unambiguous cases and probes related to contradictions in the old exact
verb/object gold are scored in separate tracks.  No expected value is passed to
the model or to the catalog compiler.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any


VERSION = "request-analysis-adversarial-v1"
CREATED = "2026-08-08"
FROZEN_SHA256 = "620803a44fc63e88d4b613963ca55e7f9a82a4c87f2f6c912b1e65904ea472f8"

LANGUAGES = ("it", "en", "fr", "es", "de")
ROLES = ("request", "forbid", "condition", "description", "quote")
ACTIONS = (
    "read", "write", "move", "delete", "create", "find", "list",
    "filter", "sort", "group", "classify", "get", "set", "send",
    "describe", "render", "extract", "compress", "compute", "compare",
    "change", "order", "share", "open", "login", "act",
)
OBJECTS = (
    "files", "dirs", "packages", "messages", "events", "contacts",
    "places", "processes", "urls", "numbers", "images", "signatures",
    "texts", "proposals", "persons", "tasks", "inputs", "approval",
    "credentials", "issues", "pulls", "calendars", "entries", "lists",
    "skills", "sites", "preferences",
)
EFFECTS = (
    "content_read", "discovery", "existence_check", "snapshot",
    "metadata_lookup", "create_artifact", "update_artifact", "delete",
    "move", "select_subset", "label_assignment", "collection_grouping",
    "transform", "human_delivery", "access_grant", "attachment_reference",
    "biometric_identity_lookup", "schedule", "other",
)
SCOPES = (
    "single_known", "collection_by_criterion", "whole_domain",
    "held_result", "new_resource", "not_applicable",
)
RESOLUTIONS = ("direct", "generalized", "technical_override")
SINK_MODES = ("none", "explicit_persistence")

HEAD_KEYS = {
    "predicate_anchor_token_id", "role", "lemma", "semantic_gloss_en",
    "semantic_action", "effect", "resource_scope",
}
PREDICATE_KEYS_BASE = {
    "predicate_anchor_token_id", "role", "verb", "verb_resolution",
    "object", "object_qualifier", "materialize_to", "sink_mode",
    "input_from_predicate_anchor_id",
}
PREDICATE_KEYS_GROUNDED_SINK = PREDICATE_KEYS_BASE | {"sink_anchor_token_id"}


def _as_tuple(value: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def expected_predicate(
    pid: str,
    anchor: str | tuple[str, ...],
    role: str,
    lemma: str | tuple[str, ...],
    gloss: str | tuple[str, ...],
    semantic_action: str | tuple[str, ...],
    effect: str,
    scope: str,
    *,
    verb: str | None = None,
    object_: str | None = None,
    qualifier: str = "none",
    materialize_to: str = "none",
    sink_mode: str = "none",
    sink_anchor: str | tuple[str, ...] | None = None,
    from_id: str | None = None,
    resolution: str | tuple[str, ...] = ("direct", "generalized"),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": pid,
        "anchor_any": list(_as_tuple(anchor)),
        "role": role,
        "lemma_any": list(_as_tuple(lemma)),
        "gloss_any": list(_as_tuple(gloss)),
        "semantic_action_any": list(_as_tuple(semantic_action)),
        "effect": effect,
        "resource_scope": scope,
        "input_from": from_id,
    }
    if role == "request":
        if verb is None or object_ is None:
            raise ValueError(f"request predicate {pid} needs G3 projection")
        result["projection"] = {
            "verb": verb,
            "object": object_,
            "object_qualifier": qualifier,
            "materialize_to": materialize_to,
            "sink_mode": sink_mode,
            "verb_resolution_any": list(_as_tuple(resolution)),
            "sink_anchor_any": ([] if sink_anchor is None
                                else list(_as_tuple(sink_anchor))),
        }
    else:
        result["projection"] = {
            "executable": False,
            "policy": "canonical_or_none_but_never_in_requested_signature",
        }
    return result


def make_case(
    cid: str,
    track: str,
    family: str,
    language: str,
    query: str,
    predicates: list[dict[str, Any]],
    requested_signature: list[str],
    *,
    legacy_topic: str | None = None,
    rationale: str = "",
) -> dict[str, Any]:
    return {
        "id": cid,
        "track": track,
        "family": family,
        "language": language,
        "query": query,
        "expected": {
            "predicates": predicates,
            "requested_signature": requested_signature,
        },
        "legacy": {
            "status": ("independent" if legacy_topic is None
                       else "adjudicated_contradiction_probe"),
            "topic": legacy_topic,
            "included_in_primary_acceptance": legacy_topic is None,
        },
        "rationale": rationale,
    }


CASES: list[dict[str, Any]] = []


def add(case: dict[str, Any]) -> None:
    CASES.append(case)


# ---------------------------------------------------------------------------
# Independent track: novel cases whose G0-G3 oracle does not inherit an exact
# legacy label.  Ten adversarial families are represented in every language.
# ---------------------------------------------------------------------------

SHOW = {
    "it": ("Mostrami il contenuto grezzo di /tmp/audit.log.", "Mostrami", "mostrare"),
    "en": ("Show me the raw contents of /tmp/audit.log.", "Show", "show"),
    "fr": ("Montre-moi le contenu brut de /tmp/audit.log.", "Montre-moi", "montrer"),
    "es": ("Muéstrame el contenido bruto de /tmp/audit.log.", "Muéstrame", "mostrar"),
    "de": ("Zeig mir den Rohinhalt von /tmp/audit.log an.", "Zeig", "anzeigen"),
}
for lang, (query, anchor, lemma) in SHOW.items():
    add(make_case(
        f"ind.show_content.{lang}", "independent", "contextual_show", lang,
        query,
        [expected_predicate(
            "p1", anchor, "request", lemma, ("show", "display"), "read",
            "content_read", "single_known", verb="read", object_="files",
            resolution="generalized",
        )],
        ["read/files"],
        rationale="A show/display surface predicate must be resolved from its content patient; raw text is preserved.",
    ))


NEGATION = {
    "it": ("Non cancellare rapporto.txt; leggine soltanto il contenuto.",
           "cancellare", "cancellare", "leggine", "leggere"),
    "en": ("Do not delete report.txt; only read its contents.",
           "delete", "delete", "read", "read"),
    "fr": ("Ne supprime pas rapport.txt ; lis seulement son contenu.",
           "supprime", "supprimer", "lis", "lire"),
    "es": ("No borres informe.txt; lee solamente su contenido.",
           "borres", "borrar", "lee", "leer"),
    "de": ("Lösche bericht.txt nicht; lies nur seinen Inhalt.",
           "Lösche", "löschen", "lies", "lesen"),
}
for lang, (query, a1, l1, a2, l2) in NEGATION.items():
    add(make_case(
        f"ind.negation.{lang}", "independent", "forbid_then_request", lang,
        query,
        [
            expected_predicate(
                "p1", a1, "forbid", l1, ("delete", "erase"), "delete",
                "delete", "single_known",
            ),
            expected_predicate(
                "p2", a2, "request", l2, "read", "read",
                "content_read", "single_known", verb="read", object_="files",
            ),
        ],
        ["read/files"],
        rationale="The prohibited deletion is represented but never executable; the positive read remains requested.",
    ))


CONDITION = {
    "it": ("Se il file lock.txt esiste, leggi il file lock.txt.", "esiste", "esistere", "leggi", "leggere"),
    "en": ("If the file lock.txt exists, read the file lock.txt.", "exists", "exist", "read", "read"),
    "fr": ("Si le fichier lock.txt existe, lis le fichier lock.txt.", "existe", "exister", "lis", "lire"),
    "es": ("Si el archivo lock.txt existe, lee el archivo lock.txt.", "existe", "existir", "lee", "leer"),
    "de": ("Wenn die Datei lock.txt existiert, lies die Datei lock.txt.", "existiert", "existieren", "lies", "lesen"),
}
for lang, (query, a1, l1, a2, l2) in CONDITION.items():
    add(make_case(
        f"ind.condition.{lang}", "independent", "condition_not_intent", lang,
        query,
        [
            expected_predicate(
                "p1", a1, "condition", l1, ("exist", "existence"), "find",
                "existence_check", "single_known",
            ),
            expected_predicate(
                "p2", a2, "request", l2, "read", "read",
                "content_read", "single_known", verb="read", object_="files",
            ),
        ],
        ["read/files"],
        rationale="A gating state is not an independently requested verification when the patient is repeated explicitly.",
    ))


PASSIVE_DESCRIPTION = {
    "it": ("Trova i documenti creati dopo lunedì.", "Trova", "trovare", "creati", "creare"),
    "en": ("Find the documents created after Monday.", "Find", "find", "created", "create"),
    "fr": ("Trouve les documents créés après lundi.", "Trouve", "trouver", "créés", "créer"),
    "es": ("Busca los documentos creados después del lunes.", "Busca", "buscar", "creados", "crear"),
    "de": ("Finde die Dokumente, die nach Montag erstellt wurden.", "Finde", "finden", "erstellt", "erstellen"),
}
for lang, (query, a1, l1, a2, l2) in PASSIVE_DESCRIPTION.items():
    add(make_case(
        f"ind.passive_description.{lang}", "independent",
        "relative_passive_description", lang, query,
        [
            expected_predicate(
                "p1", a1, "request", l1, ("find", "search"), "find",
                "discovery", "collection_by_criterion", verb="find",
                object_="files",
            ),
            expected_predicate(
                "p2", a2, "description", l2, ("create", "make"), "create",
                "create_artifact", "collection_by_criterion",
            ),
        ],
        ["find/files"],
        rationale="The passive relative clause constrains the requested set; it is not a second create operation.",
    ))


QUOTED = {
    "it": ("Cerca nelle mail la frase \"cancella tutti i file\".", "Cerca", "cercare", "cancella", "cancellare"),
    "en": ("Search messages for the literal \"delete every file\".", "Search", "search", "delete", "delete"),
    "fr": ("Cherche dans les messages la phrase «supprime tous les fichiers».", "Cherche", "chercher", "supprime", "supprimer"),
    "es": ("Busca en los mensajes la frase «borra todos los archivos».", "Busca", "buscar", "borra", "borrar"),
    "de": ("Suche in Nachrichten nach dem Satz „lösche alle Dateien“.", "Suche", "suchen", "lösche", "löschen"),
}
for lang, (query, a1, l1, a2, l2) in QUOTED.items():
    add(make_case(
        f"ind.quote.{lang}", "independent", "quoted_non_executable", lang,
        query,
        [
            expected_predicate(
                "p1", a1, "request", l1, ("search", "find"), "find",
                "discovery", "collection_by_criterion", verb="find",
                object_="messages",
            ),
            expected_predicate(
                "p2", a2, "quote", l2, ("delete", "erase"), "delete",
                "delete", "whole_domain",
            ),
        ],
        ["find/messages"],
        rationale="Predicate-like text in a literal search value must remain visible as quote but never executable.",
    ))


DEONTIC_PASSIVE = {
    "it": ("Il rapporto finale deve essere scritto in /tmp/finale.txt.", "scritto", "scrivere"),
    "en": ("The final report must be written to /tmp/final.txt.", "written", "write"),
    "fr": ("Le rapport final doit être écrit dans /tmp/final.txt.", "écrit", "écrire"),
    "es": ("El informe final debe escribirse en /tmp/final.txt.", "escribirse", "escribir"),
    "de": ("Der Abschlussbericht soll nach /tmp/final.txt geschrieben werden.", "geschrieben", "schreiben"),
}
for lang, (query, anchor, lemma) in DEONTIC_PASSIVE.items():
    add(make_case(
        f"ind.deontic_passive.{lang}", "independent",
        "passive_requested_operation", lang, query,
        [expected_predicate(
            "p1", anchor, "request", lemma, "write", "write",
            "create_artifact", "new_resource", verb="write", object_="files",
        )],
        ["write/files"],
        rationale="A deontic passive is a requested operation even without imperative morphology; auxiliaries are not predicates.",
    ))


MULTIQUERY = {
    "it": ("Trova i log di ieri, comprimili e inviameli via email.",
           ("Trova", "trovare"), ("comprimili", "comprimere"), ("inviameli", "inviare", "send")),
    "en": ("Find yesterday's logs, compress them, and email them to me.",
           ("Find", "find"), ("compress", "compress"), ("email", "email", "email")),
    "fr": ("Trouve les journaux d’hier, compresse-les et envoie-les-moi par courriel.",
           ("Trouve", "trouver"), ("compresse-les", "compresser"), ("envoie-les-moi", "envoyer", "send")),
    "es": ("Busca los registros de ayer, comprímelos y envíamelos por correo.",
           ("Busca", "buscar"), ("comprímelos", "comprimir"), ("envíamelos", "enviar", "send")),
    "de": ("Finde die Protokolle von gestern, komprimiere sie und sende sie mir per E-Mail.",
           ("Finde", "finden"), ("komprimiere", "komprimieren"), ("sende", "senden", "send")),
}
for lang, (query, first, second, third) in MULTIQUERY.items():
    add(make_case(
        f"ind.multiquery.{lang}", "independent", "clitic_dependency_chain",
        lang, query,
        [
            expected_predicate(
                "p1", first[0], "request", first[1], ("find", "search"),
                "find", "discovery", "collection_by_criterion", verb="find",
                object_="files",
            ),
            expected_predicate(
                "p2", second[0], "request", second[1], "compress",
                "compress", "transform", "held_result", verb="compress",
                object_="files", from_id="p1",
            ),
            expected_predicate(
                "p3", third[0], "request", third[1], third[2], "send",
                "human_delivery", "held_result", verb="send",
                object_="messages", from_id="p2",
            ),
        ],
        ["find/files", "compress/files", "send/messages"],
        rationale="Clitics/pronouns preserve a three-node dataflow; human delivery projects to messages regardless of payload.",
    ))


CLASSIFY_GROUP = {
    "it": ("Trova le foto del viaggio, classificale per soggetto e poi raggruppale per anno.",
           ("Trova", "trovare"), ("classificale", "classificare"), ("raggruppale", "raggruppare")),
    "en": ("Find the trip photos, classify them by subject, then group them by year.",
           ("Find", "find"), ("classify", "classify"), ("group", "group")),
    "fr": ("Trouve les photos du voyage, classe-les par sujet puis regroupe-les par année.",
           ("Trouve", "trouver"), ("classe-les", "classer"), ("regroupe-les", "regrouper")),
    "es": ("Busca las fotos del viaje, clasifícalas por tema y luego agrúpalas por año.",
           ("Busca", "buscar"), ("clasifícalas", "clasificar"), ("agrúpalas", "agrupar")),
    "de": ("Finde die Reisefotos, klassifiziere sie nach Motiv und gruppiere sie dann nach Jahr.",
           ("Finde", "finden"), ("klassifiziere", "klassifizieren"), ("gruppiere", "gruppieren")),
}
for lang, (query, first, second, third) in CLASSIFY_GROUP.items():
    add(make_case(
        f"ind.classify_group.{lang}", "independent",
        "label_vs_partition", lang, query,
        [
            expected_predicate(
                "p1", first[0], "request", first[1], ("find", "search"),
                "find", "discovery", "collection_by_criterion", verb="find",
                object_="images",
            ),
            expected_predicate(
                "p2", second[0], "request", second[1], "classify",
                "classify", "label_assignment", "held_result",
                verb="classify", object_="images", from_id="p1",
            ),
            expected_predicate(
                "p3", third[0], "request", third[1], "group", "group",
                "collection_grouping", "held_result", verb="group",
                object_="images", from_id="p2",
            ),
        ],
        ["find/images", "classify/images", "group/images"],
        rationale="Assigning labels and partitioning a held collection remain distinct canonical actions.",
    ))


REFLEXIVE_DESCRIPTION = {
    "it": ("Il file si è spostato da solo; ora leggi il contenuto del file.", "spostato", "spostare", "leggi", "leggere"),
    "en": ("The file moved by itself; now read the file's contents.", "moved", "move", "read", "read"),
    "fr": ("Le fichier s’est déplacé tout seul ; lis maintenant le contenu du fichier.", "déplacé", "déplacer", "lis", "lire"),
    "es": ("El archivo se movió solo; ahora lee el contenido del archivo.", "movió", "mover", "lee", "leer"),
    "de": ("Die Datei hat sich selbst verschoben; lies jetzt den Inhalt der Datei.", "verschoben", "verschieben", "lies", "lesen"),
}
for lang, (query, a1, l1, a2, l2) in REFLEXIVE_DESCRIPTION.items():
    add(make_case(
        f"ind.reflexive_description.{lang}", "independent",
        "reflexive_state_not_request", lang, query,
        [
            expected_predicate(
                "p1", a1, "description", l1, "move", "move", "move",
                "single_known",
            ),
            expected_predicate(
                "p2", a2, "request", l2, "read", "read",
                "content_read", "single_known", verb="read", object_="files",
            ),
        ],
        ["read/files"],
        rationale="A reflexive/spontaneous past event is descriptive, while the following explicit read is executable.",
    ))


URL_PIPELINE = {
    "it": ("Leggi il contenuto di https://example.org/info e salvalo in /tmp/info.txt.",
           ("Leggi", "leggere"), ("salvalo", "salvare")),
    "en": ("Read the content at https://example.org/info and save it to /tmp/info.txt.",
           ("Read", "read"), ("save", "save")),
    "fr": ("Lis le contenu de https://example.org/info et enregistre-le dans /tmp/info.txt.",
           ("Lis", "lire"), ("enregistre-le", "enregistrer")),
    "es": ("Lee el contenido de https://example.org/info y guárdalo en /tmp/info.txt.",
           ("Lee", "leer"), ("guárdalo", "guardar")),
    "de": ("Lies den Inhalt von https://example.org/info und speichere ihn in /tmp/info.txt.",
           ("Lies", "lesen"), ("speichere", "speichern")),
}
for lang, (query, first, second) in URL_PIPELINE.items():
    add(make_case(
        f"ind.url_pipeline.{lang}", "independent", "explicit_two_step_io",
        lang, query,
        [
            expected_predicate(
                "p1", first[0], "request", first[1], "read", "read",
                "content_read", "single_known", verb="read", object_="urls",
            ),
            expected_predicate(
                "p2", second[0], "request", second[1], ("save", "write"),
                "write", "create_artifact", "new_resource", verb="write",
                object_="files", from_id="p1",
            ),
        ],
        ["read/urls", "write/files"],
        rationale="The explicit URL read and independently anchored save are two requested predicates with dataflow.",
    ))


# ---------------------------------------------------------------------------
# Legacy-contradiction track.  These are novel paraphrases, not copied gold
# strings.  Their expected G0-G3 values come from the frozen adjudication
# overlay and are never mixed into the independent acceptance denominator.
# ---------------------------------------------------------------------------

PROCESS_SNAPSHOT = {
    "it": ("Cerca i processi ad alto consumo di memoria.", "Cerca", "cercare"),
    "en": ("Search for high-memory processes.", "Search", "search"),
    "fr": ("Recherche les processus à forte consommation de mémoire.", "Recherche", "rechercher"),
    "es": ("Busca procesos con alto consumo de memoria.", "Busca", "buscar"),
    "de": ("Suche nach speicherintensiven Prozessen.", "Suche", "suchen"),
}
for lang, (query, anchor, lemma) in PROCESS_SNAPSHOT.items():
    add(make_case(
        f"legacy.process_snapshot.{lang}", "legacy_contradiction",
        "process_find_vs_get", lang, query,
        [expected_predicate(
            "p1", anchor, "request", lemma, ("search", "find"), "find",
            "snapshot", "collection_by_criterion", verb="get",
            object_="processes", resolution="technical_override",
        )],
        ["get/processes"], legacy_topic="find/processes_vs_get/processes",
        rationale="G2 preserves search/find semantics; G3 projects a process-table criterion to the available snapshot operation.",
    ))


ATTACHMENT = {
    "it": ("Allega /tmp/logo.png al messaggio e invialo a Lucia.",
           ("Allega", "allegare"), ("invialo", "inviare")),
    "en": ("Attach /tmp/logo.png to the message and send it to Lucia.",
           ("Attach", "attach"), ("send", "send")),
    "fr": ("Joins /tmp/logo.png au message puis envoie-le à Lucia.",
           ("Joins", "joindre"), ("envoie-le", "envoyer")),
    "es": ("Adjunta /tmp/logo.png al mensaje y envíalo a Lucía.",
           ("Adjunta", "adjuntar"), ("envíalo", "enviar")),
    "de": ("Hänge /tmp/logo.png an die Nachricht an und sende sie an Lucia.",
           ("Hänge", "anhängen"), ("sende", "senden")),
}
for lang, (query, first, second) in ATTACHMENT.items():
    add(make_case(
        f"legacy.attachment.{lang}", "legacy_contradiction",
        "attachment_reference", lang, query,
        [
            expected_predicate(
                "p1", first[0], "request", first[1], "attach",
                ("get", "set", "write"), "attachment_reference",
                "single_known", verb="get", object_="files",
                resolution=("generalized", "technical_override"),
            ),
            expected_predicate(
                "p2", second[0], "request", second[1], "send", "send",
                "human_delivery", "held_result", verb="send",
                object_="messages", from_id="p1",
            ),
        ],
        ["get/files", "send/messages"], legacy_topic="attachment_action_vs_argument_prerequisite",
        rationale="The ontology lacks attach as an action; its effect is frozen, while G3 resolves the known file then sends the message.",
    ))


STORE_SPREADSHEET = {
    "it": ("Estrai totale e valuta dalle ricevute direttamente nello store dei record, poi crea un nuovo foglio di calcolo.",
           ("Estrai", "estrarre"), ("crea", "creare"), "store"),
    "en": ("Extract total and currency from the receipts directly into the record store, then create a new spreadsheet.",
           ("Extract", "extract"), ("create", "create"), "store"),
    "fr": ("Extrais le total et la devise des reçus directement dans le registre de données, puis crée une nouvelle feuille de calcul.",
           ("Extrais", "extraire"), ("crée", "créer"), "registre"),
    "es": ("Extrae el total y la moneda de los recibos directamente al almacén de registros; luego crea una hoja de cálculo nueva.",
           ("Extrae", "extraer"), ("crea", "crear"), "almacén"),
    "de": ("Extrahiere Betrag und Währung aus den Belegen direkt in den Datenspeicher und erstelle danach eine neue Tabelle.",
           ("Extrahiere", "extrahieren"), ("erstelle", "erstellen"), "Datenspeicher"),
}
for lang, (query, first, second, sink_anchor) in STORE_SPREADSHEET.items():
    add(make_case(
        f"legacy.store_sheet.{lang}", "legacy_contradiction",
        "implicit_store_sink_and_qualifier", lang, query,
        [
            expected_predicate(
                "p1", first[0], "request", first[1], "extract", "extract",
                "transform", "collection_by_criterion", verb="extract",
                object_="entries", materialize_to="entries",
                sink_mode="explicit_persistence", sink_anchor=sink_anchor,
            ),
            expected_predicate(
                "p2", second[0], "request", second[1], "create", "create",
                "create_artifact", "new_resource", verb="create",
                object_="files", qualifier="spreadsheet", from_id="p1",
            ),
        ],
        ["extract/entries", "write/entries", "create/files_spreadsheet"],
        legacy_topic="implicit_write_entries_and_files_spreadsheet_shape",
        rationale="Store persistence is an effect/sink, and spreadsheet is a qualifier rather than an object token.",
    ))


IMAGE_CARRIER = {
    "it": ("Trova le foto panoramiche, spostale in /tmp/pano e condividile con Lucia in sola lettura.",
           ("Trova", "trovare"), ("spostale", "spostare"), ("condividile", "condividere")),
    "en": ("Find the panoramic photos, move them to /tmp/pano, and share them with Lucia as read-only.",
           ("Find", "find"), ("move", "move"), ("share", "share")),
    "fr": ("Trouve les photos panoramiques, déplace-les dans /tmp/pano puis partage-les avec Lucia en lecture seule.",
           ("Trouve", "trouver"), ("déplace-les", "déplacer"), ("partage-les", "partager")),
    "es": ("Busca las fotos panorámicas, muévelas a /tmp/pano y compártelas con Lucía en modo de solo lectura.",
           ("Busca", "buscar"), ("muévelas", "mover"), ("compártelas", "compartir")),
    "de": ("Finde die Panoramafotos, verschiebe sie nach /tmp/pano und teile sie mit Lucia mit Nur-Lese-Zugriff.",
           ("Finde", "finden"), ("verschiebe", "verschieben"), ("teile", "teilen")),
}
for lang, (query, first, second, third) in IMAGE_CARRIER.items():
    add(make_case(
        f"legacy.image_carrier.{lang}", "legacy_contradiction",
        "semantic_image_vs_file_carrier", lang, query,
        [
            expected_predicate(
                "p1", first[0], "request", first[1], ("find", "search"),
                "find", "discovery", "collection_by_criterion", verb="find",
                object_="images",
            ),
            expected_predicate(
                "p2", second[0], "request", second[1], "move", "move",
                "move", "held_result", verb="move", object_="files",
                from_id="p1",
            ),
            expected_predicate(
                "p3", third[0], "request", third[1], "share", "share",
                "access_grant", "held_result", verb="share", object_="files",
                from_id="p2",
            ),
        ],
        ["find/images", "move/files", "share/files"],
        legacy_topic="semantic_images_vs_generic_file_carrier",
        rationale="Visual discovery uses images; generic move and ACL grant project through the file carrier.",
    ))


ORACLE_SPEC = {
    "g0": {
        "name": "intrinsic_structure",
        "rules": [
            "strict top/head/predicate shapes",
            "aligned semantic-head and predicate arrays",
            "valid increasing non-literal anchors",
            "valid enum values and sink alignment",
            "optional grounded-sink schema has a valid destination anchor",
            "dependency edges point only to earlier anchors",
            "requested records are complete",
        ],
    },
    "g1": {
        "name": "predicate_inventory",
        "rules": [
            "exact predicate count and source order",
            "anchor token belongs to frozen allowed set",
            "exact role",
            "exact dependency edge by predicate identity",
        ],
    },
    "g2": {
        "name": "semantic_frame",
        "rules": [
            "same-language dictionary lemma",
            "short contextual English gloss",
            "semantic action, effect and resource scope",
            "all records including forbid/condition/description/quote",
        ],
    },
    "g3": {
        "name": "catalog_projection",
        "rules": [
            "exact requested verb/object/qualifier/sink fields",
            "accepted verb-resolution class",
            "exact requested signature including materialized sink",
            "non-request records excluded from execution",
            "optional experimental compiler must yield the same signature",
        ],
    },
    "aggregation": "lexicographic; Gi is credited only if every earlier gate passes",
    "legacy_policy": "legacy_contradiction track is reported separately and excluded from primary acceptance",
}


def normalize_token(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    while text and not text[0].isalnum():
        text = text[1:]
    while text and not text[-1].isalnum():
        text = text[:-1]
    return text


def normalize_word(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or ""))
                    .casefold().strip().split())


def tokenize(query: str) -> list[str]:
    return re.findall(r"\S+", query or "", re.UNICODE)


def fixture_payload() -> dict[str, Any]:
    return {
        "version": VERSION,
        "created": CREATED,
        "languages": list(LANGUAGES),
        "oracle": ORACLE_SPEC,
        "cases": CASES,
    }


def fixture_digest() -> str:
    blob = json.dumps(
        fixture_payload(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def resolve_expected_anchor_positions(case: dict[str, Any]) -> list[list[int]]:
    tokens = tokenize(case["query"])
    normalized = [normalize_token(token) for token in tokens]
    positions: list[list[int]] = []
    for expected in case["expected"]["predicates"]:
        allowed = {normalize_token(value) for value in expected["anchor_any"]}
        positions.append([
            index for index, token in enumerate(normalized, 1)
            if token in allowed
        ])
    return positions


def validate_fixture() -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    queries: set[str] = set()
    for case in CASES:
        cid = case.get("id")
        if cid in ids:
            errors.append(f"{cid}: duplicate id")
        ids.add(cid)
        if case.get("query") in queries:
            errors.append(f"{cid}: duplicate query")
        queries.add(case.get("query"))
        if case.get("language") not in LANGUAGES:
            errors.append(f"{cid}: invalid language")
        if case.get("track") not in ("independent", "legacy_contradiction"):
            errors.append(f"{cid}: invalid track")
        expected = case.get("expected") or {}
        predicates = expected.get("predicates") or []
        positions = resolve_expected_anchor_positions(case)
        last_minimum = 0
        pred_ids: set[str] = set()
        for index, (item, candidates) in enumerate(zip(predicates, positions), 1):
            pid = item.get("id")
            if pid in pred_ids:
                errors.append(f"{cid}: duplicate predicate id {pid}")
            pred_ids.add(pid)
            later = [value for value in candidates if value > last_minimum]
            if not later:
                errors.append(f"{cid}:{pid}: no ordered anchor token for {item.get('anchor_any')}")
            else:
                last_minimum = min(later)
            if item.get("role") not in ROLES:
                errors.append(f"{cid}:{pid}: invalid role")
            if not set(item.get("semantic_action_any") or ()).issubset({"none", *ACTIONS}):
                errors.append(f"{cid}:{pid}: invalid semantic action")
            if item.get("effect") not in EFFECTS:
                errors.append(f"{cid}:{pid}: invalid effect")
            if item.get("resource_scope") not in SCOPES:
                errors.append(f"{cid}:{pid}: invalid scope")
            from_id = item.get("input_from")
            if from_id is not None and from_id not in pred_ids:
                errors.append(f"{cid}:{pid}: dependency is not earlier: {from_id}")
            projection = item.get("projection") or {}
            if item.get("role") == "request":
                if projection.get("verb") not in ACTIONS:
                    errors.append(f"{cid}:{pid}: invalid projected verb")
                if projection.get("object") not in OBJECTS:
                    errors.append(f"{cid}:{pid}: invalid projected object")
                if projection.get("sink_mode") not in SINK_MODES:
                    errors.append(f"{cid}:{pid}: invalid sink mode")
                if ((projection.get("sink_mode") == "none")
                        != (projection.get("materialize_to") == "none")):
                    errors.append(f"{cid}:{pid}: expected sink misaligned")
                if (projection.get("sink_mode") == "explicit_persistence"
                        and not projection.get("sink_anchor_any")):
                    errors.append(f"{cid}:{pid}: persistent sink lacks frozen anchor")
            elif projection.get("executable") is not False:
                errors.append(f"{cid}:{pid}: non-request projection must be non-executable")
        signature = expected.get("requested_signature")
        if not isinstance(signature, list) or not signature:
            errors.append(f"{cid}: missing requested signature")

    per_lang = collections.Counter(case["language"] for case in CASES)
    per_track_lang = collections.Counter(
        (case["track"], case["language"]) for case in CASES)
    for lang in LANGUAGES:
        if per_track_lang[("independent", lang)] != 10:
            errors.append(f"coverage: independent/{lang} != 10")
        if per_track_lang[("legacy_contradiction", lang)] != 4:
            errors.append(f"coverage: legacy_contradiction/{lang} != 4")
        if per_lang[lang] != 14:
            errors.append(f"coverage: total/{lang} != 14")
    digest = fixture_digest()
    if FROZEN_SHA256 != "TO_BE_FROZEN" and digest != FROZEN_SHA256:
        errors.append(f"freeze mismatch: expected={FROZEN_SHA256} actual={digest}")
    return errors


def validate_frame_intrinsic(frame: Any, tokens: list[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(frame, dict) or set(frame) != {"semantic_heads", "predicates"}:
        return ["top_shape"]
    heads = frame.get("semantic_heads")
    predicates = frame.get("predicates")
    if not isinstance(heads, list) or not isinstance(predicates, list):
        return ["array_shape"]
    if len(heads) != len(predicates):
        errors.append("head_predicate_count")
    sink_profiles = {
        "sink_anchor_token_id" in predicate
        for predicate in predicates if isinstance(predicate, dict)
    }
    if len(sink_profiles) > 1:
        errors.append("mixed_sink_schema_profiles")
    last_anchor = 0
    earlier: set[int] = set()
    for index, (head, predicate) in enumerate(zip(heads, predicates), 1):
        if not isinstance(head, dict) or set(head) != HEAD_KEYS:
            errors.append(f"head_{index}_shape")
            continue
        if (not isinstance(predicate, dict)
                or set(predicate) not in (
                    PREDICATE_KEYS_BASE, PREDICATE_KEYS_GROUNDED_SINK)):
            errors.append(f"predicate_{index}_shape")
            continue
        anchor = head.get("predicate_anchor_token_id")
        if (not isinstance(anchor, int) or anchor < 1 or anchor > len(tokens)
                or anchor <= last_anchor):
            errors.append(f"head_{index}_anchor")
            continue
        if predicate.get("predicate_anchor_token_id") != anchor:
            errors.append(f"predicate_{index}_anchor_alignment")
        selected = tokens[anchor - 1]
        if re.search(r"https?://|@|\d|^(?:[.~]?/|[A-Za-z]:\\|\\\\)", selected):
            errors.append(f"predicate_{index}_literal_anchor")
        role = head.get("role")
        if role not in ROLES or predicate.get("role") != role:
            errors.append(f"predicate_{index}_role_alignment")
        lemma = head.get("lemma")
        if (not isinstance(lemma, str) or not lemma.strip()
                or len(lemma) > 80 or len(lemma.split()) > 3
                or re.search(r"https?://|@|\d|[/\\]", lemma)):
            errors.append(f"head_{index}_lemma")
        gloss = head.get("semantic_gloss_en")
        if (not isinstance(gloss, str) or not gloss.strip()
                or len(gloss) > 80 or len(gloss.split()) > 3
                or not re.fullmatch(r"[A-Za-z][A-Za-z -]*", gloss.strip())):
            errors.append(f"head_{index}_gloss")
        if head.get("semantic_action") not in ("none", *ACTIONS):
            errors.append(f"head_{index}_semantic_action")
        if head.get("effect") not in EFFECTS:
            errors.append(f"head_{index}_effect")
        if head.get("resource_scope") not in SCOPES:
            errors.append(f"head_{index}_scope")
        if predicate.get("verb") not in ("none", *ACTIONS):
            errors.append(f"predicate_{index}_verb")
        if predicate.get("object") not in ("none", *OBJECTS):
            errors.append(f"predicate_{index}_object")
        if predicate.get("verb_resolution") not in RESOLUTIONS:
            errors.append(f"predicate_{index}_resolution")
        if predicate.get("sink_mode") not in SINK_MODES:
            errors.append(f"predicate_{index}_sink_mode")
        if ((predicate.get("sink_mode") == "none")
                != (predicate.get("materialize_to") == "none")):
            errors.append(f"predicate_{index}_sink_alignment")
        if "sink_anchor_token_id" in predicate:
            sink_anchor = predicate.get("sink_anchor_token_id")
            if not isinstance(sink_anchor, int) or not 0 <= sink_anchor <= len(tokens):
                errors.append(f"predicate_{index}_sink_anchor")
            elif predicate.get("sink_mode") == "none" and sink_anchor != 0:
                errors.append(f"predicate_{index}_spurious_sink_anchor")
            elif predicate.get("sink_mode") == "explicit_persistence" and sink_anchor == 0:
                errors.append(f"predicate_{index}_missing_sink_anchor")
        if predicate.get("materialize_to") not in ("none", *OBJECTS):
            errors.append(f"predicate_{index}_materialize_to")
        dependency = predicate.get("input_from_predicate_anchor_id")
        if (not isinstance(dependency, int) or dependency < 0
                or (dependency and dependency not in earlier)):
            errors.append(f"predicate_{index}_dependency")
        if (role == "request"
                and (predicate.get("verb") == "none"
                     or predicate.get("object") == "none")):
            errors.append(f"predicate_{index}_incomplete_request")
        earlier.add(anchor)
        last_anchor = anchor
    return errors


def frame_signature(frame: dict[str, Any]) -> list[str]:
    requested = [
        item for item in frame.get("predicates", [])
        if item.get("role") == "request"
    ]
    result: list[str] = []
    for index, item in enumerate(requested):
        obj = item.get("object")
        qualifier = item.get("object_qualifier")
        rendered_obj = obj if qualifier == "none" else f"{obj}_{qualifier}"
        result.append(f"{item.get('verb')}/{rendered_obj}")
        sink = (item.get("materialize_to")
                if item.get("sink_mode") == "explicit_persistence"
                else "none")
        explicit_later = any(
            later.get("verb") == "write"
            and later.get("object") == sink
            and later.get("input_from_predicate_anchor_id")
                == item.get("predicate_anchor_token_id")
            for later in requested[index + 1:]
        )
        if sink != "none" and not explicit_later:
            result.append(f"write/{sink}")
    return result


def score_case(
    case: dict[str, Any],
    frame: Any,
    *,
    compiler_signature: str | list[str] | None = None,
) -> dict[str, Any]:
    tokens = tokenize(case["query"])
    gate_errors: dict[str, list[str]] = {name: [] for name in ("g0", "g1", "g2", "g3")}
    gate_errors["g0"] = validate_frame_intrinsic(frame, tokens)
    if gate_errors["g0"]:
        return {
            "pass": {"g0": False, "g1": False, "g2": False, "g3": False},
            "errors": gate_errors,
            "frame_signature": None,
            "compiler_signature": compiler_signature,
        }

    expected = case["expected"]["predicates"]
    heads = frame["semantic_heads"]
    predicates = frame["predicates"]
    if len(heads) != len(expected):
        gate_errors["g1"].append(f"predicate_count:{len(heads)}!={len(expected)}")
    matched_anchor_by_pid: dict[str, int] = {}
    for index, exp in enumerate(expected):
        if index >= len(heads) or index >= len(predicates):
            break
        head = heads[index]
        predicate = predicates[index]
        anchor = head["predicate_anchor_token_id"]
        selected = normalize_token(tokens[anchor - 1])
        allowed_anchor = {normalize_token(value) for value in exp["anchor_any"]}
        if selected not in allowed_anchor:
            gate_errors["g1"].append(
                f"{exp['id']}.anchor:{tokens[anchor - 1]!r} not in {exp['anchor_any']!r}")
        if head["role"] != exp["role"]:
            gate_errors["g1"].append(
                f"{exp['id']}.role:{head['role']}!={exp['role']}")
        expected_dependency = 0
        if exp["input_from"] is not None:
            expected_dependency = matched_anchor_by_pid.get(exp["input_from"], -1)
        if predicate["input_from_predicate_anchor_id"] != expected_dependency:
            gate_errors["g1"].append(
                f"{exp['id']}.dependency:{predicate['input_from_predicate_anchor_id']}!={expected_dependency}")
        matched_anchor_by_pid[exp["id"]] = anchor

        lemma = normalize_word(head["lemma"])
        allowed_lemmas = {normalize_word(value) for value in exp["lemma_any"]}
        if lemma not in allowed_lemmas:
            gate_errors["g2"].append(
                f"{exp['id']}.lemma:{head['lemma']!r} not in {exp['lemma_any']!r}")
        gloss = normalize_word(head["semantic_gloss_en"])
        allowed_gloss = {normalize_word(value) for value in exp["gloss_any"]}
        if gloss not in allowed_gloss:
            gate_errors["g2"].append(
                f"{exp['id']}.gloss:{head['semantic_gloss_en']!r} not in {exp['gloss_any']!r}")
        if head["semantic_action"] not in exp["semantic_action_any"]:
            gate_errors["g2"].append(
                f"{exp['id']}.semantic_action:{head['semantic_action']} not in {exp['semantic_action_any']}")
        if head["effect"] != exp["effect"]:
            gate_errors["g2"].append(
                f"{exp['id']}.effect:{head['effect']}!={exp['effect']}")
        if head["resource_scope"] != exp["resource_scope"]:
            gate_errors["g2"].append(
                f"{exp['id']}.scope:{head['resource_scope']}!={exp['resource_scope']}")

        projection = exp["projection"]
        if exp["role"] == "request":
            for field in (
                "verb", "object", "object_qualifier", "materialize_to",
                "sink_mode",
            ):
                if predicate[field] != projection[field]:
                    gate_errors["g3"].append(
                        f"{exp['id']}.{field}:{predicate[field]}!={projection[field]}")
            if predicate["verb_resolution"] not in projection["verb_resolution_any"]:
                gate_errors["g3"].append(
                    f"{exp['id']}.verb_resolution:{predicate['verb_resolution']} not in {projection['verb_resolution_any']}")
            if "sink_anchor_token_id" in predicate:
                actual_sink_anchor = predicate["sink_anchor_token_id"]
                if projection["sink_mode"] == "none":
                    if actual_sink_anchor != 0:
                        gate_errors["g3"].append(
                            f"{exp['id']}.sink_anchor:{actual_sink_anchor}!=0")
                else:
                    allowed_sink_tokens = {
                        normalize_token(value)
                        for value in projection["sink_anchor_any"]
                    }
                    actual_sink_token = (
                        normalize_token(tokens[actual_sink_anchor - 1])
                        if 1 <= actual_sink_anchor <= len(tokens) else ""
                    )
                    if actual_sink_token not in allowed_sink_tokens:
                        gate_errors["g3"].append(
                            f"{exp['id']}.sink_anchor_token:{actual_sink_token!r} not in {projection['sink_anchor_any']!r}")

    raw_signature = frame_signature(frame)
    expected_signature = case["expected"]["requested_signature"]
    if raw_signature != expected_signature:
        gate_errors["g3"].append(
            f"frame_signature:{raw_signature!r}!={expected_signature!r}")
    normalized_compiler = compiler_signature
    if isinstance(normalized_compiler, str):
        normalized_compiler = [normalized_compiler]
    if normalized_compiler is not None and normalized_compiler != expected_signature:
        gate_errors["g3"].append(
            f"compiler_signature:{normalized_compiler!r}!={expected_signature!r}")

    passed: dict[str, bool] = {}
    prior_ok = True
    for gate in ("g0", "g1", "g2", "g3"):
        prior_ok = prior_ok and not gate_errors[gate]
        passed[gate] = prior_ok
    return {
        "pass": passed,
        "raw_gate_ok": {gate: not errors for gate, errors in gate_errors.items()},
        "errors": gate_errors,
        "frame_signature": raw_signature,
        "compiler_signature": normalized_compiler,
    }


def load_experiment_module() -> Any:
    path = Path("/tmp/metnos_unified_query_bench.py")
    if not path.exists():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location("metnos_unified_query_bench_frozen_run", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def selected_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = CASES
    if args.track != "all":
        cases = [case for case in cases if case["track"] == args.track]
    if args.languages:
        wanted = {value.strip() for value in args.languages.split(",") if value.strip()}
        cases = [case for case in cases if case["language"] in wanted]
    if args.ids:
        wanted = {value.strip() for value in args.ids.split(",") if value.strip()}
        cases = [case for case in cases if case["id"] in wanted]
    if args.limit:
        cases = cases[:args.limit]
    return cases


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [record for record in records if record.get("evaluable", True)]
    summary: dict[str, Any] = {
        "runs": len(records),
        "cases": len({record["id"] for record in records}),
        "evaluable_runs": len(evaluable),
        "transport_failures": len(records) - len(evaluable),
        "by_gate": {},
        "by_track": {},
        "by_language": {},
        "by_family": {},
    }
    for gate in ("g0", "g1", "g2", "g3"):
        ok = sum(bool(record["score"]["pass"][gate]) for record in evaluable)
        summary["by_gate"][gate] = {"ok": ok, "total": len(evaluable)}
    for dimension in ("track", "language", "family"):
        grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for record in records:
            grouped[record[dimension]].append(record)
        target = summary[f"by_{dimension}"]
        for key, rows in sorted(grouped.items()):
            eligible = [row for row in rows if row.get("evaluable", True)]
            target[key] = {
                gate: {
                    "ok": sum(bool(row["score"]["pass"][gate]) for row in eligible),
                    "total": len(eligible),
                }
                for gate in ("g0", "g1", "g2", "g3")
            }
            target[key]["transport_failures"] = len(rows) - len(eligible)
    return summary


def is_transport_failure(meta: dict[str, Any]) -> bool:
    raw = str(meta.get("raw") or "")
    return raw.startswith((
        "URLError:", "TimeoutError:", "ConnectionError:",
        "ConnectionRefusedError:", "PermissionError:",
    ))


def run(args: argparse.Namespace) -> int:
    fixture_errors = validate_fixture()
    if fixture_errors:
        print("FIXTURE INVALID", file=sys.stderr)
        for error in fixture_errors:
            print(error, file=sys.stderr)
        return 2
    experiment = load_experiment_module()
    cases = selected_cases(args)
    records: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases, 1):
        for repetition in range(1, args.k + 1):
            print(f"[{case_index:02d}/{len(cases):02d} k={repetition}] {case['id']}", flush=True)
            frame, meta = experiment.folded_call(
                case["query"], prompt_variant=args.variant,
            )
            compiler_signature = (
                experiment.folded_signature(frame) if meta.get("valid") else None
            )
            evaluable = not is_transport_failure(meta)
            if evaluable:
                score = score_case(
                    case, frame,
                    compiler_signature=compiler_signature,
                )
            else:
                score = {
                    "pass": {gate: None for gate in ("g0", "g1", "g2", "g3")},
                    "raw_gate_ok": {gate: None for gate in ("g0", "g1", "g2", "g3")},
                    "errors": {"transport": [meta.get("raw")]},
                    "frame_signature": None,
                    "compiler_signature": None,
                }
            record = {
                "id": case["id"],
                "track": case["track"],
                "family": case["family"],
                "language": case["language"],
                "query": case["query"],
                "repetition": repetition,
                "evaluable": evaluable,
                "expected": case["expected"],
                "frame": frame,
                "transport_meta": meta,
                "score": score,
            }
            records.append(record)
            status = ("NOT_EVALUATED transport"
                      if not evaluable else " ".join(
                          f"{gate.upper()}={'OK' if score['pass'][gate] else 'XX'}"
                          for gate in ("g0", "g1", "g2", "g3")))
            print(f"    {status}", flush=True)
            if args.delay_ms and not (case_index == len(cases) and repetition == args.k):
                time.sleep(args.delay_ms / 1000)
    payload = {
        "suite_version": VERSION,
        "suite_sha256": fixture_digest(),
        "variant": args.variant,
        "k": args.k,
        "summary": summarize(records),
        "records": records,
    }
    output = Path(args.output)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"records={output}")
    if any(not record.get("evaluable", True) for record in records):
        return 3
    primary = [record for record in records if record["track"] == "independent"]
    return 0 if all(record["score"]["pass"]["g3"] for record in primary) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="call the structured candidate sequentially")
    parser.add_argument("--variant", default="v21")
    parser.add_argument("--track", choices=("all", "independent", "legacy_contradiction"), default="all")
    parser.add_argument("--languages", default="")
    parser.add_argument("--ids", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--delay-ms", type=int, default=150)
    parser.add_argument("--output", default="/tmp/metnos_request_analysis_adversarial_v1_results.json")
    parser.add_argument("--export", default="", help="write the frozen fixture as JSON")
    args = parser.parse_args()

    errors = validate_fixture()
    counts = collections.Counter((case["track"], case["language"]) for case in CASES)
    print(f"version={VERSION} sha256={fixture_digest()} cases={len(CASES)}")
    print("coverage=" + json.dumps({f"{a}/{b}": n for (a, b), n in sorted(counts.items())}))
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 2
    print("fixture=VALID")
    if args.export:
        Path(args.export).write_text(json.dumps(
            {**fixture_payload(), "sha256": fixture_digest()},
            ensure_ascii=False, indent=2,
        ))
        print(f"fixture_json={args.export}")
    if args.run:
        return run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

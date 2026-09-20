#!/usr/bin/env python3
"""Natural-language resources used by deterministic routing heuristics.

The consumers retain the closed protocol identifiers (canonical actions,
objects, qualifiers and tool names) and all structural grammar.  This module
owns only user-language surface forms.  Italian and English are additive
compatibility baselines; ready third-language rows join that union through the
normal detection-lexicon resolver, while pending rows remain invisible.
"""
from __future__ import annotations

import re

import detection_lexicon as _dl


_KINDS = {
    "routing.object_synonym": "mapping",
    "routing.trie.qualifier": "mapping",
    "routing.adaptive.stopword": "phrases",
    "routing.rule.key_class": "mapping",
    "routing.rule.time_window": "regex",
    "routing.rule.person_prefix": "phrases",
    "routing.rule.image_folder": "regex",
    "routing.rule.boost.recent_count": "regex",
    "routing.rule.boost.recent_messages": "regex",
    "routing.rule.boost.read_messages": "regex",
    "routing.rule.boost.send_messages": "regex",
    "routing.rule.boost.move_spam": "regex",
    "routing.rule.boost.find_files": "regex",
    "routing.rule.boost.delete_files": "regex",
    "routing.rule.boost.write_files": "regex",
    "routing.rule.boost.tasks": "regex",
    "routing.rule.boost.count": "regex",
    "routing.rule.boost.places": "regex",
    "routing.rule.boost.github_metrics": "regex",
    "routing.rule.boost.location": "regex",
    "routing.rule.boost.now": "regex",
    "routing.rule.boost.processes": "regex",
    "routing.rule.boost.appointments": "regex",
    "routing.rule.boost.events": "regex",
    "routing.rule.boost.paired": "regex",
    "routing.rule.boost.enrolled": "regex",
    "routing.rule.boost.sort": "regex",
    "routing.quantity.frame": "regex",
    "routing.file.plural_marker": "phrases",
    "routing.drive.phantom": "phrases",
    "routing.drive.provider_clause": "regex",
    "routing.drive.action": "regex",
    "routing.drive.article": "regex",
    "routing.drive.document_noun": "regex",
    "routing.message_event.focus": "regex",
    "routing.message_event.separator": "regex",
    "routing.message_event.article": "regex",
    "routing.message_event.generic": "phrases",
    "routing.filter.operation": "mapping",
    "routing.create_only.request": "regex",
    "routing.store_target": "regex",
    "routing.result_folder_exclusion": "regex",
}

_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Register the complete historical IT/EN routing corpus idempotently."""

    R = _dl.register
    # Canonical object identities are protocol.  Their user-language aliases
    # affect routing and implicit actions, so a target locale is admitted only
    # after a complete manual review of this mapping.
    R("routing.object_synonym", "mapping", match_mode="word",
      review_policy="manual",
      it={
          "events": [
              "appuntamento", "appuntamenti", "agenda", "calendario",
              "evento", "eventi", "riunione", "riunioni", "incontro",
              "incontri", "scadenza", "scadenze",
          ],
          "messages": ["messaggio", "messaggi", "mail", "email", "posta"],
          "contacts": ["contatto", "contatti", "rubrica"],
          "files": ["file", "documento", "documenti"],
          "dirs": ["cartella", "cartelle", "directory"],
          "packages": ["pacchetto", "pacchetti"],
          "processes": ["processo", "processi"],
          "places": ["luogo", "luoghi", "posto"],
          "tasks": [
              "task", "promemoria", "timer", "ricorrente", "ricorrenti",
              "schedulato", "schedulati",
          ],
          "persons": [
              "persona", "persone", "enrollato", "enrollati", "enrollata",
              "enrollate", "enrolled", "registrata", "registrate",
              "registrato", "registrati", "volto", "volti", "viso", "visi",
          ],
          "issues": ["issue", "issues", "segnalazione", "segnalazioni", "ticket"],
          "pulls": ["pr", "pull request", "pull", "merge request"],
          "approval": [
              "approvazione", "approva", "approvare", "consenso",
              "autorizzazione", "autorizza",
          ],
          "preferences": [
              "preferenza", "preferenze", "impostazione", "impostazioni",
          ],
      },
      en={
          "events": [
              "appointment", "appointments", "calendar", "schedule", "event",
              "events", "meeting", "meetings", "deadline", "deadlines",
          ],
          "messages": ["message", "messages", "mail", "email"],
          "contacts": ["contact", "contacts"],
          "files": ["file", "document", "documents"],
          "dirs": ["folder", "directory"],
          "packages": ["package", "packages"],
          "processes": ["process", "processes"],
          "places": ["place", "places"],
          "tasks": ["task", "tasks", "reminder", "timer", "scheduled", "recurring"],
          "persons": [
              "person", "persons", "people", "enrolled", "registered", "face",
              "faces",
          ],
          "issues": ["issue", "issues", "ticket"],
          "pulls": ["pull", "pulls", "pull request", "pr", "merge request"],
          "approval": [
              "approval", "approve", "consent", "authorization", "authorize",
          ],
          "preferences": ["preference", "preferences", "setting", "settings"],
      })
    R("routing.trie.qualifier", "mapping", match_mode="word",
      it={
          "google_workspace": ["google", "gmail", "drive", "gdrive",
                               "workspace", "gcal"],
          "xlsx": ["xlsx", "spreadsheet", "foglio", "sheet"],
          "csv": ["csv", "comma"],
          "text": ["text", "testo", "txt", "md", "markdown"],
          "html": ["html", "htm", "pagina"],
          "ocr": ["ocr", "scan", "scansione"],
          "pdf": ["pdf"],
          "zip": ["zip", "archive", "archivio"],
          "image": ["foto", "immagine", "immagini", "photo", "image"],
      },
      # The old table was one additive IT/EN inventory.  Repeating borrowed
      # forms in a baseline preserves its exact union and ordering semantics.
      en={
          "google_workspace": ["google", "gmail", "drive", "gdrive",
                               "workspace", "gcal"],
          "xlsx": ["xlsx", "spreadsheet", "foglio", "sheet"],
          "csv": ["csv", "comma"],
          "text": ["text", "testo", "txt", "md", "markdown"],
          "html": ["html", "htm", "pagina"],
          "ocr": ["ocr", "scan", "scansione"],
          "pdf": ["pdf"],
          "zip": ["zip", "archive", "archivio"],
          "image": ["foto", "immagine", "immagini", "photo", "image"],
      })
    R("routing.adaptive.stopword", "phrases", match_mode="word",
      it=[
          "che", "del", "della", "dei", "delle", "con", "per", "non",
          "una", "uno", "gli", "questo", "questa", "quello", "quella",
          "essere", "avere", "stato", "stata", "molto", "poco", "tanto",
          # Technical serialization literals are language-neutral noise.
          "ok", "true", "false", "null", "none", "json", "dict", "list",
          "str", "int", "float", "bool",
      ],
      en=[
          "the", "and", "for", "from", "with", "this", "that", "these",
          "those", "have", "has", "had", "will", "would", "could",
          "should", "are", "was", "were", "been", "being",
          "ok", "true", "false", "null", "none", "json", "dict", "list",
          "str", "int", "float", "bool",
      ])
    R("routing.rule.key_class", "mapping", match_mode="word",
      it={
          "hash": ["sha256", "md5", "hash", "digest", "fingerprint",
                   "checksum", "signature"],
          "date": ["date", "datetime", "time", "when", "today", "tomorrow",
                   "yesterday", "data", "ora", "mtime", "ctime", "modified",
                   "modified_at", "iso_timestamp"],
          "size": ["size", "size_bytes", "bytes", "filesize", "len",
                   "length", "dimensione"],
          "name": ["name", "title", "label", "subject", "filename", "stem",
                   "nome", "titolo", "soggetto", "oggetto"],
          "pattern": ["pattern", "glob_pattern", "glob", "ext", "extension",
                      "suffix", "estensione"],
          "channel": ["channel", "platform", "service", "canale",
                      "piattaforma", "servizio"],
          "limit": ["limit", "top", "max", "count", "n", "numero",
                    "quanti", "quante"],
          "author": ["author", "creator", "owner", "by", "autore",
                     "proprietario"],
      },
      en={
          "hash": ["sha256", "md5", "hash", "digest", "fingerprint",
                   "checksum", "signature"],
          "date": ["date", "datetime", "time", "when", "today", "tomorrow",
                   "yesterday", "data", "ora", "mtime", "ctime", "modified",
                   "modified_at", "iso_timestamp"],
          "size": ["size", "size_bytes", "bytes", "filesize", "len",
                   "length", "dimensione"],
          "name": ["name", "title", "label", "subject", "filename", "stem",
                   "nome", "titolo", "soggetto", "oggetto"],
          "pattern": ["pattern", "glob_pattern", "glob", "ext", "extension",
                      "suffix", "estensione"],
          "channel": ["channel", "platform", "service", "canale",
                      "piattaforma", "servizio"],
          "limit": ["limit", "top", "max", "count", "n", "numero",
                    "quanti", "quante"],
          "author": ["author", "creator", "owner", "by", "autore",
                     "proprietario"],
      })

    def regex(concept: str, pattern: str) -> None:
        # Exact historic combined patterns are duplicated across the additive
        # baselines so migration cannot narrow either installed locale.
        R(concept, "regex", it=[pattern], en=[pattern])

    regex("routing.rule.time_window",
          r"\b(?:oggi|ieri|domani|today|yesterday|tomorrow|ora|now|"
          r"ultim[ae]|ultimi|ultime|last|prossim[ae]|prossimi|next|"
          r"settimana|week|mese|month|anno|year)\b")
    R("routing.rule.person_prefix", "phrases", match_mode="word",
      it=["di", "da"], en=["of", "by"])
    regex("routing.rule.image_folder", r"/Immagini/|/Photos/|/photos/|/Immagini\b")

    boosts = {
        "recent_count": r"\b(ultim[io]|recenti|primi)\s+\d+",
        "recent_messages": r"\b(ultim[io]|recenti)\s+\d+\s+(mail|messag)",
        "read_messages": r"\b(leggi|read).*(mail|email|messag)",
        "send_messages": r"\b(invia|manda|send).*(mail|email|messag)",
        "move_spam": (r"(?=.*\b(?:spam|posta indesiderata|junk)\b)"
                      r"(?=.*\b(?:sposta|move|filtra)\b)"),
        "find_files": r"\b(trova|find|cerca).*(file|cartella|directory)",
        "delete_files": r"\b(elimin|cancell|delete|rimuov).*(file|messag|mail|email)",
        "write_files": r"\b(crea|create|scriv|write).*(file|nota|note)",
        "tasks": r"\b(timer|task|promemoria|ricordami|schedule|scaden)",
        "count": r"\b(quant[io]|conta|numero di|how many|count)\b",
        "places": r"\b(bar|ristorante|pizzeria|hotel|farmacia|stazione|caff[èe])\b",
        "github_metrics": r"\b(stars|issues|contributors|forks|releases|builds?|status)\b.*(repo|github)",
        "location": r"\b(dove sono|where am i|posizione|location|gps)\b",
        "now": r"\b(che ora|what time|che giorno|orario)\b",
        "processes": r"\b(processi|process|memoria|memory|cpu|stato del sistema)\b",
        "appointments": r"\b(appuntamenti|appuntamento|meeting|incontri|riunion)\b",
        "events": r"\b(eventi.*\b(domani|oggi|settimana|mese)|calendar|agenda)\b",
        "paired": r"\b(paired)\b",
        "enrolled": r"\b(enroll|enrolled|registrat[ie])\b",
        "sort": r"\b(ordina|sort).*\b(data|date|dimens|size)",
    }
    for name, pattern in boosts.items():
        regex("routing.rule.boost." + name, pattern)

    regex("routing.quantity.frame",
          r"(?:\b(?:prim[ie]|ultim[ie]|sol[ie]|soltanto|appena|massimo|almeno|top|"
          r"first|last|only|just|at\s+most|up\s+to)\s+(?:\d+|uno|una|due|tre|"
          r"quattro|cinque|sei|sette|otto|nove|dieci|undici|dodici|venti|trenta|"
          r"quaranta|cinquanta|cento|mille|one|two|three|four|five|six|seven|"
          r"eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|hundred|"
          r"thousand)\b(?!\s*(?:giorni?|or[ae]|settiman[ae]|mes[ei]|ann[oi]|"
          r"minut[oi]|second[oi]|days?|hours?|weeks?|months?|years?|minutes?|"
          r"seconds?)\b))|(?:\b(?:\d+|uno|una|due|tre|quattro|cinque|sei|sette|"
          r"otto|nove|dieci|undici|dodici|venti|trenta|quaranta|cinquanta|cento|"
          r"mille|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
          r"twenty|thirty|forty|fifty|hundred|thousand)\s+(?:file|files|mail|"
          r"email|messaggi?|foto|immagini?|righe|line[ae]|lines?|risultati?|"
          r"results?|elementi?|element|items?|entr(?:y|ies)|record|records?|"
          r"documenti?|docs?|pdf|url|urls|link|links|pagin[ae]|pages?|foglio|"
          r"fogli|sheet|sheets)\b)")
    R("routing.file.plural_marker", "phrases", match_mode="substring",
      it=["tutti i", "tutti gli", "i file", "gli file", "ogni file",
          "i files"],
      en=["all the", "all ", "every ", "the files", "each "])
    R("routing.drive.phantom", "phrases", match_mode="substring",
      it=["gdrive", "google drive", "googledrive", "google_drive",
          "google-drive", "drive"],
      en=["gdrive", "google drive", "googledrive", "google_drive",
          "google-drive", "drive"])
    regex("routing.drive.provider_clause",
          r"\b(su|sul|sullo|sulla|in|nel|nello|dentro|da|dal|dallo|from|on)\s+"
          r"(google\s*drive|g\s*drive|gdrive|google\s*docs?|google\s*sheets?|"
          r"google\s*fogli|drive|google)\b")
    regex("routing.drive.action",
          r"\b(cerca(mi)?|trova(mi)?|search|find|apri|open|leggi|read|"
          r"scarica|download|mostra(mi)?|show)\b")
    regex("routing.drive.article",
          r"\b(il|lo|la|i|gli|le|un|uno|una|the|a|an|di|del|della|dei|degli)\b")
    regex("routing.drive.document_noun",
          r"\b(documento|documenti|document|file|foglio|fogli|spreadsheet|sheet|"
          r"doc|cartella|folder)\b")

    regex("routing.message_event.focus",
          r"\b(?:relativ[ei]?\s+a|riguardant[ei]?|concernent[ei]?|"
          r"related\s+to|concerning|regarding)\s+(.{1,500}?)(?=[.;]|$)")
    regex("routing.message_event.separator",
          r"\s*,\s*|\s+(?:o|od|oppure|e|ed|or|and)\s+")
    regex("routing.message_event.article",
          r"^(?:(?:il|lo|la|i|gli|le|un|uno|una|the|a|an)\s+)+")
    R("routing.message_event.generic", "phrases", match_mode="word",
      it=["email", "mail", "messaggi", "eventi", "appuntamenti", "impegni"],
      en=["email", "mail", "messages", "events", "appointments", "commitments"])
    R("routing.filter.operation", "mapping", match_mode="word",
      it={"deduplicate": ["dedup", "deduplicate", "deduplication",
                          "logical_dedup", "remove_duplicates", "unique",
                          "distinct", "unico", "unica", "unici", "uniche"]},
      en={"deduplicate": ["dedup", "deduplicate", "deduplication",
                          "logical_dedup", "remove_duplicates", "unique",
                          "distinct", "unico", "unica", "unici", "uniche"]})
    regex("routing.create_only.request",
          r"(?:\bnuov[aoe]\s+cartell\w*\b|\bnew\s+folder\b|"
          r"\bnon\s+sovrascriv\w*\b|\bsenza\s+sovrascriv\w*\b|"
          r"\bdo\s+not\s+overwrite\b|\bdon'?t\s+overwrite\b|"
          r"\bwithout\s+overwrit\w*\b)")
    R("routing.store_target", "regex", match_mode="word",
      review_policy="manual",
      it=[
          r"\b(?:store|archivio)\s+"
          r"(?P<target>[A-Za-z0-9_]+)\b",
      ],
      en=[
          r"\b(?:store|archive)\s+"
          r"(?P<target>[A-Za-z0-9_]+)\b",
      ])
    R("routing.result_folder_exclusion", "regex", match_mode="word",
      review_policy="manual",
      it=[r"\b(?:esclud\w*|senza\s+includ\w*|non\s+includ\w*)\b"],
      en=[r"\b(?:exclude\w*|without\s+includ\w*)\b"])


def _all_seed_rows_ready() -> bool:
    for concept, kind in _KINDS.items():
        for lang in ("it", "en"):
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


def forms(concept: str) -> list[str]:
    if _KINDS.get(concept) != "phrases":
        raise KeyError(f"unknown routing phrase concept: {concept}")
    _ensure_registered()
    return [str(form) for form in _dl.forms(concept) if str(form)]


def mapping(concept: str) -> dict[str, list[str]]:
    if _KINDS.get(concept) != "mapping":
        raise KeyError(f"unknown routing mapping concept: {concept}")
    _ensure_registered()
    return {
        str(canonical): [str(form) for form in values if str(form)]
        for canonical, values in _dl.mapping(concept).items()
        if isinstance(values, list)
    }


def native_manual_mapping(concept: str) -> dict[str, list[str]]:
    """Complete active mapping for a routing decision, or empty fail-closed."""
    if _KINDS.get(concept) != "mapping":
        raise KeyError(f"unknown routing mapping concept: {concept}")
    _ensure_registered()
    return _dl.native_ready_mapping(
        concept,
        require_manual=True,
        include_reviewed_baselines=True,
    )


def regexes(concept: str):
    if _KINDS.get(concept) != "regex":
        raise KeyError(f"unknown routing regex concept: {concept}")
    _ensure_registered()
    return _dl.regexes(concept)


def matches(concept: str, text: str) -> bool:
    if concept not in _KINDS:
        raise KeyError(f"unknown routing concept: {concept}")
    _ensure_registered()
    return _dl.match(concept, text or "")


def capture_store_target(text: str) -> str | None:
    """Return one reviewed store identifier, never an inferred fallback."""

    _ensure_registered()
    patterns = tuple(_dl.native_ready_patterns(
        "routing.store_target",
        require_manual=True,
        include_reviewed_baselines=True,
    ))
    if not patterns or any("target" not in pattern.groupindex
                           for pattern in patterns):
        return None
    candidates: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            candidate = str(match.group("target") or "")
            if not re.fullmatch(r"[A-Za-z0-9_]+", candidate):
                return None
            candidates.add(candidate)
    return next(iter(candidates)) if len(candidates) == 1 else None


def native_manual_matches(concept: str, text: str) -> bool:
    """Match one safety-relevant routing grammar, or false when unavailable."""

    if _KINDS.get(concept) != "regex":
        raise KeyError(f"unknown routing regex concept: {concept}")
    _ensure_registered()
    patterns = tuple(_dl.native_ready_patterns(
        concept,
        require_manual=True,
        include_reviewed_baselines=True,
    ))
    return bool(patterns) and any(pattern.search(text or "") for pattern in patterns)


__all__ = [
    "capture_store_target", "forms", "mapping", "matches",
    "native_manual_mapping", "native_manual_matches", "regexes",
    "register_all",
]

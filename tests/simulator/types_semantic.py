"""types_semantic.py — vocabolario chiuso tipi semantici per graph search.

Tipi atomici e compositi con relazione is_a per downcasting.

Esteso quando necessario. Aggiornare ONLY qui — single source of truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Tipi atomici (foglie del type lattice)
ATOMIC_TYPES = {
    # File system
    "file_path": {"validator": None, "parents": []},
    "dir_path": {"validator": None, "parents": []},
    "image_path": {"validator": lambda x: any(x.lower().endswith(e)
                    for e in (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif")),
                    "parents": ["file_path"]},
    "pdf_path": {"validator": lambda x: x.lower().endswith(".pdf"),
                  "parents": ["file_path"]},
    "audio_path": {"validator": lambda x: any(x.lower().endswith(e)
                    for e in (".mp3", ".wav", ".ogg", ".m4a", ".flac")),
                    "parents": ["file_path"]},
    "video_path": {"validator": lambda x: any(x.lower().endswith(e)
                    for e in (".mp4", ".mov", ".avi", ".mkv", ".webm")),
                    "parents": ["file_path"]},
    "text_path": {"validator": lambda x: any(x.lower().endswith(e)
                    for e in (".txt", ".md", ".csv", ".json", ".xml", ".html")),
                    "parents": ["file_path"]},

    # Network / URL
    "url": {"validator": lambda x: x.startswith(("http://", "https://")),
            "parents": []},
    "domain": {"validator": lambda x: "." in x and "/" not in x, "parents": []},

    # Identity / People
    "email_address": {"validator": lambda x: "@" in x and "." in x,
                       "parents": []},
    "phone": {"validator": lambda x: re.match(r"^\+?[\d\s\-()]+$", x),
              "parents": []},
    "person_name": {"validator": None, "parents": []},
    "slug": {"validator": lambda x: re.match(r"^[a-z][a-z0-9_]*$", x),
             "parents": []},
    "account_name": {"validator": None, "parents": []},

    # Time
    "time_window": {"validator": lambda x: re.match(
        r"^(today|yesterday|tomorrow|now|last-\d+[hdwmy]|next-\d+[hdwmy]|"
        r"this-(week|month|year)|next-(week|month|year)|"
        r"\d{4}-\d{2}-\d{2}|[a-z]+(day|lunedi|martedi|...))$", x.lower()),
        "parents": []},
    "iso_timestamp": {"validator": lambda x: re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", x), "parents": []},
    "duration_secs": {"validator": lambda x: str(x).isdigit(), "parents": []},

    # Patterns
    "glob_pattern": {"validator": lambda x: "*" in x or "?" in x or x.startswith("."),
                      "parents": []},
    "regex_pattern": {"validator": None, "parents": []},

    # Scalars
    "count": {"validator": lambda x: isinstance(x, int) or str(x).isdigit(),
              "parents": ["scalar_metric"]},
    "percent": {"validator": None, "parents": []},
    "size_bytes": {"validator": None, "parents": ["scalar_metric"]},
    "scalar_metric": {"validator": None, "parents": []},  # generic numeric

    # Free-form
    "free_text": {"validator": None, "parents": []},
    "json_object": {"validator": None, "parents": []},
    "bool": {"validator": lambda x: isinstance(x, bool), "parents": []},
}

# Tipi compositi (entry strutturate, list di entry)
ENTRY_SHAPES = {
    "file_entry": {
        "path": "file_path", "name": "name", "size": "size_bytes",
        "mtime": "iso_timestamp", "kind": "file_kind",
    },
    "image_entry": {
        "path": "image_path", "name": "name", "size": "size_bytes",
        "score": "scalar_metric",
    },
    "message_entry": {
        "uid": "scalar_metric", "from": "email_address", "subject": "free_text",
        "body": "free_text", "date": "iso_timestamp",
    },
    "event_entry": {
        "start": "iso_timestamp", "end": "iso_timestamp", "title": "free_text",
        "attendees": "email_address[]",
    },
    "person_entry": {
        "slug": "slug", "name": "person_name", "n_examples": "count",
        "examples": "image_entry[]",
    },
    "url_entry": {
        "url": "url", "title": "free_text", "snippet": "free_text",
    },
    "dir_entry": {
        "path": "dir_path", "name": "name", "n_children": "count",
    },
    "process_entry": {
        "pid": "count", "name": "name", "cpu_pct": "percent",
        "mem_mb": "size_bytes",
    },
    "task_entry": {
        "id": "slug", "name": "name", "trigger": "free_text",
        "next_fire": "iso_timestamp",
    },
}


@dataclass
class SemanticType:
    """Type descriptor enriched (atomic or list_of[atomic|composite])."""
    name: str          # "file_path" | "file_entry[]" | "count"
    is_list: bool      # True se array
    element_type: str  # tipo elemento (= name se non lista)

    @classmethod
    def parse(cls, raw: str) -> "SemanticType":
        if raw.endswith("[]"):
            elem = raw[:-2]
            return cls(name=raw, is_list=True, element_type=elem)
        return cls(name=raw, is_list=False, element_type=raw)


def is_compatible(provided: str, required: str) -> bool:
    """Compatibility check con downcasting (is_a).

    Esempi:
      is_compatible("image_path", "file_path") = True   (downcast)
      is_compatible("file_path", "image_path") = False  (upcast NO)
      is_compatible("file_entry[]", "file_entry[]") = True
      is_compatible("image_entry[]", "file_entry[]") = True (lista di sottotipi)
    """
    if provided == required:
        return True
    p = SemanticType.parse(provided)
    r = SemanticType.parse(required)
    if p.is_list != r.is_list:
        # Single→list autowrap (T → T[] OK: runtime envelopes in [value]).
        # Universale §7.3: executor vettoriali accettano sempre lista.
        # list→single NO.
        if not p.is_list and r.is_list:
            return _atomic_compatible(p.element_type, r.element_type)
        return False
    return _atomic_compatible(p.element_type, r.element_type)


def _atomic_compatible(provided: str, required: str) -> bool:
    if provided == required:
        return True
    if provided in ATOMIC_TYPES:
        parents = ATOMIC_TYPES[provided].get("parents", [])
        for parent in parents:
            if _atomic_compatible(parent, required):
                return True
    if provided in ENTRY_SHAPES and required in ENTRY_SHAPES:
        return _entry_compatible(provided, required)
    import os as _os
    if _os.environ.get("SIM_PATH_ENTRY_WRAP", "1") == "1":
        PATH_TO_ENTRY = {
            "file_path": "file_entry",
            "image_path": "image_entry",
            "audio_path": "audio_entry",
            "video_path": "video_entry",
            "text_path": "text_entry",
            "pdf_path": "file_entry",
            "dir_path": "dir_entry",
            "url": "url_entry",  # universal §7.3: url → url_entry singleton
            "email_address": "message_entry",  # email = inbox lookup
            "person_name": "person_entry",  # name → person registry lookup
        }
        wrapped = PATH_TO_ENTRY.get(provided)
        if wrapped:
            if _atomic_compatible(wrapped, required):
                return True
    return False


def _entry_compatible(provided: str, required: str) -> bool:
    """Composite entry compatibility: provided has at least all required fields."""
    p_shape = ENTRY_SHAPES.get(provided, {})
    r_shape = ENTRY_SHAPES.get(required, {})
    for k, v in r_shape.items():
        if k not in p_shape:
            return False
        if not _atomic_compatible(p_shape[k], v):
            return False
    return True


# Pattern arg-name → semantic_type (heuristic, no LLM)
ARG_NAME_PATTERNS = [
    # File system
    (re.compile(r"^base_?path$|^src$|^dst$|^path$|^dir(_path)?$"), "dir_path"),
    (re.compile(r"^paths$|^src_paths$|^dst_paths$"), "file_path[]"),
    (re.compile(r"^dst_template$|^dst_template_path$"), "dir_path"),
    (re.compile(r"^reference_images$|^image_paths$"), "image_path[]"),

    # Network
    (re.compile(r"^urls?$"), "url"),

    # People / identity
    (re.compile(r"^(to|from|recipient|sender|cc|bcc)$|.*email.*"), "email_address"),
    (re.compile(r"^name$"), "person_name"),  # context-dependent, default person
    (re.compile(r"^slug$"), "slug"),
    (re.compile(r"^account$"), "account_name"),
    (re.compile(r"^channel$"), "account_name"),

    # Time
    (re.compile(r"^time_window$"), "time_window"),
    (re.compile(r"^since$|^before$|^after$|^until$"), "iso_timestamp"),
    (re.compile(r"^duration.*"), "duration_secs"),

    # Patterns
    (re.compile(r"^pattern$"), "glob_pattern"),
    (re.compile(r"^patterns$"), "glob_pattern[]"),

    # Counts / limits
    (re.compile(r"^max_results$|^limit$|^top$|^count$|^max_total$"), "count"),
    (re.compile(r"^max_bytes$|^size$"), "size_bytes"),

    # Free text
    (re.compile(r"^query_text$|^query$|^body$|^content$|^text$|^message$"), "free_text"),
    (re.compile(r"^subject$|^title$"), "free_text"),

    # Booleans (handled separately via structural type)
]


def infer_semantic_type(arg_name: str, structural_type: str) -> Optional[str]:
    """Heuristic match nome arg → semantic_type. None se ambiguo (need LLM)."""
    arg_name = arg_name.lower()
    # Structural override: boolean
    if structural_type == "boolean":
        return "bool"
    if structural_type == "integer":
        return "count"  # default per integer (most are limits/counts)
    if structural_type == "object":
        return "json_object"
    # Name match
    for pattern, semantic in ARG_NAME_PATTERNS:
        if pattern.fullmatch(arg_name):
            return semantic
    return None  # ambiguous, fallback LLM

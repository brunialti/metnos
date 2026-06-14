"""skill_translator — naming + args generator (Task B).

Trasforma un `SkillSubCommand` (output di `skill_parser.parse_skill_md`)
in un `ExecutorPlan`: nome canonico Metnos, args schema, output schema,
reverse pattern, capabilities, provenance.

Determinismo §7.9: solo lookup tabellare + regole pure. Niente LLM.
Niente subprocess. Niente I/O di rete.

Riferimenti the design guide:
- §2.1 vettoriale (singolare -> anche plurale).
- §2.2 vocabolario chiuso (22 verbs / 15 objects).
- §2.3 reverse pattern (4 + `delete_<object>_by_id` come 5° pattern).
- §2.6 entries (read/find/get) vs results (set/delete/...).
- §2.8 no silent failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# R3 (24/5/2026): single source of truth = runtime/vocab.py.
# Eliminati i fallback locali `_METNOS_VERBS/_OBJECTS/_QUALIFIERS` (drift
# rispetto a vocab.py canonico, vedi ADR 0156 Naming Authority). Convertiti
# a frozenset per O(1) membership con vocab.py-tuple original.
from vocab import ACTIONS, OBJECTS, QUALIFIERS

_METNOS_VERBS: frozenset = frozenset(ACTIONS)
_METNOS_OBJECTS: frozenset = frozenset(OBJECTS)
_METNOS_QUALIFIERS: frozenset = frozenset(QUALIFIERS)


VOCAB_MAP_PATH = Path(__file__).resolve().parent / "skill_vocab_map.json"

# Cache module-level (perf, 24/5/2026): pre-fix `_load_vocab_map()` faceva
# json.loads + I/O per ogni `resolve_name`/`translate_subcommand`. Una skill
# con 19 sub-command poteva caricare il file 19 volte. Cache invalidata via
# mtime (re-import a deploy time o test che muta il file).
_VOCAB_MAP_CACHE: dict | None = None
_VOCAB_MAP_MTIME: float = 0.0


class SkillTranslateError(ValueError):
    """Sub-command non traducibile: action/domain non mappati o nome
    risultante non in §2.2."""


# ---------------------------------------------------------------------------
# Dataclass — ExecutorPlan
# ---------------------------------------------------------------------------


@dataclass
class ArgSpec:
    """Una proprieta' di `[args.properties.<name>]` nel manifest."""

    name: str
    type: str                      # JSON-schema-like: string|integer|number|array|boolean
    description: str = ""
    default: Any = None
    required: bool = False
    items_type: str | None = None  # se type=="array"
    format: str | None = None      # es. "date-time" per ISO


@dataclass
class CapabilitySpec:
    name: str
    hint: list = field(default_factory=list)


@dataclass
class ExecutorPlan:
    """Piano per UN executor. Output di translate_subcommand()."""

    name: str
    verb: str
    obj: str
    qualifier: str = ""

    # Coordinate skill -> code generator (Task C).
    skill_domain: str = ""
    skill_action: str = ""

    args: list = field(default_factory=list)
    output_kind: str = "entries"   # "entries" | "results"
    output_record_kind: str = ""   # es. "calendar_event" per kind= field

    reversible: bool = False
    reverse_pattern: str = ""      # vuoto = n/a o N/A

    capabilities: list = field(default_factory=list)

    # Provenance (popolato dal caller con info di livello-skill).
    provenance: dict = field(default_factory=dict)

    # Examples che il code generator (Task C) usera' per i test (stage 3
    # equivalente).
    examples: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Vocab map loader
# ---------------------------------------------------------------------------


def _load_vocab_map(path: Path | None = None) -> dict:
    """Carica skill_vocab_map.json con cache mtime-validated (perf).

    Caller esplicito con `path` (test) bypassa la cache. Caller default
    (path=None) usa VOCAB_MAP_PATH + cache: re-load solo se mtime cambia.
    """
    global _VOCAB_MAP_CACHE, _VOCAB_MAP_MTIME
    p = path or VOCAB_MAP_PATH
    if path is not None:
        return json.loads(p.read_text(encoding="utf-8"))
    try:
        cur_mtime = p.stat().st_mtime
    except OSError:
        cur_mtime = 0.0
    if _VOCAB_MAP_CACHE is not None and cur_mtime == _VOCAB_MAP_MTIME:
        return _VOCAB_MAP_CACHE
    _VOCAB_MAP_CACHE = json.loads(p.read_text(encoding="utf-8"))
    _VOCAB_MAP_MTIME = cur_mtime
    return _VOCAB_MAP_CACHE


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def resolve_name(domain: str, action: str, vocab_map: dict | None = None) -> tuple:
    """Ritorna `(name, verb, object, qualifier)` o solleva
    SkillTranslateError se la mapping non copre.

    ADR 0128 (12/5/2026): consulta PRIMA la tabella `contextual` per
    `<domain>:<action>` — se presente, ha priorita' su `actions` flat e
    fornisce verb canonico contestualmente al `(target_kind, side_effect)`.
    Fallback su `actions` flat (legacy ADR 0123) se la cella contestuale
    manca. Cosi' lo stesso provider-verb (es. gmail.modify, drive.share,
    sheets.update) mappa a Metnos-verbi semanticamente coerenti senza
    drift.

    DEVI: passare domain/action lowercase.
    NON DEVI: chiamare per action ambigue: il fallback synth stage 1 va
    fatto a livello superiore (questa funzione e' deterministica).
    OK: resolve_name("calendar", "list") -> ("read_events", "read", "events", "").
    OK: resolve_name("drive", "share") -> ("share_files", "share", "files", "").
    ERRORE: resolve_name("calendar", "ufoize") -> SkillTranslateError.
    """
    vm = vocab_map or _load_vocab_map()
    actions = vm["actions"]
    domains = vm["domains"]
    domain_q = vm.get("domain_qualifier", {})
    contextual = vm.get("contextual", {})

    if domain not in domains:
        raise SkillTranslateError(f"domain skill non mappato: {domain!r}")

    # ADR 0128: consulta PRIMA contextual[`<domain>:<action>`]. Se presente,
    # ha priorita' su `actions` flat. Cosi' `gmail:modify -> set` (state
    # labels) e' inconfondibile con `docs:update -> write` (body modify).
    ctx_key = f"{domain}:{action}"
    ctx_spec = contextual.get(ctx_key)
    if ctx_spec is not None:
        # Sintetizziamo uno spec-like dict per riusare il resto della logica.
        spec = {"verb": ctx_spec["verb"]}
        if "object_override" in ctx_spec:
            spec["object_override"] = ctx_spec["object_override"]
        if "qualifier" in ctx_spec:
            spec["qualifier"] = ctx_spec["qualifier"]
    else:
        if action not in actions:
            raise SkillTranslateError(f"action skill non mappata: {action!r}")
        spec = actions[action]

    obj = domains[domain]

    # `exclude_reason`: action mappata deliberatamente come "non tradurre".
    # Es. `labels` (Gmail labels: funzionalita' troppo specifica, e `_labels`
    # non e' qualifier canonico — vocab.QUALIFIERS).
    if "exclude_reason" in spec:
        raise SkillTranslateError(
            f"action {action!r} esclusa: {spec['exclude_reason']}"
        )

    verb = spec["verb"]
    # `object_override`: action specifica un object diverso da quello del
    # dominio (es. create-folder -> dirs).
    if "object_override" in spec:
        obj = spec["object_override"]
    domain_qual = domain_q.get(domain, "")
    action_qual = spec.get("qualifier", "")
    # `object_override` cancella anche il domain_qualifier: dirs invece che
    # files non ha senso con _xlsx.
    if "object_override" in spec:
        domain_qual = ""

    # Validazione qualifier action-side contro vocab.QUALIFIERS canonico
    # (the design guide §2.2). Se il valore non e' in QUALIFIERS, rigetta: il
    # vocab map e' deliberatamente conservativo (no extension senza approvazione).
    if action_qual and action_qual not in _METNOS_QUALIFIERS:
        raise SkillTranslateError(
            f"qualifier action {action_qual!r} non in vocab.QUALIFIERS "
            f"canonico (the design guide §2.2). Estendere il vocab richiede "
            f"approvazione esplicita."
        )

    if verb not in _METNOS_VERBS:
        raise SkillTranslateError(
            f"verb mappato {verb!r} non in vocabolario Metnos §2.2"
        )
    if obj not in _METNOS_OBJECTS:
        raise SkillTranslateError(
            f"object mappato {obj!r} non in vocabolario Metnos §2.2"
        )

    parts = [verb, obj]
    if domain_qual:
        parts.append(domain_qual)
    if action_qual:
        parts.append(action_qual)
    name = "_".join(parts)
    qualifier_combined = "_".join(q for q in (domain_qual, action_qual) if q)
    return name, verb, obj, qualifier_combined


def resolve_context(domain: str, action: str, vocab_map: dict | None = None) -> dict:
    """Ritorna la cella `contextual[<domain>:<action>]` o `{}`.

    ADR 0128: helper per il verifier importer_verb_verify. Espone
    `(target_kind, side_effect, verb)` derivati dal contesto in modo da
    poter controllare a posteriori che il plan finale aderisca alla
    semantica della cella.

    Determinismo §7.9: pura lookup tabellare.
    """
    vm = vocab_map or _load_vocab_map()
    ctx = vm.get("contextual", {})
    return dict(ctx.get(f"{domain}:{action}", {}))


# ---------------------------------------------------------------------------
# Args generator
# ---------------------------------------------------------------------------


# Mapping inferred_type -> (json_type, format, items_type)
_INFER_TYPE_MAP = {
    "string":               ("string", None, None),
    "string_iso_datetime":  ("string", "date-time", None),
    "string_iso_date":      ("string", "date", None),
    "integer":              ("integer", None, None),
    "number":               ("number", None, None),
    "array_csv":            ("array", None, "string"),
    "bool":                 ("boolean", None, None),
}


# Heuristic: nomi di flag/positional che identificano risorsa singola.
# Aggiungiamo SEMPRE la versione plurale (vettoriale §2.1).
_SINGULAR_RESOURCE_HINTS = {
    "id", "event_id", "message_id", "file_id", "doc_id", "sheet_id",
    "spreadsheet_id", "thread_id", "folder_id", "permission_id",
    "label_id", "contact_id", "path",
}


def _flag_name_normalize(name: str) -> str:
    """`add-labels` -> `add_labels`; `raw-query` -> `raw_query`. Lowercase."""
    return name.replace("-", "_").lower()


def _flag_required_in_all_examples(sc, flag_name: str) -> bool:
    """`required` IFF il flag CLI compare in OGNI esempio del sub-command.

    Determinismo §7.9 + universale: deriva il required da SKILL.md (gli esempi),
    NON da argparse (che non ha il concetto per i flag → marcava tutto optional,
    bug pre-2/6). Cattura repo/target/body (sempre presenti) senza marker
    espliciti. Un bool-switch non e' mai required (la presenza E' il valore).
    I positional resource-id sono gestiti a parte e NON resi hard-required
    (§2.1 vettoriale: l'executor valida 'almeno uno' fra singolare/plurale/
    entries). Conservativo: 0 esempi → non-required."""
    fl = sc.flags.get(flag_name)
    if getattr(fl, "is_bool_switch", False):
        return False
    examples = [e for e in (getattr(sc, "examples", None) or [])
                if isinstance(e, str)]
    if not examples:
        return False
    needles = {f"--{flag_name}", f"--{flag_name.replace('_', '-')}",
               f"--{_flag_name_normalize(flag_name)}"}
    # Boundary match: il flag deve essere seguito da spazio, '=' o fine token.
    # Evita che `--label` matchi spuriamente `--labels` (substring) → required
    # errato (bug review 2/6).
    pats = [re.compile(re.escape(n) + r"(?=[\s=]|$)") for n in needles]
    return all(any(p.search(ex) for p in pats) for ex in examples)


def _is_singular_resource(name_norm: str) -> bool:
    if name_norm in _SINGULAR_RESOURCE_HINTS:
        return True
    return name_norm.endswith("_id")


def _pluralize_resource(name_norm: str) -> str:
    """`event_id` -> `event_ids`; `path` -> `paths`. Heuristica leggera."""
    if name_norm.endswith("_id"):
        return name_norm + "s"
    if name_norm.endswith("y") and not name_norm.endswith(("ay", "ey", "iy", "oy", "uy")):
        return name_norm[:-1] + "ies"
    if name_norm.endswith("s"):
        return name_norm
    return name_norm + "s"


def build_args(sc, *, has_entries_output: bool) -> list:
    """Costruisce la lista di ArgSpec per un sub-command.

    Regole:
    - Per ogni flag CLI: 1 ArgSpec scalare.
    - Per ogni positional `MAIUSC_ID`: ArgSpec singolare + plurale (§2.1).
    - Se l'executor produce `entries`, aggiungi `top_k: integer default=50`.
    - Per executor che leggono finestre temporali (calendar.list pattern):
      aggiungi `time_window: string default="next-7d"`.
    """
    out: list = []

    # 1. Flags CLI -> args scalari.
    for flag_name, flag in sc.flags.items():
        norm = _flag_name_normalize(flag_name)
        json_type, fmt, items_t = _INFER_TYPE_MAP.get(
            flag.inferred_type, ("string", None, None)
        )
        spec = ArgSpec(
            name=norm,
            type=json_type,
            format=fmt,
            items_type=items_t,
            description=_describe_flag(norm, flag),
            required=_flag_required_in_all_examples(sc, flag_name),
        )
        out.append(spec)

    # 2. Positional placeholder MAIUSC -> singolare + plurale + entries.
    for pos in sc.positional_args:
        if not _looks_like_id_placeholder(pos):
            # Es. "is:unread" della gmail search (query string) -> arg `query`.
            if "query" not in {a.name for a in out}:
                out.append(ArgSpec(
                    name="query",
                    type="string",
                    description=_describe_query_for(sc.domain, sc.action),
                ))
            continue
        sing = pos.lower()  # EVENT_ID -> event_id
        plur = _pluralize_resource(sing)
        existing = {a.name for a in out}
        if sing not in existing:
            out.append(ArgSpec(
                name=sing,
                type="string",
                description=f"Identificatore singolo {sing} (forma scalare).",
            ))
        if plur not in existing:
            out.append(ArgSpec(
                name=plur,
                type="array",
                items_type="string",
                description=(
                    f"Lista identificatori {plur} (vettoriale §2.1: l'executor "
                    f"itera N volte la chiamata sottostante)."
                ),
            ))
        # `entries` da `from_step` di un executor compatibile.
        if "entries" not in existing:
            out.append(ArgSpec(
                name="entries",
                type="array",
                items_type="object",
                description=(
                    f"Lista di entries dal passo precedente: il campo `id` di "
                    f"ognuno alimenta {plur} (pipeline read -> {sc.action})."
                ),
            ))

    # 3. Vettoriale anche per flag che identificano risorsa singolare.
    new_extras: list = []
    seen = {a.name for a in out}
    for spec in out:
        if _is_singular_resource(spec.name):
            plur = _pluralize_resource(spec.name)
            if plur not in seen and plur != spec.name:
                new_extras.append(ArgSpec(
                    name=plur,
                    type="array",
                    items_type="string",
                    description=(
                        f"Versione plurale di `{spec.name}` (§2.1)."
                    ),
                ))
                seen.add(plur)
    out.extend(new_extras)

    # 4. top_k per executor che producono entries.
    if has_entries_output and "top_k" not in {a.name for a in out}:
        out.append(ArgSpec(
            name="top_k",
            type="integer",
            default=50,
            description=(
                "Cap superiore esplicito (§2.7): max entries ritornate. "
                "Cap inferiore = 0. Truncated visibility quando raggiunto."
            ),
        ))

    # 5. time_window per `calendar list` e simili (executor che leggono
    # senza id e senza query: read_events).
    is_window_reader = (
        sc.domain == "calendar"
        and sc.action == "list"
    )
    if is_window_reader and "time_window" not in {a.name for a in out}:
        out.insert(0, ArgSpec(
            name="time_window",
            type="string",
            default="next-7d",
            description=(
                "Finestra temporale: 'today', 'tomorrow', 'last-Nd', "
                "'next-Nd', 'last-week', 'next-week', range ISO "
                "'YYYY-MM-DD/YYYY-MM-DD'. Default 'next-7d'. Mutuamente "
                "esclusivo con `start`/`end`."
            ),
        ))

    # 6. calendar_id per executor su events (skill non lo emette esplicito).
    if sc.domain == "calendar" and "calendar_id" not in {a.name for a in out}:
        out.append(ArgSpec(
            name="calendar_id",
            type="string",
            default="primary",
            description="Identificatore del calendario Google. Default 'primary'.",
        ))

    return out


def _looks_like_id_placeholder(tok: str) -> bool:
    """Distingue `MESSAGE_ID`/`EVENT_ID` (placeholder doc) da:
    - valori reali tipo `is:unread`, `quarterly report`, path /a/b.
    - sigle brevi tipo `Q4` (esempio non placeholder).
    Heuristica: maiuscolo + (ha `_` separator OPPURE termina con `_ID`/`ID`
    OPPURE lunghezza >=4 con solo lettere).
    """
    if not tok or any(c in tok for c in (":", "/", " ", "@", "'", "\"", "=")):
        return False
    if not tok.isupper():
        return False
    if not tok.replace("_", "").isalnum():
        return False
    # Forte: contiene `_` separator -> e' MESSAGE_ID, EVENT_ID, etc.
    if "_" in tok:
        return True
    # Forte: termina con ID -> es. SHEETID, DOCID, FILEID.
    if tok.endswith("ID") and len(tok) >= 4:
        return True
    # Debole: solo lettere lunghe -> potenziale ID custom.
    if tok.isalpha() and len(tok) >= 4:
        return True
    return False


_FLAG_DESCRIPTIONS = {
    "max":         "Numero massimo di risultati ritornati dalla skill backend.",
    "to":          "Indirizzo destinatario (RFC 5322).",
    "from":        "Indirizzo mittente (RFC 5322), opzionalmente con display name.",
    "subject":     "Oggetto del messaggio.",
    "body":        "Corpo del messaggio (text o HTML se `html=true`).",
    "html":        "Se true, il body e' interpretato come HTML.",
    "summary":     "Titolo dell'evento (breve, mostrato in agenda).",
    "start":       "Inizio in ISO 8601 con offset esplicito (es. 'Z' o '+01:00').",
    "end":         "Fine in ISO 8601 con offset esplicito; deve essere > start.",
    "location":    "Luogo dell'evento (testo libero).",
    "attendees":   "Lista CSV o array di email partecipanti.",
    "name":        "Nome del file/risorsa.",
    "parent":      "Identificatore della cartella padre (Drive folder id).",
    "title":       "Titolo del documento/risorsa.",
    "values":      "Matrice JSON di valori (lista di liste).",
    "permanent":   "Se true, salta il cestino e cancella definitivamente.",
    "raw_query":   "Se true, la query e' passata raw alla skill (sintassi backend).",
    "role":        "Ruolo di permesso (reader|writer|commenter|owner).",
    "type":        "Tipo di permesso (user|group|domain|anyone).",
    "domain":      "Dominio del permesso (per type=domain).",
    "email":       "Email del permesso (per type=user|group).",
    "notify":      "Se true, invia notifica al destinatario.",
    "output":      "Path di output locale (download).",
    "export_mime": "MIME type di export per file Google-native (Docs/Sheets/Slides).",
    "text":        "Testo da appendere/inserire.",
    "sheet_name":  "Nome del foglio (tab) all'interno dello spreadsheet.",
    "add_labels":  "Etichette da aggiungere (id label, separati da virgole).",
    "remove_labels": "Etichette da rimuovere (id label, separati da virgole).",
}


def _describe_flag(norm_name: str, flag) -> str:
    """Descrizione human-readable per la doc del manifest."""
    base = _FLAG_DESCRIPTIONS.get(norm_name, f"Flag `--{norm_name}` della skill.")
    examples = flag.seen_values[:2]
    if examples:
        ex_str = " ".join(f"'{e}'" for e in examples)
        return f"{base} Es. {ex_str}."
    return base


_QUERY_DESC_BY_DOMAIN = {
    ("gmail", "search"): (
        "Query Gmail in sintassi nativa (es. 'is:unread', 'from:x@y newer_than:1d'). "
        "Vedi `references/gmail-search-syntax.md` per gli operatori."
    ),
    ("drive", "search"): (
        "Query Drive (testo libero o `mimeType='...'` con --raw_query)."
    ),
}


def _describe_query_for(domain: str, action: str) -> str:
    return _QUERY_DESC_BY_DOMAIN.get(
        (domain, action),
        "Stringa di ricerca per la skill backend.",
    )


# ---------------------------------------------------------------------------
# Reverse pattern (§2.3 + 5° pattern delete_<object>_by_id)
# ---------------------------------------------------------------------------


def resolve_reverse_pattern(verb: str, obj: str) -> tuple:
    """Ritorna `(reversible: bool, reverse_pattern: str)`.

    Regole §2.3 (catalogo) + estensione 5° pattern remoto:
    - read/list/find/get/filter/sort/group/classify/describe/compute/compare
      -> read-only -> `(False, "")`.
    - set_<obj>            -> `(True, "delete_<obj>_by_id")` (5° pattern).
    - delete_<obj>         -> `(False, "")` (terminale, modello delete_persons).
    - move_<obj>           -> `(True, "swap_src_dst")`.
    - create_<obj>         -> `(True, "delete_created_paths")` se obj=files,
                              `(True, "delete_created_dirs")` se obj=dirs,
                              altrimenti `(True, "delete_<obj>_by_id")`.
    - write/change/extract/compress/render/order -> `(False, "")` per ora
      (richiedono blob backup specifico, fuori scope Task B).
    - send_messages         -> `(True, "delete_messages_by_id")` (5° pattern).
    - share_<obj>          -> `(True, "delete_<obj>_permissions_by_id")` ADR 0128:
      lo share crea un permission/ACL grant remoto, reversibile via revoke
      dell'id del permesso. Per il momento usiamo il 5° pattern adattato.
    """
    READ_ONLY = {
        "read", "list", "find", "get", "filter", "sort", "group",
        "classify", "describe", "compute", "compare",
    }
    if verb in READ_ONLY:
        return False, ""
    if verb == "set":
        return True, f"delete_{obj}_by_id"
    if verb == "send":
        return True, f"delete_{obj}_by_id"
    if verb == "delete":
        return False, ""
    if verb == "move":
        return True, "swap_src_dst"
    if verb == "create":
        if obj == "files":
            return True, "delete_created_paths"
        if obj == "dirs":
            return True, "delete_created_dirs"
        return True, f"delete_{obj}_by_id"
    if verb == "write":
        # Upload Drive: il file remoto preesiste? In genere no -> reverse via id.
        return True, f"delete_{obj}_by_id"
    if verb == "share":
        # ADR 0128: revoke ACL grant via permission id. Pattern non in
        # catalogo §2.3 ancora, ma usa la stessa famiglia delete_*_by_id.
        return True, f"delete_{obj}_permissions_by_id"
    return False, ""


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def derive_capabilities(parsed_skill, sub_command) -> list:
    """Deduce `[[capabilities]]` da:
    1. frontmatter `allowed-tools` se presente (formato Anthropic puro).
    2. presenza di `scripts/` -> sempre `metnos:net` (skill remota di default
       quando wrappa una CLI di terze parti).
    3. `required_credential_files` -> `metnos:read`/`metnos:write` sul path
       (re-mappato sotto `~/.local/share/metnos/credentials/`).
    """
    out: list = []

    # 1. Allowed-tools (se presenti, hanno priorita').
    for at in parsed_skill.allowed_tools:
        out.append(CapabilitySpec(name=at.lower(), hint=[]))

    # 2. Network egress se scripts/ non vuoto (Google API, ecc.).
    if parsed_skill.scripts:
        if not any(c.name == "metnos:net" for c in out):
            host_hint = _hosts_for_domain(sub_command.domain)
            out.append(CapabilitySpec(name="metnos:net", hint=host_hint))

    # 3. Credenziali.
    for rcf in parsed_skill.required_credential_files:
        path = rcf.get("path") or ""
        if not path:
            continue
        # Re-map sotto Metnos credentials root (ADR 0082).
        metnos_path = (
            f"~/.local/share/metnos/credentials/{Path(path).stem}.enc"
        )
        if not any(
            c.name == "metnos:read" and metnos_path in c.hint for c in out
        ):
            out.append(CapabilitySpec(name="metnos:read", hint=[metnos_path]))
        if not any(
            c.name == "metnos:write" and metnos_path in c.hint for c in out
        ):
            out.append(CapabilitySpec(name="metnos:write", hint=[metnos_path]))

    return out


_DOMAIN_HOST_HINT = {
    "calendar": ["googleapis.com", "accounts.google.com"],
    "gmail":    ["googleapis.com", "accounts.google.com"],
    "drive":    ["googleapis.com", "accounts.google.com"],
    "sheets":   ["googleapis.com", "accounts.google.com"],
    "docs":     ["googleapis.com", "accounts.google.com"],
    "contacts": ["people.googleapis.com", "accounts.google.com"],
}


def _hosts_for_domain(domain: str) -> list:
    return list(_DOMAIN_HOST_HINT.get(domain, []))


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


_OUTPUT_KIND_BY_VERB = {
    # entries §2.6: arricchiscono/leggono.
    "read": "entries",
    "find": "entries",
    "get": "entries",
    "list": "entries",
    "filter": "entries",
    "describe": "entries",
    # results §2.6: trasformative.
    "set": "results",
    "delete": "results",
    "move": "results",
    "write": "results",
    "create": "results",
    "send": "results",
    "change": "results",
    "extract": "results",
    "compress": "results",
    # share (ADR 0128): outbound consent, side-effect remoto -> results.
    "share": "results",
}


_RECORD_KIND_BY_OBJ = {
    "events":   "calendar_event",
    "messages": "email_message",
    "files":    "drive_file",
    "contacts": "contact",
}


def resolve_output_kind(verb: str, obj: str) -> tuple:
    out_kind = _OUTPUT_KIND_BY_VERB.get(verb, "results")
    record = _RECORD_KIND_BY_OBJ.get(obj, "")
    return out_kind, record


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def build_provenance(parsed_skill, sub_command, *,
                     imported_from_url: str = "",
                     imported_at: str = "") -> dict:
    """Costruisce dict `[provenance]` da PSI + sub-command corrente."""
    base_url = (
        imported_from_url
        or f"agentskills.io/local/{parsed_skill.name}"
    )
    return {
        "synthesized": True,
        "imported_from": base_url,
        "source_version": parsed_skill.version,
        "source_section": sub_command.domain.title(),
        "source_subcommand": f"{sub_command.domain} {sub_command.action}",
        "imported_at": imported_at,
        "source_sha256": parsed_skill.source_sha256,
        "importer_version": "0.1.0-poc",
    }


# ---------------------------------------------------------------------------
# Translator main entry
# ---------------------------------------------------------------------------


def translate_subcommand(parsed_skill, sub_command, *,
                         imported_from_url: str = "",
                         imported_at: str = "",
                         vocab_map: dict | None = None) -> ExecutorPlan:
    """Da `(ParsedSkill, SkillSubCommand)` a `ExecutorPlan`.

    Solleva SkillTranslateError se naming non risolvibile dalla mapping
    deterministica (caller: spawnare synth stage 1 fallback).
    """
    vm = vocab_map or _load_vocab_map()
    name, verb, obj, qualifier = resolve_name(
        sub_command.domain, sub_command.action, vocab_map=vm,
    )
    output_kind, record_kind = resolve_output_kind(verb, obj)
    args = build_args(sub_command, has_entries_output=(output_kind == "entries"))
    reversible, reverse_pat = resolve_reverse_pattern(verb, obj)
    caps = derive_capabilities(parsed_skill, sub_command)
    prov = build_provenance(
        parsed_skill, sub_command,
        imported_from_url=imported_from_url, imported_at=imported_at,
    )

    return ExecutorPlan(
        name=name,
        verb=verb,
        obj=obj,
        qualifier=qualifier,
        skill_domain=sub_command.domain,
        skill_action=sub_command.action,
        args=args,
        output_kind=output_kind,
        output_record_kind=record_kind,
        reversible=reversible,
        reverse_pattern=reverse_pat,
        capabilities=caps,
        provenance=prov,
        examples=list(sub_command.examples),
    )


def _normalize_binding(skill_name: str) -> str:
    """Skill name -> binding identifier snake_case. `google-workspace` -> `google_workspace`."""
    return skill_name.lower().replace("-", "_").replace(".", "_")


def translate_skill(parsed_skill, *,
                    imported_from_url: str = "",
                    imported_at: str = "",
                    vocab_map: dict | None = None,
                    handcrafted_names: set | None = None) -> tuple:
    """Itera su tutti i sub-command della skill. Ritorna
    `(plans: list[ExecutorPlan], rejected: list[(domain, action, reason)])`.

    Naming convention §2.2 4ª famiglia qualifier "provider" (ADR 0136):
    ogni executor importato da una skill esterna riceve SEMPRE il
    binding della skill come suffix (`_<binding>`). Es. tutti i tool
    della skill `google-workspace` finiscono in `_google_workspace`,
    a prescindere dalla collisione con builtin. Questo permette al
    filtro grammar `_PROVIDER_SUFFIX_MARKERS` di escludere dal pool i
    tool che richiedono un provider esterno se la query utente non
    contiene marker (`google`, `gmail`, `drive`, ecc.). Self-hosted
    e' il default, provider esterno e' opt-in.

    Pre-16/5/2026: il suffix veniva applicato SOLO su collision con
    handcrafted — bug noto (naming asimmetrico negli imports: 10/17
    tool google-workspace senza suffix). Il fix uniforme rende ADR
    0136 consistentemente applicabile.

    Collisione interna alla skill (due sub-command stesso name): il
    secondo va in `rejected`.

    R2 (24/5/2026): verb-boundary gate via `importer_verb_verify.check_plan`.
    Plan con `aligned=False` (mismatch get_drift/change_overload/set_overload/
    share_drift/share_collapse) finisce in `rejected` con reason
    `verb_boundary: <reason>`. Audit JSONL append-only in
    `<PATH_USER_DATA>/synth_audit/imports.jsonl`.
    """
    # R2: lazy import per evitare import circolare top-level.
    from importer_verb_verify import check_plan as _verify_plan

    plans: list = []
    rejected: list = []
    seen: dict = {}
    vm = vocab_map or _load_vocab_map()
    binding = _normalize_binding(parsed_skill.name)
    # `handcrafted_names` non e' piu' usato dopo il fix uniforme suffix
    # provider (16/5/2026). Mantenuto in signature per backward compat.
    _ = handcrafted_names  # silenzia linter
    for sc in parsed_skill.sub_commands:
        try:
            plan = translate_subcommand(
                parsed_skill, sc,
                imported_from_url=imported_from_url,
                imported_at=imported_at,
                vocab_map=vm,
            )
        except SkillTranslateError as e:
            rejected.append((sc.domain, sc.action, str(e)))
            continue

        # R2 verb-boundary check PRIMA del disambiguator suffix (verb non
        # cambia con il suffix; check su verb crudo).
        verdict = _verify_plan(plan, vocab_map=vm)
        if not verdict.aligned:
            reason = f"verb_boundary: {verdict.mismatch_reason}"
            rejected.append((sc.domain, sc.action, reason))
            continue

        # SEMPRE suffix provider (ADR 0136). Pre-fix era condizionale a
        # `if plan.name in hc`; questo causava asimmetria naming.
        disambiguated = f"{plan.name}_{binding}"
        plan.name = disambiguated
        new_qual = "_".join(q for q in (plan.qualifier, binding) if q)
        plan.qualifier = new_qual
        if plan.name in seen:
            rejected.append((
                sc.domain, sc.action,
                f"name collision con {seen[plan.name]}: {plan.name}"
            ))
            continue
        seen[plan.name] = f"{sc.domain}.{sc.action}"
        plans.append(plan)
    return plans, rejected

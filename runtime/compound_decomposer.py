"""compound_decomposer.py — utilità deterministiche §7.9 per compound queries.

STORIA (ADR 0177, 24/6): conteneva ANCHE un planner deterministico
(`decompose_query`) che decomponeva i compound in step senza LLM, come
pre-stadio/mitigatore del cold-start engine. È stato ELIMINATO: il bake
`METNOS_DECOMPOSER=0` ha provato che l'engine (proposer + cache L0/L1) copre il
caso generale ed è §2.8-onesto; il decomposer divergeva (S1) e ne mascherava i
bug. Path di planning compound ora UNICO = engine.

Restano qui le UTILITÀ condivise, usate dai guard deterministici dell'engine
(`engine/dispatch.py`) e da `self_recipient_resolver`:
  - `PRODUCER_VERBS` / `MUTATING_VERBS` / `TRANSFORM_VERBS` — classi di verbi
  - `split_query_chunks` — split di una query compound nei chunk-clausola
  - `detect_chunk_action(chunk)` — (verb, object) lessicale di un chunk
  - `derive_tool_name(verb, obj, available)` — nome-tool canonico per object
  - `derive_extract_fields(query)` — campi euristici per la clausola extract
  - `_send_has_explicit_recipient(chunk)` — destinatario esplicito vs self

Universal § lingua-indipendente: vocab IT+EN via prefilter + detection_lexicon.
"""
from __future__ import annotations

import functools
import re
from collections.abc import Iterator, Mapping
from typing import Optional

import detection_lexicon as _dl  # lessici NL traducibili (gemello i18n input)
import detection_lexicon_seed_parsers as _parser_lex

# Connettori sequenziali: i SIMBOLI (,;&&) e i terminatori interrogativi o
# esclamativi sono lingua-invarianti e restano qui; le PAROLE connettore
# (e/and/poi/then/...) vivono nel concept traducibile
# `compound.connector_word` (detection_lexicon). Il pattern lessicale di split
# e' ricostruito deterministicamente dalle forme della lingua corrente.


# Apostrofi (tutte le forme Unicode: ASCII, typographic, modifier-letter, grave).
# LANGUAGE-AGNOSTIC: l'apostrofo LEGA i caratteri (elisione/contrazione) in ogni
# lingua — IT «e'»/«cos'»/«l'», FR «j'»/«qu'», EN «it's»/«don't». Un connettore
# adiacente a un apostrofo è parte di una parola elisa, NON un separatore.
_APOSTROPHES = "".join(chr(c) for c in (0x27, 0x2019, 0x02BC, 0x60))  # ' ’ ʼ `

# Confine forte fra frasi letterali. Richiedere spazio dopo i terminatori
# ASCII evita di spezzare query-string e altri token (`...?q=...`); i segni
# Unicode coprono gli equivalenti non latini con lo stesso contratto. Il
# terminatore resta nel chunk precedente, quindi ogni risultato e' ancora uno
# span letterale della richiesta e puo' essere validato senza riscritture LLM.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.?!。！？؟])\s+(?=\S)")


@functools.lru_cache(maxsize=8)
def _connector_pattern(_lang: str) -> "re.Pattern":
    words = _dl.forms("compound.connector_word")
    # If the resource is unavailable, symbols still split but no linguistic
    # connector is guessed from a built-in fallback.
    alt = "|".join(
        re.escape(str(word)).replace(r"\ ", r"\s+") for word in words
    ) if words else r"(?!x)x"
    # Boundary del connettore: NON deve essere adiacente a un apostrofo su NESSUN
    # lato (lookbehind + lookahead). Generale §7.9, nessuna parola/lingua cablata:
    # è la definizione «apostrofo = word-char» applicata al confine, non un caso
    # speciale dell'italiano. Chiude il bug «quando e' stato modificato» (e'=è)
    # senza toccare i connettori veri («leggi le mail e salvale»).
    ap = _APOSTROPHES
    return re.compile(
        r"\s*(?:,|;|\&\&?|(?<![" + ap + r"])\b(?:" + alt + r")\b(?![" + ap + r"]))\s*",
        re.IGNORECASE)

# Verb categories from §2.2 vocab (canonical):
# - Producer (read_family): find/read/get/list — produce entries
# - Mutating: write/create/set/move/delete/send/share/compress/extract/change
# - Transformative: filter/sort/group/classify/describe/render/compute/compare
PRODUCER_VERBS = {"find", "read", "get", "list"}
MUTATING_VERBS = {"write", "create", "set", "move", "delete", "send",
                   "share", "compress", "extract", "change", "order"}

# Mapping format/qualifier hint NL → (object, qualifier). Fonte UNICA condivisa
# da decompose_query (_detect_format_obj) e derive_tool_name (scelta della
# variante-qualifier query-aware). Universal §7.9, lessico curato (no special-
# case). NB: «foglio (di calcolo/elettronico)» = lo spreadsheet in IT (mancava
# → «crea un foglio» derivava create_files_doc invece di _spreadsheet).
def _compound_lexicon() -> dict[str, object] | None:
    return _parser_lex.load_family("compound")


def _format_hint_mapping(
        lexicon: dict[str, object] | None = None,
) -> dict[str, tuple[str, str]]:
    lexicon = lexicon or _compound_lexicon()
    raw = (lexicon or {}).get("parser.compound.format_hint", {})
    result: dict[str, tuple[str, str]] = {}
    for canonical, forms in raw.items():
        parts = str(canonical).split(":", 1)
        if len(parts) != 2 or not all(parts):
            continue
        for form in forms:
            surface = str(form).strip().casefold()
            if surface:
                result[surface] = (parts[0], parts[1])
    return result


class _FormatHintsView(Mapping[str, tuple[str, str]]):
    """Compatibility view for the existing dispatch consumer.

    It intentionally resolves on every operation: translations may become
    ready after module import, while a partial family must remain invisible.
    """

    def __getitem__(self, key: str) -> tuple[str, str]:
        return _format_hint_mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(_format_hint_mapping())

    def __len__(self) -> int:
        return len(_format_hint_mapping())

    def items(self):
        return _format_hint_mapping().items()


_FORMAT_HINTS: Mapping[str, tuple[str, str]] = _FormatHintsView()
TRANSFORM_VERBS = {"filter", "sort", "group", "classify", "describe",
                    "render", "compute", "compare"}


def _raw_query_chunks(query: str) -> list[str]:
    """Return literal chunks at registered connector boundaries.

    This lower-level view intentionally retains verb-less fragments: they can
    be either scalar continuations or members of a list.  Consumers choose the
    appropriate interpretation instead of losing that distinction here.
    """
    if not query or not query.strip():
        return []
    parts: list[str] = []
    connector = _connector_pattern(_dl.current_lang())
    for sentence in _SENTENCE_BOUNDARY.split(query.strip()):
        parts.extend(connector.split(sentence))
    return [p.strip() for p in parts if p.strip()]


def split_query_chunks(query: str) -> list[str]:
    """Split su confini forti e connettori sequenziali universali.

    La punteggiatura separa frasi autonome prima del lessico traducibile. In
    questo modo una richiesta composta non dipende dalla presenza di una
    congiunzione specifica e ogni chunk conserva esattamente il testo utente.
    """
    raw = _raw_query_chunks(query)
    # A comma inside a scalar value is not a clause boundary (notably
    # ``January 15, 2030``).  Reattach verb-less fragments to the preceding
    # clause; true sequential clauses retain their own canonical action.
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        merged: list[str] = []
        for part in raw:
            has_action = bool(detect_canonical_verbs_all(tokenize(part)))
            if merged and not has_action:
                merged[-1] = f"{merged[-1]}, {part}"
            else:
                merged.append(part)
        return merged
    except Exception:
        return raw


def _clean_field_name(text: str,
                      lexicon: dict[str, object] | None = None) -> str:
    """Normalizza un frammento NL in un nome-campo: taglia alla prima prep-frase,
    scarta articoli/preposizioni-composto, max 3 parole. Deterministico §7.9."""
    lexicon = lexicon or _compound_lexicon()
    if lexicon is None:
        return ""
    field_stop = {str(item).casefold() for item in
                  lexicon["parser.compound.field_stop"]}
    field_cut = {str(item).casefold() for item in
                 lexicon["parser.compound.field_cut"]}
    text = text.replace("'", " ").replace("’", " ").casefold()
    words = re.findall(r"[\w]+", text)
    kept: list[str] = []
    for w in words:
        if w in field_cut and kept:
            break
        kept.append(w)
    kept = [w for w in kept if w not in field_stop]
    return " ".join(kept[:3]).strip()


# Marcatori che DICHIARANO lo schema d'uscita (le colonne del sink) in una
# clausola create/write: «crea un foglio con (tutti i)? <marker>[:] X, Y, Z».
# Lessico curato IT+EN. `dati/data` (plurale IT / EN) = marker; NON confondere
# con il campo «data» (=date IT), distinto lessicalmente da «dati».
def _phrase_alt(forms) -> str:
    return "|".join(
        re.escape(str(form)).replace(r"\ ", r"\s+")
        for form in sorted(set(forms or ()), key=lambda item: (-len(item), item))
        if str(form).strip()
    )


def _fields_from_schema_marker(
        query: str, lexicon: dict[str, object] | None = None) -> list[str]:
    """Campi dallo SCHEMA D'USCITA dichiarato nella clausola create/write
    («…con (tutti i)? colonne/campi/dati: X, Y, Z»). Il field-list è lo schema
    del sink (arg `columns`), spesso frainteso come spec d'estrazione: qui lo
    ricaviamo deterministicamente (§7.9) quando NON c'è una clausola «estrai».
    Anti over-capture: senza «:» esplicito richiedi una LISTA (≥2 elementi)."""
    lexicon = lexicon or _compound_lexicon()
    if lexicon is None:
        return []
    markers = _phrase_alt(lexicon["parser.compound.schema_marker"])
    connectors = _phrase_alt(lexicon["parser.compound.list_connector"])
    if not markers or not connectors:
        return []
    marker_rx = re.compile(
        rf"(?<!\w)(?:{markers})(?!\w)(\s*:)?\s*(.+)$",
        re.IGNORECASE | re.UNICODE,
    )
    m = marker_rx.search(query or "")
    if not m:
        return []
    has_colon = bool(m.group(1))
    tail = m.group(2) or ""
    parts = [p for p in re.split(
        rf"\s*,\s*|\s+(?:{connectors})\s+", tail,
        flags=re.IGNORECASE | re.UNICODE,
    ) if p.strip()]
    if not has_colon and len(parts) < 2:
        return []
    out: list[str] = []
    seen: set = set()
    for p in parts:
        f = _clean_field_name(p, lexicon)
        if f and f not in seen and len(f) <= 40:
            seen.add(f)
            out.append(f)
    return out


def derive_sink_fields(query: str) -> list[str]:
    """Ricava le colonne dichiarate naturalmente per un sink tabellare.

    Copre «un foglio con origine, data e importo» senza scambiare una frase
    successiva («segnala ...», «salva ...») per altre colonne. Lo scope e'
    confinato alla clausola tabellare e termina alla punteggiatura; in assenza
    di questa forma riusa i marker/sink assignment storici.
    """
    # Chat/Markdown clients often wrap a single natural-language clause over
    # several lines (and may prefix each continuation with `>`).  Newlines are
    # presentation, not semantic boundaries: collapse them before applying the
    # clause-scoped parser, otherwise a wrapped schema becomes silently partial
    # (live: "valore" at EOL dropped "originale, origine, ..." on the next).
    normalized_query = re.sub(r"(?:^|\n)\s*>\s?", " ", query or "")
    normalized_query = re.sub(r"\s+", " ", normalized_query).strip()
    lexicon = _compound_lexicon()
    if lexicon is None:
        return []
    tabular = _phrase_alt(lexicon["parser.compound.tabular_noun"])
    with_forms = _phrase_alt(lexicon["parser.compound.with_connector"])
    if not tabular or not with_forms:
        return []
    tabular_rx = re.compile(
        rf"(?<!\w)(?:{tabular})(?!\w)[^.;:\n]{{0,100}}?"
        rf"(?<!\w)(?:{with_forms})(?!\w)\s*([^.;\n]+)",
        re.IGNORECASE | re.UNICODE,
    )
    match = tabular_rx.search(normalized_query)
    if match:
        field_clause = match.group(1) or ""
        list_connectors = _phrase_alt(
            lexicon["parser.compound.list_connector"])
        articles = _phrase_alt(lexicon["parser.compound.article"])
        artifacts = _phrase_alt(
            lexicon["parser.compound.artifact_boundary"])
        row_nouns = _phrase_alt(lexicon["parser.compound.row_noun"])
        schema_markers = _phrase_alt(
            lexicon["parser.compound.schema_marker"])
        total_quantifiers = _phrase_alt(
            lexicon["parser.compound.total_quantifier"])
        # A following output artifact belongs to the next sink, not to the
        # spreadsheet schema: "..., conflitto, e un archivio ZIP ...".  Stop
        # at that explicit noun boundary while still allowing ordinary `e`
        # inside the list of fields.
        if list_connectors and artifacts:
            article_prefix = rf"(?:(?:{articles})(?!\w)\s+)?" \
                if articles else ""
            field_clause = re.split(
                rf"\s*,?\s+(?:{list_connectors})(?!\w)\s+"
                rf"{article_prefix}(?:{artifacts})(?!\w)",
                field_clause, maxsplit=1,
                flags=re.IGNORECASE | re.UNICODE,
            )[0]
        # Il payload tabellare può seguire nella STESSA frase: «con le colonne
        # voce, stato e importo e due righe di dati: alpha, ...». Le righe non
        # sono nuove intestazioni. Taglia sul confine coordinato che introduce
        # rows/records, indipendentemente dalla quantità espressa prima.
        if list_connectors and row_nouns:
            field_clause = re.split(
                rf"\s*,?\s+(?:{list_connectors})(?!\w)\s+"
                rf"(?:(?:\d+|[^\W_]+)\s+)?(?:{row_nouns})(?!\w)",
                field_clause, maxsplit=1,
                flags=re.IGNORECASE | re.UNICODE,
            )[0]
        # La forma tabellare puo' includere un marker di schema prima della
        # lista: «spreadsheet con tutti i dati: data, descrizione, importo».
        # Quel prefisso descrive la completezza, non e' il primo campo. Questa
        # normalizzazione preserva la forma diretta Atlas («foglio con origine,
        # data, ...») e il comportamento storico del parser a marker.
        if schema_markers:
            article_prefix = rf"(?:(?:{articles})(?!\w)\s+)?" \
                if articles else ""
            field_clause = re.sub(
                rf"^\s*{article_prefix}(?:{schema_markers})(?!\w)\s*:?\s*",
                "", field_clause, flags=re.IGNORECASE | re.UNICODE,
            )
            quantifier_prefix = (
                rf"(?:(?:{total_quantifiers})(?!\w)\s+)?"
                if total_quantifiers else ""
            )
            field_clause = re.sub(
                rf"^\s*{quantifier_prefix}(?:{schema_markers})(?!\w)\s*:\s*",
                "", field_clause, flags=re.IGNORECASE | re.UNICODE,
            )
        parts = [p for p in re.split(
            rf"\s*,\s*|\s+(?:{list_connectors})\s+", field_clause,
            flags=re.IGNORECASE | re.UNICODE)
                 if p.strip()]
        out: list[str] = []
        seen: set[str] = set()
        for part in parts:
            field = _clean_field_name(part, lexicon)
            if field and field not in seen and len(field) <= 40:
                seen.add(field)
                out.append(field)
        if len(out) >= 2:
            return out
    return (_fields_from_schema_marker(normalized_query, lexicon)
            or _fields_from_sink_assignment(normalized_query, lexicon))


def _fields_from_sink_assignment(
        query: str, lexicon: dict[str, object] | None = None) -> list[str]:
    """Campi da una frase naturale di popolamento del sink.

    Copre forme come «crea uno spreadsheet e metti data e importo» / «create
    a spreadsheet and put date and amount». Il verbo canonico `write` viene
    dal vocabolario esistente; i chunk successivi senza verbo sono la
    continuazione della lista. Richiediamo almeno due campi e un formato
    tabellare esplicito, evitando di interpretare come schema un normale
    «metti il file in /tmp».
    """
    lexicon = lexicon or _compound_lexicon()
    if lexicon is None:
        return []
    q = query or ""
    ql = q.casefold()
    tabular = any(
        hint in ql and qualifier in {"spreadsheet", "xlsx", "csv"}
        for hint, (_obj, qualifier) in _format_hint_mapping(lexicon).items()
    )
    if not tabular:
        return []
    try:
        from prefilter import (tokenize as _tok,
                               detect_canonical_verbs_all as _verbs)
    except Exception:
        return []
    # A schema is a list by definition, so verb-less connector fragments are
    # list members rather than scalar continuations.
    chunks = _raw_query_chunks(q)
    annotated = [(chunk, _verbs(_tok(chunk)) or []) for chunk in chunks]
    fields: list[str] = []
    for i, (chunk, verbs) in enumerate(annotated):
        if "write" not in verbs:
            continue
        words = re.findall(r"[\w']+", chunk)
        verb_idx = next((j for j, word in enumerate(words)
                         if "write" in (_verbs(_tok(word)) or [])), None)
        if verb_idx is None or verb_idx + 1 >= len(words):
            continue
        first = _clean_field_name(" ".join(words[verb_idx + 1:]), lexicon)
        if first:
            fields.append(first)
        j = i + 1
        while j < len(annotated) and not annotated[j][1]:
            field = _clean_field_name(annotated[j][0], lexicon)
            if field:
                fields.append(field)
            j += 1
        break
    out: list[str] = []
    seen: set[str] = set()
    for field in fields:
        if field and field not in seen and len(field) <= 40:
            seen.add(field)
            out.append(field)
    return out if len(out) >= 2 else []


def derive_extract_fields(query: str) -> list[str]:
    """§7.9 deterministico: estrae i NOMI-CAMPO dalla clausola «estrai X, Y e Z»
    di una query compound — o, in assenza, dallo SCHEMA D'USCITA dichiarato nella
    clausola create/write («…con colonne/dati: X, Y, Z», vedi
    `_fields_from_schema_marker`). Serve a riempire `extract_entries.fields` quando il
    proposer DROPPA la clausola e il guard `_ensure_extract_clause` la re-inserisce
    (bug live 22/6: «...estrai titolo e orario...» → extract_entries SENZA fields →
    «missing 'fields'»). Robustezza NL→determinismo §2.4: la clausola e' spezzata
    dai connettori (anche «e» DENTRO la lista campi) → i chunk SENZA verbo sono
    continuazioni della clausola-extract. Ritorna [] se non c'e' clausola extract
    (il caller mantiene il comportamento attuale). Niente LLM."""
    lexicon = _compound_lexicon()
    if lexicon is None:
        return []
    try:
        from prefilter import (tokenize as _tok,
                               detect_canonical_verbs_all as _verbs)
    except Exception:
        return []
    # The extract payload is a list by definition.  Use literal connector
    # boundaries; the public clause splitter deliberately rejoins verb-less
    # fragments because in an ordinary clause they may be scalar values.
    chunks = _raw_query_chunks(query)
    if not chunks:
        return []
    ann = [(ch, (_verbs(_tok(ch)) or [None])[0]) for ch in chunks]
    n = len(ann)
    fields: list[str] = []
    for i, (ch, v) in enumerate(ann):
        if v != "extract":
            continue
        # primo chunk: scarta la PAROLA-verbo iniziale (es. «estrai»/«extract»).
        first = _clean_field_name(
            " ".join(re.findall(r"[\w']+", ch)[1:]), lexicon)
        if first:
            fields.append(first)
        # continuazioni: chunk seguenti SENZA verbo (resto della lista campi).
        j = i + 1
        while j < n and ann[j][1] is None:
            f2 = _clean_field_name(ann[j][0], lexicon)
            if f2:
                fields.append(f2)
            j += 1
        break
    seen: set = set()
    out: list[str] = []
    for f in fields:
        if f and f not in seen and len(f) <= 40:
            seen.add(f)
            out.append(f)
    # Fallback: nessuna clausola «estrai» → schema d'uscita della clausola create.
    return out or derive_sink_fields(query)


def detect_chunk_action(chunk: str) -> Optional[tuple[str, str]]:
    """Detect (verb, object) canonical per un chunk di query.
    Ritorna None se nessun verbo canonico o object derivabile.

    Universal §7.9: usa vocab esistenti, no patterns hardcoded.
    """
    try:
        from prefilter import (
            tokenize,
            detect_canonical_verbs_all,
            object_hint_mapping,
        )
        from vocab import canonical_object as _canon_obj
    except ImportError:
        return None

    tokens = tokenize(chunk)
    if not tokens:
        return None

    # 1. Detect verbo canonico (con clitic stripping incluso)
    verbs = detect_canonical_verbs_all(tokens)
    if not verbs:
        return None
    # An explicit transform verb owns its clause even when a field name also
    # happens to be a verb hint in another domain (``email`` commonly boosts
    # send). Sequential actions are split into separate chunks above.
    verb = next((item for item in verbs if item in TRANSFORM_VERBS), verbs[0])

    # Transform tools consume the generic entry carrier.  Incidental domain
    # words in the predicate (for example ``email`` in "filter contacts that
    # have an email address") describe a field, not a second messages action.
    # Returning the carrier here also matches derive_tool_name(), whose
    # canonical fallback for every transform is ``<verb>_entries``.
    if verb in TRANSFORM_VERBS:
        return (verb, "entries")

    # 2. Detect object canonico
    # Try the localized object-hint mapping first (più ricco)
    detected_obj = None
    for obj, hints in object_hint_mapping().items():
        for h in hints:
            h_tokens = set(h.lower().split())
            # Token-subset (preciso) per ogni hint; il fallback substring SOLO
            # per hint multi-parola (≥2 token): su mono-parola "h in chunk" dava
            # falsi positivi token-interni ("ora" in "lavora", bug 21/6).
            if h_tokens <= tokens or (len(h_tokens) >= 2 and h in chunk.lower()):
                detected_obj = obj
                break
        if detected_obj:
            break

    # Fallback: try canonical_object on each token
    if not detected_obj:
        for tok in tokens:
            obj = _canon_obj(tok)
            if obj:
                detected_obj = obj
                break

    if not detected_obj:
        return None

    return (verb, detected_obj)


def derive_tool_name(verb: str, obj: str, available_tools: set[str],
                     *, query: Optional[str] = None) -> Optional[str]:
    """Derive canonical tool name `<verb>_<obj>` o variante presente nel catalog.
    Universal §7.9: cerca nel pool tool registrato, no inventato.
    Preferenza: forma plain canonical (no qualifier) over qualifier variants.

    Provider-aware (GAP-B redesign, opt-in): se `query` ha un marker provider
    (`detection_lexicon provider.markers`) e esiste `<verb>_<obj>_<provider>` nel
    catalog, lo PREFERISCE al canonico generico — cosi' enforce/skeleton di un
    compound github risolvono `send_messages_github`, non `send_messages`.
    `query=None` (default) → comportamento v2 INVARIATO (i caller v2 non lo
    passano; lo passano solo i guard v3-gated)."""
    # 0. Provider-aware (opt-in): variante `_<provider>` quando il marker e'
    #    nella query — PRIMA del canonico generico (che la `1.` ritornerebbe).
    if query:
        try:
            from tool_grammar import active_provider_suffixes
            for _suffix in active_provider_suffixes(query):
                _cand = f"{verb}_{obj}{_suffix}"
                if _cand in available_tools:
                    return _cand
        except Exception:
            pass
    # 0.5 SCRITTORI format-aware (create/write): se la query nomina un formato
    #    («foglio»/csv/pdf/...) e la variante `<verb>_<obj>_<qual>` esiste,
    #    preferiscila AL canonico generico. Simmetria dei due scrittori-file:
    #    `write_files` esiste totipotente → la `1.` lo short-circuiterebbe a
    #    write GENERICO senza formato; `create_files` no → cadeva gia' nei suffix
    #    `4.`. Con questo derive(write, files, «…un foglio») → write_files_spreadsheet
    #    come create (FIX-5, turn 697d1d08). No-op senza query o senza hint.
    if query and verb in ("create", "write"):
        _ql = query.lower()
        for _hint, (_o, _qual) in _FORMAT_HINTS.items():
            if _hint in _ql and f"{verb}_{obj}_{_qual}" in available_tools:
                return f"{verb}_{obj}_{_qual}"
    # 1. Exact match canonical (preferito)
    canonical = f"{verb}_{obj}"
    if canonical in available_tools:
        return canonical
    # 2. READ_FAMILY swap PRIMA dei qualifier variants (find_X canonical più
    # forte che find_X_indices/find_X_empty per query generiche).
    # Ordine FISSO (§11 routing deterministico): iterare il SET dava un
    # fratello diverso a seconda dell'hash-seed del processo (find_files vs
    # get_files fra due restart — turni 2cd8862a/68f28b01). `find` per primo:
    # il produttore-pattern più generale.
    if verb in PRODUCER_VERBS:
        for alt_verb in ("find", "read", "get", "list"):
            alt = f"{alt_verb}_{obj}"
            if alt in available_tools:
                return alt
    # 3. Generic <verb>_entries universale: TRANSFORM (describe/classify/filter/
    # sort/compute/...) + EXTRACT (extract e' in MUTATING, ma `extract_entries`
    # e' il suo universale e derive(extract,messages) deve risolverlo, non None).
    # NON i produttori (find/read/get/list): un produttore senza variante reale
    # NON deve diventare find_entries (resterebbe None → no swap spurio).
    if verb in TRANSFORM_VERBS or verb == "extract":
        generic = f"{verb}_entries"
        if generic in available_tools:
            return generic
    # 4. Suffix variants (es. write_files_doc per write+files). QUERY-AWARE:
    # preferisci la variante il cui qualifier e' suggerito dalla query («foglio»
    # → spreadsheet), non l'alfabetico (che sceglierebbe _doc < _spreadsheet).
    prefix = f"{verb}_{obj}_"
    suffix_variants = sorted(t for t in available_tools if t.startswith(prefix))
    # MAI una variante PROVIDER dal fallback generico (§2.2 asse provider): il
    # provider si sceglie SOLO allo step 0 (marker nella query). Senza questo,
    # derive(delete, messages) — delete_messages non esiste, §5 mail=move —
    # ripiegava su `delete_messages_github` per una query di POSTA (T4 5/7).
    try:
        import detection_lexicon as _dlx
        _prov_sfx = tuple(_dlx.mapping("provider.markers").keys())
    except Exception:  # noqa: BLE001
        _prov_sfx = ()
    if _prov_sfx:
        suffix_variants = [t for t in suffix_variants
                           if not t.endswith(_prov_sfx)]
    if suffix_variants:
        if query:
            ql = query.lower()
            for hint, (_o, qual) in _FORMAT_HINTS.items():
                if hint in ql and f"{verb}_{obj}_{qual}" in available_tools:
                    return f"{verb}_{obj}_{qual}"
        return suffix_variants[0]  # fallback alfabetico
    return None


def _send_has_explicit_recipient(chunk: str) -> bool:
    """True se il chunk nomina un destinatario ESPLICITO per un 'send': una
    email (`x@y.z`) o `a/ad/to <NomeProprio>` (capitalizzato). I pronomi self
    (mandaMI / inviaMI / «a me») NON contano. Bias di sicurezza: in dubbio
    False → risposta in chat, niente email indesiderata (decisione 1/6, §10.2:
    "mandami il riassunto" = rispondi in chat, non spedire un'email a sé)."""
    import re as _re
    if _re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", chunk):
        return True
    lexicon = _compound_lexicon()
    if lexicon is None:
        return False
    prepositions = _phrase_alt(
        lexicon["parser.compound.recipient_preposition"])
    if not prepositions:
        return False
    # Preposizione registrata + nome proprio CAPITALIZZATO.
    # Minuscolo ("a casa", "a quella pagina") NON è un destinatario.
    match = _re.search(
        rf"(?<!\w)(?:{prepositions})(?!\w)\s+"
        rf"(?P<name>[^\W\d_][\w'.\-]+)",
        chunk, flags=_re.UNICODE,
    )
    return bool(match and match.group("name")[0].isupper())

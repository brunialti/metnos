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
from typing import Optional

import detection_lexicon as _dl  # lessici NL traducibili (gemello i18n input)

# Connettori sequenziali: i SIMBOLI (,;&&) sono lingua-invarianti e restano
# qui; le PAROLE connettore (e/and/poi/then/...) vivono nel concept
# traducibile `compound.connector_word` (detection_lexicon). Il pattern di
# split e' ricostruito deterministicamente dalle forme della lingua corrente.


# Apostrofi (tutte le forme Unicode: ASCII, typographic, modifier-letter, grave).
# LANGUAGE-AGNOSTIC: l'apostrofo LEGA i caratteri (elisione/contrazione) in ogni
# lingua — IT «e'»/«cos'»/«l'», FR «j'»/«qu'», EN «it's»/«don't». Un connettore
# adiacente a un apostrofo è parte di una parola elisa, NON un separatore.
_APOSTROPHES = "".join(chr(c) for c in (0x27, 0x2019, 0x02BC, 0x60))  # ' ’ ʼ `


@functools.lru_cache(maxsize=8)
def _connector_pattern(_lang: str) -> "re.Pattern":
    words = _dl.forms("compound.connector_word")
    alt = "|".join(words) if words else "e|and"
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
_FORMAT_HINTS = {
    "foglio di calcolo": ("files", "spreadsheet"),
    "foglio elettronico": ("files", "spreadsheet"),
    "foglio": ("files", "spreadsheet"),
    "fogli": ("files", "spreadsheet"),
    "spreadsheet": ("files", "spreadsheet"),
    "excel": ("files", "spreadsheet"),
    "xlsx": ("files", "xlsx"),
    "xls": ("files", "xlsx"),
    "csv": ("files", "csv"),
    "pdf": ("files", "pdf"),
    "doc": ("files", "doc"),
    "document": ("files", "doc"),
    "documento": ("files", "doc"),
    "json": ("files", "json"),
    "xml": ("files", "xml"),
    "html": ("files", "html"),
    "markdown": ("files", "md"),
    "md": ("files", "md"),
    "txt": ("files", "txt"),
    "text": ("files", "txt"),
}
TRANSFORM_VERBS = {"filter", "sort", "group", "classify", "describe",
                    "render", "compute", "compare"}


def split_query_chunks(query: str) -> list[str]:
    """Split query su connettori sequenziali universali. Ritorna chunks
    non vuoti puliti."""
    if not query or not query.strip():
        return []
    parts = _connector_pattern(_dl.current_lang()).split(query)
    return [p.strip() for p in parts if p.strip()]


# Nomi-campo: articoli/preposizioni-composto da scartare (IT+EN), lessico curato.
# `di/of/d` sono STOP (scartati) ma NON tagliano («numero d'ordine»→«numero ordine»).
_FIELD_STOP = {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "dei",
               "degli", "delle", "del", "dello", "della", "di", "da", "d", "l",
               "a", "ad", "ogni", "the", "an", "of", "each", "every", "its",
               "their"}
# Preposizioni che introducono una FRASE-sorgente/scope → TAGLIANO il campo
# («title from this week's events»→«title»; «dati dalle fatture»→«dati»).
_FIELD_CUT_PREP = {"from", "in", "into", "da", "dal", "dalla", "dallo", "dai",
                   "dagli", "dalle", "nel", "nella", "nello", "nei", "negli",
                   "su", "sul", "sulla", "sui", "sulle", "about", "regarding",
                   "per", "con", "tra", "fra", "presso"}


def _clean_field_name(text: str) -> str:
    """Normalizza un frammento NL in un nome-campo: taglia alla prima prep-frase,
    scarta articoli/preposizioni-composto, max 3 parole. Deterministico §7.9."""
    text = text.replace("'", " ").replace("’", " ").lower()
    words = re.findall(r"[\w]+", text)
    kept: list[str] = []
    for w in words:
        if w in _FIELD_CUT_PREP and kept:
            break
        kept.append(w)
    kept = [w for w in kept if w not in _FIELD_STOP]
    return " ".join(kept[:3]).strip()


def derive_extract_fields(query: str) -> list[str]:
    """§7.9 deterministico: estrae i NOMI-CAMPO dalla clausola «estrai X, Y e Z»
    di una query compound. Serve a riempire `extract_entries.fields` quando il
    proposer DROPPA la clausola e il guard `_ensure_extract_clause` la re-inserisce
    (bug live 22/6: «...estrai titolo e orario...» → extract_entries SENZA fields →
    «missing 'fields'»). Robustezza NL→determinismo §2.4: la clausola e' spezzata
    dai connettori (anche «e» DENTRO la lista campi) → i chunk SENZA verbo sono
    continuazioni della clausola-extract. Ritorna [] se non c'e' clausola extract
    (il caller mantiene il comportamento attuale). Niente LLM."""
    try:
        from prefilter import (tokenize as _tok,
                               detect_canonical_verbs_all as _verbs)
    except Exception:
        return []
    chunks = split_query_chunks(query)
    if not chunks:
        return []
    ann = [(ch, (_verbs(_tok(ch)) or [None])[0]) for ch in chunks]
    n = len(ann)
    fields: list[str] = []
    for i, (ch, v) in enumerate(ann):
        if v != "extract":
            continue
        # primo chunk: scarta la PAROLA-verbo iniziale (es. «estrai»/«extract»).
        first = _clean_field_name(" ".join(re.findall(r"[\w']+", ch)[1:]))
        if first:
            fields.append(first)
        # continuazioni: chunk seguenti SENZA verbo (resto della lista campi).
        j = i + 1
        while j < n and ann[j][1] is None:
            f2 = _clean_field_name(ann[j][0])
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
    return out


def detect_chunk_action(chunk: str) -> Optional[tuple[str, str]]:
    """Detect (verb, object) canonical per un chunk di query.
    Ritorna None se nessun verbo canonico o object derivabile.

    Universal §7.9: usa vocab esistenti, no patterns hardcoded.
    """
    try:
        from prefilter import (
            tokenize,
            detect_canonical_verbs_all,
            _OBJECT_HINTS,
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
    verb = verbs[0]

    # 2. Detect object canonico
    # Try _OBJECT_HINTS first (più ricco)
    detected_obj = None
    for obj, hints in _OBJECT_HINTS.items():
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
    # 1. Exact match canonical (preferito)
    canonical = f"{verb}_{obj}"
    if canonical in available_tools:
        return canonical
    # 2. READ_FAMILY swap PRIMA dei qualifier variants (find_X canonical più
    # forte che find_X_indices/find_X_empty per query generiche).
    if verb in PRODUCER_VERBS:
        for alt_verb in PRODUCER_VERBS:
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
    # 'a'/'ad'/'to' + nome proprio CAPITALIZZATO (es. "manda a Roberto").
    # Minuscolo ("a casa", "a quella pagina") NON è un destinatario.
    if _re.search(r"\b(?:a|ad|to)\s+[A-ZÀ-Þ][\wÀ-ÿ'.\-]+", chunk):
        return True
    return False


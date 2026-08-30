#!/usr/bin/env python3
"""
prefilter.py — selezione dei candidati executor (Metnos v1.1 POC).

Implementazione bag-of-words sull'`affinity` dichiarato nei manifest. La forma
con embedding MiniLM (decisa in F1=b) e' rimandata a una iterazione successiva
del POC: il bag-of-words e' un placeholder funzionante per validare la forma
del flusso "user query -> top-K -> LLM con catalogo ristretto".

Bootstrap statico (F5=c): solo affinity tags day-1; quando il mnestoma esistera'
il punteggio sara' affinity_match + history_boost.

K adattivo (deciso 26/4/2026 sera dopo stress test D-tools):
    Quando il prefilter ha alta confidenza (top-1 nettamente sopra gli altri),
    K si abbassa a K_min. Quando ha bassa confidenza (score molto vicini fra
    primi e successivi), K si alza fino a K_max. Razionale: ottimizzare il
    trade-off recall/precision contro la latenza/distrazione del LLM.

API:
    rank(query, catalog, k=10) -> list[Executor]                     (legacy)
    rank_adaptive(query, catalog, k_min=5, k_max=40) -> (list, info) (preferita)
"""
import re

from logging_setup import get_logger
log = get_logger(__name__)

# Parole Unicode senza underscore: conserva la storica separazione degli
# identifier tecnici (`read_files` -> read, files) e riconosce al contempo
# grafemi di qualunque lingua latina/non latina supportata dal catalogo.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text):
    return set(_WORD_RE.findall((text or "").lower()))


# Concept identifiers are protocol, not linguistic data.  Their payloads
# live exclusively in detection_lexicon_seed and remain dynamic at call time.
_PREFILTER_VERB_CONCEPT = "prefilter.verb_canonical"
_PREFILTER_OBJECT_CONCEPT = "prefilter.object_hint"
_PREFILTER_STOPWORD_CONCEPT = "prefilter.stopword"
_PREFILTER_CLITIC_CONCEPT = "prefilter.italian_clitic_suffix"
_PREFILTER_LOCATION_RELATIVE_CONCEPT = "prefilter.location_relative"
_PREFILTER_EXIF_CONCEPT = "prefilter.exif_marker"
_PREFILTER_TIME_CONCEPT = "prefilter.time_intent"
_PREFILTER_GENERIC_AFFINITY_CONCEPT = "prefilter.generic_affinity_verb"

# Canonical protocol order is significant only for the historical score
# tie-break. It contains no natural-language surface and therefore remains in
# the algorithmic consumer rather than in the translatable payload.
_PREFILTER_OBJECT_ORDER = (
    "messages", "files", "images", "dirs", "location", "places", "now",
    "urls", "numbers", "texts", "packages", "events", "calendars",
    "contacts", "processes", "signatures", "proposals",
)


def _detection_mapping(concept: str) -> dict[str, list[str]]:
    import detection_lexicon as _detlex

    mapping = _detlex.mapping(concept)
    return {
        str(canonical): [str(form) for form in forms if str(form)]
        for canonical, forms in mapping.items()
        if isinstance(forms, list)
    }


def verb_to_canonical_mapping() -> dict[str, str]:
    """Localized surface -> canonical action, omitting ambiguous surfaces."""
    owners: dict[str, set[str]] = {}
    for canonical, surfaces in _detection_mapping(
            _PREFILTER_VERB_CONCEPT).items():
        for surface in surfaces:
            token = surface.strip().casefold()
            if _WORD_RE.fullmatch(token):
                owners.setdefault(token, set()).add(canonical)
    return {
        token: next(iter(canonicals))
        for token, canonicals in owners.items()
        if len(canonicals) == 1
    }


def object_hint_mapping() -> dict[str, list[str]]:
    """Localized object hints with one exact compatibility baseline.

    The historical list contains one intentional duplicate that contributes
    to hit cardinality.  ``detection_lexicon.mapping`` correctly de-duplicates
    ordinary forms, so this scoring consumer reads the versioned resources and
    merges a partially materialized active language over exactly one baseline.
    """
    import detection_lexicon as _detlex

    current = _detlex.current_lang()
    native = _detlex.resource_for_language(
        _PREFILTER_OBJECT_CONCEPT, current, fallback=False, ready_only=True,
    )
    baselines = _detlex.baseline_languages(_PREFILTER_OBJECT_CONCEPT)
    candidates = [
        _detlex.resource_for_language(
            _PREFILTER_OBJECT_CONCEPT, lang, fallback=False, ready_only=True,
        )
        for lang in baselines
        if lang != current
    ]

    def _resource_size(resource) -> int:
        payload = resource.get("payload") if resource else None
        return sum(
            len(forms) for forms in payload.values()
            if isinstance(forms, list)
        ) if isinstance(payload, dict) else -1

    baseline = max(candidates, key=_resource_size, default=None)
    if native is None:
        native = baseline
        baseline = None

    def _payload(resource) -> dict[str, list[str]]:
        if (not resource or resource.get("kind") != "mapping"
                or not isinstance(resource.get("payload"), dict)):
            return {}
        return {
            str(canonical): [str(form) for form in forms if str(form)]
            for canonical, forms in resource["payload"].items()
            if isinstance(forms, list)
        }

    primary = _payload(native)
    fallback = _payload(baseline)
    merged = {canonical: list(forms) for canonical, forms in primary.items()}
    if fallback:
        for canonical, forms in fallback.items():
            if canonical not in merged:
                merged[canonical] = list(forms)
                continue
            for form in forms:
                if form not in merged[canonical]:
                    merged[canonical].append(form)
    ordered = {
        canonical: merged[canonical]
        for canonical in _PREFILTER_OBJECT_ORDER if canonical in merged
    }
    ordered.update({
        canonical: merged[canonical]
        for canonical in sorted(set(merged) - set(ordered))
    })
    return ordered


def stopwords() -> frozenset[str]:
    """Localized stopwords plus the IT/EN compatibility baselines."""
    import detection_lexicon as _detlex

    return frozenset(str(form).casefold() for form in
                     _detlex.forms(_PREFILTER_STOPWORD_CONCEPT) if form)


def italian_clitic_suffixes() -> tuple[str, ...]:
    """Localized suffix forms, longest first for deterministic stripping."""
    import detection_lexicon as _detlex

    forms = [str(form).casefold() for form in
             _detlex.forms(_PREFILTER_CLITIC_CONCEPT) if form]
    return tuple(sorted(dict.fromkeys(forms), key=lambda form: -len(form)))


def generic_affinity_verbs() -> frozenset[str]:
    """Localized action tokens excluded from distinctive affinity scoring."""
    import detection_lexicon as _detlex

    return frozenset(str(form).casefold() for form in
                     _detlex.forms(_PREFILTER_GENERIC_AFFINITY_CONCEPT) if form)


def _localized_verb_table() -> dict[str, str]:
    """Tabella operativa: risorse RM-0005 specifica + action vocabulary.

    Il detection lexicon unisce la lingua del turno alle lingue seed e si
    invalida quando una traduzione viene materializzata. Le forme non ambigue
    entrano automaticamente; una polisemia non viene mai risolta scegliendo
    un'azione per ordine di dizionario. Le preferenze storiche deliberate sono
    parte del payload versionato del concept specifico.
    """
    table = verb_to_canonical_mapping()
    try:
        from vocab import action_recognition_mapping
    except Exception:  # pragma: no cover - bootstrap minimale
        return table
    mapping = action_recognition_mapping()
    candidates: dict[str, set[str]] = {}
    for canonical, surfaces in mapping.items():
        if not isinstance(surfaces, list):
            continue
        for surface in surfaces:
            token = str(surface or "").strip().casefold()
            # Il tokenizer del prefilter lavora su token semplici; frasi e
            # forme con trattino restano al parser d'intento.
            if not _WORD_RE.fullmatch(token):
                continue
            candidates.setdefault(token, set()).add(canonical)
    for token, canonicals in candidates.items():
        if len(canonicals) == 1:
            table.setdefault(token, next(iter(canonicals)))
    return table

try:
    from vocab import SAFE_VERBS as _SAFE_VERBS_SET
except Exception:  # pragma: no cover - bootstrap minimale senza vocab
    _SAFE_VERBS_SET = frozenset()


def implements_intent_verb(candidate_verb: str, intent_verb: str) -> bool:
    """True se `candidate_verb` puo' realizzare un intento espresso con
    `intent_verb`. Concetto astratto, nessun lessico e nessuna lingua: si
    ragiona solo su token del vocabolario chiuso.

    IL VERBO ESATTO NON E' UN DATO, E' UNA CONGETTURA. L'estrattore proietta la
    richiesta su UN verbo canonico, e la proiezione e' 1:1 quindi lossy:
    «metti/salva» finisce su `write` anche quando l'operazione giusta e'
    `create`, «togli / azzera / rimetti al valore predefinito» finisce su `set`
    anche quando e' `delete`. Chiudere il pool sul verbo esatto fa sparire dal
    catalogo tool che esistono, e il planner risponde onestamente «non esiste
    un tool per togliere una preferenza» pur avendone uno (bug spreadsheet
    2-3/6/2026; E2E preferenze 29/7/2026).

    QUELLO CHE INVECE REGGE E' LA CLASSE. Su quale verbo mutante l'estrattore
    sbaglia spesso — `set`, `delete`, `change`, `move` sono tutti plausibili
    per la stessa frase — ma sul fatto che la richiesta CAMBI qualcosa non
    sbaglia. Quindi sul lato che muta il pool si chiude sulla classe, e il
    verbo esatto resta il punteggio piu' alto (+10): recall dalla classe,
    precisione dal verbo. La domanda «questa richiesta cambia qualcosa?» ha la
    stessa risposta in ogni lingua, e infatti qui non compare nessuna parola.

    Sul lato di SOLA LETTURA il verbo esatto resta un cancello: li' i verbi non
    si confondono fra loro e allargare peggiora. Misurato sul corpus dei turni
    reali (873 query, confronto sui pool costruiti dalla funzione vera):
      - classe sui DUE lati        -> 9 cambi del primo classificato, tutti
                                      regressioni (`get_now` -> `sort_entries`,
                                      `describe_entries` -> `find_files_github`)
      - classe sul SOLO lato mutante -> 13,3% dei pool toccati, 0 tool persi,
                                      0 cambi del primo classificato
    La stessa misura mostra che la tabella di coppie scritta a mano che stava
    qui (`write`<->`create`) diventa ridondante: rimossa, 0 tool persi e 0
    cambi di top-1, e `create_*` resta raggiungibile da un intento `write`.
    """
    if not candidate_verb or not intent_verb:
        return False
    if candidate_verb == intent_verb:
        return True
    if intent_verb in _SAFE_VERBS_SET:
        return False
    return candidate_verb not in _SAFE_VERBS_SET


_DIRECT_MESSAGE_RE = re.compile(
    r"^\s*(?:scrivi|write)\s+(?:a|to)\s+"
    r"(?![/\\])[^:\r\n]{1,80}\s*:\s*\S",
    re.IGNORECASE,
)


def is_direct_message_query(query: str | None) -> bool:
    """Riconosce un destinatario esplicito seguito dal corpo del messaggio.

    `scrivi a <destinatario>: <testo>` / `write to <recipient>: <text>` e'
    messaggistica, non scrittura su filesystem. Pretendere INSIEME il
    separatore destinatario/corpo e un bersaglio che non sia un path tiene le
    normali `write to /path` nel dominio dei file. Confine deterministico
    (§7.9), riusato dal gate di smoke e dalle strategie alternative di
    prefilter.
    """
    return bool(query and _DIRECT_MESSAGE_RE.match(query))


def detect_canonical_verb(qtokens, query: str | None = None):
    """Ritorna il primo verbo canonico (move/delete/read/...) trovato fra i
    token della query, o None. Importante per boost del prefilter.

    `query` e' opzionale: quando c'e', il confine strutturale
    `is_direct_message_query` vince sul token, perche' «scrivi a X: ...» e'
    un invio, non una scrittura.
    """
    if is_direct_message_query(query):
        return "send"
    verb_table = _localized_verb_table()
    for tok in sorted(qtokens):
        v = verb_table.get(tok)
        if v:
            return v
    return None


def _strip_italian_clitic(tok: str) -> str | None:
    """Universal §7.9: rimuovi clitico pronome IT. Ritorna stem o None."""
    for suf in italian_clitic_suffixes():
        if tok.endswith(suf) and len(tok) > len(suf) + 2:
            return tok[:-len(suf)]
    return None


def detect_canonical_verbs_all(qtokens) -> list[str]:
    """Ritorna TUTTI i verbi canonici distinti trovati fra i token, in ordine
    ALFABETICO dei token (deterministico §7.9 — `qtokens` e' un set prodotto
    da `tokenize`, quindi l'ordine di apparizione nella frase NON e'
    ricostruibile qui). Usato per detection multi-step (es. «fissa
    appuntamento e mandami email» -> ['create', 'send']). Lista vuota se
    nessun verbo.
    Generale: deriva dai sinonimi presenti nel detection lexicon, non da un
    caso d'uso hardcoded.

    Italian clitic stripping (§7.9 universal): "mettili"→"metti", "inviamelo"
    →"invia". Cattura clitici pronominali standard IT.
    """
    seen = []
    verb_table = _localized_verb_table()
    for tok in sorted(qtokens):
        v = verb_table.get(tok)
        if not v:
            # Try clitic stripping (mettili → metti, inviamelo → invia)
            stem = _strip_italian_clitic(tok)
            if stem:
                v = verb_table.get(stem)
        if v and v not in seen:
            seen.append(v)
    return seen


## Domain (web) detector: regex strutturale (non hardcoded TLD list).
#
# Riconosce un dominio web in una query libera (`scuola.edu.it`,
# `repubblica.it`, `metnos.com`) senza enumerare TLD. Anti-pattern §7.3:
# se domani arriva `.health` o `.school`, niente lista da aggiornare.
#
# Pattern: `<label>.<tld>[.<tld2>]` con label alfanumerico + TLD 2-24 char
# alfabetici. Filtri di scarto:
#   - estensioni filesystem comuni → e' un path, non un dominio
#   - version-numbers (`v1.2`, `1.2.3`) → label e/o TLD numerico
#   - timestamp date (`2026.01.15`) → label numerico
#
# Caso live 5/5/2026: `cerca in scuola.edu.it organico` deve → urls;
# `leggi /tmp/file.jpg` NON deve → urls.
_FS_EXTENSIONS = frozenset({
    "txt", "md", "py", "rs", "go", "c", "h", "cpp", "hpp", "js", "ts", "tsx",
    "jsx", "java", "kt", "swift", "rb", "php", "sh", "bash", "zsh", "fish",
    "json", "yaml", "yml", "toml", "ini", "cfg", "conf", "env",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "tsv",
    "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "heic", "svg",
    "mp3", "wav", "flac", "ogg", "m4a", "aac",
    "mp4", "mkv", "mov", "avi", "webm", "wmv",
    "zip", "gz", "tar", "bz2", "xz", "7z", "rar",
    "log", "bak", "tmp", "swp", "lock",
    "html", "htm", "xml", "css", "scss", "less",
    "sql", "db", "sqlite", "sqlite3",
    "pyc", "pyo", "o", "so", "dll", "dylib", "exe",
})

# Pattern: parola alfanumerica con almeno una lettera + . + label-tld.
# Negative lookbehind `(?<![/\w\-])` evita match su path tipo `/tmp/x.jpg`
# (stop dopo `/`) e su parti di identifier `foo.x.jpg`. Lookbehind `[\w\-]`
# include `_-` perche' il domain pattern non li ha mai come carattere finale.
_DOMAIN_RE = re.compile(
    r"(?<![/\w\-])"                                # not preceded by path-sep or word char
    r"(?P<label>[a-z0-9][a-z0-9\-]{0,62})"         # primary label
    r"\."
    r"(?P<tld>[a-z]{2,24})"                        # primary TLD: alfabetico puro
    r"(?:\.(?P<tld2>[a-z]{2,24}))?"                # optional ccTLD (es. ".edu.it")
    r"(?![\w\-])",                                  # not followed by word char
    re.IGNORECASE,
)


def _detect_domain_in_query(query: str) -> bool:
    """True se la query contiene una sequenza che ha forma di dominio web.

    Filtri di scarto (no false positive):
      - label puramente numerica (es. `1.2.3`, `2026.01`)
      - TLD = estensione filesystem nota (es. `file.jpg`, `notes.md`)
      - dominio 'localhost' senza punto (escluso dal pattern stesso)
    """
    if not query:
        return False
    for m in _DOMAIN_RE.finditer(query):
        label = m.group("label")
        tld = m.group("tld").lower()
        tld2 = (m.group("tld2") or "").lower()
        # filtro 1: label tutta numerica → version o date
        if label.isdigit():
            continue
        # filtro 2: TLD primario in lista estensioni filesystem
        # (`.jpg`, `.md`, `.py`, ...). Il caso `.com` / `.it` / `.edu` non
        # e' tra le estensioni e quindi passa.
        # Nota: se c'e' un secondo TLD (`.edu.it`), il primo TLD `edu` non
        # e' un'estensione → passa, e per scrupolo verifichiamo anche tld2.
        if tld in _FS_EXTENSIONS:
            continue
        if tld2 and tld2 in _FS_EXTENSIONS:
            continue
        return True
    return False


def detect_canonical_object(qtokens, query: str | None = None):
    """Ritorna l'oggetto canonico (es. 'messages', 'files') matchato nei
    token della query. Conta le occorrenze e ritorna quello con piu' hit.

    Layer 2 (5/5/2026): se la query contiene un dominio web (`scuola.edu.it`,
    `metnos.com`), object='urls' viene OVERRIDE. Generale per qualsiasi TLD,
    sostituisce la vecchia lista hardcoded di TLD negli object hint.
    """
    scores = {}
    for obj, hints in object_hint_mapping().items():
        hit = sum(1 for h in hints if h in qtokens)
        if hit:
            scores[obj] = hit
    # Override strutturale: presenza di dominio web → urls.
    if query and _detect_domain_in_query(query):
        # Add+force: se urls non era detected via token, lo introduciamo;
        # se lo era, ne aumentiamo lo score per assicurarci che vinca il
        # tie-break con altri object eventualmente menzionati.
        scores["urls"] = scores.get("urls", 0) + 5
    if not scores:
        return None
    # Tie-break: il primo nel dict order (Python 3.7+ preserva ordine).
    return max(scores.keys(), key=lambda o: scores[o])


def affinity_score(query_tokens, executor, *,
                   query_canonical_verb=None, query_canonical_object=None,
                   query_canonical_verbs=None,
                   query_raw=None):
    """Score con preferenza forte al VERBO CANONICO della query, all'oggetto
    canonico, e all'affinity (verbi/azioni dichiarati nel manifest);
    soft-match cap-ato sui token rari.

    Pesi:
    - VERB BOOST: +10 se il nome dell'executor e' `<canonical>_*` (es. query
      "sposta..." → boost a tutti i `move_*`).
    - OBJECT BOOST: +6 se il nome contiene `<canonical_object>` (suffisso o
      qualifier). Disambigua fra move_files e move_messages.
    - hard match (token query ∈ tag affinity): peso 4 per token.
    - soft match (token query ∈ tokenize(description) escluse stopwords e i
      token gia' contati come hard): peso 1, cappato a 3 (per evitare che
      description verbose dominino).

    §7.3 Task #41 (28/5/2026) — opt-in METNOS_PREFILTER_RULES=1 attiva 4 rule
    aggiuntive portate da e2e/simulator (path-promote, query-pattern, producer
    compat, rare-token penalty). Bench 446q baseline: prefilter top-1 47% →
    atteso 65-75% post-rules. Vedi runtime/prefilter_rules.py.

    Riferimento al caso live 29/4/2026: query "sposta in Posta indesiderata le
    mail" privilegiava read_messages (description ricca + affinity over-tagged)
    su move_messages. Il verb-boost+object-boost risolve.
    """
    aff_tokens = set()
    for tag in executor.affinity:
        aff_tokens.update(tokenize(tag))
    desc_tokens = tokenize(executor.description)
    hard_matches = query_tokens & aff_tokens
    hard = len(hard_matches) * 4
    soft_pool = (query_tokens & desc_tokens) - hard_matches - stopwords()
    soft = min(len(soft_pool), 3)
    verb_boost = 0
    canonical_verbs = tuple(query_canonical_verbs or (
        (query_canonical_verb,) if query_canonical_verb else ()))
    if canonical_verbs:
        _first = executor.name.split("_", 1)[0]
        if _first in canonical_verbs:
            verb_boost = 10
        elif any(implements_intent_verb(_first, verb)
                 for verb in canonical_verbs):
            # Stessa classe, verbo diverso: qui il verbo e' gia' solo un boost
            # (nessuno viene escluso), quindi il fratello non prende nulla e
            # decidono affinity e oggetto.
            verb_boost = 0
    object_boost = 0
    if query_canonical_object:
        # Match se l'object canonico e' parte del nome dell'executor (es.
        # "messages" in "move_messages" o "files" in "read_files_csv").
        name_parts = executor.name.split("_")
        if query_canonical_object in name_parts:
            object_boost = 6
    base = hard + soft + verb_boost + object_boost

    # §7.3 opt-in rule porting da simulator
    import os
    if os.environ.get("METNOS_PREFILTER_RULES", "0") == "1" and query_raw:
        try:
            from prefilter_rules import (compute_rule_boost,
                                          compute_rare_penalty)
            rule_boost = compute_rule_boost(
                query_raw, query_tokens, query_canonical_verb, executor)
            rare_pen = compute_rare_penalty(query_tokens, executor)
            base += rule_boost + rare_pen
        except Exception as _e:  # §2.8 no silent failure
            log.warning("prefilter_rules failed for %s: %s",
                        executor.name, _e)

    return base


def rank(query, catalog, k=10, min_score=1):
    """Forma legacy (K fisso). Usata da test esistenti."""
    catalog = _filter_dormant(catalog)
    qtokens = tokenize(query)
    if not qtokens:
        return list(catalog)[:k]
    canonical_verbs = detect_canonical_verbs_all(qtokens)
    canonical_verb = canonical_verbs[0] if canonical_verbs else None
    canonical_object = detect_canonical_object(qtokens, query)
    # §7.3 Task #41: init rare-tokens cache (idempotente) per rule penalty
    import os as _os_pref
    if _os_pref.environ.get("METNOS_PREFILTER_RULES", "0") == "1":
        try:
            from prefilter_rules import init_rare_tokens
            init_rare_tokens(catalog)
        except Exception as _e:  # §2.8 no silent failure
            log.warning("init_rare_tokens failed: %s", _e)
    scored = [(affinity_score(qtokens, e,
                              query_canonical_verb=canonical_verb,
                              query_canonical_verbs=canonical_verbs,
                              query_canonical_object=canonical_object,
                              query_raw=query), e)
              for e in catalog]
    scored.sort(key=lambda p: (-p[0], getattr(p[1], "name", "")))
    above = [e for s, e in scored if s >= min_score]
    if above:
        return above[:k]
    return [e for _, e in scored[:k]]


def _confidence(scores):
    """
    Misura della confidenza del prefilter, in [0, 1].
    1 = top-1 domina nettamente; 0 = scores ravvicinati o tutti zero.
    Heuristica:
        - se top-1 == 0 (nessun match): confidenza = 0
        - se top-1 > 0 e top-2 == 0: confidenza = 1 (dominio assoluto)
        - altrimenti: (top-1 - top-2) / top-1
    """
    if not scores or scores[0] == 0:
        return 0.0
    if len(scores) < 2 or scores[1] == 0:
        return 1.0
    return max(0.0, (scores[0] - scores[1]) / scores[0])


def adaptive_k(scores, k_min=5, k_max=40):
    """
    Calcola K dato il vettore di scores ordinato decrescente.

    Politica:
        confidenza alta (>= 0.7)  -> K = k_min   (top-1 chiaro, basta poco)
        confidenza media (0.3-0.7)-> K interpolato linearmente fra min e max
        confidenza bassa (< 0.3)  -> K = k_max   (scegli largo, lascia decidere al LLM)

    In aggiunta, K non puo' eccedere il numero di score >= 1 (no padding di tools
    irrilevanti); se TUTTI gli score sono zero, ritorna k_min comunque.
    """
    conf = _confidence(scores)
    if conf >= 0.7:
        K = k_min
    elif conf <= 0.3:
        K = k_max
    else:
        # interpola: conf=0.7 -> k_min, conf=0.3 -> k_max
        frac = (0.7 - conf) / 0.4
        K = int(k_min + frac * (k_max - k_min))
    n_useful = sum(1 for s in scores if s >= 1)
    if n_useful > 0:
        # Cap su n_useful: non riempire con tools a score zero (dispersivo).
        K = min(K, n_useful)
    else:
        # Nessun match: floor a k_min ma non oltre la dimensione del catalogo.
        K = min(k_min, len(scores))
    K = min(K, len(scores))
    return K, conf


# Vocabolario classificato — importato da vocab.py (single source of truth).
# Aggiungere/togliere un verbo dalle classi si fa in vocab.py.
from vocab import (
    PRECURSOR_VERBS as _PRECURSOR_VERBS,
    PRODUCER_VERBS as _PRODUCER_VERBS,
)
# Tool di manipolazione dati: utili come step intermedi in QUASI tutti i pipeline.
# Cross-tool dependencies query-driven: alcuni tool hanno una semantica che
# richiede UN ALTRO tool come precursor SOLO se la query ha un certo marker.
# Esempio: find_places funziona stand-alone per query con luogo esplicito
# ("ristoranti a Brescia"), ma per query location-relative ("vicino a me",
# "qui", "intorno") richiede get_location prima per risolvere il "me" in
# coordinate. Il PLANNER prompt §5 prescrive get_location per "DOVE-SONO",
# ma se get_location non e' nei candidati esposti il modello non puo' chiamarlo.
# Regole come tuple (consumer_name, provider_name, query_markers).
# Tool "stella" per oggetto: per ogni OBJECT del vocabolario c'e' un
# executor primario che va INCLUSO nei candidati anche se l'intent del
# turno ha picked un verbo diverso. Es. per "places" il primario e'
# find_places: vale sia per query "trova/cerca/find" sia per query
# ellittiche o senza verbo ("farmacia piu vicina") che l'intent extractor
# mappa a verbo generico (get/list). Senza injection il PLANNER vedrebbe
# solo i get_* del dominio, non riconoscerebbe find_places, e attiverebbe
# request_new_executor su un nome che esiste gia' (caso live 1/5/2026).
_OBJECT_PRIMARY_TOOLS = {
    # Iniezione automatica nel pool dei top-K quando l'object e' detectato
    # nella query: garantisce che il PLANNER veda l'executor canonico
    # PRIMA di scivolare a `request_new_executor` (caso ricorrente 4/5/2026:
    # "Quante istanze di claudio sono running" non includeva get_processes
    # → synt scattava su executor gia' esistente).
    "places":    ("find_places",),
    "processes": ("get_processes",),
    "messages":  ("read_messages", "send_messages",
                   "move_messages", "find_messages"),
    "persons":   ("get_persons", "set_persons",
                   "find_persons_indices", "delete_persons"),
    "tasks":     ("list_tasks", "read_tasks", "create_tasks",
                   "delete_tasks", "set_tasks", "read_tasks_history"),
    "files":     ("find_files", "find_files_hash", "read_files", "get_files"),
    "dirs":      ("list_dirs", "find_dirs"),
    "urls":      ("find_urls", "get_urls", "read_urls_html", "read_urls_pdf"),
    # Calendar events (Google Workspace skill, importati 10/5/2026,
    # rinominato ADR 0128 12/5/2026: set_events -> create_events).
    # create_events (crea), read_events (lettura), delete_events (cancella).
    "events":    ("create_events", "read_events", "delete_events"),
    "calendars": ("create_calendars", "delete_calendars"),
    # Contatti Google Workspace (read_contacts dal skill):
    "contacts":  ("read_contacts",),
    "images":    ("find_images_indices", "change_images", "find_files",
                   "find_files_hash", "get_files"),
    "packages":  ("find_packages",),  # canonical handcrafted name (no get_packages)
    "numbers":   (),  # niente primary, lascia al ranker
    "texts":     ("read_files", "filter_texts_lines"),
    "signatures": ("get_signatures",),
    # ADR 0090 (4-5/5/2026): get_inputs e' il motore UI dichiarativo per
    # raccolta valori dall'utente (dialog/form/voice). Iniezione su query
    # come "chiedimi", "dialogo", "form", "modulo": il PLANNER vede subito
    # il tool canonico invece di scivolare a request_new_executor.
    "inputs":    ("get_inputs",),
}

_QUERY_DEPENDENT_PRECURSOR_CONCEPTS = (
    ("find_places", "get_location", _PREFILTER_LOCATION_RELATIVE_CONCEPT),
)


# EXIF-intent markers (4/6/2026): `get_files` (azione_oggetto = get+files, il
# tool EXIF/dates/place/gps/device) va iniettato nel pool SOLO quando la query
# riguarda i METADATI di scatto di una foto, NON per query generiche su file
# (es. "elenca i file con la dimensione" → get_files NON serve, find_files ha
# gia' size → evita il misroute get_files(fields=["size"]) che e' enum-invalid).
# Deterministico §7.9: substring match. Vedi core-rule §5 EXIF→get_files.
# Shell-intent hints (ADR 0088): query con questi marker triggerano
# l'iniezione automatica di `admin` nel pool top-K. Il pianificatore
# vede admin come tool ordinario, lo seleziona, il vaglio always-on
# emette la carta dialog manager.
#
# Detection: word-boundary regex (12/5/2026). Match per substring naive
# generava falsi positivi disastrosi (es. "ferma" matchava "conferma",
# "afferma", "fermata"; "share" matchava "shared" e cosi' via; "kill"
# matchava "skill"). Con `\b...\b` la condizione e' "token intero".
# Gli hint multi-word (ip route, comando shell, log di sistema) restano
# match come frase intera grazie a `\b` alle estremita'. Determinismo
# §7.9: regex compilata, niente LLM.
def _detect_time_intent(qlow: str) -> bool:
    """True se la query chiede ora/data corrente. Match word-boundary."""
    import detection_lexicon as _detlex

    return _detlex.match(_PREFILTER_TIME_CONCEPT, qlow or "")


def _detect_shell_intent(qlow: str) -> bool:
    """Whether an asserted shell/admin form exists in the active registry."""
    try:
        import detection_lexicon as _detlex
        from safety.canonicalize import command_grammar_binaries

        query = qlow or ""
        names = {
            str(name).casefold() for name in command_grammar_binaries() if name
        }
        matches = list(_COMMAND_TOKEN_RE.finditer(query))
        tokens = [match.group(0).casefold() for match in matches]
        groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for begin, stop in _detlex.phrase_spans("admin.shell_intent", query):
            if _detlex.asserted_at(query, begin, command_scope=True):
                group_start = max(
                    (query.rfind(boundary, 0, begin) + 1
                     for boundary in ",.;:!?"),
                    default=0,
                )
                groups.setdefault(
                    (group_start, _clause_end(query, begin)), [],
                ).append((begin, stop))
        for _clause, spans in sorted(groups.items()):
            identities = {
                identity
                for begin, stop in spans
                if (identity := _shell_operation_identity(
                    query, begin, stop, matches, tokens, names,
                )) is not None
            }
            if not _later_polarity_revokes_operation(
                    query, identities, max(stop for _begin, stop in spans),
                    matches, tokens, names):
                return True
        return False
    except Exception as exc:
        log.warning("shell intent lexicon unavailable: %s", exc)
        return False


_COMMAND_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+-]*", re.IGNORECASE)

# A command name is not sufficient evidence by itself: the safety grammar also
# contains binaries whose names are ordinary natural-language words (``date``,
# ``file``, ``last``, ``who``, ...).  Require a generic invocation signal near
# the binary, or an unmistakably command-line-shaped argument after a binary at
# the start of the request.  Invocation surfaces live in the translatable
# detection lexicon; adding a binary to the canonical grammar never requires
# adding that binary here.
_CLI_FIRST_ARGUMENT_RE = re.compile(
    r"\s*(?:"
    r"--?[a-z0-9]"                    # option: -c, --count
    r"|(?:\.{0,2}/|~/)[^\s]+"       # absolute/relative/home path
    r"|[a-z_][a-z0-9_]*=[^\s]+"      # key=value
    r"|(?:https?|ftp|sftp)://[^\s]+" # URL
    r"|\d{1,3}(?:\.\d{1,3}){3}\b"  # IPv4-shaped target
    r"|[a-z0-9](?:[a-z0-9-]*\.)+[a-z]{2,}\b"  # DNS-shaped target
    r")",
    re.IGNORECASE,
)


def _clause_end(query: str, start: int) -> int:
    end = len(query)
    for boundary in ",.;:!?":
        found = query.find(boundary, start)
        if found >= 0:
            end = min(end, found)
    return end


def _polarity_scope_end(query: str, start: int) -> int:
    """End of a negative scope; commas/colons may introduce target lists."""
    end = len(query)
    for boundary in ".;!?":
        found = query.find(boundary, start)
        if found >= 0:
            end = min(end, found)
    return end


def _explicit_command_targets(
        query: str, polarity_stop: int, command_matches, command_tokens,
        command_names) -> set[str]:
    """Grammar binaries explicitly scoped by a following polarity marker.

    The first binary must be structurally adjacent either to the polarity or
    to its invocation phrase.  Once that binding is established, every later
    grammar binary in the same negative scope belongs conservatively to that
    scope. Commas and colons do not end it because they commonly coordinate or
    introduce targets (``do not execute: mount, or systemctl``). This is
    deliberately fail-closed: ambiguous explanatory prose may suppress an
    admin suggestion, but cannot expose one.
    """
    import detection_lexicon as _detlex

    end = _polarity_scope_end(query, polarity_stop)
    candidates = [
        (match, token)
        for match, token in zip(command_matches, command_tokens)
        if token in command_names and polarity_stop <= match.start() < end
    ]
    for index, (match, token) in enumerate(candidates):
        invocation = _detlex.phrase_before(
            "syntax.command_invocation", query, match.start(),
        )
        if (invocation is not None and invocation[0] >= polarity_stop
                and not query[polarity_stop:invocation[0]].strip()):
            return {item for _match, item in candidates[index:]}
        # ``do not ping ...`` is also explicit: the grammar binary immediately
        # follows the polarity phrase. A word merely mentioned in a reason
        # (``do not do it because date is slow``) has intervening prose and
        # cannot neutralize an anaphoric revocation.
        if not query[polarity_stop:match.start()].strip():
            return {item for _match, item in candidates[index:]}
    return set()


def _later_polarity_revokes_operation(
        query: str, operations, after: int, command_matches,
        command_tokens, command_names) -> bool:
    """Whether later negative polarity revokes the current operation group.

    A negation or inhibition with no following grammar binary in its clause is
    anaphoric (``non farlo``, ``avoid executing it``) and therefore revokes the
    current command. If it names the same binary it also revokes it. A
    different named binary scopes the polarity to that other operation.
    """
    import detection_lexicon as _detlex

    operation_set = (
        {operations} if isinstance(operations, str) else set(operations)
    )
    unknown_identity = not operation_set

    polarity_spans = sorted({
        span
        for concept in ("syntax.negation", "syntax.inhibition")
        for span in _detlex.phrase_spans(concept, query, start=after)
    })
    for _begin, stop in polarity_spans:
        named = _explicit_command_targets(
            query, stop, command_matches, command_tokens, command_names,
        )
        if not named or unknown_identity:
            return True
        operation_set.difference_update(named)
        if not operation_set:
            return True
    return False


def _shell_operation_identity(
        query: str, begin: int, stop: int, command_matches, command_tokens,
        command_names) -> str | None:
    """Best structural identity for an asserted shell-intent span."""
    import detection_lexicon as _detlex

    surface = query[begin:stop].casefold()
    if surface in command_names:
        return surface
    end = _clause_end(query, stop)
    for match, token in zip(command_matches, command_tokens):
        if token not in command_names or not stop <= match.start() < end:
            continue
        invocation = _detlex.phrase_before(
            "syntax.command_invocation", query, match.start(),
        )
        if invocation is not None and invocation[0] == begin:
            return token
    return None


def _detect_command_grammar_intent(query: str, *, command_names=None) -> bool:
    """Whether the query invokes a command understood by the safety grammar.

    This is the generic, low-priority fallback for shell commands.  Natural
    language hints above can still promote ``admin`` strongly.  A literal
    command name is accepted only with a localized invocation phrase or,
    when it starts the request, command-line-shaped syntax.  This prevents
    grammar binaries such as ``date``, ``file`` and ``who`` from turning
    ordinary prose into administrative intent.  The result only exposes the
    guarded admin tool; it never grants permission.

    ``command_names`` is injectable so the invariant can be tested with a
    future command without editing production tables.
    """
    if command_names is None:
        try:
            from safety.canonicalize import command_grammar_binaries
            command_names = command_grammar_binaries()
        except Exception as exc:
            log.warning("command grammar unavailable for admin fallback: %s", exc)
            return False
    names = {str(name).casefold() for name in command_names if name}
    matches = list(_COMMAND_TOKEN_RE.finditer(query or ""))
    tokens = [match.group(0).casefold() for match in matches]
    for index, (match, token) in enumerate(zip(matches, tokens)):
        if token not in names:
            continue
        try:
            import detection_lexicon as _detlex
            if not _detlex.native_ready_forms(
                    "syntax.command_invocation", require_manual=True):
                continue
            invocation_span = _detlex.phrase_before(
                "syntax.command_invocation", query or "", match.start())
            asserted = (
                invocation_span is not None
                and _detlex.asserted_at(
                    query or "", match.start(), command_scope=True,
                )
            )
            revoked_later = _later_polarity_revokes_operation(
                query or "", {token}, match.end(), matches, tokens, names,
            )
        except Exception as exc:
            log.warning("command assertion guard unavailable: %s", exc)
            invocation_span = None
            asserted = False
            revoked_later = True
        if invocation_span is not None and asserted and not revoked_later:
            return True
        if index == 0 and _CLI_FIRST_ARGUMENT_RE.match(
                (query or "")[match.end():]):
            try:
                cli_asserted = _detlex.asserted_at(
                    query or "", match.start(), command_scope=True,
                )
            except Exception as exc:
                log.warning("CLI assertion guard unavailable: %s", exc)
                cli_asserted = False
            if cli_asserted and not revoked_later:
                return True
    return False


def affinity_phrase_score(query, executor) -> int:
    """Return the strongest distinctive multi-token affinity match.

    A score is intentionally available separately from pool recall so the
    deterministic routing guards can compare two already-admitted executors
    without duplicating the affinity semantics.  Single-token tags and generic
    action verbs never constitute enough evidence for a routing rewrite.
    """
    qtokens = tokenize(query) if query else set()
    if not qtokens:
        return 0
    best = 0
    for tag in (getattr(executor, "affinity", None) or []):
        distinctive = tokenize(tag) - stopwords() - generic_affinity_verbs()
        if len(distinctive) >= 2 and distinctive <= qtokens:
            best = max(best, len(distinctive))
    return best


def affinity_phrase_recall(query, catalog, *, exclude_names=frozenset(), cap=3):
    """Cross-object recall affinity-based (misroute live 10/6/2026: "quali
    account mail hai?" → read_messages legge 426 email). Causa: l'intent
    extractor classifica l'OBJECT sbagliato ("mail" domina su "account" →
    object=messages) e il pool gated per object esclude A MONTE il tool
    giusto (find_credentials, object=credentials) = RECALL miss. L'affinity
    boost di rank_with_intent non basta: riordina DENTRO il pool, non lo
    allarga.

    Criterio SCOPED (deterministico §7.9, zero dizionari per-frase): un tag
    affinity MULTI-parola (dato curato del manifest) i cui token distintivi
    (>=2 dopo aver tolto stopword e verbi generici) sono TUTTI nella query
    e' un segnale forte e intenzionale ("quali account") → il tool entra nel
    pool anche se verb/object dell'intent differiscono. Match parziale o su
    tag singola-parola NON recupera: "archivio"+"cartella" (tag separati di
    move_messages) non deve sporcare le query move_files. Cap deterministico
    (ordinamento -n_token, name) per l'igiene del pool.

    Ritorna lista executor (mai i gia' presenti in `exclude_names`)."""
    if not tokenize(query):
        return []
    hits = []
    for e in _filter_dormant(catalog):
        name = getattr(e, "name", None)
        if not name or name in exclude_names:
            continue
        best = affinity_phrase_score(query, e)
        if best:
            hits.append((best, name, e))
    hits.sort(key=lambda h: (-h[0], h[1]))
    return [e for _, _, e in hits[:cap]]


def rank_with_intent(query, catalog, intent, *, k=3):
    """Ranking quando un intent_extractor ha gia' identificato verb+object.

    Filtra il catalog per `name.startswith(verb_)`; fra i match preferisce
    quelli con object nei name_parts. Cap a `k` (default 3 — coerente con
    "max 3 candidates" — Roberto 29/4/2026).

    Per verbi CONSUMER (qualunque verbo non in _PRODUCER_VERBS — quindi
    move/delete/send/write/extract/create + describe/classify/filter/sort/
    group/render/set/compress/compute/compare) include automaticamente UN
    precursor (read/find/get/list per lo stesso object) perche' il planner
    ha bisogno di leggere/cercare prima di agire o riassumere/filtrare.
    Senza il precursor il planner chiamerebbe il verbo-finale con from_step=0
    (caso live 29/4/2026 sera + regressione 30/4/2026 mattina su
    "riassumi le mail importanti": verb=describe non era destructive,
    nessun precursor → describe_entries con history vuota).
    """
    # Skip dormant: come rank_adaptive, vedi _filter_dormant.
    catalog = _filter_dormant(catalog)
    verb = (intent or {}).get("verb")
    obj = (intent or {}).get("object")
    if not verb:
        return None  # caller fa fallback lexicon
    qtokens = tokenize(query) if query else set()
    # §7.3 opt-in: rule_boost wire-in nel path intent-driven (era applicato
    # solo nel fallback BoW). Gating env METNOS_PREFILTER_RULES=1.
    import os as _os_intent
    _rules_on = (_os_intent.environ.get("METNOS_PREFILTER_RULES", "0") == "1"
                  and query)
    _rule_fn = None
    if _rules_on:
        try:
            from prefilter_rules import compute_rule_boost, init_rare_tokens
            init_rare_tokens(catalog)
            _rule_fn = compute_rule_boost
        except Exception as _e:  # §2.8 no silent failure
            log.warning("prefilter_rules init in rank_with_intent: %s", _e)
            _rule_fn = None
    primary = []
    for e in catalog:
        parts = e.name.split("_")
        _first = parts[0] if parts else ""
        if _first == verb:
            s = 10
        elif implements_intent_verb(_first, verb):
            # Stessa classe, verbo diverso: NESSUN bonus di verbo. Entra solo
            # se oggetto, qualifier o affinity lo sostengono — il verbo esatto
            # resta preferito di 10 punti (vedi `implements_intent_verb`).
            s = 0
        else:
            continue
        declared_objects = set(
            getattr(e, "planning_object_aliases", None) or ())
        if obj and (obj in parts or obj in declared_objects):
            # L'oggetto è il confine di dominio dell'intento: un tool del
            # dominio giusto con verbo fratello deve precedere un verbo esatto
            # applicato all'oggetto sbagliato. Il match esatto verbo+oggetto
            # resta comunque nettamente primo (10 + 12).
            s += 12
        # Qualifier bonus SOLO se il qualifier matcha un token nella query
        # (es. query "leggi il csv" → bonus per read_files_csv). Altrimenti
        # il generico `read_files` deve poter battere i qualified per query
        # senza qualifier-keyword (regressione 30/4/2026: "leggi /etc/hostname"
        # picked read_files_csv per cap k=3).
        if len(parts) >= 3:
            qualifiers = parts[2:]
            if qtokens and any(q in qtokens for q in qualifiers):
                s += 2  # forte bonus se il qualifier matcha la query
            # else: nessun bonus — il generico (parts=[verb,obj]) puo' battere
        # Affinity-match boost (8/6/2026, decisione Roberto): le keyword affinity
        # sono dato CURATO e DETERMINISTICO. I token-query che matchano l'affinity
        # DISTINTIVA del tool (esclusi i verbi generici, e split degli hyphen tipo
        # "primo-piano") rompono i PAREGGI verso il tool giusto fra fratelli con
        # stesso object (es. "viso/primo piano" → find_images_indices vs
        # find_images_web, entrambi object=images). Universale §7.3, deterministico
        # §7.9. Cap +3 per non scavalcare il match verbo+object (16).
        # Normalizzatore UNICO (9/6/2026): i token affinity passano da `tokenize`
        # — lo STESSO dei qtokens — altrimenti i termini accentati del manifest
        # ("novità" → qtoken "novit") non matchano MAI per costruzione (misroute
        # live "cerca novità su <tema>": find_urls fuori pool, vinceva find_issues).
        if qtokens:
            aff_tokens = set()
            for a in (getattr(e, "affinity", None) or []):
                aff_tokens.update(tokenize(a))
            aff_tokens -= generic_affinity_verbs()
            s += min(len(qtokens & aff_tokens), 3)
        if _rule_fn is not None:
            try:
                s += _rule_fn(query, qtokens, verb, e)
            except Exception as _e:
                log.warning("rule_boost in rank_with_intent for %s: %s",
                             e.name, _e)
        primary.append((s, e))
    primary.sort(key=lambda p: (-p[0], getattr(p[1], "name", "")))

    # Se nessun executor matcha il verbo dell'intent (es. query compound dove
    # l'estrattore ha pickato un verbo intermedio come "group" senza alcun
    # group_* in catalog), l'intent e' "debole". Ricadi su bag-of-words
    # tornando None: il fallback sceglie un set piu' ampio basato su token
    # match (regressione 30/4/2026 UC3: "Trova ... raggruppa ..." → intent
    # group → solo read_files come precursor → planner privo di find_files).
    #
    # ECCEZIONE (1/5/2026): per i universal-helper verbs (classify, filter,
    # sort, describe, compute) il tool e' iniettato in-process da
    # agent_runtime, non vive nel catalog manifest. NON ritornare None:
    # procedi al precursor injection (caller comporra' classify_entries +
    # read_messages tramite il path universal-helper).
    _UNIVERSAL_HELPER_VERBS = ("classify", "filter", "sort", "describe", "compute")
    if not primary and verb not in _UNIVERSAL_HELPER_VERBS:
        return None

    # Precursor injection PRIMA del check incoherent: se il verb e' consumer
    # e c'e' obj, aggiungi i producer (read/find/list/get) dell'obj. Solo
    # cosi' un intent come `compute, dirs` (compute_* non ha dirs ma find_dirs
    # si) viene riconosciuto come coerente; senza injection prima, il check
    # successivo lo rejecterebbe.
    if verb not in _PRODUCER_VERBS and obj:
        # Verbo CONSUMER (describe, classify, filter, move, delete, send,
        # compress, write, compute, ...) → aggiungi TUTTI i precursor
        # producer del medesimo oggetto disponibili in catalog (uno per pverb
        # in _PRECURSOR_VERBS). Cap k_max=8 li accomoda.
        seen = {e.name for _, e in primary}
        for pverb in _PRECURSOR_VERBS:
            for e in catalog:
                parts = e.name.split("_")
                if parts[0] == pverb and obj in parts and e.name not in seen:
                    primary.append((5, e))   # score < primary, ma >0
                    seen.add(e.name)
                    break  # uno per pverb, non tutti i qualified

    # Object primary tools: per ogni OBJECT esistono executor "stella" che
    # vanno SEMPRE inclusi nei candidati per quell'object, anche se l'intent
    # ha picked un verbo diverso (caso 1/5/2026 "Farmacia piu vicina":
    # intent get/places → solo get_* nei candidati → find_places escluso →
    # PLANNER attiva request_new_executor su nome esistente).
    if obj and obj in _OBJECT_PRIMARY_TOOLS:
        seen_for_obj = {e.name for _, e in primary}
        for primary_name in _OBJECT_PRIMARY_TOOLS[obj]:
            if primary_name in seen_for_obj:
                continue
            primary_exec = next((e for e in catalog if e.name == primary_name), None)
            if primary_exec is not None:
                primary.append((8, primary_exec))  # score sopra precursor generici (5) sotto match diretto (10+)
                seen_for_obj.add(primary_name)

    # Cross-tool query-driven precursors: relazione tecnica locale, marker NL
    # nel detection lexicon centrale.
    # Aggiunge un provider quando il consumer e' nei candidati E la query
    # contiene un marker semantico che lo rende necessario. Es. find_places
    # + "vicino a me" → inietta get_location.
    qlow = (query or "").lower()
    seen_names = {e.name for _, e in primary}
    import detection_lexicon as _detlex_prefilter
    for cons_name, prov_name, marker_concept \
            in _QUERY_DEPENDENT_PRECURSOR_CONCEPTS:
        if cons_name not in seen_names:
            continue
        if not _detlex_prefilter.match(marker_concept, qlow):
            continue
        if prov_name in seen_names:
            continue
        prov_exec = next((e for e in catalog if e.name == prov_name), None)
        if prov_exec is not None:
            primary.append((7, prov_exec))  # score sopra precursor generici
            seen_names.add(prov_name)

    # EXIF injection condizionale (4/6/2026): get_files entra nel pool SOLO se
    # la query ha marker EXIF (scatto/gps/camera/...) e l'object e' files/images.
    # Cosi' le query EXIF-by-path lo vedono (core-rule §5), ma le query generiche
    # su file ("dimensione/elenco") NON sono tentate da get_files (che e' EXIF-only
    # → get_files(fields=["size"]) = enum-invalid, misroute 4/6). Deterministico §7.9.
    if obj in ("files", "images") and "get_files" not in seen_names \
            and _detlex_prefilter.match(_PREFILTER_EXIF_CONCEPT, qlow):
        gf = next((e for e in catalog if e.name == "get_files"), None)
        if gf is not None:
            primary.append((9, gf))  # alta priorita': intento EXIF esplicito
            seen_names.add("get_files")

    # Admin shell injection (ADR 0088, 4/5/2026): query con shell-intent
    # marker (mount/kill/systemctl/...) → admin a priorità massima.
    # Permette al PLANNER di sceglierlo invece di scivolare a
    # `request_new_executor` o produrre final_answer di resa.
    # 22/5/2026: shell-intent esteso a long-tail sysinfo (port/socket/lsmod/
    # gpu/...). Se admin gia' in seen_names (matched per affinity), lo
    # promuoviamo comunque al top — il PLANNER deve vederlo come prima
    # opzione, non al 6° posto.
    shell_intent = _detect_shell_intent(qlow)
    command_fallback = _detect_command_grammar_intent(qlow)
    if shell_intent:
        admin_exec = next((e for e in catalog if e.name == "admin"), None)
        if admin_exec is not None:
            primary = [(s, e) for s, e in primary if e.name != "admin"]
            primary.insert(0, (15, admin_exec))
            seen_names.add("admin")
    elif command_fallback and "admin" not in seen_names:
        admin_exec = next((e for e in catalog if e.name == "admin"), None)
        if admin_exec is not None:
            # A recognised command is evidence for the fallback, not evidence
            # that it should outrank a purpose-built executor.
            primary.append((1, admin_exec))
            seen_names.add("admin")

    # Time intent injection (6/5/2026): "che ore sono", "what time", etc.
    # → inietta get_now con priorità massima. Senza questo il prefilter
    # ranking BoW fallisce perche' il vocabolario obj non ha "time"
    # come oggetto canonico, e l'affinity (ora/orario) non matcha tutti
    # i casi morfologici (ore plurale, "che ora").
    if _detect_time_intent(qlow) and "get_now" not in seen_names:
        get_now_exec = next((e for e in catalog if e.name == "get_now"), None)
        if get_now_exec is not None:
            primary.insert(0, (15, get_now_exec))
            seen_names.add("get_now")

    # Check coerenza: dopo l'injection, deve esserci almeno un candidato
    # con obj in name. Se ancora nessuno (es. obj=messages ma no executor
    # con messages), fallback BoW per pesare i token della query.
    if obj and not any(obj in e.name.split("_") for _, e in primary):
        return None

    primary.sort(key=lambda p: (-p[0], getattr(p[1], "name", "")))
    # Layer 1 (5/5/2026): force-include dei primary tools dell'object oltre
    # il cap top-K. La tupla `_OBJECT_PRIMARY_TOOLS[obj]` dichiara TUTTI gli
    # executor canonici per il dominio (es. urls → find_urls, get_urls,
    # read_urls_html). Senza force-include, il top-K=8 puo' tagliare uno di
    # quelli (es. read_urls_html score 8 vs 8 verbi-match score 10) → step
    # successivo non vede il consumer. Generale: vale per ogni object detected.
    primary_names = set(_OBJECT_PRIMARY_TOOLS.get(obj, ())) if obj else set()
    head = [e for _, e in primary[:k]]
    head_names = {e.name for e in head}
    forced = []
    for _, e in primary:
        if e.name in primary_names and e.name not in head_names:
            forced.append(e)
            head_names.add(e.name)
    result = head + forced
    if command_fallback:
        admin_exec = next((e for _s, e in primary if e.name == "admin"), None)
        if admin_exec is not None and all(e.name != "admin" for e in result):
            result.append(admin_exec)
    return result


def _filter_dormant(catalog):
    """Skip executor dormant (ADR 15/5/2026): importati da skill senza
    credenziali (es. *_google_workspace pre-OAuth). Visibili in
    `metnos-skills list` per introspezione, nascosti al PLANNER. Pattern
    deterministico §7.9: attributo `dormant: bool` settato dal loader."""
    return [e for e in catalog if not getattr(e, "dormant", False)]


def rank_adaptive(query, catalog, k_min=5, k_max=8, *, llm_call=None,
                   prefer_intent=True):
    """Dispatcher modulare (17/5/2026): delega alla strategy selezionata da
    env `METNOS_PREFILTER` (default `legacy` = comportamento storico).

    Strategy registrate in `runtime/prefilter_strategies/__init__.py`.
    Per backward compat, in assenza di env var o per `METNOS_PREFILTER=
    legacy|token_flat` ritorna esattamente il comportamento di
    `_rank_adaptive_legacy` originale.

    Telemetria opt-in (`METNOS_PREFILTER_TELEMETRY=1`): logga ogni call su
    `~/.local/share/metnos/prefilter_telemetry.jsonl` per A/B compare.

    Compare mode (`METNOS_PREFILTER=compare:a,b`): esegue entrambi A e B in
    sequenza, ritorna A, ma logga B come confronto.
    """
    import os as _os
    chosen_env = _os.environ.get("METNOS_PREFILTER", "").strip().lower()
    if not chosen_env or chosen_env in ("legacy", "token_flat"):
        # Fast path: nessun overhead per il default.
        result = _rank_adaptive_legacy(
            query, catalog, k_min=k_min, k_max=k_max,
            llm_call=llm_call, prefer_intent=prefer_intent,
        )
        if _os.environ.get("METNOS_PREFILTER_TELEMETRY", "0") == "1":
            _log_telemetry("legacy", query, result)
        return result
    # Modular path
    from prefilter_strategies import select_strategy
    import time as _time
    primary_name = chosen_env
    secondary_name = None
    if chosen_env.startswith("compare:"):
        parts = chosen_env.split(":", 1)[1].split(",")
        primary_name = parts[0].strip()
        if len(parts) > 1:
            secondary_name = parts[1].strip()
    primary = select_strategy(primary_name)
    t0 = _time.perf_counter()
    result = primary.rank(query, catalog, k_min=k_min, k_max=k_max,
                          llm_call=llm_call, prefer_intent=prefer_intent)
    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
    _log_telemetry(primary.name, query, result, elapsed_ms=elapsed_ms)
    # Compare mode: lancia secondary, logga ma non ritorna.
    if secondary_name:
        try:
            secondary = select_strategy(secondary_name)
            t1 = _time.perf_counter()
            sec_result = secondary.rank(
                query, catalog, k_min=k_min, k_max=k_max,
                llm_call=llm_call, prefer_intent=prefer_intent,
            )
            sec_elapsed_ms = int((_time.perf_counter() - t1) * 1000)
            _log_telemetry(
                secondary.name, query, sec_result,
                elapsed_ms=sec_elapsed_ms, compare_against=primary.name,
            )
        except Exception as ex:
            import logging
            logging.getLogger(__name__).warning(
                "compare-mode secondary %r failed: %s", secondary_name, ex)
    return result


def _log_telemetry(strategy_name: str, query: str, result, *,
                    elapsed_ms: int | None = None,
                    compare_against: str | None = None) -> None:
    """Append JSONL telemetry record. Best-effort, fail-silent."""
    try:
        import json
        import hashlib
        import time
        candidates, route_info = result if isinstance(result, tuple) else (result, {})
        top3 = []
        for e in (candidates or [])[:3]:
            n = getattr(e, "name", None) or str(e)[:60]
            top3.append(n)
        rec = {
            "ts": time.time(),
            "strategy": strategy_name,
            "query_hash": hashlib.sha256(query.encode()).hexdigest()[:12],
            "query_len": len(query),
            "n_candidates": len(candidates or []),
            "top3": top3,
            "confidence": (route_info or {}).get("confidence"),
            "reason": (route_info or {}).get("reason"),
        }
        if elapsed_ms is not None:
            rec["elapsed_ms"] = elapsed_ms
        if compare_against:
            rec["compare_against"] = compare_against
        import config as _C  # §7.11 (local import per evitare circular)
        p = _C.PATH_USER_DATA / "prefilter_telemetry.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _rank_adaptive_legacy(query, catalog, k_min=5, k_max=8, *, llm_call=None,
                           prefer_intent=True):
    """
    Forma adattiva (preferita v1.1) con intent extractor LLM-based opzionale.

    Pipeline (Roberto 29/4/2026):
        1. Se llm_call disponibile e prefer_intent=True: chiama
           intent_extractor.extract_intent → {verb, object}.
        2. Se intent valido produce candidati >0: ritorna max-3 ranked
           per verb+object boost.
        3. Altrimenti fallback al ranking bag-of-words (verb_boost+object_boost
           via lexicon, soft-match cap-ato).

    Vantaggio dell'intent extractor: robusto a variazioni di linguaggio
    ("archivia", "svuota cestino", "metti in spam") che il lexicon manuale
    non copre. Latenza tipica ~350ms con il modello locale del livello fast.

    Filtro relativo (28/4 sera): tieni solo score >= max(1, top_score / 2).
    Evita di passare al planner tool con affinity bassa che fanno rumore (il
    modello locale sotto-pesa le description e si attacca a nomi calamita). Cap superiore a
    k_max comunque.
    """
    # Skip dormant (skill_credentials check, ADR 15/5/2026): il PLANNER
    # non deve vedere executor inattivi per mancanza di OAuth/token.
    catalog = _filter_dormant(catalog)
    # 1. Intent extractor (LLM-based) se disponibile
    if llm_call is not None and prefer_intent:
        try:
            from intent_extractor import extract_intent
            intent = extract_intent(query, llm_call)
        except Exception:
            intent = None
        if intent and intent.get("verb"):
            picked = rank_with_intent(query, catalog, intent, k=k_max)
            if picked:
                return picked, {
                    "chosen_k": len(picked),
                    "confidence": 1.0,  # LLM-determined intent → high confidence
                    "reason": "intent",
                    "intent": intent,
                }
    # 2. Fallback bag-of-words
    qtokens = tokenize(query)
    if not qtokens:
        executors = list(catalog)
        return executors[:k_min], {"chosen_k": min(k_min, len(executors)),
                                    "confidence": 0.0, "scores_top": [], "reason": "empty_query"}
    canonical_verbs = detect_canonical_verbs_all(qtokens)
    canonical_verb = canonical_verbs[0] if canonical_verbs else None
    canonical_object = detect_canonical_object(qtokens, query)
    # §7.3 Task #41 (28/5/2026): pass query_raw per attivare typed-rules
    # (input_coverage, schema_field) gated da METNOS_PREFILTER_RULES=1.
    # Init rare-tokens cache (idempotente, no-op se env=0).
    import os as _os_pref
    if _os_pref.environ.get("METNOS_PREFILTER_RULES", "0") == "1":
        try:
            from prefilter_rules import init_rare_tokens
            init_rare_tokens(catalog)
        except Exception:
            pass
    scored = [(affinity_score(qtokens, e,
                              query_canonical_verb=canonical_verb,
                              query_canonical_verbs=canonical_verbs,
                              query_canonical_object=canonical_object,
                              query_raw=query), e)
              for e in catalog]
    scored.sort(key=lambda p: (-p[0], getattr(p[1], "name", "")))
    scores = [s for s, _ in scored]
    top_score = scores[0] if scores else 0
    semantic_reason = ""
    # Semantic fallback (BGE-M3) quando hard match troppo debole: query con
    # typo, sinonimi semantici, declinazioni irregolari, cross-lingua. Costa
    # ~25ms quando attivato; skip quando hard match e' gia' confident.
    try:
        from affinity_semantic import (
            is_enabled as _sem_enabled, threshold as _sem_threshold,
            alpha as _sem_alpha, build_or_load_cache as _sem_build,
            semantic_max_per_executor as _sem_max,
        )
        if _sem_enabled() and top_score < _sem_threshold():
            _cache = _sem_build(list(catalog))
            if _cache is not None:
                _semmap = _sem_max(query, _cache)
                if _semmap:
                    _a = _sem_alpha()
                    scored = [(s + _a * _semmap.get(e.name, 0.0), e)
                              for s, e in scored]
                    scored.sort(key=lambda p: (-p[0], getattr(p[1], "name", "")))
                    scores = [s for s, _ in scored]
                    top_score = scores[0] if scores else 0
                    semantic_reason = "semantic_fallback"
    except Exception as _e:
        # §2.8: fallback silente ma TRACCIATO — il ranking hard match resta
        # valido (flusso invariato); debug per non inquinare i log quando il
        # modulo semantico non e' installato.
        log.debug("prefilter: semantic fallback (BGE-M3) fallito: %r", _e)
    rel_cutoff = max(1, top_score // 2)
    relevant = [(s, e) for s, e in scored if s >= rel_cutoff]
    if len(relevant) < k_min:
        # garantisci almeno k_min, anche pescando sotto la soglia
        relevant = scored[:k_min]
    K = min(len(relevant), k_max)
    K = max(K, k_min) if scored else 0
    selected = [e for _, e in scored[:K]]
    # Layer 1 (5/5/2026): force-include dei primary tools per l'object
    # detectato anche nel fallback BoW. Pareggia il comportamento di
    # rank_with_intent: la tupla `_OBJECT_PRIMARY_TOOLS[obj]` entra TUTTA
    # nel pool oltre il top-K, garantita visibile al PLANNER.
    if canonical_object and canonical_object in _OBJECT_PRIMARY_TOOLS:
        sel_names = {e.name for e in selected}
        for primary_name in _OBJECT_PRIMARY_TOOLS[canonical_object]:
            if primary_name in sel_names:
                continue
            primary_exec = next((e for e in catalog if e.name == primary_name), None)
            if primary_exec is not None:
                selected.append(primary_exec)
                sel_names.add(primary_name)
    # Admin shell injection (ADR 0088) anche nel fallback BoW: se la query
    # ha shell-intent marker e admin esiste nel catalog, garantisce che
    # sia in cima al pool (head-injection). 22/5/2026: anche se gia'
    # presente per affinity, lo promuoviamo a position 0.
    qlow_bow = (query or "").lower()
    shell_intent = _detect_shell_intent(qlow_bow)
    command_fallback = _detect_command_grammar_intent(qlow_bow)
    if shell_intent:
        admin_exec = next((e for e in catalog if e.name == "admin"), None)
        if admin_exec is not None:
            selected = [e for e in selected if e.name != "admin"]
            selected = [admin_exec] + selected[:max(0, k_max - 1)]
    elif command_fallback:
        admin_exec = next((e for e in catalog if e.name == "admin"), None)
        if admin_exec is not None and all(e.name != "admin" for e in selected):
            # Preserve the normal top-K ordering and add one guarded fallback.
            selected.append(admin_exec)
    _, conf = adaptive_k(scores, k_min, k_max)
    return selected, {
        "chosen_k": K,
        "confidence": round(conf, 3),
        "top_score": top_score,
        "rel_cutoff": rel_cutoff,
        "scores_top": scores[:max(K, 5)],
        "reason": (semantic_reason or
                   (f"rel_cutoff>={rel_cutoff}" if rel_cutoff > 1
                    else "k_min_floor")),
    }


def explain(query, catalog, k=5):
    qtokens = tokenize(query)
    print(f"query='{query}'  tokens={sorted(qtokens)}")
    scored = sorted(
        ((affinity_score(qtokens, e), e) for e in catalog),
        key=lambda p: p[0], reverse=True,
    )
    print(f"top {k}:")
    for s, e in scored[:k]:
        aff_match = qtokens & set(t for tag in e.affinity for t in tokenize(tag))
        print(f"  score={s:3d}  {e.name:14s}  match={sorted(aff_match)}")
    K, conf = adaptive_k([s for s, _ in scored])
    print(f"  --> adaptive_k={K} (confidence={conf:.2f})")


if __name__ == "__main__":
    from loader import load_catalog
    cat = load_catalog()
    queries = [
        "che ora e?",
        "leggi il file ~/notes/diary.md",
        "scarica https://httpbin.org/get",
        "salva il documento sul disco",
        "scrivi una nota e mandala via mail",
        "fai qualcosa di carino",  # query vaga
    ]
    for q in queries:
        print()
        explain(q, cat)

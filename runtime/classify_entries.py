"""runtime.classify_entries — builtin LLM-augmented classifier.

Pattern terza categoria di executor (28-29/4/2026, ratificato in
`feedback_llm_augmented_executors`): vive nel runtime, no manifest su
disco, no subprocess. Simmetrico a `describe_entries` ma riassume non
linguisticamente bensi' tassonomicamente: data una lista di entries +
una dimensione di classificazione (es. 'relevance') + un set chiuso di
classi (es. ['junk','low','medium','high']), arricchisce ogni entry
con un campo `<dimension>` etichettato.

Strategia:

  1. **Pre-filter tassonomico opzionale** (`pre_filter=True`): per
     `data_kind='email'` usa `category_hints` (gia' calcolate da
     `parse_envelope` → `_category_hints`) per etichettare i casi
     inequivocabili senza scomodare il LLM. Default OFF: in
     osservazione, attivare quando si ha confidenza che le regole
     tassonomiche non sotto-etichettino.

  2. **Batch LLM**: le entries residue (non pre-filtrate) vanno al LLM
     in batch da `batch_size`. Output atteso: JSON array di stringhe,
     una per entry, nello stesso ordine. Tier `fast` di default
     (riconciliazione tassonomica e' compito facile).

  3. **Output**: lista arricchita con campo `<dimension>` su ciascuna
     entry. NON partiziona — la partizione si fa con `filter_entries`
     downstream (`where_field='<dimension>'`, `where_in=[...]`).

Lega con la convenzione di truncation (`feedback_truncation_visibility`):
se la lista d'ingresso era gia' troncata, l'arricchimento la conserva
truncata; se classify_entries dovesse a sua volta cappare per OOM,
dichiara nel risultato.
"""
from __future__ import annotations

import json
import re

from llm_helpers import call_llm

from logging_setup import get_logger
log = get_logger(__name__)

# Default classes per dimensione conosciuta.
DEFAULT_CLASSES = {
    "relevance": ["junk", "low", "medium", "high"],
    "urgency": ["none", "later", "soon", "now"],
    "sentiment": ["negative", "neutral", "positive"],
    "topic": [],  # caller deve fornire le classi
}

# Default criterion per dimensione (italiano, prescrittivo).
DEFAULT_CRITERIA = {
    "relevance": (
        "rilevanza per l'utente: high = richiede azione o e' personale/da "
        "persone reali; medium = informativa o di servizio (conferme, "
        "transazioni, alert utili); low = newsletter, aggiornamenti, "
        "promozioni di servizi che l'utente segue; junk = spam evidente, "
        "promozionale aggressiva, mailing-list non desiderate."
    ),
    "urgency": (
        "urgenza temporale: now = richiede risposta entro poche ore; "
        "soon = entro qualche giorno; later = senza scadenze pressanti; "
        "none = informativa, nessuna azione richiesta."
    ),
    "sentiment": (
        "tono complessivo del messaggio: positive (entusiasta, "
        "ringraziamento, conferma positiva), neutral (informativo, "
        "transazionale), negative (lamentela, errore, rifiuto)."
    ),
}

# Pre-filter regole tassonomiche per email (data_kind='email').
# Mappa frozenset di category_hints richieste -> classe relevance.
# Solo regole CONSERVATIVE: in caso di dubbio, NON pre-filtrare.
_EMAIL_RELEVANCE_RULES = [
    (frozenset({"list", "bulk", "noreply"}), "junk"),
    (frozenset({"list", "esp"}), "low"),
    (frozenset({"list", "noreply"}), "low"),
    (frozenset({"bulk", "noreply"}), "low"),
]

# Campi default per data_kind, ottimizzati per non saturare il context.
DEFAULT_FIELDS = {
    "email": ["from", "subject", "body_preview", "category_hints"],
    "web_result": ["title", "url", "snippet"],
    "file": ["name", "kind", "size"],
}


def _detect_kind(entries: list, hint: str | None) -> str:
    if hint and hint != "auto":
        return hint
    kinds = {e.get("kind") for e in entries if isinstance(e, dict)}
    kinds.discard(None)
    if len(kinds) == 1:
        return next(iter(kinds))
    first = entries[0] if entries else {}
    if isinstance(first, dict):
        if "from" in first and "subject" in first:
            return "email"
        if "url" in first and ("title" in first or "snippet" in first):
            return "web_result"
        if "path" in first:
            return "file"
    return "generic"


def _project_entry(e: dict, fields: list[str] | None) -> dict:
    """Riduce una entry ai campi richiesti (per non saturare il LLM
    con body completi). Se fields=None, ritorna la entry intera."""
    if not fields:
        return e
    return {k: e.get(k) for k in fields if k in e}


def _pre_filter_email(entries: list[dict], dimension: str) -> tuple[list[int], dict]:
    """Applica regole tassonomiche conservative su email (solo dimension
    'relevance' per ora). Ritorna (indici_pre_filtrati, mappa_indice→classe)."""
    if dimension != "relevance":
        return [], {}
    pre = {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        hints = set(e.get("category_hints") or [])
        if not hints:
            continue
        for needed, label in _EMAIL_RELEVANCE_RULES:
            if needed.issubset(hints):
                pre[i] = label
                break
    return list(pre.keys()), pre


def _build_prompt(dimension: str, classes: list[str], criterion: str, kind: str, n: int) -> str:
    """Compone il prompt LLM per il classifier. Prompt persistito in
    `runtime/prompts/<lang>/classify_entries.j2` (ADR 0092 Phase 2)."""
    import prompt_loader
    from config import DEFAULT_LANG
    classes_str = ", ".join(repr(c) for c in classes)
    # Esempio dimensionato su n: emette n etichette plausibili scelte fra
    # le classi ammesse, in modo che il modello veda subito un output di
    # forma corretta. La prima classe e' usata come fill di default per
    # tenere l'esempio dentro al vocab. Pattern stabile per LLM medi.
    example_labels = [classes[i % len(classes)] for i in range(min(n, 3))]
    if n > 3:
        example_labels = example_labels + [f"... ({n} totali)"]
    example_str = json.dumps(example_labels[:3] if n <= 3 else example_labels[:3] + ["..."])
    return prompt_loader.get(
        "classify_entries",
        DEFAULT_LANG,
        n=n, kind=kind, dimension=dimension,
        classes_str=classes_str, criterion=criterion,
        example_n=min(n, 3), example_str=example_str,
    )


_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*\]", re.DOTALL)


def _parse_labels(text: str, classes: list[str], n: int) -> list[str] | None:
    """Estrae l'array JSON dalla risposta LLM. Robusto a wrapping
    accidentale (markdown fences, prosa attorno). Ritorna None se non
    parsabile o di lunghezza errata."""
    if not text:
        return None
    candidates = []
    try:
        v = json.loads(text)
        if isinstance(v, list):
            candidates.append(v)
    except Exception as _e:  # silent swallow (auto-fixed)
        log.warning("silent exception in %s: %s", __name__, _e)
    for m in _JSON_ARRAY_RE.finditer(text):
        try:
            v = json.loads(m.group(0))
            if isinstance(v, list):
                candidates.append(v)
        except Exception:
            continue
    valid_classes = set(classes)
    for v in candidates:
        if len(v) != n:
            continue
        if not all(isinstance(x, str) for x in v):
            continue
        if not all(x in valid_classes for x in v):
            continue
        return list(v)
    return None


def _classify_batch(items: list[dict], dimension: str, classes: list[str],
                    criterion: str, kind: str, tier: str,
                    fields: list[str] | None) -> tuple[list[str] | None, dict]:
    projected = [_project_entry(e, fields) for e in items]
    prompt = _build_prompt(dimension, classes, criterion, kind, len(items))
    # Output corto: ~5 token per label * n + boilerplate JSON
    max_tok = max(64, 12 * len(items))
    text, meta = call_llm(projected, prompt, tier=tier,
                          max_tokens=max_tok, temperature=0.0)
    labels = _parse_labels(text, classes, len(items))
    return labels, meta


def _auto_tier(n_items: int, fields: list[str] | None) -> str:
    """Per classificare un set chiuso di label, fast basta nei piccoli
    batch. Per batch grandi diamo middle che ha piu' margine sulla
    coerenza dell'output strutturato."""
    if n_items <= 30:
        return "fast"
    return "middle"


CLASSIFY_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_entries",
        "description": (
            "Classifica una lista di entries omogenee (mail, file, web "
            "results, ecc.) lungo una DIMENSIONE (default 'relevance'; "
            "altre: 'urgency', 'sentiment', 'topic') e arricchisce ogni "
            "entry con il campo `<dimension>` etichettato con una delle "
            "CLASSES (default per relevance: junk/low/medium/high). NON "
            "partiziona: per tenere solo le 'rilevanti' usa filter_entries "
            "(from_step=N, where_field='relevance', where_in=['high',"
            "'medium']) come step successivo. PASSAGGIO DELLA LISTA: usa "
            "`from_step=N` dove N e' il numero dello step precedente che "
            "ha prodotto la lista (es. read_messages al passo 1 → "
            "from_step=1). Il runtime recupera automaticamente le "
            "entries dallo scratchpad. Loop e ragionamento DENTRO "
            "l'executor: passi una reference, ricevi la stessa lista "
            "arricchita. Per email e' anche disponibile un pre-filter "
            "tassonomico opt-in che etichetta cheap i casi inequivocabili "
            "(newsletter+noreply+bulk = junk) senza chiamare il LLM."
        ),
        "parameters": {
            "type": "object",
            "required": ["from_step"],
            "properties": {
                "from_step": {
                    "type": "integer",
                    "description": "Numero dello step precedente (in questo "
                                   "turno) che ha prodotto la lista da "
                                   "classificare. Es. se al passo 1 hai "
                                   "chiamato read_messages, qui passi "
                                   "from_step=1.",
                    "minimum": 1,
                },
                "dimension": {
                    "type": "string",
                    "description": "Dimensione di classificazione. "
                                   "'relevance' (default), 'urgency', "
                                   "'sentiment', 'topic'. Il valore diventa "
                                   "il nome del campo aggiunto a ciascuna "
                                   "entry.",
                    "default": "relevance",
                },
                "classes": {
                    "type": "array",
                    "description": "Set chiuso di classi ammesse. Default "
                                   "per dimension: relevance=['junk','low',"
                                   "'medium','high'], urgency=['none','later',"
                                   "'soon','now'], sentiment=['negative',"
                                   "'neutral','positive']. Per 'topic' DEVI "
                                   "fornire le classi (es. ['lavoro','famiglia',"
                                   "'svago']).",
                    "items": {"type": "string"},
                },
                "criterion": {
                    "type": "string",
                    "description": "Criterio in linguaggio naturale che "
                                   "definisce le classi. Default ragionevole "
                                   "per relevance/urgency/sentiment; per "
                                   "'topic' e dimensioni custom il caller "
                                   "DEVE fornirlo. Esempio: \"rilevante = "
                                   "richiede una mia azione; junk = "
                                   "newsletter, promo\".",
                },
                "data_kind": {
                    "type": "string",
                    "description": "Tipo semantico delle entries ('email', "
                                   "'web_result', 'file', 'generic'). "
                                   "'auto' (default) deduce. Determina i "
                                   "fields default mostrati al classifier.",
                    "default": "auto",
                },
                "pre_filter": {
                    "type": "boolean",
                    "description": "Se true e data_kind='email' e dimension="
                                   "'relevance', applica regole tassonomiche "
                                   "conservative basate su category_hints "
                                   "(list+bulk+noreply -> junk, ecc.) prima "
                                   "del LLM. Le entries pre-filtrate non "
                                   "vanno al LLM. Default false: in "
                                   "osservazione finche' non si convalida "
                                   "che le regole non sotto-etichettino.",
                    "default": False,
                },
                "batch_size": {
                    "type": "integer",
                    "description": "Dimensione del batch al LLM. Default 30. "
                                   "Batch piu' grossi = meno chiamate ma "
                                   "piu' rischio di output mal-formato.",
                    "default": 30,
                },
                "tier": {
                    "type": "string",
                    "description": "Tier LLM. 'auto' (default) sceglie fast "
                                   "per batch piccoli, middle per piu' "
                                   "grossi. Per task soggettivi o domini "
                                   "tecnici considera middle/wise.",
                    "enum": ["auto", "fast", "middle", "wise"],
                    "default": "auto",
                },
                "fields": {
                    "type": "array",
                    "description": "Quali campi della entry mostrare al "
                                   "classifier. Default per data_kind: email "
                                   "= [from, subject, body_preview, "
                                   "category_hints]. Riduci se le entries "
                                   "hanno body lunghi: il classifier non "
                                   "ha bisogno di tutto.",
                    "items": {"type": "string"},
                },
            },
        },
    },
}


def _resolve_open_field(entries: list, dimension: str) -> str | None:
    """Trova il campo delle entries che realizza una `dimension` APERTA
    (group-by-field). Match lessicale puro sui NOMI dei campi presenti
    (esatto → case-insensitive → substring bidirezionale): nessun dizionario
    di sinonimi NL. Ritorna il nome-campo o None se nessuno combacia."""
    keys: list[str] = []
    for e in entries[:20]:
        if isinstance(e, dict):
            for k in e.keys():
                if isinstance(k, str) and k not in keys:
                    keys.append(k)
    if not keys:
        return None
    d = (dimension or "").strip().lower()
    if not d:
        return None
    for k in keys:                       # esatto / case-insensitive
        if k.lower() == d:
            return k
    for k in keys:                       # substring bidirezionale
        kl = k.lower()
        if d in kl or kl in d:
            return k
    return None


def handle_classify_entries(args, *, verbose: bool = False) -> dict:
    entries = (args or {}).get("entries")
    if not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid 'entries' (must be a list)"}

    dimension = (args or {}).get("dimension") or "relevance"
    classes = (args or {}).get("classes")
    if not classes:
        classes = list(DEFAULT_CLASSES.get(dimension) or [])
    # Dimensione APERTA (4/6): nessun set CHIUSO di classi (es. dimension=
    # 'sender'/'domain'/'topic' → valori illimitati, data-derivati).
    # classify_entries è per tassonomie chiuse; su un campo aperto NON va in
    # errore (romperebbe la pipeline, §2.8/§2.11). Se le entries hanno quel
    # campo, raggruppa DETERMINISTICAMENTE etichettando ogni entry col proprio
    # valore (group-by-field, no LLM, §7.9); altrimenti passthrough onesto con
    # nota (il describe a valle riassume). Universale, ZERO dizionari sinonimi.
    if not classes:
        ent_list = entries if isinstance(entries, list) else []
        _kind = _detect_kind(ent_list, (args or {}).get("data_kind"))
        result_entries = [dict(e) if isinstance(e, dict) else e for e in ent_list]
        field = _resolve_open_field(ent_list, dimension)
        if field:
            counts: dict = {}
            for e in result_entries:
                if isinstance(e, dict):
                    val = e.get(field)
                    lbl = str(val).strip() if val not in (None, "") else "(unknown)"
                    e[dimension] = lbl
                    counts[lbl] = counts.get(lbl, 0) + 1
            return {"ok": True, "entries": result_entries, "counts": counts,
                    "dimension": dimension, "classes": sorted(counts),
                    "kind": _kind, "open_dimension": True,
                    "grouped_by_field": field, "pre_filtered": 0,
                    "llm_classified": 0, "in_tokens": 0, "out_tokens": 0,
                    "latency_ms": 0}
        return {"ok": True, "entries": result_entries, "counts": {},
                "dimension": dimension, "classes": [], "kind": _kind,
                "open_dimension": True,
                "note": (f"dimension {dimension!r} is open-ended and no matching "
                         f"field was found; entries returned unchanged"),
                "pre_filtered": 0, "llm_classified": 0, "in_tokens": 0,
                "out_tokens": 0, "latency_ms": 0}
    if not isinstance(classes, list) or not all(isinstance(c, str) for c in classes):
        return {"ok": False, "error": "'classes' must be list[str]"}

    criterion = (args or {}).get("criterion") or DEFAULT_CRITERIA.get(dimension)
    if not criterion:
        # §2.8/§7.9: nessun criterion esplicito né default per questa dimensione
        # → sintetizza un criterion GENERICO da dimension+classes invece di
        # fallire (un hard-fail romperebbe la pipeline — es. task 'mail
        # importanti' → dimension='importance' senza criterion → terminator).
        # L'LLM classifica con la guida generica. Universale, ZERO hardcoding
        # per-dimensione (vale per importance/urgency/priority/topic/...).
        criterion = (
            f"Classifica ogni elemento in base alla dimensione «{dimension}», "
            f"assegnando esattamente UNA fra le classi: {', '.join(classes)}. "
            f"Usa il significato comune di «{dimension}» e il contenuto "
            f"dell'elemento (es. mittente, oggetto, testo) per decidere.")

    data_kind = (args or {}).get("data_kind") or "auto"
    kind = _detect_kind(entries, data_kind)
    pre_filter = bool((args or {}).get("pre_filter", False))
    batch_size = int((args or {}).get("batch_size", 30))
    if batch_size <= 0 or batch_size > 200:
        return {"ok": False, "error": "batch_size must be in 1..200"}
    tier = (args or {}).get("tier") or "auto"
    fields = (args or {}).get("fields") or DEFAULT_FIELDS.get(kind)

    if not entries:
        return {"ok": True, "entries": [], "counts": {c: 0 for c in classes},
                "pre_filtered": 0, "llm_classified": 0, "dimension": dimension,
                "classes": classes, "kind": kind,
                "in_tokens": 0, "out_tokens": 0, "latency_ms": 0}

    # 1. Pre-filter tassonomico (solo email, solo relevance, solo opt-in).
    pre_indices: list[int] = []
    pre_labels: dict[int, str] = {}
    if pre_filter and kind == "email":
        pre_indices, pre_labels = _pre_filter_email(entries, dimension)

    # 2. Indici da classificare via LLM = tutti meno i pre-filtrati.
    llm_indices = [i for i in range(len(entries)) if i not in pre_labels]

    # 3. Costruisci risultato: copia delle entries arricchite con campo dimension.
    result_entries = [dict(e) if isinstance(e, dict) else e for e in entries]
    for i, lbl in pre_labels.items():
        if isinstance(result_entries[i], dict):
            result_entries[i][dimension] = lbl

    # 4. Batch LLM su llm_indices.
    if tier == "auto":
        tier = _auto_tier(len(llm_indices), fields)
    in_tok = out_tok = lat = 0
    model_used = ""
    failed_batches = 0
    # Catchall: classe usata per entries con label fuori-set o batch fallito.
    # Preferisce 'altro'/'other'/'misc'/'unknown' se presente, sennoSize l'ultima classe.
    classes_set = set(classes)
    catchall = None
    for cand in ("altro", "other", "misc", "unknown"):
        if cand in classes_set:
            catchall = cand; break
    if catchall is None:
        catchall = classes[-1]

    if llm_indices:
        for batch_start in range(0, len(llm_indices), batch_size):
            chunk_idx = llm_indices[batch_start:batch_start + batch_size]
            chunk = [result_entries[i] for i in chunk_idx]
            try:
                labels, meta = _classify_batch(chunk, dimension, classes,
                                               criterion, kind, tier, fields)
            except Exception as e:
                failed_batches += 1
                if verbose:
                    print(f"[classify] batch failed: {e}")
                # Chiudi il conto: marca tutto il chunk come catchall
                # (CLAUDE.md 2.8: nessun silent loss di entries).
                for target in chunk_idx:
                    if isinstance(result_entries[target], dict):
                        result_entries[target][dimension] = catchall
                continue
            in_tok += meta.get("in_tokens", 0) or 0
            out_tok += meta.get("out_tokens", 0) or 0
            lat += meta.get("latency_ms", 0) or 0
            model_used = meta.get("model", model_used)
            if labels is None:
                failed_batches += 1
                if verbose:
                    print(f"[classify] batch produced unparsable output (size={len(chunk)})")
                for target in chunk_idx:
                    if isinstance(result_entries[target], dict):
                        result_entries[target][dimension] = catchall
                continue
            # labels list disponibile: usa label LLM, fallback catchall se fuori set.
            for j, lbl in enumerate(labels):
                target = chunk_idx[j]
                if isinstance(result_entries[target], dict):
                    if lbl not in classes_set:
                        lbl = catchall
                    result_entries[target][dimension] = lbl
            # Se labels < chunk_idx (LLM ha emesso meno etichette di quante
            # entries), riempi il resto con catchall.
            if len(labels) < len(chunk_idx):
                for j in range(len(labels), len(chunk_idx)):
                    target = chunk_idx[j]
                    if isinstance(result_entries[target], dict):
                        result_entries[target][dimension] = catchall

    # 5. Counts (solo entries effettivamente classificate).
    counts: dict[str, int] = {c: 0 for c in classes}
    unclassified = 0
    for e in result_entries:
        if not isinstance(e, dict):
            unclassified += 1
            continue
        v = e.get(dimension)
        if v in counts:
            counts[v] += 1
        else:
            unclassified += 1

    out = {
        "ok": True,
        "entries": result_entries,
        "counts": counts,
        "pre_filtered": len(pre_labels),
        # §2.8: conteggio DERIVATO dai risultati reali (entries inviate al LLM
        # che hanno ottenuto una classe valida) — non `len - failed*batch_size`
        # che poteva andare NEGATIVO su batch parziali/falliti.
        "llm_classified": sum(
            1 for i in llm_indices
            if isinstance(result_entries[i], dict)
            and result_entries[i].get(dimension) in counts),
        "unclassified": unclassified,
        "failed_batches": failed_batches,
        "dimension": dimension,
        "classes": classes,
        "kind": kind,
        "tier": tier,
        "model": model_used,
        "in_tokens": in_tok,
        "out_tokens": out_tok,
        "latency_ms": lat,
    }
    return out


# --- API per chiamate da altri executor (Python diretto) -------------------

def classify(items, *, dimension: str = "relevance", classes: list | None = None,
             criterion: str | None = None, data_kind: str = "auto",
             pre_filter: bool = False, batch_size: int = 30,
             tier: str = "auto", fields: list[str] | None = None) -> list[dict]:
    """Funzione di alto livello per altri executor. Ritorna SOLO la lista
    di entries arricchite (lo stesso ordine di input). Solleva RuntimeError
    se la chiamata fallisce."""
    res = handle_classify_entries({
        "entries": items, "dimension": dimension, "classes": classes,
        "criterion": criterion, "data_kind": data_kind,
        "pre_filter": pre_filter, "batch_size": batch_size,
        "tier": tier, "fields": fields,
    })
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "classify failed"))
    return res["entries"]


BUILTIN_INPROC_SPECS = [{
    "name": "classify_entries", "tool_spec": CLASSIFY_ENTRIES_TOOL,
    "affinity": ["classifica", "categorizza", "classify", "categorize", "entries"],
}]

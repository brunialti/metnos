#!/usr/bin/env python3
"""route_disambiguation.py — §2.11 (notify-then-ask) applicato al ROUTING.

Quando la query attiva ≥2 OGGETTI-produttori distinti senza un vincitore netto
(es. «leggi le mail e i file pdf»: messages vs files), il runtime non tira a
indovinare: emette un form get_inputs con le interpretazioni candidate. La
scelta dell'utente ri-esegue la query con l'oggetto FISSATO.

Deterministico (§7.9): segnale = conteggio di hint per oggetto
(`prefilter._OBJECT_HINTS`, lessico curato), nessun LLM. Soglia env
`METNOS_ROUTE_AMBIGUITY_TAU` (default 0.5): ambiguo se il 2° oggetto ha score
>= tau * top. «tau non troppo alto» = chiede solo quando gli oggetti competono
davvero, non sui casi chiari.

Confini (mai un form spurio):
- solo se ≥2 oggetti-PRODUTTORI distinti superano la soglia;
- oggetti non-produttori (now/location) esclusi: non sono scelte d'azione;
- gate `METNOS_ROUTE_DISAMBIGUATION` (default ON); off → noop totale.
"""
from __future__ import annotations

import os


# Oggetti su cui ha senso disambiguare (hanno un produttore read/find/get).
# Esclusi now/location (atomici, non scelte d'azione su «cosa leggere»).
_PRODUCER_OBJECTS = frozenset({
    "messages", "files", "images", "dirs", "urls", "events", "calendars",
    "contacts", "places", "packages", "processes", "texts", "numbers",
})


def _tau() -> float:
    try:
        return float(os.environ.get("METNOS_ROUTE_AMBIGUITY_TAU", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _enabled() -> bool:
    return os.environ.get("METNOS_ROUTE_DISAMBIGUATION", "1").lower() \
        not in ("0", "off", "no", "false")


def object_hint_scores(query: str) -> dict[str, int]:
    """{oggetto: n_hint distinti nella query}, solo oggetti-produttori. §7.9."""
    if not query or not isinstance(query, str):
        return {}
    from prefilter import _WORD_RE, _OBJECT_HINTS
    toks = set(_WORD_RE.findall(query.lower()))
    out: dict[str, int] = {}
    for obj, hints in _OBJECT_HINTS.items():
        if obj not in _PRODUCER_OBJECTS:
            continue
        n = len(toks & set(hints))
        if n:
            out[obj] = n
    return out


def _intent_objects(intent) -> set[str]:
    """Oggetti risolti dall'NLU: le azioni DECOMPOSTE (compound) PIU' l'oggetto
    PRIMARIO `intent.object`. Per le query mono-clausola `actions` e' vuoto
    (l'extract_intent popola `actions` solo sui compound) ma `intent.object` e'
    risolto — senza includerlo, il gate anti-argomento sarebbe morto in prod
    sulle mono-clausola (bug live 2e7916f0: «sposta le email nella cartella X»,
    actions=[], object='messages'). Distingue un COMPOUND legittimo («manda mail
    E crea evento» = 2 azioni) da un'AMBIGUITA' (1 clausola, piu' oggetti)."""
    out: set[str] = set()
    for a in (getattr(intent, "actions", None) or []):
        o = a.get("object") if isinstance(a, dict) else None
        if o:
            out.add(o)
    obj = getattr(intent, "object", None)
    if isinstance(obj, str) and obj:
        out.add(obj)
    return out


def detect_object_ambiguity(query: str, intent=None) -> list[str] | None:
    """Ritorna gli oggetti-candidati (>=2, ordinati per score desc) se la query
    e' AMBIGUA sull'oggetto; None altrimenti. Deterministico, mai eccezioni.

    Gate anti-compound: se l'intent ha gia' un'azione DISTINTA per ciascun
    oggetto in gara (li copre tutti), e' un compound legittimo (fai X su A e Y
    su B) → NON ambiguo. Ambiguo solo quando l'intent ne ha SCARTATO uno (piu'
    oggetti competono per la stessa clausola)."""
    if not _enabled():
        return None
    scores = object_hint_scores(query)
    if len(scores) < 2:
        return None
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ordered[0][1]
    tau = _tau()
    cands = [obj for obj, sc in ordered if sc >= tau * top]
    if len(cands) < 2:
        return None
    # Filesystem-siblings: files e dirs NON competono come domini distinti — «i
    # file DELLA cartella X» è un'unica operazione filesystem (list_dirs elenca i
    # file di una dir; find_files cerca file dentro dirs). Il form «cartelle vs
    # file» sarebbe spurio (turn 0264fcf9: score pari 1/1, il gate anti-argomento
    # non li separa). Se gli UNICI competitori sono {files, dirs} → non è
    # ambiguità di DOMINIO → non chiedere (il planner risolve a list_dirs/find_files).
    if set(cands) <= {"files", "dirs"}:
        return None
    # Anti-compound + anti-argomento (NLU-first §7.9, nessun hardcoding): l'intent
    # NLU ha gia' decomposto la clausola. Un candidato lessicale che l'intent NON
    # ha promosso ad azione e' un ARGOMENTO/secondario (es. «nella cartella X» =
    # destinazione, non oggetto in gara), NON un competitore — A MENO che sia
    # forte quanto l'oggetto risolto dall'intent (allora e' competizione vera, es.
    # «controlla la posta E i documenti»). Regola generale sullo score lessicale,
    # senza lista di preposizioni:
    #   - tutti i candidati coperti dall'intent → compound legittimo → non chiede;
    #   - candidato scoperto STRETTAMENTE piu' debole del piu' forte coperto →
    #     argomento/secondario → fidati dell'NLU → non chiede;
    #   - scoperto forte quanto il coperto (>=) → competizione reale → chiede.
    # Limite onesto: un argomento-destinazione forte quanto il primario (raro)
    # chiede ancora — ma il dialogo ora funziona (B), non e' piu' un vicolo cieco.
    if intent is not None:
        covered = _intent_objects(intent)
        if covered:
            uncovered_cands = [c for c in cands if c not in covered]
            if not uncovered_cands:
                return None  # compound: l'intent copre tutti i candidati
            covered_cands = [c for c in cands if c in covered]
            if covered_cands:
                max_cov = max(scores[c] for c in covered_cands)
                max_unc = max(scores[c] for c in uncovered_cands)
                if max_unc < max_cov:
                    return None  # scoperti piu' deboli = argomenti → fidati NLU
    return cands


# Etichetta i18n per oggetto (chiave MSG_OBJ_<obj>); fallback = nome oggetto.
def _object_label(obj: str) -> str:
    from messages import get as _msg
    txt = _msg(f"MSG_OBJ_{obj.upper()}")
    return obj if txt.startswith("<missing") else txt


def build_disambiguation_form(query: str, objects: list[str]) -> dict:
    """Costruisce l'osservazione `needs_inputs` (form get_inputs) che chiede
    all'utente quale oggetto intendeva. on_complete `rerun_query_disambiguated`
    ri-esegue la query originale con l'oggetto FISSATO. §2.11."""
    from messages import get as _msg
    options = [{"value": o, "label": _object_label(o)} for o in objects]
    dialog = [{
        "var": "object",
        "prompt": _msg("MSG_ROUTE_DISAMBIG_PROMPT"),
        "schema": {"kind": "choice", "choices": options},
        "optional": False,
    }]
    return {
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": _msg("MSG_ROUTE_DISAMBIG_TITLE"),
            "dialog": dialog,
            "fmt": "auto",
            "on_complete": {
                "type": "rerun_query_disambiguated",
                "query": query,
            },
            "timeout_s": 3600,
        },
    }

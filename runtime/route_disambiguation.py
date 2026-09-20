#!/usr/bin/env python3
"""route_disambiguation.py — §2.11 (notify-then-ask) applicato al ROUTING.

Quando la query attiva ≥2 OGGETTI-produttori distinti senza un vincitore netto
(es. «leggi le mail e i file pdf»: messages vs files), il runtime non tira a
indovinare: emette un form get_inputs con le interpretazioni candidate. La
scelta dell'utente ri-esegue la query con l'oggetto FISSATO.

Il lessico curato (`prefilter._OBJECT_HINTS`) produce soltanto un inventario
chiuso di possibili oggetti. Quando più oggetti restano plausibili, una
classificazione semantica separata decide se sono più bersagli legittimi, un
bersaglio con argomenti nominali, oppure una vera alternativa ambigua. Soltanto
quest'ultimo esito apre il form; un guasto del classificatore lascia lavorare il
planner, invece di interrompere una richiesta chiara con una scelta artificiale.

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


_RELATIONS = frozenset({
    "SINGLE_TARGET", "MULTI_TARGET", "AMBIGUOUS", "UNKNOWN",
})


def _semantic_relation(query: str, candidates: list[str], intent, *,
                       llm_call, lang: str) -> str:
    """Classify relations among closed candidates without selecting a tool."""

    import json
    import prompt_loader

    payload = {
        "language": lang,
        "user_query": query,
        "candidate_objects": list(candidates),
        "resolved_primary_object": str(getattr(intent, "object", "") or ""),
        "resolved_actions": [
            {
                "verb": str(action.get("verb") or ""),
                "object": str(action.get("object") or ""),
            }
            for action in (getattr(intent, "actions", None) or ())
            if isinstance(action, dict)
        ],
    }
    try:
        prompt = prompt_loader.get("route_object_relation", lang)
        raw = llm_call(
            prompt,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            max_tokens=12,
        )
    except TypeError:
        try:
            raw = llm_call(
                prompt,
                json.dumps(payload, ensure_ascii=False,
                           separators=(",", ":")),
                max_tokens=12,
            )
        except Exception:
            return "UNKNOWN"
    except Exception:
        return "UNKNOWN"
    if isinstance(raw, str):
        value = raw
    elif hasattr(raw, "text"):
        value = getattr(raw, "text", "")
    elif isinstance(raw, dict):
        value = raw.get("text") or ""
    else:
        value = ""
    normalized = str(value).strip().upper()
    return normalized if normalized in _RELATIONS else "UNKNOWN"


def detect_object_ambiguity(query: str, intent=None, *, llm_call=None,
                            lang: str = "it") -> list[str] | None:
    """Ritorna gli oggetti-candidati (>=2, ordinati per score desc) se la query
    e' AMBIGUA sull'oggetto; None altrimenti. Fail-soft, mai eccezioni.

    Il conteggio lessicale non decide mai l'ambiguità: serve soltanto a
    costruire il piccolo vocabolario chiuso sottoposto al classificatore.
    Senza un classificatore disponibile il gate declina e il planner conserva
    la richiesta integrale."""
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
    actions = tuple(
        action for action in (getattr(intent, "actions", None) or ())
        if isinstance(action, dict)
        and (action.get("verb") or action.get("object"))
    )
    if len(actions) >= 2:
        # The semantic extractor already established a compound request.  A
        # dialog that forces one object for the whole query would destroy it.
        return None
    if not callable(llm_call):
        return None
    relation = _semantic_relation(
        query, cands, intent, llm_call=llm_call, lang=lang)
    return cands if relation == "AMBIGUOUS" else None


# Etichetta i18n per oggetto (chiave MSG_OBJ_<obj>); fallback = nome oggetto.
def _object_label(obj: str) -> str:
    from messages import get as _msg
    txt = _msg(f"MSG_OBJ_{obj.upper()}")
    return obj if txt.startswith("<missing") else txt


def build_disambiguation_form(
        query: str, objects: list[str], *, replay_query: str = "") -> dict:
    """Costruisce l'osservazione `needs_inputs` (form get_inputs) che chiede
    all'utente quale oggetto intendeva. on_complete `rerun_query_disambiguated`
    ri-esegue la query originale con l'oggetto FISSATO. ``query`` può essere
    la forma normalizzata usata dal planner; ``replay_query`` conserva invece
    la richiesta integrale quando uno stadio precedente ha estratto vincoli
    strutturali (per esempio placement o credenziali).  Il dialogo non deve
    mai ricostruire quei vincoli dalla prosa ripulita. §2.11."""
    from messages import get as _msg
    options = [{"value": o, "label": _object_label(o)} for o in objects]
    dialog = [{
        "var": "object",
        "prompt": _msg("MSG_ROUTE_DISAMBIG_PROMPT"),
        "schema": {"kind": "choice", "choices": options},
        "optional": False,
    }]
    original = (
        replay_query.strip()
        if isinstance(replay_query, str) and replay_query.strip()
        else query
    )
    return {
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": _msg("MSG_ROUTE_DISAMBIG_TITLE"),
            "dialog": dialog,
            "fmt": "auto",
            "on_complete": {
                "type": "rerun_query_disambiguated",
                "query": original,
            },
            "timeout_s": 3600,
        },
    }

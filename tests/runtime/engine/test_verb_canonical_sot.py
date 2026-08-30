"""P3 (ADR 0191 §5) — SoT unica prefilter ↔ vocab per i verbi canonici.

Il #7 lamentava una «doppia SoT»: la vecchia tabella del prefilter mantenuta a mano,
indipendente da `vocab.ACTION_MAPPING`. La risoluzione NON e' una derivazione cieca
(regredirebbe): il concept specifico e' un'euristica di BOOST del prefilter, tarata
su verbi POLISEMICI per CONTENITORE (es. `mostra` = render un grafico OPPURE read il
contenuto di un file). `vocab.ACTION_MAPPING` e' il canonico del planner/synt.

Questo test impone la coerenza per TUTTI i verbi non-polisemici (cattura drift
accidentale) e dichiara ESPLICITAMENTE le eccezioni polisemiche con motivazione.
Una nuova divergenza fallisce qui → va corretta o aggiunta con rationale.

Nota (verificata): `apri`/`open` mappano a `read` in ENTRAMBE le SoT (nessun
conflitto). Il routing «apri booking» -> OPEN sessione e' ottenuto dal guard
`_ensure_site_session_precursor` + object=sites, NON dalla tabella verbi.
"""
from __future__ import annotations

import sys
from pathlib import Path


import prefilter
import vocab


# Eccezioni POLISEMICHE dichiarate: (token) -> (canonico vocab, canonico prefilter).
# Il prefilter tara sul caso d'uso piu' comune del boost (contenitore/file),
# vocab sul canonico del planner. Divergenza INTENZIONALE, non drift.
_POLYSEMOUS_EXCEPTIONS = {
    "mostra": ("render", "read"),  # "mostra il grafico" (render) vs "mostra il file" (read)
    "show": ("render", "read"),
    "tell": ("send", "get"),       # "tell Bob" (send) vs "tell me the weather" (get)
    "visualizza": ("render", "read"),  # format output vs inspect content
    "discard": ("filter", "delete"),   # discard from set vs destroy object
    "classifica": ("classify", "sort"),  # label entries vs rank/order them
    "etichetta": ("set", "classify"),  # persistent label vs inferred class
    "label": ("set", "classify"),       # persistent label vs inferred class
    "indicizza": ("order", "create"),   # durable order vs create index
    "index": ("order", "create"),       # durable order vs create index
    "order": ("order", "sort"),         # persistent order vs in-memory sort
}


def _single_token(syn: str) -> bool:
    return bool(syn) and " " not in syn and "-" not in syn


def test_prefilter_verb_table_consistent_with_vocab_except_documented():
    verb_map = prefilter.verb_to_canonical_mapping()
    contradictions = []
    for canonical, spec in vocab.ACTION_MAPPING.items():
        if not isinstance(spec, dict):
            continue
        for lang in ("it", "en"):
            for syn in spec.get(lang, []):
                if not _single_token(syn):
                    continue
                mapped = verb_map.get(syn)
                if mapped is None or mapped == canonical:
                    continue
                exc = _POLYSEMOUS_EXCEPTIONS.get(syn)
                if exc == (canonical, mapped):
                    continue  # divergenza dichiarata e motivata
                contradictions.append((syn, canonical, mapped))
    assert not contradictions, (
        "Drift prefilter↔vocab su verbi non dichiarati polisemici: "
        + ", ".join(f"{s!r} vocab={v!r} prefilter={p!r}"
                    for s, v, p in sorted(contradictions))
        + ". Correggi la tabella o aggiungi l'eccezione con rationale.")


def test_documented_exceptions_are_still_divergent():
    """Se un'eccezione smette di divergere (le SoT convergono), va RIMOSSA da qui
    per non mascherare una futura vera drift."""
    stale = []
    verb_map = prefilter.verb_to_canonical_mapping()
    for token, (want_vocab, want_pref) in _POLYSEMOUS_EXCEPTIONS.items():
        pref = verb_map.get(token)
        in_vocab = any(
            token in spec.get(lang, [])
            for spec in vocab.ACTION_MAPPING.values()
            if isinstance(spec, dict) for lang in ("it", "en")
            if vocab.ACTION_MAPPING.get(want_vocab) is spec)
        if pref != want_pref or not in_vocab:
            stale.append(token)
    assert not stale, (
        f"Eccezioni polisemiche non piu' valide (aggiornare/rimuovere): {stale}")


def test_apri_open_agree_read_in_both_sot():
    # Verificato: nessun conflitto su apri/open (entrambe -> read). Il routing
    # OPEN per i siti passa dal guard, non da qui.
    verb_map = prefilter.verb_to_canonical_mapping()
    assert verb_map.get("apri") == "read"
    assert verb_map.get("open") == "read"
    read_syn = (vocab.ACTION_MAPPING["read"].get("it", [])
                + vocab.ACTION_MAPPING["read"].get("en", []))
    assert "apri" in read_syn and "open" in read_syn


def test_avvia_start_launch_resolve_to_run_not_web_open():
    """Le forme semplici arrivano dalla risorsa attiva, non da una tabella cablata."""
    for token in ("avvia", "avviare", "run", "start", "launch"):
        assert prefilter.detect_canonical_verb(prefilter.tokenize(token)) == "run"
    open_surfaces = (
        vocab.ACTION_MAPPING["open"].get("it", [])
        + vocab.ACTION_MAPPING["open"].get("en", [])
    )
    assert not set(("avvia", "run", "start", "launch")) & set(open_surfaces)

    for query in (
        "Avvia Blocco note sul mio dispositivo Windows",
        "Start Notepad on my Windows device",
        "Launch Calculator",
    ):
        detected = prefilter.detect_canonical_verbs_all(
            prefilter.tokenize(query))
        assert "run" in detected
        assert "open" not in detected

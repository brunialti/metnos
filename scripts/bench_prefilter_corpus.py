#!/usr/bin/env python3
"""Misura il prefilter bag-of-words sul corpus congelato di query reali.

Perche' esiste (6/8/2026): `data/prefilter_corpus_snapshot.jsonl` era scritto
da `build_prefilter_corpus.py` e non lo leggeva nessuno. Ogni volta che si
tocca il punteggio del prefilter serve un A/B, e senza uno strumento nel repo
lo si riscrive a mano — cioe' non lo si fa.

Che cosa misura: `prefilter.rank` (il path bag-of-words, quello che l'engine usa
quando l'intent e' debole, `routing_pool.py`) contro il `first_tool` osservato
in esercizio. NON misura `rank_with_intent`, che ha un altro punteggio.

Uso:
    python3 scripts/bench_prefilter_corpus.py                 # numeri
    python3 scripts/bench_prefilter_corpus.py --dump prima.json
    python3 scripts/bench_prefilter_corpus.py --confronta prima.json

A/B tipico: `--dump` prima della modifica, `--confronta` dopo. Il confronto
elenca ogni query in cui il primo cambia, marcata MEGLIO / PEGGIO / neutro
rispetto all'atteso: un delta di totali senza le righe non dice se il
cambiamento e' quello che si voleva.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RADICE / "runtime"))

CORPUS = _RADICE / "data" / "prefilter_corpus_snapshot.jsonl"


def _corpus() -> list[dict]:
    return [json.loads(riga) for riga in CORPUS.read_text().splitlines() if riga.strip()]


def _authored_catalog() -> list:
    """The checkout's manifests, with the fields the bag-of-words score reads.

    The live catalog is opened only through the Birth chain (RM-0008), which a
    developer checkout does not have; an A/B of the score or of an affinity
    edit measures exactly these manifests.
    """
    import tomllib
    from types import SimpleNamespace

    entries = []
    for path in sorted((_RADICE / "executors").glob("*/manifest.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        description = data.get("description") or ""
        if isinstance(description, dict):
            description = description.get("it") or next(iter(description.values()), "")
        entries.append(SimpleNamespace(name=data["name"], affinity=list(data.get("affinity") or []),
                                       description=str(description)))
    return entries


def misura() -> tuple[dict, dict]:
    """Ritorna (totali, primo-per-query)."""
    from prefilter import rank

    try:
        from loader import load_catalog
        catalogo = list(load_catalog())
    except Exception as exc:  # noqa: BLE001 - checkout without the Birth chain
        print(f"catalogo vivo non disponibile ({type(exc).__name__}): manifest del checkout",
              file=sys.stderr)
        catalogo = _authored_catalog()
    nomi = {e.name for e in catalogo}
    top1 = top3 = top10 = valutabili = 0
    primi: dict[str, str] = {}
    for caso in _corpus():
        atteso = caso["first_tool"]
        if atteso not in nomi:
            continue          # tool ritirato dopo la fotografia del corpus
        valutabili += 1
        ordinati = [e.name for e in rank(caso["query"], catalogo, k=10)]
        primi[caso["query"]] = ordinati[0] if ordinati else ""
        top1 += ordinati[:1] == [atteso]
        top3 += atteso in ordinati[:3]
        top10 += atteso in ordinati
    return ({"valutabili": valutabili, "top1": top1, "top3": top3,
             "top10": top10}, primi)


def confronta(primi: dict, riferimento: dict) -> None:
    atteso_di = {c["query"]: c["first_tool"] for c in _corpus()}
    cambi = [(q, riferimento[q], primi[q]) for q in riferimento
             if q in primi and riferimento[q] != primi[q]]
    print(f"cambi di top-1: {len(cambi)}")
    for query, prima, dopo in cambi:
        atteso = atteso_di.get(query)
        verdetto = ("MEGLIO" if dopo == atteso
                    else "PEGGIO" if prima == atteso else "neutro")
        print(f"  [{verdetto}] {query[:60]!r}: {prima} -> {dopo} "
              f"(atteso {atteso})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", metavar="FILE",
                        help="scrive il primo-per-query, per un A/B successivo")
    parser.add_argument("--confronta", metavar="FILE",
                        help="confronta con un dump precedente")
    args = parser.parse_args()

    totali, primi = misura()
    print(json.dumps(totali, ensure_ascii=False))
    if args.dump:
        Path(args.dump).write_text(json.dumps(primi, ensure_ascii=False,
                                              sort_keys=True, indent=0))
    if args.confronta:
        confronta(primi, json.loads(Path(args.confronta).read_text()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

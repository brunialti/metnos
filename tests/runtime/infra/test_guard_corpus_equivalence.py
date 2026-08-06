"""L'oracolo di equivalenza sui PIANI REALI (6/8/2026).

`test_provenance_equivalence` fa la stessa cosa su 11 casi scritti a mano. Qui
il corpus sono i piani veri: 804 casi estratti dal registro di un'istanza in
esercizio (osservazioni + fastpath), deduplicati per forma e ANONIMIZZATI —
niente nomi, niente percorsi personali, niente contenuto bake-ato.

A che serve: prima di sostituire una guardia con una regola, o di ritirarla, si
deve poter dire con un numero che cosa cambia. Su un caso scritto a mano si
vede quello che si e' pensato di guardare; su 804 piani veri si vede anche il
resto. Il 6/8 questo strumento ha misurato tre modifiche al motore: 0
differenze, 0 differenze, 122 differenze **tutte spiegate** una per una.

Il golden e' un'IMPRONTA per caso, non l'uscita intera: un file di uscite
peserebbe come il corpus. Su divergenza il test stampa ingresso e uscita del
caso, che e' quello che serve per capire.

LIMITE DA TENERE A MENTE leggendo una divergenza: i casi che vengono dalle
osservazioni NON hanno il testo della query (il registro non lo conserva), solo
quelli che vengono dai fastpath ce l'hanno. Una guardia che legge la query si
comporta qui in modo diverso da come si comporta in esercizio — e la differenza
non e' neutra, e' sistematica. Misurato il 6/8 ritirando
`overwrite_phantom_install_args`: con la query vuota la condizione «il percorso
non e' nominato dalla query» e' sempre vera, quindi la guardia sembrava
riparare 41 piani che in produzione non toccava affatto. Se una divergenza
riguarda una guardia che legge la query, verificarla sui casi con `query`
piena prima di trarne conclusioni.

Rigenerare (SOLO quando il cambiamento e' voluto, mai per far passare il test):

    ./.venv/bin/python -m pytest tests/runtime -q     # prima: dev'essere verde
    ./.venv/bin/python tests/runtime/infra/test_guard_corpus_equivalence.py --regen

Il corpus si ricostruisce da un'istanza reale con
`internal/tools/build_guard_corpus.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("METNOS_ENGINE", "v3")

_DATA = Path(__file__).resolve().parent.parent / "data"
_CORPUS = _DATA / "guard_corpus_real.json"
_GOLDEN = _DATA / "guard_corpus_golden.json"

# Guardie che il corpus esercita davvero. Serve a due cose: dire con onesta'
# che cosa NON e' coperto, e accorgersi se il corpus invecchia fino a non
# esercitare piu' nulla (una regressione silenziosa dello strumento stesso).
_ATTESE_ATTIVE = 13


def _catalog():
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    return filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)


def _casi():
    return json.loads(_CORPUS.read_text())


def _esegui(caso, cat):
    from engine import dispatch as D
    from engine.types import Framework, Intent
    verb, obj = ((caso["sig"] or "").split("|") + ["", ""])[:2]
    intent = Intent(verb=verb, object=obj,
                    actions=[{"verb": verb, "object": obj}])
    out = D._apply_deterministic_structure_guards(
        Framework.from_dict(caso["plan"]), intent, caso.get("query") or "", cat)
    return out.to_dict()


def _impronta(d) -> str:
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_pipeline_riproduce_il_golden():
    assert _CORPUS.exists() and _GOLDEN.exists(), "corpus o golden mancante"
    golden = json.loads(_GOLDEN.read_text())
    cat = _catalog()
    casi = _casi()
    assert len(casi) == len(golden), (
        f"il corpus ha {len(casi)} casi, il golden {len(golden)}: rigenera")
    diffs = []
    for i, caso in enumerate(casi):
        got = _esegui(caso, cat)
        if _impronta(got) != golden[i]:
            diffs.append(
                f"caso {i} [{caso['sig']}] query={caso.get('query')!r}\n"
                f"    ingresso: {json.dumps(caso['plan'], ensure_ascii=False)[:300]}\n"
                f"    uscita  : {json.dumps(got, ensure_ascii=False)[:300]}")
        if len(diffs) >= 5:
            diffs.append(f"... (fermato ai primi 5 di almeno {len(diffs)})")
            break
    assert not diffs, (
        f"EQUIVALENZA ROTTA su {len(diffs)} casi reali. Se il cambiamento e'\n"
        "VOLUTO: spiega ogni differenza, poi rigenera con --regen.\n\n"
        + "\n".join(diffs))


def test_il_corpus_esercita_ancora_le_guardie():
    """Un corpus che non fa piu' sparare nessuno non protegge nulla."""
    from engine import dispatch as D
    from engine.types import Framework, Intent
    cat = _catalog()
    attive = set()
    for caso in _casi():
        verb, obj = ((caso["sig"] or "").split("|") + ["", ""])[:2]
        intent = Intent(verb=verb, object=obj,
                        actions=[{"verb": verb, "object": obj}])
        fw = Framework.from_dict(caso["plan"])
        query = caso.get("query") or ""
        for g in D.GUARD_PIPELINE:
            prima = fw.to_dict()
            fw = g.fn(fw, intent, query, cat)
            if fw.to_dict() != prima:
                attive.add(g.name)
    assert len(attive) >= _ATTESE_ATTIVE, (
        f"il corpus esercita solo {len(attive)} guardie (attese >= "
        f"{_ATTESE_ATTIVE}): {sorted(attive)}")


def regen():
    cat = _catalog()
    golden = [_impronta(_esegui(c, cat)) for c in _casi()]
    _GOLDEN.write_text(json.dumps(golden, indent=0))
    print(f"golden rigenerato: {len(golden)} casi → {_GOLDEN}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))
    if "--regen" in sys.argv:
        regen()

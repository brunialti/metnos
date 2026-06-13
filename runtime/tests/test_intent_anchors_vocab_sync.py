"""Guard anti-drift: gli anchor del classificatore intent
(`intent_classifier/anchors.py`) DEVONO coprire ESATTAMENTE il vocabolario
chiuso `vocab.OBJECTS` (§2.2), in IT e in EN.

Razionale: il ramo di soccorso di `intent_extractor` (attivo in prod via
`METNOS_INTENT_CLASSIFIER=1`) usa questi anchor per cosine-similarity quando
l'LLM non produce object valido. Un object presente in vocab ma SENZA anchor
non e' classificabile → il classificatore cade sull'object piu' vicino =
misroute silenzioso. Bug storico (14/6): anchors fermo a 19 mentre vocab era a
22 — mancavano `calendars`/`issues`/`pulls`. Determinismo §7.9: confronto di
insiemi, niente modello.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RUNTIME))

from vocab import OBJECTS as VOCAB_OBJECTS  # noqa: E402


def _load_anchors():
    # Carica intent_classifier/anchors.py ISOLATO: evita il __init__ del package
    # (importerebbe il loader/modello). Il guard interno gira a import-time, quindi
    # un drift farebbe gia' fallire exec_module.
    path = _RUNTIME / "intent_classifier" / "anchors.py"
    spec = importlib.util.spec_from_file_location("_anchors_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_anchors_cover_vocab_objects_exactly():
    anchors = _load_anchors()
    voc = set(VOCAB_OBJECTS)
    it, en = set(anchors.ANCHORS_IT), set(anchors.ANCHORS_EN)
    assert it == voc, (
        f"ANCHORS_IT drift vs vocab.OBJECTS: "
        f"missing={sorted(voc - it)} extra={sorted(it - voc)}")
    assert en == voc, (
        f"ANCHORS_EN drift vs vocab.OBJECTS: "
        f"missing={sorted(voc - en)} extra={sorted(en - voc)}")
    assert set(anchors.OBJECTS) == voc


def test_anchor_texts_are_nonempty():
    anchors = _load_anchors()
    for obj, txt in {**anchors.ANCHORS_IT, **anchors.ANCHORS_EN}.items():
        assert txt and txt.strip(), f"anchor vuoto per object {obj!r}"

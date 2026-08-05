"""Regressione del cosine-floor L1 (autopath path-2 intent_hash, 14/6).

Il fallback intent_hash (verb|object) coincide anche fra query con SLOT diversi
(«mail di X» vs «tutte le mailbox 24h»): serviva il champion altrui. Fix: floor
`cosine(query, cluster-autopath) ≥ COSINE_FLOOR_INTENT` (0.87 dal 9/7 — era 0.82; su dati
reali: within-cluster p05=0.870 → 0 regressione; same-intent-cross-cluster
p50=0.795 → rigetta i misroute). Sotto floor: astieniti (None → engine pieno).

Test deterministici: `embed` mockato → cosine controllato via `_pack` (no BGE-M3).
"""
from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path
from unittest import mock


import engine.autopath as AP  # noqa: E402
from engine.types import Intent, Framework, StepSpec  # noqa: E402


def _pack(vec) -> bytes:
    return b"".join(struct.pack("<f", float(x)) for x in vec)


# Vettori a cosine NOTO vs base [1,0]:
_BASE = _pack([1.0, 0.0])           # cluster dell'autopath
_NEAR_086 = _pack([0.88, 0.4750])   # cosine ≈ 0.88 (fra FLOOR 0.87 e HIGH 0.90)
_NEAR_095 = _pack([0.95, 0.3122])   # cosine ≈ 0.95 (≥ HIGH → path 1)
_FAR_075 = _pack([0.75, 0.6614])    # cosine ≈ 0.75 (< FLOOR → astieniti)

_INTENT = Intent(verb="read", object="messages", keywords=["mailbox"])
_FW = Framework(steps=[StepSpec(tool="read_messages", args={}),
                       StepSpec(tool="final_answer", args={})],
                final_message="ok")


def _embed_map(q: str):
    return {"seed": _BASE, "near086": _NEAR_086,
            "near095": _NEAR_095, "far075": _FAR_075}.get(q)


def _seed_autopath(tmp, monkeypatch):
    """DB fresco + un autopath ACTIVE col cluster contenente l'osservazione _BASE."""
    monkeypatch.setattr(AP, "_db_path", lambda: Path(tmp) / "autopath.sqlite")
    AP._DB_INIT_DONE = False
    with mock.patch("engine.cluster.embed", side_effect=_embed_map):
        AP.record_observation(turn_id="t0", intent=_INTENT, framework=_FW,
                              query="seed", latency_ms=10)
        res = AP.record_feedback("t0", "ok")
    assert res.get("ok"), res
    # autopath promosso con cluster_id valido?
    c = AP._conn()
    row = c.execute("SELECT id, cluster_id FROM autopaths "
                    "WHERE status='active' AND champion=1").fetchone()
    c.close()
    assert row and row[1], "autopath non promosso o senza cluster"
    return row


def test_path2_serve_above_floor_no_regression(tmp_path, monkeypatch):
    # cosine 0.88: path1 fallisce (<0.90) MA floor 0.87 passa → SERVE (no-regressione)
    _seed_autopath(tmp_path, monkeypatch)
    with mock.patch("engine.cluster.embed", side_effect=_embed_map):
        hit = AP.lookup("near086", _INTENT)
    assert hit is not None, "sibling sopra-floor NON servito (regressione!)"


def test_path2_abstain_below_floor(tmp_path, monkeypatch):
    # cosine 0.75 < FLOOR 0.82: deve ASTENERSI (None → engine pieno)
    _seed_autopath(tmp_path, monkeypatch)
    with mock.patch("engine.cluster.embed", side_effect=_embed_map):
        hit = AP.lookup("far075", _INTENT)
    assert hit is None, "query semanticamente distante servita (floor non applicato)"


def test_path1_cluster_high_unaffected(tmp_path, monkeypatch):
    # cosine 0.95 ≥ COSINE_HIGH: path 1 serve, il floor non c'entra
    _seed_autopath(tmp_path, monkeypatch)
    with mock.patch("engine.cluster.embed", side_effect=_embed_map):
        hit = AP.lookup("near095", _INTENT)
    assert hit is not None, "path-1 (cluster ≥ HIGH) regredito"


def test_floor_is_env_tunable(tmp_path, monkeypatch):
    # abbassando il floor sotto 0.75, la query distante torna a servire → prova
    # che è proprio il floor a fare da gate (non altro).
    _seed_autopath(tmp_path, monkeypatch)
    monkeypatch.setattr(AP, "COSINE_FLOOR_INTENT", 0.70)
    with mock.patch("engine.cluster.embed", side_effect=_embed_map):
        hit = AP.lookup("far075", _INTENT)
    assert hit is not None, "con floor=0.70 la query 0.75 deve servire"

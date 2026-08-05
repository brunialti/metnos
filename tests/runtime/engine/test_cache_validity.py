"""ADR 0182 — disciplina di validità per le decisioni cachate (catalog-sig).

Proprietà: ogni piano cachato porta la firma del mondo in cui fu deciso; alla
lettura, mondo cambiato ⇒ MISS. Due assi:
  - tools_sig: digest dei tool referenziati (re-sign/rimozione ⇒ mismatch);
  - pool_sig: famiglie di candidati per l'intent (fratello nuovo ⇒ mismatch).
Layer: L0 fastpath (stamp+lookup+validate), L1 autopath (observation→promote→
lookup, refresh su ri-promozione), alternative-cache proposer (epoch in key).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

os.environ.setdefault("METNOS_ENGINE", "v3")


def _ex(name, digest="d0"):
    return SimpleNamespace(name=name, digest=digest)


def _cat(*pairs):
    return [_ex(n, d) for n, d in pairs]


def _fw(*tools):
    from engine.types import Framework, StepSpec
    return Framework(steps=[StepSpec(tool=t, args={}) for t in tools]
                     + [StepSpec(tool="final_answer", args={})])


def _intent(verb="list", obj="dirs", actions=None):
    from engine.types import Intent
    return Intent(verb=verb, object=obj, actions=actions or [])


BASE = (("list_dirs", "dA"), ("create_files_spreadsheet", "dB"),
        ("find_files", "dC"), ("read_files", "dD"), ("get_now", "dE"))


# ── funzioni di firma ─────────────────────────────────────────────────────

def test_tools_sig_stable_and_sensitive_to_digest():
    from engine.cache_validity import tools_sig
    fw = _fw("list_dirs", "create_files_spreadsheet")
    s1 = tools_sig(fw, _cat(*BASE))
    assert s1 == tools_sig(fw, _cat(*BASE))          # stabile
    changed = (("list_dirs", "dA2"),) + BASE[1:]     # re-sign di list_dirs
    assert s1 != tools_sig(fw, _cat(*changed))
    unrelated = BASE[:2] + (("find_files", "dC2"),) + BASE[3:]
    assert s1 == tools_sig(fw, _cat(*unrelated))     # tool NON referenziato


def test_tools_sig_missing_tool_differs():
    from engine.cache_validity import tools_sig
    fw = _fw("list_dirs")
    with_tool = tools_sig(fw, _cat(*BASE))
    without = tools_sig(fw, _cat(*BASE[1:]))         # list_dirs sparito
    assert with_tool != without


def test_pool_sig_new_sibling_invalidates():
    from engine.cache_validity import pool_sig
    it = _intent(verb="list", obj="dirs")
    s1 = pool_sig(it, _cat(*BASE))
    # fratello NUOVO nella famiglia produttori di `dirs`
    grown = BASE + (("find_dirs", "dF"),)
    assert s1 != pool_sig(it, _cat(*grown))
    # tool nuovo FUORI famiglia (altro oggetto, verbo non-produttore) → invariata
    other = BASE + (("send_messages", "dG"),)
    assert s1 == pool_sig(it, _cat(*other))


def test_pool_sig_file_carrier_capability_invalidates_image_plan():
    """Una capability files nuova può servire un intent images per path.

    La relazione viene dal vocabolario canonico FILE_CARRIER_OBJECTS: non
    dipende dalle parole della query e resta quindi sicura per la L1 condivisa.
    """
    from engine.cache_validity import pool_sig

    it = _intent(verb="find", obj="images")
    before = _cat(("find_images_indices", "dI"),
                  ("find_files", "dF"))
    s1 = pool_sig(it, before)
    after = before + [_ex("find_files_hash", "dH")]
    assert s1 != pool_sig(it, after)

    unrelated = before + [_ex("find_processes", "dP")]
    assert s1 == pool_sig(it, unrelated)


def test_pool_sig_compound_uses_all_clauses():
    from engine.cache_validity import pool_sig
    it = _intent(actions=[{"verb": "list", "object": "dirs"},
                          {"verb": "write", "object": "files"}])
    s1 = pool_sig(it, _cat(*BASE))
    grown = BASE + (("write_files_doc", "dH"),)      # famiglia write|files
    assert s1 != pool_sig(it, _cat(*grown))


def test_catalog_epoch_changes_on_any_digest():
    from engine.cache_validity import catalog_epoch
    e1 = catalog_epoch(_cat(*BASE))
    assert e1 == catalog_epoch(_cat(*BASE))
    assert e1 != catalog_epoch(_cat(*(BASE[:-1] + (("get_now", "dE2"),))))


def test_validate_empty_sig_is_miss():
    from engine.cache_validity import validate
    ok, why = validate("", "", _fw("list_dirs"), _intent(), _cat(*BASE))
    assert ok is False and "pre-ADR-0182" in why


def test_validate_roundtrip():
    from engine.cache_validity import plan_sigs, validate
    fw, it, cat = _fw("list_dirs"), _intent(), _cat(*BASE)
    ts, ps = plan_sigs(fw, it, cat)
    assert validate(ts, ps, fw, it, cat) == (True, "")
    # mondo cambiato: digest del tool referenziato
    cat2 = _cat(*((("list_dirs", "dA9"),) + BASE[1:]))
    ok, why = validate(ts, ps, fw, it, cat2)
    assert ok is False and "tools_sig" in why
    # mondo cambiato: fratello nuovo in famiglia
    cat3 = _cat(*(BASE + (("read_dirs", "dZ"),)))
    ok, why = validate(ts, ps, fw, it, cat3)
    assert ok is False and "pool_sig" in why


# ── L0 fastpath: stamp + lookup + invalidazione ───────────────────────────

def _fp_isolated(tmp_path, monkeypatch):
    from engine import fastpath as fp
    monkeypatch.setattr(fp, "_db_path", lambda: tmp_path / "fastpaths.sqlite")
    return fp


def test_fastpath_stamps_and_returns_sigs(tmp_path, monkeypatch):
    fp = _fp_isolated(tmp_path, monkeypatch)
    from engine import cache_validity as cv
    fw, it = _fw("list_dirs"), _intent()
    fp_id = fp.record_success("elenca la cartella /tmp", fw, intent=it,
                              catalog=_cat(*BASE))
    assert fp_id > 0
    hit = fp.lookup("elenca la cartella /tmp")
    assert hit is not None and hit.match_kind == "hash"
    assert hit.tools_sig and hit.pool_sig
    ok, _ = cv.validate(hit.tools_sig, hit.pool_sig, hit.framework, it,
                        _cat(*BASE))
    assert ok is True
    # mondo cambiato → la validate respinge l'hit
    ok, why = cv.validate(hit.tools_sig, hit.pool_sig, hit.framework, it,
                          _cat(*((("list_dirs", "dA9"),) + BASE[1:])))
    assert ok is False and "tools_sig" in why


def test_fastpath_rerecord_refreshes_sigs(tmp_path, monkeypatch):
    fp = _fp_isolated(tmp_path, monkeypatch)
    fw, it = _fw("list_dirs"), _intent()
    fp.record_success("q di prova", fw, intent=it, catalog=_cat(*BASE))
    s1 = fp.lookup("q di prova").tools_sig
    world2 = _cat(*((("list_dirs", "dA9"),) + BASE[1:]))
    fp.record_success("q di prova", fw, intent=it, catalog=world2)  # refresh
    s2 = fp.lookup("q di prova").tools_sig
    assert s1 != s2


def test_fastpath_no_catalog_records_empty_sigs(tmp_path, monkeypatch):
    """Senza catalogo (chiamante diretto): sig VUOTE = miss-once onesto,
    e NESSUN load_catalog implicito (side-effect aging)."""
    fp = _fp_isolated(tmp_path, monkeypatch)
    fp.record_success("q senza catalogo", _fw("list_dirs"), intent=_intent())
    hit = fp.lookup("q senza catalogo")
    assert hit is not None and hit.tools_sig == "" and hit.pool_sig == ""


# ── L1 autopath: observation → promote → lookup ───────────────────────────

def _ap_isolated(tmp_path, monkeypatch):
    from engine import autopath as ap
    monkeypatch.setattr(ap, "_db_path", lambda: tmp_path / "autopath.sqlite")
    return ap


def test_autopath_promotion_carries_sigs(tmp_path, monkeypatch):
    ap = _ap_isolated(tmp_path, monkeypatch)
    from engine import cache_validity as cv
    monkeypatch.setattr(ap, "MIN_OBS_PROMOTE", 1)
    fw, it = _fw("list_dirs"), _intent()
    ap.record_observation(turn_id="t1", intent=it, framework=fw, query="",
                          catalog=_cat(*BASE))
    out = ap.record_feedback("t1", "ok")
    apid = out.get("promoted_autopath_id")
    assert apid, out
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "autopath.sqlite"))
    ts, ps = c.execute("SELECT tools_sig, pool_sig FROM autopaths "
                       "WHERE id = ?", (apid,)).fetchone()
    c.close()
    assert ts and ps
    ok, _ = cv.validate(ts, ps, fw, it, _cat(*BASE))
    assert ok is True


def test_autopath_repromotion_refreshes_empty_sigs(tmp_path, monkeypatch):
    """Riga pre-migrazione (sig vuote): il nuovo feedback ✓ le rinfresca."""
    ap = _ap_isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(ap, "MIN_OBS_PROMOTE", 1)
    fw, it = _fw("list_dirs"), _intent()
    ap.record_observation(turn_id="t1", intent=it, framework=fw, query="",
                          catalog=_cat(*BASE))
    out = ap.record_feedback("t1", "ok")
    apid = out["promoted_autopath_id"]
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "autopath.sqlite"))
    c.execute("UPDATE autopaths SET tools_sig='', pool_sig='' WHERE id=?",
              (apid,))
    c.commit(); c.close()
    ap.record_observation(turn_id="t2", intent=it, framework=fw, query="",
                          catalog=_cat(*BASE))
    ap.record_feedback("t2", "ok")                    # ri-promozione
    c = sqlite3.connect(str(tmp_path / "autopath.sqlite"))
    ts, ps = c.execute("SELECT tools_sig, pool_sig FROM autopaths "
                       "WHERE id = ?", (apid,)).fetchone()
    c.close()
    assert ts and ps


# ── alternative-cache proposer: epoch nella chiave ────────────────────────

def test_proposer_cache_key_includes_epoch():
    from engine.proposer_metis import MetisProposer
    p = MetisProposer.__new__(MetisProposer)   # niente __init__ (solo _cache_key)
    it = _intent()
    k1 = p._cache_key("q", it, "it", _cat(*BASE))
    k2 = p._cache_key("q", it, "it", _cat(*BASE))
    assert k1 == k2
    k3 = p._cache_key("q", it, "it",
                      _cat(*((("list_dirs", "dA9"),) + BASE[1:])))
    assert k1 != k3

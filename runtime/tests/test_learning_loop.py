"""W1 learning-loop (ADR 0185) — crescere dall'esito dei turni reali.

Trigger (a): turno engine OK e costoso ripetuto → autopath SHADOW
             (`autopath.seed_from_run`), confermato champion dal primo ✓.
Trigger (b): lacuna terminator ricorrente → change_intent PROPOSED
             (`learning_loop.propose_from_lacuna`), dedup per fingerprint,
             MAI resurrezione di una proposta rifiutata.
Review (c):  `task_learning_loop_review` pota i seed shadow stantii.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

os.environ.setdefault("METNOS_ENGINE", "v3")


@pytest.fixture()
def ap_isolated(tmp_path, monkeypatch):
    from engine import autopath as ap
    monkeypatch.setattr(ap, "_db_path", lambda: tmp_path / "autopath.sqlite")
    monkeypatch.setattr(ap._cluster, "embed", lambda q: None)
    return ap


def _fw(*tools):
    from engine.types import Framework, StepSpec
    return Framework(
        steps=[StepSpec(tool=t, args={}) for t in tools],
        final_message="ok")


def _intent(verb="read", obj="files"):
    from engine.types import Intent
    return Intent(verb=verb, object=obj)


def _catalog(*names):
    from types import SimpleNamespace
    return [SimpleNamespace(name=n, digest=f"sha256:{n}") for n in names]


# ── Trigger (a): seed shadow ────────────────────────────────────────────────

def test_seed_from_run_after_repeat(ap_isolated):
    ap = ap_isolated
    fw = _fw("find_files", "read_files", "extract_entries", "create_files_doc")
    it = _intent()
    cat = _catalog("find_files", "read_files", "extract_entries",
                   "create_files_doc")
    # 1ª osservazione: sotto SEED_REPEAT → nessun seed
    ap.record_observation(turn_id="t1", intent=it, framework=fw,
                          query="q", catalog=cat)
    assert ap.seed_from_run(intent=it, framework=fw, n_steps=4,
                            catalog=cat) is None
    # 2ª: soglia raggiunta → SHADOW seminato
    ap.record_observation(turn_id="t2", intent=it, framework=fw,
                          query="q", catalog=cat)
    ap_id = ap.seed_from_run(intent=it, framework=fw, n_steps=4, catalog=cat)
    assert ap_id
    c = ap._conn()
    shadow, = c.execute("SELECT shadow FROM autopaths WHERE id=?",
                        (ap_id,)).fetchone()
    c.close()
    assert shadow == 1


def test_seed_requires_costly_turn(ap_isolated):
    ap = ap_isolated
    fw = _fw("get_now")
    it = _intent("get", "now")
    for t in ("t1", "t2", "t3"):
        ap.record_observation(turn_id=t, intent=it, framework=fw,
                              query="che ore sono", catalog=_catalog("get_now"))
    # n_steps sotto soglia → mai seed (il cold-start economico non si semina)
    assert ap.seed_from_run(intent=it, framework=fw, n_steps=1,
                            catalog=_catalog("get_now")) is None


def test_seed_noop_if_autopath_exists(ap_isolated):
    ap = ap_isolated
    fw = _fw("a", "b", "c", "d")
    it = _intent("find", "messages")
    cat = _catalog("a", "b", "c", "d")
    for t in ("t1", "t2"):
        ap.record_observation(turn_id=t, intent=it, framework=fw,
                              query="q", catalog=cat)
    first = ap.seed_from_run(intent=it, framework=fw, n_steps=5, catalog=cat)
    assert first
    # già presente → None (niente doppioni né rumore)
    assert ap.seed_from_run(intent=it, framework=fw, n_steps=5,
                            catalog=cat) is None


def test_human_feedback_confirms_shadow_to_champion(ap_isolated):
    ap = ap_isolated
    fw = _fw("a", "b", "c", "d")
    it = _intent("send", "messages")
    cat = _catalog("a", "b", "c", "d")
    for t in ("t1", "t2"):
        ap.record_observation(turn_id=t, intent=it, framework=fw,
                              query="q", catalog=cat)
    ap_id = ap.seed_from_run(intent=it, framework=fw, n_steps=6, catalog=cat)
    assert ap_id
    # ✓ umano sull'ULTIMO turno osservato → ri-promozione → shadow azzerato
    ap.record_feedback("t2", "ok")
    c = ap._conn()
    shadow, = c.execute("SELECT shadow FROM autopaths WHERE id=?",
                        (ap_id,)).fetchone()
    c.close()
    assert shadow == 0


def test_seed_counts_by_intent_not_framework_pair(ap_isolated):
    """Le parafrasi producono piani DIVERSI (misurato 6/7): il conteggio è
    per intent — la seconda osservazione, anche con framework diverso,
    semina il piano del run corrente."""
    ap = ap_isolated
    it = _intent("read", "files")
    fw1 = _fw("find_files", "read_files", "extract_entries", "create_files_doc")
    fw2 = _fw("find_dirs", "read_files", "extract_entries", "create_files_doc")
    cat = _catalog("find_files", "find_dirs", "read_files",
                   "extract_entries", "create_files_doc")
    ap.record_observation(turn_id="t1", intent=it, framework=fw1,
                          query="parafrasi uno", catalog=cat)
    ap.record_observation(turn_id="t2", intent=it, framework=fw2,
                          query="parafrasi due", catalog=cat)
    ap_id = ap.seed_from_run(intent=it, framework=fw2, n_steps=4, catalog=cat)
    assert ap_id
    c = ap._conn()
    fj, shadow = c.execute(
        "SELECT framework_json, shadow FROM autopaths WHERE id=?",
        (ap_id,)).fetchone()
    c.close()
    assert shadow == 1
    assert "find_dirs" in fj  # seminato il piano del RUN corrente (fw2)


# ── Trigger (b): lacuna → proposta ──────────────────────────────────────────

@pytest.fixture()
def ci_isolated(tmp_path, monkeypatch):
    import change_intents as ci
    monkeypatch.setattr(ci, "_DB_PATH", tmp_path / "change_intents.sqlite",
                        raising=False)
    # il modulo può usare una funzione per il path: copri entrambe le forme
    if hasattr(ci, "_db_path"):
        monkeypatch.setattr(ci, "_db_path",
                            lambda: tmp_path / "change_intents.sqlite")
    ci.init_db()
    return ci


def test_lacuna_below_threshold_no_intent(ci_isolated):
    import learning_loop as ll
    assert ll.propose_from_lacuna(
        lacuna_id="l1", query="fai una cosa nuova", verb="compute",
        object_="images", error_class="out_of_scope", n_seen=2) is None


def test_lacuna_at_threshold_creates_proposed_intent(ci_isolated):
    import learning_loop as ll
    ci = ci_isolated
    iid = ll.propose_from_lacuna(
        lacuna_id="l2", query="calcola l'istogramma delle foto",
        verb="compute", object_="images", error_class="out_of_scope",
        n_seen=3)
    assert iid
    got = ci.get_intent(iid)
    assert got.state == ci.STATE_PROPOSED
    assert got.origin_module == "learning_loop"
    assert got.intent_kind == ci.KIND_CREATE_EXECUTOR
    assert got.intent_target == "compute_images"


def test_lacuna_repeat_converges_no_duplicate(ci_isolated):
    import learning_loop as ll
    ci = ci_isolated
    a = ll.propose_from_lacuna(lacuna_id="l3", query="q", verb="order",
                               object_="files", error_class="wrong_tool",
                               n_seen=3)
    b = ll.propose_from_lacuna(lacuna_id="l3", query="q", verb="order",
                               object_="files", error_class="wrong_tool",
                               n_seen=4)
    assert a and b
    rows = ci.list_intents(origin_module="learning_loop", limit=50)
    assert len([r for r in rows if r.intent_target == "order_files"]) == 1


def test_rejected_proposal_does_not_resurrect(ci_isolated):
    import learning_loop as ll
    ci = ci_isolated
    iid = ll.propose_from_lacuna(lacuna_id="l4", query="q", verb="share",
                                 object_="dirs", error_class="out_of_scope",
                                 n_seen=3)
    ci.apply_decision(iid, action="reject", by="test", reason="no")
    # la stessa lacuna ricorre ancora → l'upsert PRESERVA lo stato rejected
    ll.propose_from_lacuna(lacuna_id="l4", query="q", verb="share",
                           object_="dirs", error_class="out_of_scope",
                           n_seen=5)
    got = ci.get_intent(iid)
    assert got.state == ci.STATE_REJECTED


def test_usage_error_classes_do_not_propose(ci_isolated):
    import learning_loop as ll
    assert ll.propose_from_lacuna(
        lacuna_id="l5", query="q", verb="read", object_="files",
        error_class="wrong_args", n_seen=9) is None


# ── Review (c): prune shadow stantii ────────────────────────────────────────

def test_review_prunes_stale_shadow(ap_isolated, monkeypatch):
    ap = ap_isolated
    fw = _fw("a", "b", "c", "d")
    it = _intent("list", "dirs")
    cat = _catalog("a", "b", "c", "d")
    for t in ("t1", "t2"):
        ap.record_observation(turn_id=t, intent=it, framework=fw,
                              query="q", catalog=cat)
    ap_id = ap.seed_from_run(intent=it, framework=fw, n_steps=4, catalog=cat)
    assert ap_id
    # invecchia il seed oltre TTL
    c = ap._conn()
    c.execute("UPDATE autopaths SET ts_last_used='2020-01-01T00:00:00Z', "
              "ts_created='2020-01-01T00:00:00Z' WHERE id=?", (ap_id,))
    c.commit(); c.close()
    import jobs.maintenance_tasks as mt
    rep = mt.task_learning_loop_review()
    assert rep["shadow_pruned"] >= 1
    c = ap._conn()
    assert c.execute("SELECT COUNT(*) FROM autopaths WHERE id=?",
                     (ap_id,)).fetchone()[0] == 0
    c.close()


# ── Reaper C3 (ADR 0182 follow-up): morte-da-catalogo per autopath ──────────

def test_prune_kills_autopath_with_missing_tool(ap_isolated):
    ap = ap_isolated
    it = _intent("send", "messages")
    cat = _catalog("send_messages", "tool_sparito")
    fw = _fw("send_messages", "tool_sparito", "x", "y")
    for t in ("t1", "t2"):
        ap.record_observation(turn_id=t, intent=it, framework=fw,
                              query="q", catalog=cat)
    ap_id = ap.seed_from_run(intent=it, framework=fw, n_steps=4, catalog=cat)
    assert ap_id
    # catalogo SENZA tool_sparito → morte C3
    rep = ap.prune(catalog_names={"send_messages", "x", "y"})
    assert rep["autopaths_dead_catalog"] == 1
    # senza catalog_names → MAI morte (contratto §2.8 no falsi kill)
    rep2 = ap.prune(catalog_names=None)
    assert rep2["autopaths_dead_catalog"] == 0

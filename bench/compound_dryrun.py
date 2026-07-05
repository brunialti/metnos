#!/usr/bin/env python3
"""Compound planning dry-run — banco di prova FASE 3 (redesign proposer).

Replica la SOLA PIANIFICAZIONE L3 di `dispatch.run_turn` (normalize store →
build_routing_pool → proposer.propose → guard get_inputs/dropped → structure
guards) SENZA eseguire: nessun commento github, nessuna scrittura store. Stampa
il framework pianificato + un checklist di accettazione (provider/ordine/args).

Reimpiega le funzioni REALI di dispatch (no copia di logica): l'unica cosa
replicata e' la SEQUENZA di orchestrazione (run_turn:652-737), cosi' il dry-run
riflette la produzione per costruzione.

Uso:
  METNOS_ENGINE=v3   python3 bench/compound_dryrun.py [--query "..."]
  METNOS_ENGINE=metis python3 bench/compound_dryrun.py     # confronto v2
"""
from __future__ import annotations
import argparse, os, sys, dataclasses
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "runtime"))

# Env di PRODUZIONE (drop-in proposer-hardening.conf, §11). setdefault: un env
# esplicito (METNOS_ENGINE=v3) vince. Engine v3 = redesign sotto test.
os.environ.setdefault("METNOS_ENGINE", "v3")  # default = PROD (drop-in v3); metis via env
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_PREFILTER_RULES", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")

# FASE 3 publish (frasi provate V1-V6, issue-flow 17/6): nominare la COLONNA
# `status` (non «stato») + «commento su github» + «aggiorna ... posted».
FASE3_DEFAULT = ("Cerca nello store github_issue_qa le entries con campo status "
                 "uguale a 'approved', pubblica il commento su github e poi "
                 "aggiorna nello store il campo status a 'posted'")

# Varianti di stress: A=3 clausole (find/send/set); B=4 clausole con approvazione
# umana (find/approval/send/set); C=phrasing originale «db locale» (issue-flow
# 17/6, quello che falliva: misroute find_pulls_github + compound non assemblato).
VARIANTS = {
    "A_3clause": FASE3_DEFAULT,
    "B_4clause_approval": (
        "Cerca nello store github_issue_qa le entries con campo status uguale a "
        "'approved', chiedi la mia approvazione, pubblica il commento su github "
        "e infine aggiorna nello store il campo status a 'posted'"),
    "C_dblocale_orig": (
        "Leggi dal db locale le issue con stato 'approved', pubblica il commento "
        "su Github e salva lo stato 'posted'"),
    # D: 4 clausole HARD generali (no provider/store/approval) → prova la
    # capacita' UNIVERSALE di comporre compound 4+ (obiettivo Roberto), senza i
    # confondenti di FASE 3. find→compress→send→delete (ordine + from_step).
    "D_general_4clause": (
        "Trova i file .log nella cartella /tmp/logs, comprimili in un archivio "
        "zip, mandami l'archivio via mail a roberto@example.com e poi cancella "
        "i file .log originali"),
    # E/F: altre 4-clausole HARD sane, domini DIVERSI → robustezza della
    # composizione compound oltre il singolo caso D (evidenza per la decisione
    # binding-skeleton).
    "E_find_compress_move_send": (
        "Trova i file .tmp nella cartella /tmp, comprimili in backup.zip, "
        "spostali nella cartella /archivio e mandami conferma via mail a "
        "roberto@example.com"),
    "F_read_write_send_move": (
        "Leggi le mail non lette di oggi, salvale nello store posta, mandamene "
        "il riepilogo via mail a roberto@example.com e poi spostale nella "
        "cartella Archivio"),
}


def acceptance_intent_order(steps, actions):
    """Generico: gli step HARD (producer/mutating) appaiono nell'ORDINE di
    intent.actions (universale «ordine corretto»). SOFT (transform) ignorati."""
    try:
        import naming_grammar as _ng
        from compound_decomposer import TRANSFORM_VERBS as _SOFT
    except Exception:
        return {"order_matches_intent": None, "n_exec": 0}
    act_seq = [((a.get("verb") or ""), (a.get("object") or "")) for a in actions]
    hard = []
    for s in steps:
        t = s.get("tool")
        if not t or t == "final_answer":
            continue
        nc = _ng.parse_name(t)
        if nc and nc.verb in _SOFT:
            continue
        hard.append((nc.verb if nc else "", nc.obj if nc else ""))
    # ogni step HARD deve corrispondere, in ordine, a una action (per verbo)
    ai = 0
    matched = 0
    for hv, ho in hard:
        while ai < len(act_seq) and act_seq[ai][0] != hv:
            ai += 1
        if ai < len(act_seq):
            matched += 1
            ai += 1
    return {"order_matches_intent": matched == len(hard) and len(hard) >= 2,
            "n_hard": len(hard), "n_exec": len([s for s in steps
                                                if s.get("tool") != "final_answer"])}


def acceptance_general(steps):
    """Checklist variante D: 4 verbi attesi presenti + ordine find<compress<
    send<delete (universale, no FASE 3-specifico)."""
    tools = [s.get("tool") for s in steps if s.get("tool")]
    idx = {t: i for i, t in enumerate(tools)}
    def vfam(prefix):  # primo tool col verbo
        return next((t for t in tools if t.split("_", 1)[0] == prefix), None)
    f, c = vfam("find"), vfam("compress")
    s, d = vfam("send"), vfam("delete")
    present = all([f, c, s, d])
    def before(a, b):
        return a in idx and b in idx and idx[a] < idx[b]
    order = present and before(f, c) and before(c, s) and before(s, d)
    return {"4 verbs present (find/compress/send/delete)": present,
            "order find<compress<send<delete": order,
            "n_exec": len([t for t in tools if t != "final_answer"])}


def build_calls():
    from llm_router import LLMRouter
    r = LLMRouter()
    def fast(system, user, max_tokens=320, think=False):
        return getattr(r.provider("fast").chat(system, user, max_tokens=max_tokens,
                                                temperature=0, think=think), "text", "")
    def wise(system, user, *, max_tokens=2048, think=True, **kw):
        ck = {"max_tokens": max_tokens, "think": think}
        if kw.get("grammar") is not None: ck["grammar"] = kw["grammar"]
        return (getattr(r.provider("wise").chat(system, user, **ck), "text", "") or "").strip()
    return fast, wise


def plan_only(query, cat, fast, wise):
    """Pianifica come run_turn L3, senza eseguire. Ritorna il Framework finale."""
    from engine.types import Intent
    from engine.routing_pool import build_routing_pool
    from engine.proposer import get_proposer
    from engine import dispatch as D
    from engine.executor import compute_framework_hash
    from intent_extractor import extract_intent

    ir = extract_intent(query, fast) or {}
    intent = Intent(verb=(ir.get("verb") or "").lower(),
                    object=(ir.get("object") or "").lower(),
                    keywords=list(ir.get("keywords") or []),
                    confidence=float(ir.get("confidence") or 1.0),
                    lang="it", actions=list(ir.get("actions") or []))
    actions_str = [(a.get("verb"), a.get("object")) for a in (intent.actions or [])]

    # Allineato a run_turn: de-contaminazione oggetti-clausola (v3) PRIMA di
    # normalize_store/pool. Senza, il bench misurava come se il guard non
    # esistesse (il guard vive in run_turn, plan_only lo replica a mano).
    from engine import is_v3
    if is_v3():
        D._decontaminate_clause_objects(intent, query)
        D._fix_unroutable_verbs(intent, query, cat)
    D._normalize_store_clauses(intent, query, cat)
    pool = build_routing_pool(query, intent, cat)
    proposer = get_proposer()
    fw = proposer.propose(query=query, intent=intent, pool=pool,
                          excluded_hashes=set(), llm_call=wise, lang="it", catalog=cat)
    if fw is None:
        return None, intent, actions_str, pool
    # guard get_inputs-misroute (run_turn:680)
    if D._is_get_inputs_misroute(fw):
        _h = compute_framework_hash(fw)
        fw2 = proposer.propose(query=query, intent=intent, pool=pool,
                               excluded_hashes={_h}, llm_call=wise, lang="it",
                               catalog=cat, exclude_tools=("get_inputs",))
        if fw2 is not None and not D._is_get_inputs_misroute(fw2):
            fw = fw2
    # guard decomposizione incompleta (run_turn:706)
    dropped = D._dropped_required_verbs(fw, query, intent)
    if dropped:
        _h = compute_framework_hash(fw)
        cover = [a for a in (intent.actions or [])
                 if isinstance(a, dict) and a.get("verb") in dropped]
        try:
            setattr(intent, "_repropose_cover", cover)
            fw2 = proposer.propose(query=query, intent=intent, pool=pool,
                                   excluded_hashes={_h}, llm_call=wise, lang="it",
                                   catalog=cat)
        finally:
            try: delattr(intent, "_repropose_cover")
            except Exception: pass
        if fw2 is not None and len(D._dropped_required_verbs(fw2, query, intent)) < len(dropped):
            fw = fw2
    # structure guards deterministici (run_turn:736)
    fw = D._apply_deterministic_structure_guards(fw, intent, query, cat)
    return fw, intent, actions_str, pool


def _steps(fw):
    d = dataclasses.asdict(fw) if fw and dataclasses.is_dataclass(fw) else (fw or {})
    return d.get("steps") or []


def acceptance(steps):
    """Checklist FASE 3: provider / ordine / status arg."""
    tools = [s.get("tool") for s in steps if s.get("tool")]
    execs = [t for t in tools if t != "final_answer"]
    idx = {t: i for i, t in enumerate(tools)}
    has = lambda t: t in tools
    # provider: post via send_messages_github, NON il generico send_messages
    prov = has("send_messages_github") and not has("send_messages")
    # ordine: find_entries < send_messages_github < write_entries
    def before(a, b):
        return has(a) and has(b) and idx[a] < idx[b]
    order = (before("find_entries", "send_messages_github")
             and before("send_messages_github", "write_entries"))
    # args: write_entries con status=posted (top-level O dentro fields={} =
    # shape update dello store generico). FASE 3 → 'posted'.
    we = next((s for s in steps if s.get("tool") == "write_entries"), None)
    we_args = (we.get("args") or {}) if we else {}
    we_status = (str(we_args.get("status", "")).lower()
                 or str((we_args.get("fields") or {}).get("status", "")).lower()
                 or str((we_args.get("set_fields") or {}).get("status", "")).lower())
    status_ok = bool(we) and we_status == "posted"
    return {"provider(send_github)": prov, "order(find<send<write)": order,
            "status=posted": status_ok, "n_exec": len(execs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default=None, help="query singola (override varianti)")
    ap.add_argument("--variant", default=None, help="A_3clause|B_4clause_approval|C_dblocale_orig")
    ap.add_argument("--runs", type=int, default=1, help="ripetizioni (flakiness)")
    args = ap.parse_args()

    from store_bootstrap import register_builtin_stores
    register_builtin_stores()
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from agent_runtime import _engine_v2_catalog_with_builtins
    cat = _engine_v2_catalog_with_builtins(
        filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER))
    fast, wise = build_calls()

    if args.query:
        queries = {"custom": args.query}
    elif args.variant:
        queries = {args.variant: VARIANTS[args.variant]}
    else:
        queries = VARIANTS

    engine = os.environ.get("METNOS_ENGINE")
    print(f"\n===== ENGINE={engine}  runs={args.runs} =====")
    for vname, query in queries.items():
        print(f"\n--- {vname} ---\nQUERY: {query}")
        all_plans = []
        last = None
        for r in range(args.runs):
            fw, intent, actions, pool = plan_only(query, cat, fast, wise)
            steps = _steps(fw)
            plan_sig = " → ".join(s.get("tool") for s in steps)
            all_plans.append(plan_sig)
            last = (steps, intent, actions, pool)
        steps, intent, actions, pool = last
        flaky = len(set(all_plans)) > 1
        print(f"intent.actions = {actions}")
        print(f"PLAN{' (FLAKY!)' if flaky else ''}:")
        for i, s in enumerate(steps, 1):
            print(f"  {i}) {s.get('tool')}  args={s.get('args') or {}}")
        if flaky:
            for p in sorted(set(all_plans)):
                print(f"   variant-seen: {p}")
        if "general" in vname:
            acc = acceptance_general(steps)
        elif vname.startswith(("E_", "F_")):
            acc = acceptance_intent_order(steps, last[2] and
                                          [{"verb": v, "object": o} for v, o in last[2]])
        else:
            acc = acceptance(steps)
        allok = all(v is True for k, v in acc.items() if k not in ("n_exec", "n_hard"))
        print(f"ACCEPTANCE{'  ✓ALL' if allok and not flaky else ''}:")
        for k, v in acc.items():
            mark = "OK " if v is True else ("-- " if v is False else "   ")
            print(f"  {mark}{k}: {v}")


if __name__ == "__main__":
    main()

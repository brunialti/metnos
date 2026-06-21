"""engine/fastpath_promote.py — promozione fastpath L0 → executor synt.

Un CLUSTER di fastpath ricorrenti (stessa shape di piano + stesso intent)
è il segnale che un pattern multi-step merita un executor di prima classe.
Job notturno (task_fastpath_promotion, accanto a prune/reaper). Due tier:

  TIER 1 (default, LIVE) — il candidato emette una PROPOSTA human-gated nel
  backlog introvertiva (`proposals_state`, kind='fastpath_promote'): visibile
  in /admin/proposals e nell'hub unificato. L'approve scrive il marker
  `synt_pending/` (stesso canale accept→synth delle proposte introspettive,
  consumato da telos_synth_consumer → handle_synth_request).

  TIER 2 (flag `METNOS_FASTPATH_AUTOPROMOTE`, OFF default) — sopra un floor
  MOLTO più alto auto-sintetizza SUBITO via `handle_synth_request` (pipeline
  synt COMPLETA 5 stadi + birth test + firma + install: l'executor nasce
  vagliato come ogni synt — è ciò che rende l'auto-promozione sicura).
  Hard cap 1 tentativo/notte.

GATING CONSERVATIVO (vincolo n.1, memoria 1017-proposte/2-accettate):
cluster-based MAI per-istanza; SOLO multi-step (il mono-step ha già il suo
executor: il valore L0 lì è saltare l'LLM, non il piano); ≥3 fastpath
DISTINTI; uso cumulato ≥ soglia; età ≥30g; dedupe vs catalog (§2.2 famiglia)
+ vs proposte pendenti (sig_key idempotente + generalize introvertiva);
cap emissioni nuove per notte. Niente catalog completo → niente emissione
(meglio nessuna proposta che un duplicato, §2.8).

PROVENIENZA: ogni candidato emesso registra i (fp_id, canonical_hash) di
origine in `fastpath.record_promotion` → quando l'executor entra nel
catalog, la morte C2 è ESATTA per provenienza (vedi fastpath.prune).
Limite documentato: per i candidati "composizione" (la catena USA già la
famiglia verb_object dell'intent) il nome finale richiede un qualifier
scelto dall'umano → niente provenienza automatica né marker synth
(l'approve resta bookkeeping; pulizia fastpath via /admin/praxis).

§7.9 deterministico: nessun LLM in detection; LLM solo dentro la pipeline
synt riusata. §7.2: riusa proposals_state, proposal_actions, synth_request.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from . import fastpath as _fp
from .types import Framework
from .executor import compute_framework_hash

log = logging.getLogger(__name__)

KIND = "fastpath_promote"

# Tool il cui wrapping in un executor sintetizzato è scorretto o pericoloso:
# meta-tool del runtime (sintesi, sudoer, sessioni autenticate, scratchpad).
# Set CHIUSO (§2.2): estendere solo per la stessa classe di motivi.
NON_PROMOTABLE_TOOLS = frozenset({
    "admin", "sudoer", "request_new_executor", "login_session",
    "scratchpad_read",
})

# ── Floor (env-tunable; default PROPOSTI, da confermare con Roberto) ───────


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def tier1_floor() -> dict:
    return {
        "min_cluster": _env_int("METNOS_FP_PROMOTE_MIN_CLUSTER", 3),
        "min_uses": _env_int("METNOS_FP_PROMOTE_MIN_USES", 15),
        "min_age_days": _env_int("METNOS_FP_PROMOTE_MIN_AGE_DAYS", 30),
        "max_new_per_night": _env_int("METNOS_FP_PROMOTE_MAX_PER_NIGHT", 3),
    }


def tier2_floor() -> dict:
    return {
        "min_cluster": _env_int("METNOS_FP_AUTOPROMOTE_MIN_CLUSTER", 5),
        "min_uses": _env_int("METNOS_FP_AUTOPROMOTE_MIN_USES", 50),
        "min_age_days": _env_int("METNOS_FP_PROMOTE_MIN_AGE_DAYS", 30),
        "min_nights": _env_int("METNOS_FP_AUTOPROMOTE_MIN_NIGHTS", 3),
    }


def autopromote_enabled() -> bool:
    """Flag tier 2 — OFF di default finché Roberto non lo abilita."""
    return (os.environ.get("METNOS_FASTPATH_AUTOPROMOTE", "")
            .strip().lower() in ("1", "true", "on"))


# ── Detection (deterministica, cluster-based) ───────────────────────────────


def _parse_iso_ts(s: str) -> float:
    # calendar.timegm interpreta la struct come UTC (la stringa è '...Z'):
    # mktime la interpretava come LOCAL e `- time.timezone` sbagliava di 1h su
    # host in DST (time.timezone ignora l'ora legale). UTC-safe (bug 21/6).
    import calendar
    try:
        return float(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError, OverflowError):
        return 0.0


# famiglia §2.2 (verb_object[_qualifier]) — definizione unica in fastpath
_in_family = _fp._in_family


def sig_key_for(expected_name: str, chain: list[str]) -> list:
    """Chiave STABILE della proposta (dedupe per costruzione in
    proposals_state): nome atteso + catena tool della shape. I membri del
    cluster (fp_id) NON entrano: cambiano notte per notte."""
    return [KIND, expected_name, list(chain)]


def detect_candidates(*, catalog_names: set,
                      now_ts: Optional[float] = None) -> dict:
    """Identifica i cluster candidati alla promozione. Deterministico.

    Cluster = fastpath con stessa SHAPE di piano (compute_framework_hash:
    sequenza tool + args keys, MAI i valori literal) e stesso intent
    (verb, object) — la semantica che il sistema già usa per naming §2.2 e
    morte C2. Membri distinti per costruzione (canonical_hash UNIQUE).

    Gate tier 1 (tutti AND, conteggi onesti in `rejected`):
      multi-step ≥2 · niente NON_PROMOTABLE_TOOLS · membri ≥ min_cluster ·
      usi cumulati ≥ min_uses · età cluster (membro più vecchio) ≥
      min_age_days · intent presente + expected_name §2.2-valido · famiglia
      intent nel catalog ma NON nella catena → già coperto (la C2 name-based
      li poterà comunque).

    `kind` del candidato: 'free' (famiglia intent assente dal catalog: il
    nome stem è sintetizzabile direttamente) oppure 'composition' (la catena
    usa già la famiglia: serve qualifier umano → solo tier 1, no marker).

    Returns: {"candidates": [...], "rejected": {reason: n}, "scanned": n}.
    """
    floor = tier1_floor()
    now = now_ts if now_ts is not None else time.time()
    rejected: dict[str, int] = {}

    def _rej(reason: str, n: int = 1) -> None:
        rejected[reason] = rejected.get(reason, 0) + n

    try:
        c = _fp._conn()
        rows = c.execute(
            "SELECT id, canonical_text, canonical_hash, framework_json, "
            "intent_verb, intent_object, created_at, n_uses "
            "FROM fastpaths").fetchall()
        c.close()
    except Exception as ex:
        log.warning("fastpath_promote: load store fallito: %r", ex)
        return {"candidates": [], "rejected": {"store_error": 1}, "scanned": 0}

    clusters: dict[tuple, dict] = {}
    for fp_id, ctext, chash, fjson, iverb, iobj, created_at, n_uses in rows:
        try:
            fw = Framework.from_dict(json.loads(fjson))
        except Exception:
            _rej("unparseable")
            continue
        tools = _fp.framework_tools(fjson)
        if len(tools) < 2:
            # SOLO multi-step: il mono-step ha già il suo executor.
            _rej("mono_step")
            continue
        if not iverb or not iobj:
            _rej("no_intent")
            continue
        shape = compute_framework_hash(fw)
        key = (shape, iverb, iobj)
        cl = clusters.setdefault(key, {
            "shape": shape, "verb": iverb, "object": iobj,
            "chain": tools, "members": [], "samples": [],
            "cum_uses": 0, "oldest_ts": now,
        })
        cl["members"].append((int(fp_id), chash or ""))
        if len(cl["samples"]) < 3:
            cl["samples"].append(ctext)
        cl["cum_uses"] += int(n_uses or 0)
        created_ts = _parse_iso_ts(created_at or "")
        if created_ts and created_ts < cl["oldest_ts"]:
            cl["oldest_ts"] = created_ts

    candidates: list[dict] = []
    for cl in clusters.values():
        if NON_PROMOTABLE_TOOLS.intersection(cl["chain"]):
            _rej("non_promotable_tool")
            continue
        if len(cl["members"]) < floor["min_cluster"]:
            _rej("too_few_members")
            continue
        if cl["cum_uses"] < floor["min_uses"]:
            _rej("low_usage")
            continue
        age_days = (now - cl["oldest_ts"]) / 86400.0
        if age_days < floor["min_age_days"]:
            _rej("too_young")
            continue
        expected_name = f"{cl['verb']}_{cl['object']}"
        try:
            from naming_grammar import validate_name
            if not validate_name(expected_name).ok:
                _rej("invalid_name")
                continue
        except ImportError:
            _rej("invalid_name")
            continue
        family_in_catalog = any(_in_family(n, expected_name)
                                for n in catalog_names)
        family_in_chain = any(_in_family(t, expected_name)
                              for t in cl["chain"])
        if family_in_catalog and not family_in_chain:
            # Un executor della famiglia copre già l'intent e il piano non
            # lo usa: la morte C2 name-based poterà questi fastpath alla
            # prossima notte — proporre sarebbe un duplicato.
            _rej("already_covered")
            continue
        kind = "composition" if family_in_chain else "free"
        candidates.append({
            "expected_name": expected_name,
            "kind": kind,
            "chain": cl["chain"],
            "shape": cl["shape"],
            "n_distinct": len(cl["members"]),
            "cum_uses": cl["cum_uses"],
            "age_days": round(age_days, 1),
            "members": cl["members"],
            "samples": cl["samples"],
            "sig_key": sig_key_for(expected_name, cl["chain"]),
        })

    # Ordine deterministico: usi cumulati desc, poi nome (§7.9).
    candidates.sort(key=lambda d: (-d["cum_uses"], d["expected_name"]))
    return {"candidates": candidates, "rejected": rejected,
            "scanned": len(rows)}


# ── Tier 1: emissione proposta nel backlog introvertiva ─────────────────────


def _generalize_pending(chain: list[str]) -> bool:
    """True se esiste già una proposta introvertiva GENERALIZE attiva
    (pending/dormant) per la STESSA catena: emettere anche la nostra
    sarebbe rumore doppio sullo stesso pattern."""
    try:
        import proposals_state as ps
        key = ps._canonical(["generalize", list(chain)])
        conn = ps._open()
        try:
            r = conn.execute(
                "SELECT state FROM proposals_state WHERE sig_key = ?",
                (key,)).fetchone()
        finally:
            conn.close()
        return bool(r) and r["state"] in ("pending", "dormant")
    except Exception:
        return False


def intent_text(candidate: dict) -> str:
    """Testo intent per la cascata synt (stage 1 NAMING legge le richieste
    utente reali; la catena dà il contesto del piano da coprire). Input
    LLM-facing interno, non user-facing."""
    chain = " → ".join(candidate.get("chain", []))
    samples = " · ".join(f"«{s}»" for s in candidate.get("samples", [])[:3])
    if not samples:
        return (f"Un executor che copre in un solo passo il pattern "
                f"ricorrente di piano: {chain}. Input e output coerenti "
                f"con la catena coperta.")
    return (f"Un executor che copre in un solo passo queste richieste "
            f"ricorrenti: {samples}. Oggi richiedono la catena {chain}. "
            f"Input e output coerenti con la catena coperta.")


def run_nightly(*, catalog_names: Optional[set] = None,
                now_ts: Optional[float] = None) -> dict:
    """Job notturno: detection → tier 1 (proposte) → tier 2 (auto, flag).

    `catalog_names` DEVE essere il set COMPLETO dei tool invocabili (stesso
    contratto di fastpath.prune). None → SOLO report della detection a
    catalog vuoto impossibile: niente emissione (un dedupe-vs-catalog cieco
    produrrebbe duplicati, §2.8 + gating conservativo).
    """
    floor = tier1_floor()
    report: dict = {"ok": True, "floor_tier1": floor,
                    "emitted": [], "refreshed": [], "deferred_cap": [],
                    "provenance_rows": 0}
    if not catalog_names:
        report.update({"ok": False, "reason": "catalog_unavailable",
                       "candidates": 0, "rejected": {}})
        log.warning("fastpath_promote: catalog incompleto → nessuna "
                    "emissione stanotte")
        return report

    det = detect_candidates(catalog_names=catalog_names, now_ts=now_ts)
    report["scanned"] = det["scanned"]
    report["rejected"] = det["rejected"]
    report["candidates"] = len(det["candidates"])

    try:
        import proposals_state as ps
    except ImportError:
        report.update({"ok": False, "reason": "proposals_state_unavailable"})
        return report

    new_budget = floor["max_new_per_night"]
    for cand in det["candidates"]:
        if _generalize_pending(cand["chain"]):
            det["rejected"]["pending_generalize"] = (
                det["rejected"].get("pending_generalize", 0) + 1)
            continue
        key = ps._canonical(cand["sig_key"])
        conn = ps._open()
        try:
            existing = conn.execute(
                "SELECT state FROM proposals_state WHERE sig_key = ?",
                (key,)).fetchone()
        finally:
            conn.close()
        is_new = existing is None
        if is_new and new_budget <= 0:
            # Cap anti-esplosione sulle NUOVE emissioni; i refresh delle
            # proposte già note passano sempre (n_seen serve al tier 2).
            report["deferred_cap"].append(cand["expected_name"])
            continue
        row = ps.touch_or_insert(cand["sig_key"], KIND, cand["cum_uses"])
        cand["state"] = row.state
        cand["n_seen"] = row.n_seen
        cand["last_action"] = row.last_action
        if is_new:
            new_budget -= 1
            report["emitted"].append(cand["expected_name"])
            log.info("fastpath_promote: PROPOSTA %s (membri=%d usi=%d "
                     "età=%.0fg kind=%s)", cand["expected_name"],
                     cand["n_distinct"], cand["cum_uses"],
                     cand["age_days"], cand["kind"])
        else:
            report["refreshed"].append(cand["expected_name"])
        # Provenienza: solo per i candidati 'free' (nome direttamente
        # sintetizzabile). Inerte finché l'executor non entra nel catalog.
        if cand["kind"] == "free":
            report["provenance_rows"] += _fp.record_promotion(
                cand["expected_name"], cand["members"], tier=1)

    report["tier2"] = _maybe_autopromote(det["candidates"], catalog_names)
    return report


# ── Approve tier 1 → marker synt (stesso canale accept→synth telos) ─────────


def _cluster_samples(expected_name: str, chain: list[str]) -> list[str]:
    """Ri-estrae fino a 3 query rappresentative del cluster dal vivo store
    (per l'intent text del marker all'approve). Store potato nel frattempo
    → lista vuota: intent_text degrada alla sola catena."""
    parts = expected_name.split("_", 1)
    if len(parts) != 2:
        return []
    verb, obj = parts
    out: list[str] = []
    try:
        c = _fp._conn()
        rows = c.execute(
            "SELECT canonical_text, framework_json FROM fastpaths "
            "WHERE intent_verb = ? AND intent_object = ? "
            "ORDER BY n_uses DESC", (verb, obj)).fetchall()
        c.close()
        for ctext, fjson in rows:
            if _fp.framework_tools(fjson) == list(chain):
                out.append(ctext)
                if len(out) >= 3:
                    break
    except Exception:
        return []
    return out


def on_proposal_approved(sig_key) -> dict:
    """Effetto operativo dell'approve umano su una proposta
    kind='fastpath_promote' (chiamato dalle route admin DOPO mark_action).

    Riusa il canale accept→synth esistente (proposal_actions): scrive il
    marker `synt_pending/<sig>.json` che telos_synth_consumer (notturno)
    consegna a handle_synth_request → pipeline synt completa. Idempotente
    per signature. Composizione (la catena usa già la famiglia §2.2 del
    nome) → nessun marker: il nome finale richiede un qualifier scelto
    dall'umano (handle_synth_request con lo stem corto-circuiterebbe
    'already_in_catalog').

    Returns dict {kind, ...} per visibilità nella risposta HTTP (§2.8).
    """
    try:
        parsed = sig_key if isinstance(sig_key, list) else json.loads(sig_key)
    except (TypeError, ValueError):
        return {"kind": "noop", "reason": "sig_key_unparseable"}
    if (not isinstance(parsed, list) or len(parsed) < 3
            or parsed[0] != KIND or not isinstance(parsed[2], list)):
        return {"kind": "noop", "reason": "not_a_fastpath_promote_sig"}
    expected_name = str(parsed[1])
    chain = [str(t) for t in parsed[2]]
    if any(_in_family(t, expected_name) for t in chain):
        return {"kind": "noop",
                "reason": "composition_requires_human_naming",
                "expected_name": expected_name}
    candidate = {"expected_name": expected_name, "chain": chain,
                 "samples": _cluster_samples(expected_name, chain)}
    import hashlib
    import proposal_actions as pa
    # Stessa canonicalizzazione di proposals_state._canonical → l'hash
    # coincide con il prop_id introvertiva dell'hub (`intr:<sha16>`).
    canonical = json.dumps(parsed, sort_keys=True, default=str)
    sig = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    created = pa._write_marker(pa.SYNT_PENDING_DIR, sig, {
        "sig": sig,
        "prop_id": f"intr:{sig}",
        "source": f"introvertiva:{KIND}",
        "kind": "synt_request",
        "expected_name": expected_name,
        "intent": intent_text(candidate),
        "proposed_action": intent_text(candidate),
        "rationale": "promozione cluster fastpath L0 (approve umano)",
        "ts": time.time(),
        "by": "admin",
    })
    log.info("fastpath_promote: approve %s → marker synt_pending "
             "(created=%s)", expected_name, created)
    return {"kind": "synt_pending", "sig": sig, "created": created,
            "expected_name": expected_name,
            "marker_path": str(pa.SYNT_PENDING_DIR / f"{sig}.json")}


# ── Tier 2: auto-promozione (flag OFF default, floor alto, cap 1/notte) ─────


def _maybe_autopromote(candidates: list[dict], catalog_names: set) -> dict:
    """Auto-sintetizza AL MASSIMO UN candidato per notte, e solo se:
    flag ON + floor tier 2 superato + candidato 'free' + proposta vista da
    ≥ min_nights notti (shape stabile) + nessuna decisione umana contraria
    (rejected/blocked/applied fermano l'auto). La sintesi passa dalla
    pipeline synt COMPLETA (handle_synth_request: 5 stadi + birth test +
    firma + install). Il tentativo (anche fallito) consuma il budget della
    notte: niente retry-storm."""
    out: dict = {"enabled": autopromote_enabled(), "attempted": None,
                 "eligible": []}
    if not out["enabled"]:
        return out
    floor = tier2_floor()
    out["floor"] = floor
    eligible = []
    for cand in candidates:
        if cand.get("kind") != "free":
            continue
        if "state" not in cand:
            continue  # non emessa stanotte (dedupe generalize / cap)
        if cand["n_distinct"] < floor["min_cluster"]:
            continue
        if cand["cum_uses"] < floor["min_uses"]:
            continue
        if cand["age_days"] < floor["min_age_days"]:
            continue
        if cand.get("n_seen", 0) < floor["min_nights"]:
            continue  # shape non ancora stabile attraverso le notti
        if cand.get("last_action"):
            continue  # decisione umana presa: l'auto NON la scavalca
        if cand.get("state") not in ("pending", "dormant"):
            continue
        eligible.append(cand)
    out["eligible"] = [c["expected_name"] for c in eligible]
    if not eligible:
        return out

    cand = eligible[0]  # già ordinati per usi desc (deterministico)
    out["attempted"] = cand["expected_name"]
    try:
        from synth_request import handle_synth_request
        result = handle_synth_request(
            {"expected_name": cand["expected_name"],
             "intent": intent_text(cand)},
            user_query=(cand.get("samples") or [cand["expected_name"]])[0])
    except Exception as ex:
        out["ok"] = False
        out["error"] = f"{type(ex).__name__}: {ex}"
        log.warning("fastpath_promote: auto-promote %s fallita: %r",
                    cand["expected_name"], ex)
        return out

    out["ok"] = bool(result.get("ok")) and bool(result.get("installed"))
    out["result"] = {k: result.get(k) for k in
                     ("ok", "installed", "synthesized", "proposed_name",
                      "already_in_catalog", "redirected", "reason",
                      "install_error", "elapsed_s")}
    if out["ok"]:
        final_name = result.get("proposed_name") or cand["expected_name"]
        _fp.record_promotion(final_name, cand["members"], tier=2)
        try:
            import proposals_state as ps
            ps.mark_action(cand["sig_key"], "approve")
        except Exception:
            log.warning("fastpath_promote: mark_action post-auto fallita "
                        "(proposta resta pending, innocuo)")
        log.info("fastpath_promote: AUTO-PROMOSSO %s (da %d fastpath, "
                 "%d usi)", final_name, cand["n_distinct"],
                 cand["cum_uses"])
    return out

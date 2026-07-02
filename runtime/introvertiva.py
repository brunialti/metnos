#!/usr/bin/env python3
"""introvertiva — MVP delle operazioni introvertive di Metnos.

L'introvertiva opera DA DENTRO il sistema (cron / soglia / manuale), non da
query utente. Lavora sul corpus mnestoma per migliorare il catalogo invece
di rispondere a un nuovo turno.

Unica operazione attiva:
  - DEDUPE     segnala doppioni/orfani mnest (alias/deprecate su accept)

RITIRATE il 2/7/2026 per la regola dei livelli (Roberto 13/6, VINCOLANTE:
«nessuna sovrapposizione fra livelli») e per il principio «se un meccanismo
non serve non serve» (Roberto 2/7):
  - SPECIALIZE (default-arg dominante → executor esteso): un default in un
    arg è SEMPRE compito di L0 (il fastpath memoizza query→piano args
    inclusi) — l'executor-copia inquina il catalogo.
  - GENERALIZE (catena ricorrente → executor macro): duplicava con evidenza
    più debole ciò che fanno già L1 autopath (impara le pipeline dai turni
    REALI, con args e feedback ✓) e il promoter ETA (promozione a executor
    con soglie di frequenza reali + evaluator). Tre meccanismi per lo
    stesso segnale = due di troppo.
Le righe storiche in proposals_state restano leggibili (adapter
change_intent invariato); il codice vive in git.

MVP 1/5/2026 sera: identificazione + ranking + audit log JSONL append-only.
NESSUNA promozione/sintesi automatica (richiede smoke replay + manual review).

Riferimenti:
  - bacino: <install_root>/workspace/.mnestoma/mnest.sqlite (mnests + events)
  - audit:  ~/.local/share/metnos/introvertiva/<op>_<ts>.jsonl
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from mnestoma import Mnestoma  # noqa: E402
import config as _C  # §7.11

AUDIT_DIR = _C.PATH_USER_DATA / "introvertiva"



def _audit_write(op: str, records: list[dict]) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out = AUDIT_DIR / f"{op}_{ts}.jsonl"
    with out.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def candidates_dedupe(*, min_uses: int = 1) -> list[dict]:
    """Identifica mnest candidati a dedupe (rename / merge / cleanup).

    MVP: solo segnalazione, no execute. Tre famiglie:
      - mnest legacy con executor rinominati (require manifest superseded_by)
      - mnest deprecated piu' giovani del TTL
      - proto orfani (state='proto' AND uses<=1 AND age>30d)

    NB: replay algoritmico completo della bonifica 30/4 non implementato qui:
    richiede mapping legacy→corrente che oggi e' empirico (web_fetch ↔
    get_urls, list_dir ↔ list_dirs, find_file ↔ find_files), non
    derivabile univocamente dal manifest. Da estendere quando manifest
    superseded_by sara' pervasivo.
    """
    mn = Mnestoma()
    cands = []
    # Famiglia 1: mnest con src/dst non in catalog (legacy)
    from loader import load_catalog
    cat = load_catalog()
    catalog_names = {e.name for e in cat}
    for r in mn.conn.execute(
        "SELECT id, src_executor, dst_executor, uses, weight, state FROM mnests "
        "WHERE state = 'active'"
    ):
        src_orphan = r["src_executor"] not in catalog_names
        dst_orphan = r["dst_executor"] not in catalog_names
        if src_orphan or dst_orphan:
            cands.append({
                "kind": "legacy_orphan",
                "mnest_id": r["id"],
                "src_executor": r["src_executor"],
                "dst_executor": r["dst_executor"],
                "uses": r["uses"],
                "weight": r["weight"],
                "src_in_catalog": not src_orphan,
                "dst_in_catalog": not dst_orphan,
            })
    return cands


# --- Diff fra audit log (signal long-period) -----------------------------

def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _candidate_key(rec: dict, op: str) -> str:
    """Chiave stabile per matchare candidati fra run distinti."""
    if op == "generalize":
        return "→".join(rec.get("pattern", []))
    if op == "specialize":
        return f"{rec.get('executor')}::{rec.get('arg_name')}::{rec.get('dominant_value')}"
    if op == "dedupe":
        return f"{rec.get('mnest_id')}::{rec.get('kind')}"
    return json.dumps(rec, sort_keys=True)


def diff_audit(op: str) -> dict:
    """Confronta gli ULTIMI DUE audit log dell'op (cronologia introvertiva
    long-period). Output: {added, removed, persisted, grew, shrunk}.

    `added`: candidati comparsi solo nell'ultimo run.
    `removed`: candidati nell'avant-ultimo, scomparsi nell'ultimo.
    `persisted`: candidati in entrambi (= pattern stabili nel tempo).
    `grew/shrunk`: persisted con metric (uses o dominance) diversa.
    """
    if not AUDIT_DIR.exists():
        return {"error": f"audit dir non esiste: {AUDIT_DIR}"}
    files = sorted(AUDIT_DIR.glob(f"candidates_{op}_*.jsonl"))
    if len(files) < 2:
        return {
            "error": f"servono almeno 2 audit log per '{op}', trovati {len(files)}",
            "files": [str(f.name) for f in files],
        }
    prev_recs = _read_audit(files[-2])
    curr_recs = _read_audit(files[-1])
    prev_map = {_candidate_key(r, op): r for r in prev_recs
                if "_kind" not in r}
    curr_map = {_candidate_key(r, op): r for r in curr_recs
                if "_kind" not in r}
    added = [curr_map[k] for k in (curr_map.keys() - prev_map.keys())]
    removed = [prev_map[k] for k in (prev_map.keys() - curr_map.keys())]
    persisted_keys = curr_map.keys() & prev_map.keys()
    grew, shrunk, stable = [], [], []
    metric = "uses" if op == "generalize" else "total_uses"
    for k in persisted_keys:
        p, c = prev_map[k], curr_map[k]
        pm, cm = p.get(metric, 0), c.get(metric, 0)
        if cm > pm:
            grew.append({"key": k, "prev": pm, "curr": cm, "delta": cm - pm})
        elif cm < pm:
            shrunk.append({"key": k, "prev": pm, "curr": cm, "delta": cm - pm})
        else:
            stable.append({"key": k, "uses": cm})
    return {
        "op": op,
        "prev_run": files[-2].name,
        "curr_run": files[-1].name,
        "n_added": len(added), "added": added[:10],
        "n_removed": len(removed), "removed": removed[:10],
        "n_persisted": len(persisted_keys),
        "n_grew": len(grew), "grew": sorted(grew, key=lambda x: -x["delta"])[:10],
        "n_shrunk": len(shrunk), "shrunk": sorted(shrunk, key=lambda x: x["delta"])[:5],
        "n_stable": len(stable),
    }


# --- Orchestrator ----------------------------------------------------------

def _sig_key_for(op: str, cand: dict):
    """Chiave canonica di un candidato per proposals_state.

    Le shape sono il CONTRATTO con lo storico del DB e con l'adapter
    change_intent (`change_intent_adapters/introvertiva.py`):
      dedupe     → ["dedupe", reason, src, dst]
    (generalize → ["generalize", [chain]] e specialize → ["specialize",
    executor, arg, valore] esistono SOLO come shape storiche nel DB: i
    generatori sono ritirati — 2/7/2026 — l'adapter le legge.)
    Ritorna None per record non candidabili (diagnostici, campi mancanti).
    """
    if op == "dedupe":
        a, b = cand.get("src_executor"), cand.get("dst_executor")
        if not a or not b:
            return None
        return ["dedupe", cand.get("kind", ""), a, b]
    if op == "generalize":
        pattern = cand.get("pattern") or []
        if not pattern:
            return None
        return ["generalize", list(pattern)]
    return None


def sync_proposals_state(out: dict) -> dict:
    """Proietta i candidati di un run in `proposals_state` (touch_or_insert).

    E' il passo che tiene VIVO il lifecycle pending→dormant→riemersione e la
    vista /admin/changes: senza, i generatori scrivono solo audit JSONL che
    nessuno consuma. Chiamato dal task notturno `introvertiva_propose`.
    Ritorna i conteggi per operazione.
    """
    import proposals_state as ps
    counts: dict[str, int] = {}
    for op in ("dedupe",):
        n = 0
        for cand in out.get(op) or []:
            if not isinstance(cand, dict) or cand.get("_kind"):
                continue  # record diagnostici, non candidati
            key = _sig_key_for(op, cand)
            if key is None:
                continue
            uses = int(cand.get("uses") or cand.get("total_uses") or 0)
            ps.touch_or_insert(key, op, uses)
            n += 1
        counts[op] = n
    return counts


def run_all(*, audit: bool = True) -> dict:
    """Esegue l'unica op attiva (dedupe), summary + audit JSONL."""
    out = {
        "ts": int(time.time()),
        "dedupe": candidates_dedupe(),
    }
    if audit:
        for op in ("dedupe",):
            if out[op]:
                p = _audit_write(f"candidates_{op}", out[op])
                out[f"{op}_audit"] = str(p)
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("op", choices=["dedupe", "all", "diff"])
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--diff-op", choices=["dedupe"],
                   help="Per `op=diff`: quale operazione confrontare.")
    args = p.parse_args()
    if args.op == "diff":
        if not args.diff_op:
            print("--diff-op richiesto per `op=diff`", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(diff_audit(args.diff_op), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.op == "all":
        r = run_all(audit=not args.no_audit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        fn = {"dedupe": candidates_dedupe}[args.op]
        r = fn()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if not args.no_audit and r:
            ap = _audit_write(f"candidates_{args.op}", r)
            print(f"\naudit: {ap}", file=sys.stderr)

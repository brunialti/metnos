# SPDX-License-Identifier: AGPL-3.0-only
"""llm_cost_sink.py — sink persistente di metering per le chiamate LLM.

Si aggancia all'hook universale `llm_telemetry.add_sink(...)` e scrive UNA riga
JSONL per chiamata con i soli campi di costo/uso (NIENTE prompt: privacy + size):

    ts, ts_iso, provider, model, kind, tier, in_tokens, out_tokens,
    cost_usd, latency_ms

Scopo (eval Headroom, report_headroom_frontier_2026-06-15): misurare la spesa
frontier REALE — volume chiamate Opus e soprattutto lo **split input/output**
(l'output Opus costa 5× l'input; Headroom comprime solo l'input). Filtra poi
`provider == "anthropic"` per isolare il frontier.

Copertura: si installa da `llm_provider` all'import, quindi cattura ogni processo
che fa chiamate LLM (HTTP server, agent runtime, executor `consult_frontier` che
importa `llm_router`/`llm_provider`). Best-effort: non rompe mai una chiamata.

Disattivabile con env `METNOS_LLM_COST_LOG=0`. Path override con
`METNOS_LLM_COST_LOG_PATH`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("metnos.llm.cost")

# Tariffe: fonte UNICA `runtime/llm_pricing.py` (§7.2, consolidamento 15/6/2026).
from llm_pricing import cost_usd as _cost_usd  # noqa: E402


def _default_path() -> Path:
    env = os.environ.get("METNOS_LLM_COST_LOG_PATH")
    if env:
        return Path(env)
    # repo root = parent di runtime/
    return Path(__file__).resolve().parents[1] / "data" / "telemetry" / "llm_usage.jsonl"


def _sink(rec: dict) -> None:
    """Osservatore registrato su llm_telemetry. Solo metering, mai prompt."""
    try:
        in_tok = int(rec.get("in_tokens") or 0)
        out_tok = int(rec.get("out_tokens") or 0)
        provider = rec.get("provider")
        model = rec.get("model")
        ts = time.time()
        row = {
            "ts": round(ts, 3),
            "ts_iso": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "kind": rec.get("kind"),
            "tier": rec.get("tier"),
            "in_tokens": in_tok,
            "out_tokens": out_tok,
            "cost_usd": _cost_usd(provider, model, in_tok, out_tok),
            "latency_ms": rec.get("latency_ms"),
        }
    except Exception:  # noqa: BLE001 — non costruibile: salta
        return
    # 1) Riga di log strutturata: SEMPRE (fallback se il file non è scrivibile,
    #    es. da un executor con FS ristretto — la riga resta nei log).
    try:
        log.info("LLM_USAGE %s", json.dumps(row, ensure_ascii=False, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass
    # 2) JSONL append-only durabile (riusa la primitiva §7.2).
    try:
        from audit_jsonl import append_jsonl
        append_jsonl(_default_path(), row)
    except Exception:  # noqa: BLE001 — metering best-effort
        pass


_installed = False


def install() -> bool:
    """Registra il sink su llm_telemetry (idempotente). No-op se disabilitato."""
    global _installed
    if _installed:
        return True
    if os.environ.get("METNOS_LLM_COST_LOG", "1") == "0":
        return False
    try:
        import llm_telemetry  # late import: evita cicli a import-time
        llm_telemetry.add_sink(_sink)
        _installed = True
        return True
    except Exception:  # noqa: BLE001
        return False


# ── Aggregazione / report (per il follow-up del report Headroom) ────────────

def summarize(path: str | os.PathLike | None = None, *,
              provider: str | None = None, since_days: float | None = None) -> dict:
    """Aggrega il JSONL per (provider, model): calls, token in/out, costo, share input."""
    p = Path(path) if path else _default_path()
    cutoff = (time.time() - since_days * 86400) if since_days else None
    agg: dict[tuple, dict] = {}
    if not p.exists():
        return {"path": str(p), "rows": 0, "groups": {}}
    rows = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if cutoff and (r.get("ts") or 0) < cutoff:
                continue
            if provider and r.get("provider") != provider:
                continue
            rows += 1
            key = (r.get("provider"), r.get("model"))
            a = agg.setdefault(key, {"calls": 0, "in_tokens": 0, "out_tokens": 0,
                                     "cost_usd": 0.0})
            a["calls"] += 1
            a["in_tokens"] += int(r.get("in_tokens") or 0)
            a["out_tokens"] += int(r.get("out_tokens") or 0)
            a["cost_usd"] += float(r.get("cost_usd") or 0.0)
    return {"path": str(p), "rows": rows, "groups": agg}


def _print_summary(s: dict) -> None:
    print(f"# LLM usage — {s['path']}  ({s['rows']} righe)\n")
    hdr = f"{'provider/model':36} {'calls':>6} {'in_tok':>12} {'out_tok':>12} {'in%':>5} {'cost $':>10}"
    print(hdr)
    print("-" * len(hdr))
    tot_c = tot_i = tot_o = 0
    tot_cost = 0.0
    for (prov, model), a in sorted(s["groups"].items(),
                                   key=lambda kv: -kv[1]["cost_usd"]):
        tin, tout = a["in_tokens"], a["out_tokens"]
        share = round(100 * tin / (tin + tout)) if (tin + tout) else 0
        print(f"{(str(prov)+'/'+str(model)):36} {a['calls']:>6} {tin:>12,} "
              f"{tout:>12,} {share:>4}% {a['cost_usd']:>10.4f}")
        tot_c += a["calls"]; tot_i += tin; tot_o += tout; tot_cost += a["cost_usd"]
    print("-" * len(hdr))
    shr = round(100 * tot_i / (tot_i + tot_o)) if (tot_i + tot_o) else 0
    print(f"{'TOTALE':36} {tot_c:>6} {tot_i:>12,} {tot_o:>12,} {shr:>4}% {tot_cost:>10.4f}")
    print("\nNB: Headroom comprime solo l'input. ROI ~ in% alta + input ridondante.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Aggrega il metering LLM (JSONL).")
    ap.add_argument("--path", default=None)
    ap.add_argument("--provider", default=None, help="es. anthropic (= frontier)")
    ap.add_argument("--days", type=float, default=None, help="solo ultime N giorni")
    args = ap.parse_args()
    _print_summary(summarize(args.path, provider=args.provider, since_days=args.days))

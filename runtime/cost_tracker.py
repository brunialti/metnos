#!/usr/bin/env python3
"""
cost_tracker.py — contatore della spesa LLM (Metnos v1.1 POC).

API decisa in B3.1+B3.2 (sessione 26/4/2026):
    estimate_pre_call(provider, model, in_tokens) -> Decimal
    record_post_call(provider, model, in_tokens, out_tokens) -> Decimal
    remaining_budget_eur() -> Decimal
    is_exhausted() -> bool

Storage: JSONL append-only, un file per mese in
~/.local/share/metnos/cost/YYYY-MM.jsonl. Cache in-memory del totale corrente
ricostruita al boot dallo scan del file mensile.

Comportamento al cap raggiunto: record_post_call e' permesso (non vogliamo
rifiutare di registrare cio' che e' gia' speso); is_exhausted ritorna True;
il chiamante decide cosa fare (errore franco vs degradazione - decisione v1.2).

Pricing in v1.1 POC: tabella hardcoded conservativa. local = 0.
"""
import json
import time
from decimal import Decimal
from pathlib import Path

import config as _C  # §7.11

COST_DIR = _C.PATH_USER_DATA / "cost"

# Prezzo in EUR per 1k tokens. Tabella conservativa, da revedere col tempo.
PRICING = {
    ("ollama", "*"):                        {"in": Decimal("0"),      "out": Decimal("0")},
    ("llamacpp", "*"):                      {"in": Decimal("0"),      "out": Decimal("0")},
    ("stub", "*"):                          {"in": Decimal("0"),      "out": Decimal("0")},
    ("anthropic", "claude-haiku-4-5"):      {"in": Decimal("0.0008"), "out": Decimal("0.004")},
    ("anthropic", "claude-sonnet-4-6"):     {"in": Decimal("0.003"),  "out": Decimal("0.015")},
    ("anthropic", "claude-opus-4-7"):       {"in": Decimal("0.015"),  "out": Decimal("0.075")},
    # OpenAI pricing 2026 (€/1k tokens, conservativo dal listino USD * 0.92)
    ("openai", "gpt-4.1"):                  {"in": Decimal("0.0018"), "out": Decimal("0.0073")},
    ("openai", "gpt-4o"):                   {"in": Decimal("0.0023"), "out": Decimal("0.0091")},
    ("openai", "gpt-4o-mini"):              {"in": Decimal("0.00014"),"out": Decimal("0.00055")},
    ("openai", "gpt-5"):                    {"in": Decimal("0.0114"), "out": Decimal("0.0457")},
}


def _price_for(provider, model):
    key = (provider, model)
    if key in PRICING:
        return PRICING[key]
    fallback = (provider, "*")
    if fallback in PRICING:
        return PRICING[fallback]
    # Default conservativo: tratta come Sonnet
    return PRICING.get(("anthropic", "claude-sonnet-4-6"), {"in": Decimal("0.003"), "out": Decimal("0.015")})


class CostExhausted(Exception):
    pass


class CostTracker:
    def __init__(self, monthly_cap_eur=Decimal("30"), now_fn=time.time):
        self.monthly_cap_eur = Decimal(str(monthly_cap_eur))
        self.now_fn = now_fn
        self._month_total = self._load_current_month()

    def _current_month_path(self):
        COST_DIR.mkdir(parents=True, exist_ok=True)
        return COST_DIR / f"{time.strftime('%Y-%m', time.localtime(self.now_fn()))}.jsonl"

    def _load_current_month(self):
        path = self._current_month_path()
        if not path.exists():
            return Decimal("0")
        total = Decimal("0")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    total += Decimal(str(rec.get("cost_eur", "0")))
                except Exception:
                    continue
        return total

    def estimate_pre_call(self, provider, model, in_tokens):
        p = _price_for(provider, model)
        return (Decimal(in_tokens) * p["in"] / Decimal(1000))

    def record_post_call(self, provider, model, in_tokens, out_tokens):
        p = _price_for(provider, model)
        cost = (Decimal(in_tokens) * p["in"] + Decimal(out_tokens) * p["out"]) / Decimal(1000)
        rec = {
            "ts": self.now_fn(),
            "provider": provider,
            "model": model,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "cost_eur": str(cost),
        }
        with open(self._current_month_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self._month_total += cost
        return cost

    def remaining_budget_eur(self):
        return self.monthly_cap_eur - self._month_total

    def is_exhausted(self):
        return self._month_total >= self.monthly_cap_eur

    def month_total_eur(self):
        return self._month_total


if __name__ == "__main__":
    t = CostTracker(monthly_cap_eur=Decimal("30"))
    print(f"month total: EUR {t.month_total_eur()}")
    print(f"remaining:   EUR {t.remaining_budget_eur()}")
    print(f"exhausted:   {t.is_exhausted()}")
    # Simula una chiamata local (free)
    c = t.record_post_call("ollama", "qwen2.5:7b-instruct", 200, 150)
    print(f"after local call: cost={c}, total={t.month_total_eur()}")
    # Simula stima online
    e = t.estimate_pre_call("anthropic", "claude-sonnet-4-6", 1000)
    print(f"estimate sonnet 1k in: EUR {e}")

#!/usr/bin/env python3
"""llm_router.py — tier resolver fast/middle/wise/frontier per Metnos v1.1.

Architettura: 4 tier (fast / middle / wise / frontier).
fast/middle/wise: locali di default, "self-hosted first" (CLAUDE.md §10.3).
frontier: opt-in online per casi che richiedono massima qualita'
(es. Opus 4.7 per code-gen complesso, traduzioni di livello superiore,
synth wise di nuovo executor critico). Aggiunto 5/5/2026 sera.

Regole canoniche di alias:
    - fast assente     -> errore al boot (safety net obbligatorio)
    - middle assente   -> alias UP a wise (stesso modello concreto)
    - wise assente     -> errore al boot (no degradazione silenziosa a fast)
    - frontier assente -> opzionale, errore SOLO se chiamato esplicitamente
                          (i caller di tier="frontier" devono gestire fallback)

Config TOML in workspace/.config/llm_tiers.toml. Se manca, default
baked-in: tutti i tier locali (fast/middle/wise) puntano allo stesso
llama-server :8080 (Qwen3.6-35B-A3B + MTP self-speculative interna),
differenze solo nei parametri per-call. Frontier = Anthropic Opus 4.7 opt-in.
La verita' canonica e' in `DEFAULT_TIERS` (sotto) — vedi ADR 0146.

API:
    router = LLMRouter()
    provider = router.provider(tier="wise")
    result   = router.chat_with_tools(system, user, tools, tier="wise")
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    tomllib = None

sys.path.insert(0, str(Path(__file__).parent))
import config as _C  # §7.11
from llm_provider import (  # noqa: E402
    ChatResult, ToolUseResult, ProviderError, make_provider_from_spec,
)


def _default_config_path() -> Path:
    """Preferenza:
      1. env METNOS_LLM_TIERS_CONFIG
      2. ~/.config/metnos/llm_tiers.toml  (canonical user, ADR 0089)
      3. <install_root>/workspace/.config/llm_tiers.toml  (legacy fallback)
    """
    v = os.environ.get("METNOS_LLM_TIERS_CONFIG")
    if v:
        return Path(v)
    home_cfg = _C.PATH_USER_CONFIG / "llm_tiers.toml"
    if home_cfg.exists():
        return home_cfg
    # ADR 0148 rename-resilient: derive from this module's location.
    return Path(__file__).resolve().parents[1] / "workspace" / ".config" / "llm_tiers.toml"


CONFIG_PATH = _default_config_path()


# Default baked-in — single source of truth per ADR 0146 (18/5/2026).
# I tre tier locali (fast/middle/wise) puntano allo stesso processo
# llama-server :8080 (Qwen3.6-35B-A3B main + MTP self-speculative interna,
# `--spec-type draft-mtp`: il drafter e' la testa MTP del modello stesso,
# non un secondo modello via `-md`). La differenza fra tier e' solo nei
# parametri per-call (think, num_predict) — non nel modello servito.
# Qualsiasi modifica a questo dict aggiorna la realta' del progetto:
# tutti gli altri doc (CLAUDE.md §11, ADR 0146, docs/LLM_TIERS.md)
# rinviano qui, non duplicano i valori. Supersedes ADR 0044.
DEFAULT_TIERS = {
    "fast": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": "http://127.0.0.1:8080",
        "think": False,
        "num_predict": 400,
    },
    "middle": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": "http://127.0.0.1:8080",
    },
    "wise": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": "http://127.0.0.1:8080",
    },
    # frontier: opt-in online, "il migliore solo se serve". Caller deve
    # chiamare esplicitamente tier="frontier" + gestire fallback se la API
    # key non e' configurata. Default Opus 4.7 (top-of-line Claude 4.X).
    "frontier": {
        "provider": "anthropic",
        "model": "claude-opus-4-7",
    },
}

# Whitelist per il quality floor del wise. Modelli locali noti come "level Gemma 4"
# o provider online accettati.
WISE_QUALITY_WHITELIST_LOCAL = {
    # llamacpp models (substring match)
    "gemma-4-26",       # Gemma 4 26B
    "gemma3:27",        # Gemma 3 27B
    "qwen3:32",         # Qwen 3 32B
    "qwen3:72",
    "llama4",           # Llama 4 anything
}
WISE_QUALITY_WHITELIST_ONLINE_PROVIDERS = {"anthropic", "openai", "google", "mistral"}


class TierConfigError(Exception):
    """Configurazione dei tier non valida (es. wise mancante)."""


def _load_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    if tomllib is None:
        raise RuntimeError("tomllib non disponibile (richiede Python 3.11+).")
    return tomllib.loads(path.read_text(encoding="utf-8"))


# Tier canonici riconosciuti come sezioni top-level flat (oltre al nested
# `[tiers.<name>]`). Ogni nome qui entra nella mappa `tiers` se presente
# come `[<name>]` con almeno `provider` + `model`.
_TOP_LEVEL_TIER_NAMES = ("fast", "middle", "wise", "frontier")


def _normalize_tiers_dict(cfg: dict) -> dict:
    """Estrae la mappa tier dal config TOML supportando due formati:

      - Nested (legacy):  `[tiers.fast] provider=... model=...`
      - Flat (canonical user, ~/.config/metnos/llm_tiers.toml):
                          `[fast] provider=... model=...`
                          `[[wise.fallback]] provider=... model=...`

    Flat ha precedenza su nested quando entrambi presenti per lo stesso
    tier (la sezione flat e' l'override utente esplicito).

    `fallback` (lista di {provider, model}) e' supportato sia flat
    (`[[wise.fallback]]`) sia nested (`[tiers.wise.fallback]`).
    """
    out: dict = {}
    nested = cfg.get("tiers") or {}
    if isinstance(nested, dict):
        for k, v in nested.items():
            if isinstance(v, dict):
                out[k] = dict(v)
    for name in _TOP_LEVEL_TIER_NAMES:
        section = cfg.get(name)
        # A tier is an ABSTRACT role binding: it is configured as soon as a
        # `provider` is named. `model` is optional (a local llama-server serves
        # whatever it has loaded); `endpoint`/`base_url` are aliases. We do not
        # require a concrete model or any accelerator to recognise a tier.
        if isinstance(section, dict) and "provider" in section:
            spec = dict(section)
            if "endpoint" not in spec and "base_url" in spec:
                spec["endpoint"] = spec["base_url"]
            out[name] = spec
    return out


def _wise_passes_quality_floor(spec: dict) -> bool:
    """Whether the wise tier is acceptably configured.

    Tiers are an ABSTRACT role binding: any named provider satisfies the
    wise role. We deliberately do NOT gate on model identity or require an
    accelerator — the operator chooses the concrete model, and a weaker
    local model means weaker planning, not a configuration error. The old
    model-name whitelist coupled the abstraction to specific GGUFs and went
    stale; abstraction first (user directive, tiers pure-abstract)."""
    return bool(spec.get("provider"))


# Preambolo per il caso γ: middle aliasato a wise (stesso modello fisico).
# Applicato SOLO quando middle e wise puntano allo stesso modello — fornisce
# il "ruolo" diverso senza richiedere un secondo modello. fast e wise restano
# canonici: il system del chiamante e' la voce primaria.
MIDDLE_ALIASED_PREAMBLE = (
    "Sei nel ruolo di valutatore: prima di decidere considera 2-3 alternative, "
    "esplicita brevemente la scelta, poi rispondi.\n\n"
)


# Repertorio dei prompt addendum provider-specifici. Caricato da file TOML:
#   1. <install_root>/runtime/prompts.toml         (default bundled)
#   2. ~/.config/metnos/prompts.toml            (override utente, opzionale)
# Origine empirica delle scoperte: vedi memorie di progetto.

PROMPTS_BUNDLED_PATH = Path(__file__).parent / "prompts.toml"
PROMPTS_USER_PATH = _C.PATH_USER_CONFIG / "prompts.toml"


# Fallback in-code se entrambi i file mancano (test, container minimali).
_PROMPTS_FALLBACK = [
    {"provider": "anthropic", "model_pattern": "claude-*", "use_case": "code_gen",
     "text": "\n\nVincoli: codice fedele alla spec. Regex semplice. Niente lookbehind/lookahead."},
    {"provider": "llamacpp",  "model_pattern": "gemma-*",  "use_case": "code_gen",
     "text": "\n\nVincoli: raw string r'...' con UN backslash. Niente triple-quote docstring."},
    {"provider": "ollama",    "model_pattern": "qwen*",    "use_case": "code_gen",
     "text": "\n\nVincoli: compila python_code per intero (def invoke + def main). Mai vuoto."},
]


def _load_prompts_repertoire() -> list[dict]:
    """Carica gli hint da file. Se nessun file esiste, ritorna fallback."""
    out: list[dict] = []
    for p in (PROMPTS_BUNDLED_PATH, PROMPTS_USER_PATH):
        if not p.exists() or tomllib is None:
            continue
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in data.get("hint") or []:
            if all(k in entry for k in ("provider", "model_pattern", "use_case", "text")):
                out.append(dict(entry))
    if not out:
        out = list(_PROMPTS_FALLBACK)
    return out


# Cache module-level: caricato al primo uso.
_PROMPTS_CACHE: list[dict] | None = None


def _prompts() -> list[dict]:
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is None:
        _PROMPTS_CACHE = _load_prompts_repertoire()
    return _PROMPTS_CACHE


def reload_prompts():
    """Forza il ricaricamento dal disco. Utile dopo edit del file user."""
    global _PROMPTS_CACHE
    _PROMPTS_CACHE = None


def code_gen_hint_for(provider_name: str, model: str | None = None) -> str:
    """Ritorna l'addendum per la coppia (provider, model). Primo match vince."""
    if not provider_name:
        return ""
    model_str = (model or "*").strip()
    for h in _prompts():
        if h["provider"] != provider_name:
            continue
        if h["use_case"] != "code_gen":
            continue
        if not fnmatch.fnmatch(model_str, h["model_pattern"]):
            continue
        return h["text"]
    return ""


class LLMRouter:
    """Router dei tier LLM. Carica config, valida wise floor, espone API.

    L'API `chat()` e `chat_with_tools()` accetta `tier='fast'/'middle'/'wise'`
    (default 'fast') e antepone il preambolo del tier al system prompt.
    """

    def __init__(self, *, config_path: Optional[Path] = None,
                 tiers_override: Optional[dict] = None,
                 use_preambles: bool = True):
        self.use_preambles = use_preambles
        if tiers_override is not None:
            tiers = dict(tiers_override)
        else:
            cfg = _load_config_file(config_path or CONFIG_PATH)
            tiers = _normalize_tiers_dict(cfg) or dict(DEFAULT_TIERS)

        # Regola: fast obbligatorio
        if "fast" not in tiers:
            raise TierConfigError(
                "tier 'fast' non configurato. fast e' obbligatorio (safety net)."
            )
        # Regola: wise obbligatorio
        if "wise" not in tiers:
            raise TierConfigError(
                "tier 'wise' non configurato. wise non degrada a fast: "
                "configura un wise locale (es. gemma-4-26B su llama-server) "
                "oppure un provider online (anthropic, openai)."
            )
        # Regola: wise quality floor
        if not _wise_passes_quality_floor(tiers["wise"]):
            raise TierConfigError(
                f"tier 'wise' sotto la soglia di qualita': spec={tiers['wise']}. "
                "Vedi memoria 'Wise tier — soglia minima Gemma 4 26B'."
            )
        # Regola: middle assente -> alias UP a wise (stesso modello concreto)
        if "middle" not in tiers:
            tiers["middle"] = dict(tiers["wise"])
            tiers["middle"]["_aliased_from_wise"] = True

        self.tiers = tiers
        self._provider_cache = {}

    def provider(self, tier: str = "fast"):
        if tier not in {"fast", "middle", "wise", "frontier"}:
            raise ValueError(f"unknown tier: {tier!r}")
        if tier == "frontier" and tier not in self.tiers:
            raise TierConfigError(
                "tier 'frontier' richiesto ma non configurato. Aggiungi a "
                "~/.config/metnos/llm_tiers.toml: [tiers.frontier] "
                "provider='anthropic' model='claude-opus-4-7'."
            )
        if tier in self._provider_cache:
            return self._provider_cache[tier]
        spec = {k: v for k, v in self.tiers[tier].items()
                if not k.startswith("_")}
        prov = make_provider_from_spec(spec)
        self._provider_cache[tier] = prov
        return prov

    def is_aliased(self, tier: str) -> bool:
        return bool(self.tiers.get(tier, {}).get("_aliased_from_wise"))

    def fallback_chain(self, tier: str) -> list[dict]:
        """Ritorna la catena di provider per `tier`: primary + fallback
        secondari. Lista di spec {provider, model, ...} pronte per
        `make_provider_from_spec`. Vuota se il tier non e' configurato.

        Usata da `consult_frontier` per ritentare con fallback se primary
        fallisce (es. Opus 4.7 → GPT-5 → fail). Niente fallback chain per
        fast/middle/wise di default (catena = primary only); se servisse
        in futuro, basta aggiungere `[[wise.fallback]]` in llm_tiers.toml.
        """
        spec = self.tiers.get(tier)
        if not spec:
            return []
        primary = {k: v for k, v in spec.items()
                   if not k.startswith("_") and k != "fallback"}
        out = [primary]
        for f in (spec.get("fallback") or []):
            if not isinstance(f, dict):
                continue
            if "provider" not in f or "model" not in f:
                continue
            out.append(dict(f))
        return out

    def describe(self) -> dict:
        out = {}
        for t in ("fast", "middle", "wise", "frontier"):
            if t not in self.tiers:
                continue
            spec = self.tiers[t]
            out[t] = {
                "provider": spec.get("provider"),
                "model":    spec.get("model"),
                "aliased":  bool(spec.get("_aliased_from_wise")),
                "fallback": [
                    {"provider": f.get("provider"), "model": f.get("model")}
                    for f in (spec.get("fallback") or [])
                ],
            }
        return out

    def _system_for_tier(self, system: str, tier: str,
                         provider_name: str | None = None,
                         provider_model: str | None = None,
                         for_code: bool = False) -> str:
        sys = system or ""
        if self.use_preambles:
            # Solo middle-aliased riceve preambolo (caso γ).
            if tier == "middle" and self.is_aliased("middle"):
                sys = MIDDLE_ALIASED_PREAMBLE + sys
        if for_code and provider_name:
            sys = sys + code_gen_hint_for(provider_name, provider_model)
        return sys

    def chat(self, system, user, *, tier="fast", for_code: bool = False,
             **kwargs) -> ChatResult:
        prov = self.provider(tier)
        return prov.chat(
            self._system_for_tier(system, tier, prov.name, prov.model, for_code),
            user, **kwargs,
        )

    def chat_with_tools(self, system, user, tools, *, tier="fast", history=None,
                        for_code: bool = False, **kwargs) -> ToolUseResult:
        prov = self.provider(tier)
        return prov.chat_with_tools(
            self._system_for_tier(system, tier, prov.name, prov.model, for_code),
            user, tools, history=history, **kwargs,
        )


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="LLM tier router")
    ap.add_argument("--describe", action="store_true",
                    help="stampa la configurazione effettiva dei tier")
    ap.add_argument("--tier", default="fast", choices=["fast", "middle", "wise"])
    ap.add_argument("--prompt", default="Rispondi solo: OK")
    ap.add_argument("--system", default="Sei un assistente conciso.")
    args = ap.parse_args()

    r = LLMRouter()
    if args.describe:
        import json
        print(json.dumps(r.describe(), indent=2, ensure_ascii=False))
        return
    res = r.chat(args.system, args.prompt, tier=args.tier, max_tokens=2048)
    print(f"[{args.tier}] {res.provider}:{res.model}  {res.latency_ms}ms  "
          f"in={res.in_tokens} out={res.out_tokens}")
    print(f"  text: {res.text!r}")


if __name__ == "__main__":
    _cli()

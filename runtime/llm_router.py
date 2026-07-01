#!/usr/bin/env python3
"""llm_router.py — tier resolver fast/middle/wise/frontier per Metnos v1.1.

Architettura: 4 tier (fast / middle / wise / frontier).
fast/middle/wise: locali di default, "self-hosted first" (the design guide §10.3).
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
    ChatResult, ToolUseResult, make_provider_from_spec,
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
# tutti gli altri doc (the design guide §11, ADR 0146) rinviano QUI, non
# duplicano i valori: questo dict e' la SoT del mapping tier→modello.
# Supersedes ADR 0044.
#
# ⏱️ MAPPING TIER→MODELLO FISICO — snapshot al 2026-06-09 (l'UNICO punto del
# codice con nomi modello concreti; altrove si parla solo di tier virtuali
# fast/middle/wise/frontier). Aggiornare qui + la data quando cambia il modello.

# Ultimo default per i tier locali quando NULLA e' configurato (tier
# pure-abstract: l'endpoint REALE vive in llm_tiers.toml, vedi
# `tier_endpoint`). Niente altri ":8080" hardcoded nel runtime.
LOCAL_DEFAULT_ENDPOINT = "http://127.0.0.1:8080"

DEFAULT_TIERS = {
    "fast": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
        "think": False,
        "num_predict": 400,
    },
    "middle": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
    },
    "wise": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
    },
    # frontier: opt-in online, "il migliore solo se serve". Caller deve
    # chiamare esplicitamente tier="frontier" + gestire fallback se la API
    # key non e' configurata. Default Opus 4.8 (top-of-line Claude 4.X; Fable
    # ritirato — 404 «use Opus 4.8», 21/6/2026; era 4.7).
    "frontier": {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
    },
}

# (Le ex-whitelist di NOMI modello per il quality-floor del wise sono state
# rimosse: i tier sono astratti, non si gata sull'identità del modello.)


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


_TIERS_FILE_CACHE: dict = {"key": None, "tiers": None}


def _tiers_from_config() -> dict:
    """tiers da llm_tiers.toml, con cache invalidata su (path, mtime): il file
    viene RI-LETTO solo se cambia (prima si ri-parsava il TOML a OGNI call_llm,
    hot path). Mantiene la semantica «config reload prende effetto» §2.8."""
    import os
    path = _default_config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    key = (str(path), mtime)
    if _TIERS_FILE_CACHE["key"] != key:
        try:
            tiers = _normalize_tiers_dict(_load_config_file(path))
        except Exception:
            tiers = {}
        _TIERS_FILE_CACHE["key"] = key
        _TIERS_FILE_CACHE["tiers"] = tiers
    return _TIERS_FILE_CACHE["tiers"] or {}


def tier_endpoint(tier: str = "middle") -> str:
    """Endpoint HTTP del tier VIRTUALE — SoT unica per i consumer fuori
    dal router (llm_helpers.call_llm, path deterministico /props +
    /apply-template). Risoluzione: llm_tiers.toml (env
    METNOS_LLM_TIERS_CONFIG > ~/.config/metnos > legacy workspace,
    cache invalidata su mtime) -> DEFAULT_TIERS; `LOCAL_DEFAULT_ENDPOINT`
    solo come ultimo default se nulla e' configurato (tier pure-abstract,
    §7.11). `endpoint`/`base_url` sono alias come nel router."""
    tiers = _tiers_from_config()
    if not tiers:
        tiers = DEFAULT_TIERS
    # Alias come LLMRouter.__init__: middle assente -> wise; poi fast.
    spec = tiers.get(tier) or tiers.get("wise") or tiers.get("fast") or {}
    ep = spec.get("endpoint") or spec.get("base_url") or ""
    if not ep:
        ep = DEFAULT_TIERS.get(tier, {}).get("endpoint") or LOCAL_DEFAULT_ENDPOINT
    return str(ep).rstrip("/")


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
                "configura un wise locale (un llama-server) "
                "oppure un provider online (anthropic, openai)."
            )
        # Regola: wise quality floor
        if not _wise_passes_quality_floor(tiers["wise"]):
            raise TierConfigError(
                f"tier 'wise' senza provider configurato: spec={tiers['wise']}. "
                "I tier sono astratti: basta dichiarare un `provider`."
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
            _fm = DEFAULT_TIERS["frontier"]  # SoT model id, mai hardcoded altrove
            raise TierConfigError(
                "tier 'frontier' richiesto ma non configurato. Aggiungi a "
                "~/.config/metnos/llm_tiers.toml: [tiers.frontier] "
                f"provider='{_fm.get('provider', 'anthropic')}' model='{_fm['model']}'."
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
        fallisce (es. Opus 4.7 → un frontier secondario → fail). Niente fallback chain per
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

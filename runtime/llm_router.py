#!/usr/bin/env python3
"""llm_router.py — logical LLM tier resolver for Metnos.

Architettura: 5 tier (fast / middle / wise / creative / frontier). ``fast``
possiede tre livelli deterministici (micro / procedural / fidelity). I primi
quattro tier sono locali di default, "self-hosted first" (the design guide §10.3).
frontier: opt-in online per casi che richiedono massima qualita'
(es. Opus 4.8 per code-gen complesso, traduzioni di livello superiore,
synth wise di nuovo executor critico). Aggiunto 5/5/2026 sera.

Regole canoniche di alias dei binding fisici:
    - fast assente     -> binding predefinito (un file parziale e' un override)
    - middle assente   -> binding predefinito (mai degradazione a fast)
    - wise assente     -> binding predefinito (mai degradazione a fast)
    - frontier assente -> opzionale, errore SOLO se chiamato esplicitamente
                          (i caller di tier="frontier" devono gestire fallback)

Config TOML in workspace/.config/llm_tiers.toml. Se manca, default
baked-in: tutti i tier locali puntano allo stesso
llama-server :8080 (Qwen3.6-35B-A3B + MTP self-speculative interna),
con policy di generazione proprie. Frontier = Anthropic Opus 4.8 opt-in.
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
from dataclasses import dataclass
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
# I quattro tier locali possono puntare allo stesso processo
# llama-server :8080 (Qwen3.6-35B-A3B main + MTP self-speculative interna,
# `--spec-type draft-mtp`: il drafter e' la testa MTP del modello stesso,
# non un secondo modello via `-md`). La differenza fra tier puo' stare nel
# binding e nella policy centrale (think, temperature, reasoning_budget), mai
# in override disseminati nei call site.
# Qualsiasi modifica a questo dict aggiorna la realta' del progetto:
# tutti gli altri doc (the design guide §11, ADR 0146) rinviano QUI, non
# duplicano i valori: questo dict e' la SoT del mapping tier→modello.
# Supersedes ADR 0044.
#
# ⏱️ MAPPING TIER→MODELLO FISICO — snapshot al 2026-06-09 (l'UNICO punto del
# codice con nomi modello concreti; altrove si parla solo di tier virtuali
# fast/middle/wise/creative/frontier). Aggiornare qui + la data quando cambia il
# modello.

# Ultimo default per i tier locali quando NULLA e' configurato (tier
# pure-abstract: l'endpoint REALE vive in llm_tiers.toml, vedi
# `tier_endpoint`). Niente altri ":8080" hardcoded nel runtime.
LOCAL_DEFAULT_ENDPOINT = "http://127.0.0.1:8080"

FAST_LEVEL_ORDER = ("micro", "procedural", "fidelity")
FAST_DEFAULT_LEVEL = "micro"

# I livelli sono default del router, non profili nei caller. Oggi condividono
# intenzionalmente la stessa policy; in futuro l'operatore può configurare un
# solo livello in ``[fast.level.<name>]`` senza cambiare alcun workload.
DEFAULT_FAST_LEVELS = {
    "micro": {
        "think": False,
        "temperature": 0.0,
        "reasoning_budget": 0,
    },
    "procedural": {
        "think": False,
        "temperature": 0.0,
        "reasoning_budget": 0,
    },
    "fidelity": {
        "think": False,
        "temperature": 0.0,
        "reasoning_budget": 0,
    },
}

DEFAULT_TIERS = {
    "fast": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
        **DEFAULT_FAST_LEVELS[FAST_DEFAULT_LEVEL],
    },
    "middle": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
        "think": False,
        "temperature": 0.0,
        "reasoning_budget": 0,
    },
    "wise": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
        # Current Qwen deployment: keep hidden reasoning disabled until its
        # server exposes a reliable separate reasoning channel.  ``wise``
        # remains independently configurable and may bind a stronger model.
        "think": False,
        "temperature": 0.0,
        "reasoning_budget": 0,
    },
    "creative": {
        "provider": "llamacpp",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "endpoint": LOCAL_DEFAULT_ENDPOINT,
        # Divergent prose and proposals have a dedicated role.  It is never
        # inherited by ``middle`` or ``wise`` workloads.
        "think": False,
        "temperature": 0.35,
        "reasoning_budget": 0,
    },
    # frontier: opt-in online, "il migliore solo se serve". Caller deve
    # chiamare esplicitamente tier="frontier" + gestire fallback se la API
    # key non e' configurata. Default Opus 4.8 (top-of-line Claude 4.X; Fable
    # ritirato — 404 «use Opus 4.8», 21/6/2026; era 4.7).
    "frontier": {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "think": False,
        "temperature": 0.0,
        "reasoning_budget": 0,
    },
}

TIER_ORDER = tuple(DEFAULT_TIERS)
FRONTIER_TIER = "frontier"

# ``middle`` remains an independent legacy role. ``creative`` has its own
# policy but, until an administrator materializes `[creative]`, uses the wise
# physical binding so existing four-tier TOML files remain valid.
TIER_BINDING_ALIASES: dict[str, str] = {"creative": "wise"}

# (Le ex-whitelist di NOMI modello per il quality-floor del wise sono state
# rimosse: i tier sono astratti, non si gata sull'identità del modello.)


class TierConfigError(Exception):
    """Configurazione dei tier non valida (es. wise mancante)."""

    def __init__(self, message: str, *, tier: str = ""):
        super().__init__(message)
        self.tier = tier


class _TierBoundProvider:
    """Provider concreto con la policy del tier applicata al confine.

    ``LLMRouter.provider(tier)`` è un'API pubblica usata da alcuni consumer
    storici. Se restituisse il provider nudo, quei consumer salterebbero la
    policy ``think``/``temperature`` configurata per il tier. Questo adapter
    mantiene il contratto del provider (attributi inoltrati con
    ``__getattr__``), applica sempre la policy del tier e rifiuta un secondo
    profilo di decoding introdotto dal singolo chiamante.
    """

    __slots__ = ("_provider", "_policy", "_tier")

    def __init__(self, provider, policy: dict, tier: str):
        self._provider = provider
        self._policy = dict(policy)
        self._tier = tier

    def __getattr__(self, name: str):
        return getattr(self._provider, name)

    def _call_kwargs(self, kwargs: dict) -> dict:
        resolved = dict(kwargs)
        forbidden = sorted(set(resolved) & set(INFERENCE_POLICY_KEYS))
        if forbidden:
            raise TierConfigError(
                "tier-owned generation policy cannot be overridden per call: "
                + ", ".join(forbidden))
        for key in ("temperature", "think"):
            if key in self._policy:
                resolved[key] = self._policy[key]

        # ``reasoning_budget`` è un parametro di llama.cpp. Non inviarlo a
        # provider che non lo supportano e non renderlo un override implicito
        # quando il thinking è spento.
        if (
                getattr(self._provider, "name", "") == "llamacpp"
                and resolved.get("think") is True
        ):
            resolved["reasoning_budget"] = max(
                1, int(self._policy.get("reasoning_budget") or 0))
        return resolved

    def chat(self, system, user, **kwargs):
        from llm_telemetry import mark_call_started, tier_context

        call_kwargs = self._call_kwargs(kwargs)
        with tier_context(self._tier):
            mark_call_started()
            return self._provider.chat(system, user, **call_kwargs)

    def chat_with_tools(self, system, user, tools, history=None, **kwargs):
        from llm_telemetry import mark_call_started, tier_context

        call_kwargs = self._call_kwargs(kwargs)
        with tier_context(self._tier):
            mark_call_started()
            return self._provider.chat_with_tools(
                system, user, tools, history=history, **call_kwargs)


def provider_from_tier_spec(
        tier: str, spec: dict, *, level: str | None = None,
):
    """Build a concrete provider while preserving the logical tier contract.

    This is the canonical boundary for fallback chains too.  Constructing a
    provider directly from a fallback spec would otherwise bypass both the
    tier inference policy and tier-level telemetry.
    """

    completed = complete_tier_spec(tier, spec, level=level)
    provider_spec = {
        key: value for key, value in completed.items()
        if key not in INFERENCE_POLICY_KEYS
    }
    provider = make_provider_from_spec(provider_spec)
    return _TierBoundProvider(provider, completed, tier)


def _load_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    if tomllib is None:
        raise RuntimeError("tomllib non disponibile (richiede Python 3.11+).")
    return tomllib.loads(path.read_text(encoding="utf-8"))


# Tier canonici riconosciuti come sezioni top-level flat (oltre al nested
# `[tiers.<name>]`). Ogni nome qui entra nella mappa `tiers` se presente
# come `[<name>]` con almeno `provider` + `model`.
_TOP_LEVEL_TIER_NAMES = TIER_ORDER


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


@dataclass(frozen=True, slots=True)
class TierConfigDocument:
    """Esito osservabile della configurazione dei tier linguistici.

    ``tiers`` usa lo stesso normalizzatore del router, quindi la console non
    mantiene una seconda interpretazione dei formati flat e legacy.
    """

    path: Path
    exists: bool
    tiers: dict
    error: str = ""


def tier_config_document(
        config_path: Path | None = None,
) -> TierConfigDocument:
    """Legge e normalizza il TOML LLM senza caricare provider o modelli."""

    path = Path(config_path) if config_path is not None else _default_config_path()
    if not path.exists():
        return TierConfigDocument(path, False, {})
    try:
        raw = _load_config_file(path)
        tiers = _normalize_tiers_dict(raw)
    except Exception as exc:
        return TierConfigDocument(path, True, {}, str(exc))
    return TierConfigDocument(path, True, tiers)


_TIERS_FILE_CACHE: dict = {"key": None, "tiers": None}

INFERENCE_POLICY_KEYS = (
    "think", "temperature", "reasoning_budget",
)


def _tier_and_level(
        tier: str, level: str | None = None,
) -> tuple[str, str | None]:
    """Validate the closed request vocabulary before provider resolution."""

    request_level = getattr(tier, "level", None)
    tier_name = str(tier)
    if level is not None and request_level not in (None, level):
        raise ValueError("conflicting fast level in LLM request")
    selected_level = level if level is not None else request_level
    if tier_name not in DEFAULT_TIERS:
        raise ValueError(
            f"unknown tier {tier_name!r}; valid: {list(DEFAULT_TIERS)}")
    if tier_name != "fast":
        if selected_level is not None:
            raise ValueError("level is valid only for tier 'fast'")
        return tier_name, None
    selected_level = selected_level or FAST_DEFAULT_LEVEL
    if selected_level not in FAST_LEVEL_ORDER:
        raise ValueError(
            f"unknown fast level {selected_level!r}; "
            f"valid: {list(FAST_LEVEL_ORDER)}")
    return tier_name, selected_level


def _fast_level_overrides(spec: dict, level: str) -> dict:
    """Extract one optional ``[fast.level.<name>]`` override."""

    level_map = spec.get("level")
    if not isinstance(level_map, dict):
        return {}
    override = level_map.get(level)
    return dict(override) if isinstance(override, dict) else {}


def complete_tier_spec(
        tier: str, spec: dict | None = None, *, level: str | None = None,
) -> dict:
    """Complete one tier binding with its effective logical defaults.

    This is the common, side-effect-free completion step used by the runtime
    resolver and by read-only introspection surfaces.  Keeping it here avoids
    a second interpretation of which values are inherited from the tier and
    which belong to the concrete provider.

    ``spec`` is an already selected binding (configured, aliased, or empty).
    Provider/model/endpoint defaults are inherited only when they remain
    compatible with the tier's default provider; inference-policy defaults
    always belong to the logical tier.
    """

    tier, level = _tier_and_level(tier, level)
    default = dict(DEFAULT_TIERS[tier])
    if tier == "fast":
        default.update(DEFAULT_FAST_LEVELS[level])
    raw = dict(spec or {})
    level_override = _fast_level_overrides(raw, level) if tier == "fast" else {}
    raw.pop("level", None)
    chosen = raw
    chosen.update(level_override)
    if not chosen:
        return default
    if (not chosen.get("model")
            and chosen.get("provider") == default.get("provider")):
        chosen["model"] = default.get("model")
    if (not chosen.get("endpoint") and not chosen.get("base_url")
            and chosen.get("provider") == default.get("provider")):
        chosen["endpoint"] = default.get("endpoint")
    for key in INFERENCE_POLICY_KEYS:
        if key not in chosen and key in default:
            chosen[key] = default[key]
    if "endpoint" not in chosen and chosen.get("base_url"):
        chosen["endpoint"] = chosen["base_url"]
    return chosen


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


def _alias_binding(target: str, source: str, spec: dict) -> dict:
    """Copy a physical binding without copying another tier's policy."""

    aliased = {
        key: value for key, value in dict(spec).items()
        if key not in INFERENCE_POLICY_KEYS and not str(key).startswith("_")
    }
    aliased["_aliased_from"] = source
    return aliased


def _resolve_tier_bindings(configured: dict) -> dict:
    """Validate and complete the logical role map without loading providers."""

    if not configured:
        return {name: dict(spec) for name, spec in DEFAULT_TIERS.items()}
    tiers = {
        str(name): dict(spec) for name, spec in configured.items()
        if name in TIER_ORDER and isinstance(spec, dict)
    }
    # A user document is an override, not an all-or-nothing duplicate of the
    # factory map. The three legacy local roles remain available from defaults
    # when omitted; ``creative`` is then bound to wise below until configured.
    # Explicit invalid bindings still fail below.
    for baseline in ("fast", "middle", "wise"):
        if baseline not in tiers:
            tiers[baseline] = dict(DEFAULT_TIERS[baseline])

    fast_levels = tiers["fast"].get("level")
    if fast_levels is not None:
        if not isinstance(fast_levels, dict):
            raise TierConfigError("fast.level deve essere una tabella TOML", tier="fast")
        unknown = sorted(set(fast_levels) - set(FAST_LEVEL_ORDER))
        if unknown:
            raise TierConfigError(
                "livello fast sconosciuto: " + ", ".join(unknown), tier="fast")
        if any(not isinstance(value, dict) for value in fast_levels.values()):
            raise TierConfigError(
                "ogni fast.level deve essere una tabella TOML", tier="fast")

    # ``provider = "none"`` is the installer's explicit representation of an
    # optional frontier that has not been enabled.  It is semantically the
    # same as an omitted section, so an explicit call gets the canonical
    # TierConfigError instead of reaching an unknown concrete provider.
    frontier = tiers.get("frontier")
    if (isinstance(frontier, dict)
            and str(frontier.get("provider") or "").strip().casefold() == "none"):
        tiers.pop("frontier", None)

    # Every materialized role needs a concrete provider.  Validate here so a
    # Models-page edit fails immediately and diagnostically, rather than only
    # when a later workload happens to instantiate the provider.
    for tier_name, spec in tiers.items():
        if not _wise_passes_quality_floor(spec):
            raise TierConfigError(
                f"tier {tier_name!r} senza provider configurato. I tier sono "
                "astratti: dichiara almeno `provider`.",
                tier=tier_name,
            )
    for target, source in TIER_BINDING_ALIASES.items():
        if target not in tiers:
            tiers[target] = _alias_binding(target, source, tiers[source])
    return tiers


def resolved_tier_spec(tier: str, *, level: str | None = None) -> dict:
    """Resolve one logical tier to provider binding plus inference policy.

    User configuration owns provider/model/endpoint and may override policy.
    Missing policy fields inherit from the logical tier defaults, not from the
    concrete provider.  This keeps callers independent from model parameters:
    they select a tier, while this resolver decides how that tier reasons.
    """

    tier, level = _tier_and_level(tier, level)
    configured = _tiers_from_config()
    bindings = _resolve_tier_bindings(configured)
    if tier not in bindings:
        # Frontier is the only optional role.  Explicit escalation must never
        # silently become a local fast call.
        raise TierConfigError(
            f"tier {tier!r} richiesto ma non configurato", tier=tier)
    chosen = dict(bindings[tier])
    return complete_tier_spec(tier, chosen, level=level)


def tier_endpoint(tier: str = "fast", *, level: str | None = None) -> str:
    """Endpoint HTTP del tier VIRTUALE — SoT unica per i consumer fuori
    dal router (llm_helpers.call_llm, path deterministico /props +
    /apply-template). Risoluzione: llm_tiers.toml (env
    METNOS_LLM_TIERS_CONFIG > ~/.config/metnos > legacy workspace,
    cache invalidata su mtime) -> DEFAULT_TIERS; `LOCAL_DEFAULT_ENDPOINT`
    solo come ultimo default se nulla e' configurato (tier pure-abstract,
    §7.11). `endpoint`/`base_url` sono alias come nel router."""
    tier, level = _tier_and_level(tier, level)
    spec = resolved_tier_spec(tier, level=level)
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
    provider = str(spec.get("provider") or "").strip().casefold()
    return bool(provider and provider != "none")


# Repertorio dei prompt addendum provider-specifici. Caricato da file TOML:
#   1. <install_root>/runtime/prompts.toml         (default bundled)
#   2. ~/.config/metnos/prompts.toml            (override utente, opzionale)
# Origine empirica delle scoperte: vedi memorie di progetto.

PROMPTS_BUNDLED_PATH = Path(__file__).parent / "prompts.toml"
PROMPTS_USER_PATH = _C.PATH_USER_CONFIG / "prompts.toml"


def _load_prompts_repertoire() -> list[dict]:
    """Load provider hints from governed files; absence means no addendum."""
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

    L'API `chat()` e `chat_with_tools()` accetta uno dei nomi in
    :data:`TIER_ORDER` (default ``fast``).
    """

    def __init__(self, *, config_path: Optional[Path] = None,
                 tiers_override: Optional[dict] = None):
        if tiers_override is not None:
            tiers = dict(tiers_override)
        else:
            # Resolve the default path when the router is created.  The
            # administration UI may create the canonical user file after this
            # module was imported; a process-lifetime CONFIG_PATH would make
            # a successful save ineffective until a service restart.
            cfg = _load_config_file(config_path or _default_config_path())
            tiers = _normalize_tiers_dict(cfg)

        tiers = _resolve_tier_bindings(tiers)

        # Config parziali possono dichiarare solo il provider. Completa il
        # modello dalla SoT del tier, ma solo quando il provider coincide:
        # nessun modello di un vendor viene applicato a un altro vendor.
        for tier_name, spec in tiers.items():
            default = DEFAULT_TIERS.get(tier_name) or {}
            if (isinstance(spec, dict) and not spec.get("model")
                    and spec.get("provider") == default.get("provider")
                    and default.get("model")):
                spec["model"] = default["model"]

        self.tiers = tiers
        self._provider_cache = {}

    def provider(self, tier: str = "fast", *, level: str | None = None):
        tier, level = _tier_and_level(tier, level)
        if tier == FRONTIER_TIER and tier not in self.tiers:
            _fm = DEFAULT_TIERS[FRONTIER_TIER]  # SoT model id, mai hardcoded altrove
            raise TierConfigError(
                "tier 'frontier' richiesto ma non configurato. Aggiungi a "
                "~/.config/metnos/llm_tiers.toml: [tiers.frontier] "
                f"provider='{_fm.get('provider', 'anthropic')}' model='{_fm['model']}'.",
                tier=FRONTIER_TIER,
            )
        cache_key = (tier, level)
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]
        spec = {k: v for k, v in self.tiers[tier].items()
                if not k.startswith("_")}
        bound = provider_from_tier_spec(tier, spec, level=level)
        self._provider_cache[cache_key] = bound
        return bound

    def is_aliased(self, tier: str) -> bool:
        tier, _ = _tier_and_level(tier)
        return bool(self.tiers.get(tier, {}).get("_aliased_from"))

    def fallback_chain(self, tier: str, *, level: str | None = None) -> list[dict]:
        """Ritorna la catena di provider per `tier`: primary + fallback
        secondari. Lista di spec {provider, model, ...} pronte per
        `make_provider_from_spec`. Vuota se il tier non e' configurato.

        Usata da `consult_frontier` per ritentare con fallback se primary
        fallisce (es. Opus 4.8 → un frontier secondario → fail). Niente fallback chain per
        per i tier locali di default (catena = primary only); se servisse
        in futuro, basta aggiungere `[[wise.fallback]]` in llm_tiers.toml.
        """
        tier, level = _tier_and_level(tier, level)
        spec = self.tiers.get(tier)
        if not spec:
            return []
        primary = {k: v for k, v in spec.items()
                   if not k.startswith("_") and k != "fallback"}
        out = [complete_tier_spec(tier, primary, level=level)]
        for f in (spec.get("fallback") or []):
            if not isinstance(f, dict):
                continue
            if "provider" not in f or "model" not in f:
                continue
            fallback = dict(f)
            # A fallback changes the physical binding, not the logical
            # contract.  It inherits the administrator-configured tier policy
            # unless that fallback explicitly declares a provider-specific
            # override.
            for key in INFERENCE_POLICY_KEYS:
                if key not in fallback and key in spec:
                    fallback[key] = spec[key]
            out.append(complete_tier_spec(tier, fallback, level=level))
        return out

    def describe(self) -> dict:
        out = {}
        for t in TIER_ORDER:
            if t not in self.tiers:
                continue
            spec = self.tiers[t]
            out[t] = {
                "provider": spec.get("provider"),
                "model":    spec.get("model"),
                "aliased":  bool(spec.get("_aliased_from")),
                "aliased_from": spec.get("_aliased_from") or "",
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
        if for_code and provider_name:
            sys = sys + code_gen_hint_for(provider_name, provider_model)
        return sys

    def chat(self, system, user, *, tier="fast", level: str | None = None,
             for_code: bool = False,
             **kwargs) -> ChatResult:
        tier, level = _tier_and_level(tier, level)
        prov = self.provider(tier, level=level)
        return prov.chat(
            self._system_for_tier(system, tier, prov.name, prov.model, for_code),
            user, **kwargs,
        )

    def chat_with_tools(self, system, user, tools, *, tier="fast",
                        level: str | None = None, history=None,
                        for_code: bool = False,
                        **kwargs) -> ToolUseResult:
        tier, level = _tier_and_level(tier, level)
        prov = self.provider(tier, level=level)
        return prov.chat_with_tools(
            self._system_for_tier(system, tier, prov.name, prov.model, for_code),
            user, tools, history=history, **kwargs,
        )


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="LLM tier router")
    ap.add_argument("--describe", action="store_true",
                    help="stampa la configurazione effettiva dei tier")
    ap.add_argument("--tier", default="fast", choices=TIER_ORDER)
    ap.add_argument("--level", choices=FAST_LEVEL_ORDER)
    ap.add_argument("--prompt", default="Rispondi solo: OK")
    ap.add_argument("--system", default="Sei un assistente conciso.")
    args = ap.parse_args()

    r = LLMRouter()
    if args.describe:
        import json
        print(json.dumps(r.describe(), indent=2, ensure_ascii=False))
        return
    res = r.chat(args.system, args.prompt, tier=args.tier, level=args.level,
                 max_tokens=2048)
    selected = args.tier if not args.level else f"{args.tier}.{args.level}"
    print(f"[{selected}] {res.provider}:{res.model}  {res.latency_ms}ms  "
          f"in={res.in_tokens} out={res.out_tokens}")
    print(f"  text: {res.text!r}")


if __name__ == "__main__":
    _cli()

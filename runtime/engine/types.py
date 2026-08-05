"""engine/types.py — dataclass canonici condivisi fra tutti i layer.

Tipo single-source-of-truth per:
  - Intent: output di intent_extractor (verb/object/keywords)
  - Framework: output di Proposer (steps, fillers, final_message)
  - StepResult / RunResult: output di Executor (per step + aggregato)
  - Error / ErrorClass: classificazione errori (4 classi strutturali)

§7.3 universalità: nessun campo domain-specific. Aggiungere nuovo
executor non richiede modifiche qui.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Intent (output intent_extractor) ──────────────────────────────────────

@dataclass
class Intent:
    verb: str = ""
    object: str = ""
    keywords: list[str] = field(default_factory=list)
    confidence: float = 1.0
    lang: str = "it"
    # Decomposizione compound (4/6): per una query multi-azione l'intent LLM
    # ritorna la LISTA ordinata dei sotto-intenti {verb,object}, uno per
    # clausola. `verb`/`object` restano il PRIMARIO (actions[0]) per back-compat;
    # `actions` abilita il ranking pool per-clausola in dispatch (fix routing
    # compound SENZA dizionari di sinonimi — multilingue via LLM).
    actions: list[dict] = field(default_factory=list)

    def is_complete(self) -> bool:
        return bool(self.verb and self.object)


# ── Framework (output Proposer, input Executor) ───────────────────────────

@dataclass
class StepSpec:
    """Singolo step del framework. Args può contenere placeholder
    ${FILLER:name}, ${stepN.field}, ${RUNTIME:key}, from_step:int.
    """
    tool: str
    args: dict = field(default_factory=dict)
    if_prev_entries_nonempty: bool = False  # guard mutating step


@dataclass
class FillerSpec:
    """Specifica per ${FILLER:name} risolto via LLM fast tier."""
    prompt: str = ""
    default: str = ""
    tier: str = "fast"


@dataclass
class Framework:
    steps: list[StepSpec] = field(default_factory=list)
    fillers: dict[str, FillerSpec] = field(default_factory=dict)
    final_message: str = ""  # template con placeholder ${stepN.field}
    # Budget elevato esclusivamente da normalizzatori deterministici interni.
    # Non viene deserializzato dall'output LLM né serializzato nelle cache: un
    # piano proposto resta quindi soggetto al cap ordinario dell'Executor,
    # mentre una pipeline canonica può dichiarare il proprio numero finito di
    # passi dopo essere stata ricostruita e validata dal runtime.
    runtime_step_cap: int = field(default=0, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict) -> "Framework":
        """Costruisce Framework da dict (output Proposer JSON parse)."""
        if not isinstance(d, dict):
            return cls()
        # Tollera output LLM malformato (§2.8/§7.3): uno step o un filler
        # emesso come stringa invece che come oggetto NON deve far crashare il
        # parse dei candidati (prima: `s.get`/`v.get` su str → AttributeError →
        # planner a mani vuote). Gli elementi non-dict vengono ignorati.
        steps = [
            StepSpec(
                tool=s.get("tool", ""),
                args=s.get("args") or {},
                if_prev_entries_nonempty=bool(s.get("if_prev_entries_nonempty")),
            )
            for s in (d.get("steps") or [])
            if isinstance(s, dict)
        ]
        fillers = {
            k: FillerSpec(
                prompt=v.get("prompt", ""),
                default=v.get("default", ""),
                tier=v.get("tier", "fast"),
            )
            for k, v in (d.get("fillers") or {}).items()
            if isinstance(v, dict)
        }
        return cls(
            steps=steps,
            fillers=fillers,
            final_message=d.get("final_message", ""),
        )

    def to_dict(self) -> dict:
        return {
            "steps": [
                {"tool": s.tool, "args": s.args,
                 **({"if_prev_entries_nonempty": True}
                    if s.if_prev_entries_nonempty else {})}
                for s in self.steps
            ],
            "fillers": {
                k: {"prompt": v.prompt, "default": v.default, "tier": v.tier}
                for k, v in self.fillers.items()
            },
            "final_message": self.final_message,
        }


# ── Run result (output Executor) ──────────────────────────────────────────

@dataclass
class StepRun:
    step_idx: int
    tool: str
    args: dict
    result: dict
    ok: bool
    latency_ms: int
    # «Semina di turno» (ADR 0177 M1): natura dello step quando entra come
    # SEED (stato-pregresso iniettato prima del piano, vs step eseguito ORA).
    #   "live" — eseguito in questo turno (default, ogni step reale).
    #   "input" — seed CONSUMABILE: un input pronto che il primo step reale
    #             usa via from_step=1 (es. foto @uploaded). NON è "fatto".
    #   "done" — seed GIÀ ESEGUITO in un turno precedente (continuazione di un
    #            dialogo/gate): il proposer NON deve ri-emetterlo, gli step a
    #            valle lo referenziano via from_step. Guardia dedup deterministica.
    kind: str = "live"
    # Host di ESECUZIONE reale dello step (data-locality per la co-location
    # consumer↔producer, 7/7/2026): "server" (locale su .33) o un device_id
    # (girato in remoto). Un consumer from_step di un producer "server" deve
    # restare sul server (i suoi entries/path sono dati locali). Derivato da
    # `result["_ran_on_device"]` (settato al choke-point invoke_executor).
    host: str = "server"
    # Autorita' dei DATI prodotti. Di norma coincide con `host`, ma un
    # trasformatore puro eseguito sul server (es. filter_entries) conserva i
    # path del producer Windows: quei path devono continuare a essere letti
    # sul device. Separare i due concetti evita di interpretare C:\\... come un
    # path locale solo perche' il filtro e' computato su .33.
    data_host: str | None = None


@dataclass
class RunResult:
    steps: list[StepRun] = field(default_factory=list)
    final_text: str = ""
    final_kind: str = ""        # answer | error | ask
    framework_hash: str = ""
    ok_count: int = 0
    elapsed_ms: int = 0
    aborted_reason: str = ""
    # gate-resume (20/6/2026): dialog_id del gate get_approval che ha messo in
    # PAUSA la pipeline (decision=input_required). Il bridge lo usa per
    # persistere il contesto di ripresa nel dialog (on_complete
    # resume_engine_gate); "" se nessun gate in pausa.
    gate_dialog_id: str = ""


# ── Error classification (4 classi strutturali §7.3) ──────────────────────

ErrorClass = str  # alias: 'wrong_tool' | 'wrong_args' | 'missing_input' | 'out_of_scope'

ERROR_CLASSES = ("wrong_tool", "wrong_args", "missing_input", "out_of_scope")
RECOVERABLE = frozenset({"wrong_tool", "wrong_args", "missing_input"})

# Errori operativi dichiarati dagli executor: cambiare piano o strumento non
# ripara rete/servizio. Restano fuori dalle quattro classi strutturali del
# recovery, ma impediscono di degradare a ``wrong_args`` e false lacune synt.
OPERATIONAL_ERROR_CLASSES = frozenset({
    "network", "timeout", "server_error", "rate_limited", "sidecar_down",
    "provider_unavailable", "service_unavailable", "exception",
    "browser_unavailable", "side_browser_unavailable", "navigation_failed",
    "index_missing", "schema_too_old",
})


def result_error_classes(result: dict | None) -> tuple[str, ...]:
    """Classi strutturate top-level e per-item, deduplicate in ordine."""
    if not isinstance(result, dict):
        return ()
    values = [result.get("error_class")]
    failed = result.get("failed")
    if isinstance(failed, list):
        values.extend(item.get("error_class") for item in failed
                      if isinstance(item, dict))
    seen = set()
    out = []
    for value in values:
        value = str(value or "").strip().lower()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def result_error_detail(result: dict | None, *, max_items: int = 3) -> str:
    """Primo dettaglio top-level o errori per-item deduplicati e limitati."""
    if not isinstance(result, dict):
        return ""
    direct = (result.get("final_message_hint") or result.get("error")
              or result.get("message"))
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    failed = result.get("failed")
    if not isinstance(failed, list):
        return ""
    parts = []
    for item in failed:
        if not isinstance(item, dict):
            continue
        detail = item.get("error") or item.get("message")
        if isinstance(detail, str) and detail.strip():
            clean = detail.strip()
            if clean not in parts:
                parts.append(clean)
        if len(parts) >= max(1, max_items):
            break
    return "; ".join(parts)

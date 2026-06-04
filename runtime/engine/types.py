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
from typing import Any, Optional


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


@dataclass
class RunResult:
    steps: list[StepRun] = field(default_factory=list)
    final_text: str = ""
    final_kind: str = ""        # answer | error | ask
    framework_hash: str = ""
    ok_count: int = 0
    elapsed_ms: int = 0
    aborted_reason: str = ""


# ── Error classification (4 classi strutturali §7.3) ──────────────────────

ErrorClass = str  # alias: 'wrong_tool' | 'wrong_args' | 'missing_input' | 'out_of_scope'

ERROR_CLASSES = ("wrong_tool", "wrong_args", "missing_input", "out_of_scope")
RECOVERABLE = frozenset({"wrong_tool", "wrong_args", "missing_input"})

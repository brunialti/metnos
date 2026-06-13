"""engine/validator.py — Layer 2: typecheck framework pre-execute (opt-in).

Cattura errori prima dell'esecuzione:
  - tool inesistente nel catalog
  - args type mismatch (string vs array vs dict)
  - requires_one_of violato
  - from_step out-of-range
  - placeholder ${stepN.field} non risolvibile

Riusa `validate_args` esistente + catalog lookup. Senza Validator
attivo (default OFF), errori vengono catturati dall'Executor a runtime
con costo LLM call sprecato.

§7.9 deterministic: zero LLM. Lookup catalog + schema check.

Toggle: METNOS_VALIDATOR=1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .types import Framework

log = logging.getLogger(__name__)


@dataclass
class ValidationError:
    step_idx: int
    code: str  # tool_unknown | invalid_args | from_step_invalid | requires_one_of_violated
    detail: str


@dataclass
class ValidationResult:
    ok: bool
    errors: list[ValidationError] = field(default_factory=list)


def _is_placeholder(value) -> bool:
    """True se il valore contiene un placeholder ${...} che l'Executor
    risolve a runtime (${stepN.field}, ${steps.N.field}, ${RUNTIME:key},
    ${FILLER:name}). Un required così "valorizzato" NON è mancante:
    il check va delegato a runtime, non bloccato qui (§7.9).
    """
    if isinstance(value, str):
        return "${" in value
    if isinstance(value, dict):
        return any(_is_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(_is_placeholder(v) for v in value)
    return False


class Validator:
    """Typecheck framework prima di Executor.run()."""

    def __init__(self, catalog: list):
        """catalog: list di Executor objects (con .name, .args_schema)."""
        self._catalog_by_name = {
            getattr(e, "name", None): e for e in catalog if getattr(e, "name", None)
        }

    def check(self, framework: Framework) -> ValidationResult:
        errors: list[ValidationError] = []
        for i, step in enumerate(framework.steps, start=1):
            # Tool exist? (final_answer è virtual, ammesso)
            if step.tool == "final_answer":
                continue
            exec_obj = self._catalog_by_name.get(step.tool)
            if exec_obj is None:
                errors.append(ValidationError(
                    step_idx=i, code="tool_unknown",
                    detail=f"tool '{step.tool}' non nel catalog"))
                continue
            # from_step bounds
            fs = step.args.get("from_step")
            if isinstance(fs, int):
                if fs < 1 or fs >= i:
                    errors.append(ValidationError(
                        step_idx=i, code="from_step_invalid",
                        detail=f"from_step={fs} fuori range [1, {i-1}]"))
            # Args schema check
            schema = getattr(exec_obj, "args_schema", None) or {}
            err = self._check_args(step.args, schema)
            if err:
                errors.append(ValidationError(
                    step_idx=i, code="invalid_args", detail=err))
        return ValidationResult(ok=not errors, errors=errors)

    def _check_args(self, args: dict, schema: dict) -> Optional[str]:
        """Lightweight: required + requires_one_of + type check sui top-level."""
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        # Required: la chiave deve essere presente in args. Placeholder-aware
        # §7.9 — un required coperto da from_step/entries (piping upstream) o
        # da un placeholder `${...}` (risolto a runtime dall'Executor:
        # ${stepN.field}, ${RUNTIME:key}, ${FILLER:name}) NON è "mancante".
        # Coerente con agent_runtime.validate_args (from_step → entries).
        for r in required:
            if r in args and not _is_placeholder(args.get(r)):
                continue  # valore concreto presente
            if r in args:
                continue  # placeholder ${...}: risolto dall'Executor
            # chiave assente: tollerata solo se coperta da piping upstream
            if r == "from_step" and "entries" in args:
                continue
            if r == "entries" and (args.get("from_step") is not None):
                continue
            # qualsiasi required soddisfatto da from_step (resolver lo espande
            # a `entries` prima dell'invoke) → non mancante
            if "from_step" in args and r not in ("from_step",):
                continue
            return f"missing required arg '{r}'"
        # requires_one_of
        for group in schema.get("requires_one_of") or []:
            if not isinstance(group, list) or not group:
                continue
            provided = False
            for k in group:
                v = args.get(k)
                if v is None or v == "":
                    continue
                if isinstance(v, (list, dict)) and not v:
                    continue
                provided = True
                break
            if not provided:
                return f"requires_one_of {group} violato"
        # Type check (basic)
        for k, v in args.items():
            if k.startswith("_") or k in ("from_step", "entries"):
                continue  # runtime-injected / piping
            decl = props.get(k)
            if not decl:
                continue  # unknown arg, lascia passare (executor tollerante)
            expected = decl.get("type")
            if expected == "array" and not isinstance(v, list):
                return f"arg '{k}' atteso array, ricevuto {type(v).__name__}"
            if expected == "object" and not isinstance(v, dict):
                return f"arg '{k}' atteso object, ricevuto {type(v).__name__}"
            if expected == "string" and not isinstance(v, (str, int, float)):
                # int/float tollerati come string-coercible
                return f"arg '{k}' atteso string, ricevuto {type(v).__name__}"
            if expected == "boolean" and not isinstance(v, (bool, str, int)):
                return f"arg '{k}' atteso boolean, ricevuto {type(v).__name__}"
        return None

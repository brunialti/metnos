"""engine/validator.py — Layer 2: typecheck framework pre-execute.

Cattura errori prima dell'esecuzione:
  - tool inesistente nel catalog
  - args type mismatch (string vs array vs dict)
  - requires_one_of violato
  - from_step out-of-range
  - placeholder ${stepN.field} non risolvibile

Riusa `validate_args` esistente + catalog lookup. Il Validator è attivo per
default; se viene disabilitato esplicitamente, gli errori vengono catturati
dall'Executor a runtime con il costo di una chiamata LLM sprecata.

§7.9 deterministic: zero LLM. Lookup catalog + schema check.

Toggle: METNOS_VALIDATOR=1 (default); 0 lo disabilita.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from from_step_projection import (
    carries_upstream_payload, projection_can_fill,
    required_source_context_fields,
)
from messages import get as _msg

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
            if provided:
                continue
            # Un consumatore in pipeline non porta ancora il dato: `from_step`
            # diventa `entries` — o l'arg-lista naturale del consumatore — solo
            # all'invoke. Il ramo dei `required` qui sopra lo sa gia'; questo non
            # lo sapeva, e bocciava la forma CANONICA del piping (§4.1) su nove
            # gruppi del catalogo, fra cui read_files, delete_files e get_urls.
            # Ogni bocciatura costava un re-propose LLM, e il piano rifatto
            # poteva essere peggiore: misurato il 6/8 su «trova i file .md ... e
            # leggili», dove il proposer riproponeva find_files_hash (duplicati)
            # al posto di find_files e il turno finiva «Nessun risultato».
            # Un gruppo di soli scalari resta violato: il contesto scalare ha il
            # suo controllo, con un messaggio proprio (§2.8).
            if (carries_upstream_payload(args)
                    and projection_can_fill(schema, group)):
                continue
            return f"requires_one_of {group} violato"
        missing_context = required_source_context_fields(
            args, schema, allow_deferred_from_step=True)
        if missing_context:
            return _msg(
                "ERR_SOURCE_CONTEXT_REQUIRED",
                fields=", ".join(missing_context),
            )
        # Type check (basic)
        for k, v in args.items():
            if k.startswith("_") or k in ("from_step", "entries"):
                continue  # runtime-injected / piping
            decl = props.get(k)
            if not decl:
                continue  # unknown arg, lascia passare (executor tollerante)
            declared = decl.get("type")
            expected = (
                declared if isinstance(declared, list)
                else [declared] if isinstance(declared, str)
                else []
            )

            def _matches(kind):
                if kind == "array":
                    return isinstance(v, list)
                if kind == "object":
                    return isinstance(v, dict)
                if kind == "string":
                    # int/float tollerati come string-coercible (legacy).
                    return isinstance(v, (str, int, float)) and not isinstance(v, bool)
                if kind == "boolean":
                    return isinstance(v, (bool, str, int))
                if kind == "integer":
                    return isinstance(v, int) and not isinstance(v, bool)
                if kind == "number":
                    return isinstance(v, (int, float)) and not isinstance(v, bool)
                if kind == "null":
                    return v is None
                return True

            if expected and not any(_matches(kind) for kind in expected):
                return (
                    f"arg '{k}' atteso {' | '.join(expected)}, "
                    f"ricevuto {type(v).__name__}"
                )
        return None

"""One pre-execution correction for an unproved row destination."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
import re

from .executor import compute_execution_fingerprint
from .partition_plans import PartitionDestinationError

log = logging.getLogger(__name__)
_ABSENT = object()
_STEP_REFERENCE = re.compile(r"(\$\{\s*step)\d+(?=\.)")
_MAX_VALUE = 4096
_MAX_CONTEXT = 16384
_PATCH_GRAMMAR = r'''root ::= "{" ws "\"value\"" ws ":" ws string ws "}"
string ::= "\"" char* "\""
char ::= [^"\\\x00-\x1f] | "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
ws ::= [ \t\n\r]*'''


def _local_target(raw, error, catalog):
    """Locate one unchanged argument across a possible step renumbering.

    A normalized ordinal is never used as a raw ordinal. Only the ordinal
    inside a step reference may differ; ambiguity or any other value change
    refuses repair. New arguments synthesized by guards are not editable.
    """
    diagnostic = error.diagnostic
    argument, tool = diagnostic.get("argument"), diagnostic.get("tool")
    steps = getattr(error, "repair_steps", ())
    if not argument or not tool or not 0 < error.step <= len(steps):
        return None
    current = steps[error.step - 1].args.get(argument)
    if not isinstance(current, str) or not current or len(current) > _MAX_VALUE:
        return None
    signature = _STEP_REFERENCE.sub(r"\1#", current)
    def matches(name, args):
        value = args.get(argument) if isinstance(args, dict) else None
        return (name == tool and isinstance(value, str)
                and _STEP_REFERENCE.sub(r"\1#", value) == signature)
    positions = [i for i, item in enumerate(raw["steps"])
                 if matches(item["tool"], item["args"])]
    if len(positions) != 1 or sum(matches(s.tool, s.args) for s in steps) != 1:
        return None
    declarations = {t.name: t for t in catalog or ()}
    declaration = declarations.get(tool)
    schema = getattr(declaration, "args_schema", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if argument not in properties or not isinstance(properties[argument], dict):
        return None
    field_schema = properties[argument]
    if field_schema.get("type", "string") != "string":
        return None
    # Static contract metadata and plan templates only; no result rows,
    # credentials, provider settings, unrelated arguments or complete plan.
    field_schema = deepcopy(field_schema)
    declared_limit = field_schema.get("maxLength", _MAX_VALUE)
    if type(declared_limit) is not int or declared_limit < 1:
        return None
    field_schema["maxLength"] = min(declared_limit, _MAX_VALUE)
    context = {"argument": argument, "value": current,
               "schema": field_schema, "related_templates": []}
    for reference in diagnostic.get("references", ()):
        number = reference.get("step")
        if (not reference.get("declares_row_destination")
                or not isinstance(number, int) or not 0 < number <= len(steps)):
            continue
        producer = steps[number - 1]
        template = producer.args.get("path_template")
        if isinstance(template, str) and len(template) <= _MAX_VALUE:
            context["related_templates"].append({"step": number, "tool": producer.tool,
                "from_step": producer.args.get("from_step"), "value": template})
    if len(json.dumps(context, ensure_ascii=False)) > _MAX_CONTEXT:
        return None
    return positions[0], argument, context


def _patch_value(response, schema):
    """Accept exactly one string value; duplicate keys and plans are invalid."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate patch key")
            result[key] = value
        return result
    if not isinstance(response, str) or len(response) > _MAX_CONTEXT:
        raise ValueError("invalid patch response")
    # Runtime callbacks return text. Reuse thinking removal, tolerate a
    # complete JSON fence, but never fish a patch out of another plan.
    from .proposer import _strip_think
    response = _strip_think(response).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", response)
    if fenced:
        response = fenced.group(1)
    patch = json.loads(response, object_pairs_hook=unique)
    value = patch.get("value") if isinstance(patch, dict) else None
    if (not isinstance(patch, dict) or set(patch) != {"value"}
            or not isinstance(value, str) or not value.strip()
            or len(value) > _MAX_VALUE or "${" in value):
        raise ValueError("invalid destination patch")
    from jsonschema import Draft7Validator
    # Only local string constraints: never resolve a remote schema reference.
    Draft7Validator({key: schema[key] for key in
        ("type", "minLength", "maxLength", "pattern", "enum", "const")
        if key in schema}).validate(value)
    return value


def _patch_call_options(intent):
    """Keep the ordinary planner workload; only the response shape shrinks."""
    from llm_workloads import tier_for
    grammar = os.environ.get("METNOS_PROPOSER_GRAMMAR", "1") == "1"
    fast = intent.confidence >= float(os.environ.get("METNOS_PROPOSER_FAST_CONFIDENCE", "0.70"))
    workload = "planner.grammar" if grammar else "planner.routine" if fast else "planner.deliberate"
    options = {"max_tokens": 1024, "tier_override": tier_for(workload)}
    if grammar:
        options["grammar"] = _PATCH_GRAMMAR
    return options


class _RepairCallLimit(RuntimeError):
    """A proposer fallback cannot spend another repair model call."""


def guard_with_partition_repair(framework, *, apply_guards,
                                proposal_kwargs, intent, llm_call):
    """Return the guarded plan and its accepted, pre-guard snapshot.

    Only the first L3 structure pass uses this helper. A rejected proposal
    is never executed. The caller's normal validation and consent boundaries
    still run after a successful correction. Raw snapshots stay in memory;
    diagnostics contain only outcome codes, indices, counters and digests.
    The proposer is deliberately not called again: its graph is frozen and
    the model can only replace the single refused template string.
    """
    rejected_raw = deepcopy(framework.to_dict())
    rejected_fingerprint = compute_execution_fingerprint(framework)
    try:
        return apply_guards(framework), rejected_raw
    except PartitionDestinationError as original:
        evidence = {
            "outcome": "proposal_error", "step": original.step,
            "forwarded_calls": 0, "blocked_calls": 0,
            "rejected_fingerprint": rejected_fingerprint,
            "candidate_fingerprint": "",
        }
        original.rejected_raw = rejected_raw
        original.repair_evidence = evidence

        def bounded_call(*args, **kwargs):
            if evidence["forwarded_calls"]:
                evidence["blocked_calls"] += 1
                raise _RepairCallLimit("partition repair call budget exhausted")
            # Count attempts before forwarding, including provider failures.
            evidence["forwarded_calls"] += 1
            return llm_call(*args, **kwargs)

        previous = getattr(intent, "_recovery_signal", _ABSENT)
        try:
            target = _local_target(rejected_raw, original, proposal_kwargs.get("catalog"))
            if target is None:
                evidence["outcome"] = "unmapped_target"
            else:
                from .proposer import _render_recovery_signal
                from .types import Framework
                position, argument, context = target
                intent._recovery_signal = {**deepcopy(original.diagnostic), "repair_context": context}
                system = _render_recovery_signal(intent, proposal_kwargs.get("lang", "it"))
                evidence["outcome"] = "patch_error"
                response = bounded_call(system, proposal_kwargs["query"], **_patch_call_options(intent))
                value = _patch_value(response, context["schema"])
                candidate = Framework.from_dict(deepcopy(rejected_raw))
                candidate.steps[position].args[argument] = value
                accepted_raw = deepcopy(candidate.to_dict())
                fingerprint = compute_execution_fingerprint(candidate)
                evidence["candidate_fingerprint"] = fingerprint
                if fingerprint == rejected_fingerprint:
                    evidence["outcome"] = "unchanged_proposal"
                else:
                    evidence["outcome"] = "guard_error"
                    guarded = apply_guards(candidate)
                    evidence["outcome"] = "accepted"
                    guarded._partition_repair_rejected_raw = rejected_raw
                    guarded._partition_repair_evidence = evidence
                    return guarded, accepted_raw
        except PartitionDestinationError:
            evidence["outcome"] = "destination_rejected"
        except Exception:
            # Do not log exception text: provider/parser errors can hold data.
            pass
        finally:
            if previous is _ABSENT:
                try:
                    delattr(intent, "_recovery_signal")
                except AttributeError:
                    pass
            else:
                intent._recovery_signal = previous
            log.info("[partition_repair] %s", evidence)
        raise original from None

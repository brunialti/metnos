"""synt_multistage.py — synth orchestrator multistage.

Idea (Roberto, 28/4/2026): dividere la creazione di un executor in 5 stadi,
ognuno con prompt ristretto, tier LLM proporzionato al compito, context
vincolato dai precedenti. Una creazione di executor e' una-tantum (l'utente
accetta la latenza maggiore — multistage costa ~5 round-trip ma converge
meglio del single-prompt mostruoso).

Ordine: dai compiti piu' procedurali/lookup (facili per LLM) a quelli piu'
creativi (richiedono fluidita' linguistica).

Stages:
  1. NAMING+CLASS (procedurale, middle): nome conforme vocabolario chiuso +
                                        revertible/critical/target_kind.
                                        LOOKUP nel vocabolario.
  2. SIGNATURE   (procedurale, middle): args_schema + capabilities + reverse_pattern.
                                        SCHEMA JSON strutturato.
  3. TESTS       (procedurale, middle): 4-6 birth test TOML con expect concrete.
                                        STRUTTURA fissa (input/expect/setup).
  4. DESCRIPTION (creative): description LLM-readable + affinity.
                             PROSA narrativa.
  5. CODE        (creativo+procedurale, wise): file Python invoke + reverse() se serve.
                                                LOGIC che ha tutto il contesto.

Contratti logici (ADR 0207, risolti da ``llm_workloads``): stage 1-3
``middle``, stage 4 ``creative``, stage 5 ``wise`` e verifica semantica
``wise``. Provider, modello e decoding appartengono alla configurazione del
tier; questi stadi possiedono soltanto schema e tetto dell'output.

Ogni stage scrive checkpoint. Se uno stage fallisce, abbandono con motivo.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from logging_setup import get_logger
log = get_logger(__name__)

import prompt_loader
import i18n as _i18n

# ---- Vocabolario chiuso (ADR 0045 + naming convention stringente) ----------
# Centralizzato in runtime/vocab.py — single source of truth.

from vocab import (
    ACTIONS as _VOCAB_ACTIONS,
    OBJECTS as _VOCAB_OBJECTS,
    render_action_categories_block,
    render_action_mapping_block,
    render_objects_inline,
    render_qualifiers_inline,
)
VOCAB_ACTIONS = list(_VOCAB_ACTIONS)
VOCAB_OBJECTS = list(_VOCAB_OBJECTS)

REVERSE_PATTERNS = ["swap_src_dst", "delete_created_dirs",
                    "delete_created_paths", "restore_blob_backup"]

NAME_RE = re.compile(r"^([a-z]+)_([a-z]+)(?:_([a-z][a-z0-9_]*))?$")


# ---- Stage 1 — NAMING + CLASSIFICATION -------------------------------------
# Prompt persistito in `runtime/prompts/<lang>/synt_naming.j2` (ADR 0092 Phase 2).
# Caricato via `prompt_loader.get('synt_naming', **vars)` in `run_stage1`.


# ---- Stages 2-5 prompts persistiti (ADR 0092 Phase 2) ---------------------
# Tutti i prompt synt sono ora in `runtime/prompts/<lang>/synt_*.j2`:
#   - synt_signature.j2 (stage 2)
#   - synt_tests.j2     (stage 3)
#   - synt_description.j2 (stage 4)
#   - synt_code.j2      (stage 5 generic)
#   - synt_code_addendum_<verb>.j2 per i 12 verbi specializzati.
# Caricati via `prompt_loader.get(role, **vars)` ai call site di run_stage*.

# Verbi del vocabolario chiuso che hanno un addendum specializzato per stage 5.
# Gli altri verbi cadono sul prompt generico (synt_code.j2) senza addendum.
SPECIALIZED_VERBS = (
    "move", "delete", "send", "write", "extract", "read", "create",
    "find", "list", "get", "filter", "describe",
)


def _stage5_prompt_for_verb(verb: str, **vars) -> str:
    """Compone il prompt di stage 5 per il verbo dato.

    Pattern: rendiamo `synt_code` (generico) con i `vars`; se il verbo e'
    specializzato anteponiamo l'addendum della stessa lingua. L'ordine non
    dipende da un titolo tradotto dentro il template, quindi funziona anche
    quando viene aggiunta una lingua nuova.

    A2 19/5/2026 v4: inietta `available_i18n_keys` (subset chiavi per
    famiglia) cosi' il LLM riusa chiavi esistenti invece di inventarne.
    Cap di sicurezza in i18n.keys_for_synth_context (max_per_family=30).
    """
    if "available_i18n_keys" not in vars:
        try:
            from i18n import keys_for_synth_context
            # Each key with the values its text needs: «KEY(arg, reason)».
            # Shown bare, every key was called bare (12/11/2026).
            vars["available_i18n_keys"] = {
                family: [key + (f"({', '.join(needed)})" if needed else "")
                         for key in keys for needed in (message_values(key),)]
                for family, keys in keys_for_synth_context().items()}
        except Exception:
            vars["available_i18n_keys"] = {}
    lang = _i18n.current_lang()
    generic = prompt_loader.get("synt_code", lang, **vars)
    if verb in SPECIALIZED_VERBS:
        addendum = prompt_loader.get(f"synt_code_addendum_{verb}", lang)
        return addendum.rstrip() + "\n\n" + generic
    return generic


# ---- Validation helpers ----------------------------------------------------

def validate_stage1(out: dict) -> Optional[str]:
    if out.get("name") is None:
        # legittima rinuncia
        return None
    name = out.get("name", "")
    m = NAME_RE.match(name)
    if not m:
        return f"name {name!r} non rispetta schema action_object[_qualifier] minuscolo"
    action, obj = m.group(1), m.group(2)
    if action not in VOCAB_ACTIONS:
        return f"action {action!r} non in vocabolario chiuso ({len(VOCAB_ACTIONS)} ammesse)"
    if obj not in VOCAB_OBJECTS:
        return f"object {obj!r} non in vocabolario chiuso ({len(VOCAB_OBJECTS)} ammessi)"
    canonical_error = _canonical_name_error(name)
    if canonical_error:
        return f"name {name!r}: {canonical_error}"
    # Inietta action/object derivati dal name: run_stage2 li legge come
    # stage1["action"]/["object"] → senza, l'LLM che omette le chiavi causava
    # KeyError (bug 21/6). setdefault: un valore esplicito dell'LLM vince.
    out.setdefault("action", action)
    out.setdefault("object", obj)
    if not isinstance(out.get("revertible"), bool):
        return "revertible deve essere bool"
    if not isinstance(out.get("critical"), bool):
        return "critical deve essere bool"
    if out.get("target_kind") not in ("path_glob", "host", "exact", "none"):
        return f"target_kind {out.get('target_kind')!r} non valido"
    return None


def validate_stage2(out: dict, stage1: dict) -> Optional[str]:
    if not isinstance(out.get("args_required"), list):
        return "args_required deve essere lista"
    if not isinstance(out.get("args_properties"), dict):
        return "args_properties deve essere dict"
    if not isinstance(out.get("capabilities"), list):
        return "capabilities deve essere lista"
    rp = out.get("reverse_pattern")
    if stage1["revertible"]:
        if rp not in REVERSE_PATTERNS:
            return f"revertible=true ma reverse_pattern={rp!r} non in catalogo {REVERSE_PATTERNS}"
    else:
        if rp is not None:
            return f"revertible=false ma reverse_pattern={rp!r} (deve essere null)"
    return None


def settle_undo_claim(stage1: dict, stage2: dict) -> bool:
    """Keep «revertible» only where the birth door can prove the undo.

    Stage 1 guesses whether the action can be undone and stage 2 picks a
    catalog pattern, but the door proves an undo only on the contract shapes
    it owns a fixture for. A claim outside them was refused at the very end,
    after every other step. Decided here by the door's own rule, not by the
    model: the claim is withdrawn, the later stages build a tool that says it
    cannot be undone, and such a tool is always the person's to allow.
    Returns True when the claim was withdrawn.
    """
    if not stage1.get("revertible"):
        return False
    from executor_birth_property_runner import undo_proof_available
    contract = {
        "reverse_pattern": stage2.get("reverse_pattern"),
        "capabilities": stage2.get("capabilities") or [],
        "args": {"type": "object",
                 "required": stage2.get("args_required") or [],
                 "properties": stage2.get("args_properties") or {}},
    }
    if undo_proof_available(contract):
        return False
    stage1["revertible"] = False
    stage2["reverse_pattern"] = None
    return True


def validate_stage3(out: dict) -> Optional[str]:
    """Stage 3 = TESTS. Si aspetta {tests: list[...]} con >= 3 entries."""
    from executor_birth_functional import validate_functional_cases
    try:
        validate_functional_cases(out.get("tests"))
    except ValueError as exc:
        return str(exc)
    return None


def validate_stage4(out: dict) -> Optional[str]:
    """Stage 4 = DESCRIPTION + AFFINITY."""
    if not isinstance(out.get("description"), str) or len(out["description"]) < 80:
        return "description troppo corta o non stringa (>= 80 char attesi)"
    if "\n" in out["description"]:
        return "description non deve contenere newline (e' una stringa TOML)"
    # Regole FISICHE §2.5 (SoT manifest_rules): description = SOLO testa, no coda.
    try:
        from manifest_rules import HEAD_MAX, DESC_MAX
    except Exception:
        HEAD_MAX, DESC_MAX = 240, 280
    desc = out["description"]
    if len(desc) > DESC_MAX:
        return (f"description {len(desc)} char > {DESC_MAX}: deve essere SOLO la testa "
                f"§2.5 (SCOPO/PATTERN/NON/OUT). Niente coda implementativa.")
    _cut = desc.find("OUT:")
    head = desc[:_cut] if _cut > 0 else desc
    if len(head) > HEAD_MAX:
        return f"testa (->OUT:) {len(head)} char > {HEAD_MAX}: accorcia SCOPO/PATTERN/NON."
    if not isinstance(out.get("affinity"), list) or len(out["affinity"]) < 4:
        return "affinity deve essere lista di almeno 4 keyword"
    return None


# ---- Stub di orchestrazione (da collegare a LLMRouter reale) ---------------

@dataclass
class StageResult:
    stage: int
    success: bool
    output: dict = field(default_factory=dict)
    error: Optional[str] = None
    raw_text: str = ""
    in_tokens: int = 0
    out_tokens: int = 0
    latency_ms: int = 0


@dataclass
class MultistageRun:
    user_request: str
    proposal_id: str
    stages: list[StageResult] = field(default_factory=list)
    final_state: str = "in_progress"  # 'in_progress'|'synthesized'|'abandoned'|'rejected'|'rejected_semantic_drift'
    abandon_reason: Optional[str] = None
    name: Optional[str] = None  # popolato dopo stage 1
    code_text: Optional[str] = None  # popolato dopo stage 4
    # Stage 6 (ADR 0114): {aligned: bool, mismatch: str, model: str, raw: ...}
    semantic_verdict: Optional[dict] = None

    def total_latency_ms(self) -> int:
        return sum(s.latency_ms for s in self.stages)

    def total_tokens(self) -> tuple[int, int]:
        return (sum(s.in_tokens for s in self.stages), sum(s.out_tokens for s in self.stages))


def _parse_json_strict(text: str) -> Optional[dict]:
    """Tenta di parsare JSON tollerando fence markdown e prosa attorno."""
    if not text:
        return None
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    # Trova primo { e ultimo } se prosa attorno
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def decided_name_fields(name: str) -> dict | None:
    """Action, object and qualifier of a name that is already canonical, or None.

    The same check stage 1 applies to a name the model proposes.
    """
    match = NAME_RE.match(name or "")
    if not match or _canonical_name_error(name):
        return None
    return {"name": name, "action": match.group(1), "object": match.group(2),
            "qualifier": match.group(3)}


def _canonical_name_error(name: str) -> str | None:
    """Why the standard would refuse this name at delivery, or None.

    Stage 1 used to check verb and object only; a qualifier that does not go
    with the object passed here and was refused five stages later.
    """
    from naming_grammar import validate_name
    verdict = validate_name(name)
    if verdict.ok:
        return None
    reason = verdict.reason or "name is not canonical"
    # Say which objects the qualifier goes with: told only «not valid», the
    # model proposed the same name again.
    from vocab import QUALIFIER_OBJECT_COMPAT
    parts = verdict.components
    allowed = QUALIFIER_OBJECT_COMPAT.get(getattr(parts, "qualifier", None) or "")
    if allowed and getattr(parts, "obj", None) not in allowed:
        reason += f" The qualifier '{parts.qualifier}' goes only with: {', '.join(sorted(allowed))}."
    return reason


def run_stage1(user_request: str, llm_call, *, decided_name: str = "") -> StageResult:
    """llm_call: callable(system, user, max_tokens) -> {text, in_tokens, out_tokens, latency_ms}.

    ``max_tokens`` e' il tetto strutturale dell'output dello stage. La policy
    di reasoning non viene stimata o compensata qui: appartiene al tier.

    ``decided_name``: a canonical name the caller already decided (a proposal
    is born from a measured verb and object). The model then fills only the
    remaining fields; asked to choose again it picked another name, or a verb
    outside the vocabulary, and the proposal could never be applied.
    """
    decided = decided_name_fields(decided_name) if decided_name else None
    if decided:
        user_request = (
            f"{user_request}\n\nThe name is already decided: `{decided['name']}`. "
            "Return exactly this name and fill the other fields for it.")
    first = _run_stage1_once(user_request, llm_call, decided)
    if first.success or not first.output or decided:
        return first
    # The name was refused by a rule the model can follow once told which.
    return _run_stage1_once(
        f"{user_request}\n\nCORRECTION: your previous answer was refused: {first.error}. "
        "Choose a name the vocabulary allows.", llm_call, decided)


def _run_stage1_once(user_request: str, llm_call, decided: dict | None) -> StageResult:
    stage1_prompt = prompt_loader.get(
        "synt_naming",
        _i18n.current_lang(),
        n_actions=len(_VOCAB_ACTIONS),
        action_categories_block=render_action_categories_block(),
        action_mapping_block=render_action_mapping_block(_i18n.current_lang()),
        n_objects=len(_VOCAB_OBJECTS),
        objects_inline=render_objects_inline(),
        qualifiers_inline=render_qualifiers_inline(),
    )
    res = llm_call(stage1_prompt, user_request, max_tokens=1800)
    text = res.get("text", "")
    out = _parse_json_strict(text)
    if out is None:
        return StageResult(stage=1, success=False, error="JSON parse failed", raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    if decided:
        out.update(decided)
        out.pop("reason_no_name", None)
    if out.get("name") is None and out.get("reason_no_name"):
        return StageResult(stage=1, success=True, output=out, raw_text=text,
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    err = validate_stage1(out)
    if err:
        return StageResult(stage=1, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=1, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def run_stage2(user_request: str, stage1: dict, llm_call) -> StageResult:
    user_prompt = prompt_loader.get(
        "synt_signature",
        _i18n.current_lang(),
        name=stage1["name"], action=stage1["action"], object=stage1["object"],
        qualifier=str(stage1.get("qualifier")),
        revertible=str(stage1["revertible"]),
        critical=str(stage1["critical"]),
        target_kind=stage1["target_kind"],
        user_request=user_request,
    )
    res = llm_call("", user_prompt, max_tokens=4000)
    text = res.get("text", "")
    out = _parse_json_strict(text)
    if out is None:
        return StageResult(stage=2, success=False, error="JSON parse failed", raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    if out.get("capabilities") == []:
        # Declaring nothing means asking for nothing: in the sandbox that is
        # pure computation, and the standard wants it said. A tool that needs
        # more fails its own cases in the isolated runner.
        out["capabilities"] = [{"name": "compute:pure"}]
    err = validate_stage2(out, stage1)
    if err:
        return StageResult(stage=2, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=2, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def test_generation_options(provider) -> dict:
    """Constrain JSON syntax when supported; semantic checks stay mandatory."""
    if not getattr(provider, "supports_grammar", False):
        return {}
    from tool_grammar import generate_json_grammar
    return {"grammar": generate_json_grammar(decimal_numbers_only=True)}


# The fields of the standard answer that hold one value per item, and the
# other fields of that answer a case may check.
_ITEM_FIELDS = ("results", "entries", "failed")
_ANSWER_FIELDS = _ITEM_FIELDS + ("truncated", "truncated_what", "used", "available_total",
                                 "cap_field", "cap_value")


# What the isolated runner prepares for every case (the same list the case
# prompt gives); a file to create goes under the output directory.
_FIXTURE_ROOT = "fixture"
_FIXTURE_PATHS = ("fixture", "fixture/input.txt", "fixture/empty.txt", "fixture/output")
_FIXTURE_OUTPUT = "fixture/output/"
_GLOB_SIGNS = "*?["


def _absent_fixture(value) -> str | None:
    """The first prepared-directory path in ``value`` that will not exist."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return next((found for found in map(_absent_fixture, value) if found), None)
    if not isinstance(value, str) or value.split("/", 1)[0] != _FIXTURE_ROOT:
        return None
    if (value.rstrip("/") in _FIXTURE_PATHS or value.startswith(_FIXTURE_OUTPUT)
            or any(sign in value for sign in _GLOB_SIGNS)):
        return None
    return value


def stage3_cases_problem(stage2: dict, tests: list) -> str | None:
    """A defect of the functional cases that no code could satisfy, or None.

    Decided by reading the cases, with no model: the same input expected both
    to succeed and to fail, or ``from_step`` passed as a value (in a test
    there is no earlier step: the records go in ``entries``). Whether an
    empty value is valid depends on the tool, so it is not judged here.

    An expectation outside the standard answer is such a defect too: a bare
    value where the answer holds a list («results: 2» against «[2]»), or a
    field the answer does not have. Measured on 12/11/2026, it was the most
    frequent reason a sound candidate failed its own cases. So is a case that
    must succeed on a prepared file that will not be there.
    """
    verdicts: dict[str, tuple[str, bool]] = {}
    for case in tests or []:
        checked = (case.get("expect") or {}).get("field_eq")
        for field, value in (checked.items() if isinstance(checked, dict) else ()):
            if field not in _ANSWER_FIELDS:
                return (f"case {case.get('name')!r} checks the field {field!r}, which the answer "
                        "does not have: computed values go in `results`, records read or "
                        "enriched go in `entries`, one item per input item")
            if field in _ITEM_FIELDS and not isinstance(value, list):
                return (f"case {case.get('name')!r} expects {field}="
                        f"{json.dumps(value, ensure_ascii=False)[:80]}: `{field}` is always a "
                        "list with one item per input item, even for a single item")
        arguments = case.get("input") or {}
        absent = _absent_fixture(arguments)
        if absent and (case.get("expect") or {}).get("ok") is True:
            return (f"case {case.get('name')!r} must succeed on {absent!r}, which does not "
                    f"exist: the only prepared paths are {', '.join(_FIXTURE_PATHS[1:])}; "
                    f"a file to create goes under {_FIXTURE_OUTPUT}")
        # An explicit null is an argument left out, not a reference.
        if arguments.get("from_step") is not None:
            return (f"case {case.get('name')!r} passes from_step={arguments['from_step']!r}: "
                    "a test has no earlier step, pass the concrete records in `entries`")
        expected = (case.get("expect") or {}).get("ok")
        if not isinstance(expected, bool):
            continue
        key = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        seen = verdicts.get(key)
        if seen and seen[1] is not expected:
            return (f"cases {seen[0]!r} and {case.get('name')!r} give the same input "
                    f"{key[:160]} and expect opposite outcomes")
        verdicts[key] = (str(case.get("name")), expected)
    return None


# What the signature may declare about an argument besides its type.
_ARGUMENT_FACTS = ("enum", "minimum", "maximum", "minLength", "maxLength",
                   "pattern", "format", "default")
_ARGUMENT_TEXT_LIMIT = 300


def stage3_args_contract(stage2: dict) -> str:
    """What the case writer reads about each argument: one line with type,
    declared constraints and meaning, the same meaning the code stage reads.

    With the bare type alone («email:string») the shortest valid value was any
    one-letter text: the cases asked the code to accept «a» as an address, the
    code refused it, and three rewrites of the code could not settle a
    disagreement that was in the case (12/11/2026).
    """
    required = set(stage2.get("args_required") or [])
    lines = []
    for name, spec in (stage2.get("args_properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        kind = str(spec.get("type", "?"))
        items = spec.get("items")
        if isinstance(items, dict) and items.get("type"):
            kind += f" of {items['type']}"
        facts = [kind] + (["required"] if name in required else [])
        facts += [f"{key}={json.dumps(spec[key], ensure_ascii=False)}"
                  for key in _ARGUMENT_FACTS if key in spec]
        meaning = spec.get("description")
        if isinstance(meaning, dict):          # one text per language
            meaning = meaning.get(_i18n.current_lang()) or next(iter(meaning.values()), "")
        meaning = " ".join(str(meaning or "").split())[:_ARGUMENT_TEXT_LIMIT]
        lines.append(f"- {name} ({', '.join(facts)})" + (f": {meaning}" if meaning else ""))
    return "\n".join(lines)


def run_stage3(user_request: str, stage1: dict, stage2: dict, llm_call, *,
               real_requests: tuple[str, ...] = (), correction: str = "") -> StageResult:
    """Stage 3 = TESTS. Procedurale, struttura fissa.

    ``real_requests``: what people actually asked when the capability was
    missing. The cases are built on them, so the tool is tried on the use it
    is born for and not on inputs a model made up. ``correction``: why the
    cases of a first attempt were refused.
    """
    args_summary = stage3_args_contract(stage2)
    user_prompt = prompt_loader.get(
        "synt_tests",
        _i18n.current_lang(),
        name=stage1["name"],
        args_required=str(stage2["args_required"]),
        args_properties_summary=args_summary,
        capabilities=str([c.get("name") for c in stage2.get("capabilities", [])]),
        revertible=str(stage1["revertible"]),
        reverse_pattern=str(stage2.get("reverse_pattern")),
        user_request=user_request,
        real_requests="\n".join(f"- {text}" for text in real_requests),
        correction=correction,
    )
    res = llm_call("", user_prompt, max_tokens=4000,
                   **test_generation_options(llm_call))
    text = res.get("text", "")
    out = _parse_json_strict(text)
    # A test has no earlier step: a reference to one is an argument that
    # means nothing there. Refused, it came back the same and cost every
    # attempt (12/11/2026); it is left out, and the case is judged on the rest.
    for case in (out or {}).get("tests") or []:
        if isinstance(case, dict) and isinstance(case.get("input"), dict):
            case["input"].pop("from_step", None)
    if out is None:
        return StageResult(stage=3, success=False, error="JSON parse failed", raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    err = validate_stage3(out) or stage3_cases_problem(stage2, out.get("tests"))
    if err:
        return StageResult(stage=3, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=3, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def stage4_out_shape(stage1: dict, stage3: dict) -> str:
    """The OUT chapter, decided by code: the standard shape the code stage is
    required to return, with the list field the functional cases check.

    The description is written before the code and without seeing the cases,
    so a free OUT chapter contradicted the code (measured on 11/11/2026:
    four candidates out of six refused by the final review for this alone).
    """
    checked = {field for case in (stage3 or {}).get("tests") or []
               for field in ((case.get("expect") or {}).get("field_eq") or {})}
    named = [field for field in ("entries", "results") if field in checked]
    if len(named) == 1:
        field = named[0]
    else:
        from vocab import SAFE_VERBS
        field = "entries" if (stage1 or {}).get("action") in SAFE_VERBS else "results"
    return "{ok,ok_count,fail_count," + field + ",failed}"


def _stage4_example_call(name: str, stage3: dict, limit: int = 140) -> str:
    """One call the functional cases really make, as the PATTERN's raw material."""
    cases = (stage3 or {}).get("tests") or []
    case = next((c for c in cases if (c.get("expect") or {}).get("ok") is True),
                cases[0] if cases else None)
    arguments = (case or {}).get("input") or {}
    call = name + "(" + ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in arguments.items()) + ")"
    if len(call) > limit:
        call = name + "(" + ", ".join(f"{key}=..." for key in arguments) + ")"
    return call


def _with_out_shape(description: str, shape: str) -> str:
    """Replace whatever follows ``OUT:`` with the decided shape."""
    cut = description.find("OUT:")
    head = (description[:cut] if cut >= 0 else description).rstrip()
    return f"{head} OUT: {shape}."


def run_stage4(user_request: str, stage1: dict, stage2: dict, stage3: dict, llm_call,
               *, correction: str = "") -> StageResult:
    """Stage 4 = DESCRIPTION + AFFINITY. Creativo (prosa).

    ``correction`` carries the refusal of a first attempt: the model rewrites
    knowing what was wrong, instead of being asked the same question again.
    """
    try:
        from manifest_rules import HEAD_MAX as _hm, DESC_MAX as _dm
    except Exception:
        _hm, _dm = 240, 280
    out_shape = stage4_out_shape(stage1, stage3)
    user_prompt = prompt_loader.get(
        "synt_description",
        _i18n.current_lang(),
        name=stage1["name"],
        args_required=str(stage2["args_required"]),
        capabilities=str([c.get("name") for c in stage2.get("capabilities", [])]),
        revertible=str(stage1["revertible"]),
        reverse_pattern=str(stage2.get("reverse_pattern")),
        num_tests=len(stage3.get("tests", [])),
        user_request=user_request,
        head_max=_hm, desc_max=_dm,
        args_all=str(sorted(stage2.get("args_properties") or {})),
        example_call=_stage4_example_call(stage1["name"], stage3),
        out_shape=out_shape,
        correction=correction,
    )
    res = llm_call("", user_prompt, max_tokens=2200)
    text = res.get("text", "")
    out = _parse_json_strict(text)
    if out is None:
        return StageResult(stage=4, success=False, error="JSON parse failed", raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    if isinstance(out.get("description"), str) and "\n" not in out["description"]:
        out["description"] = _with_out_shape(out["description"], out_shape)
    err = validate_stage4(out)
    if err:
        return StageResult(stage=4, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=4, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def message_values(key: str) -> tuple[str, ...]:
    """The values the catalog text of ``key`` needs, by name; none when the
    key or the catalog cannot be read."""
    from string import Formatter

    try:
        text = _i18n.get_for_language(key, _i18n.current_lang())
        return tuple(sorted({name for _, name, _, _ in Formatter().parse(text)
                             if name and name.isidentifier()}))
    except Exception:  # noqa: BLE001 - an unreadable catalog or text judges nothing
        return ()


def stage5_messages_problem(code: str) -> str | None:
    """The catalog messages called without a value their texts need, or None.

    A key whose text holds «{addr}» was called bare, and the person would have
    read the placeholder itself (12/11/2026). Decided on the syntax tree and
    the catalog, with no model; a call that passes ``**values`` is not judged.
    All of them are named at once, each with the call to write.
    """
    import ast

    incomplete: dict[str, tuple[str, ...]] = {}
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        called, key = node.func, node.args[0]
        by_name = isinstance(called, ast.Name) and called.id == "_msg"
        by_module = (isinstance(called, ast.Attribute) and called.attr == "get"
                     and isinstance(called.value, ast.Name) and called.value.id == "messages")
        if (not (by_name or by_module) or not isinstance(key, ast.Constant)
                or not isinstance(key.value, str)
                or any(keyword.arg is None for keyword in node.keywords)):
            continue
        needed = message_values(key.value)
        if set(needed) - {keyword.arg for keyword in node.keywords}:
            incomplete[key.value] = needed
    if not incomplete:
        return None
    calls = "; ".join(
        f"messages.get({key!r}, {', '.join(name + '=...' for name in needed)})"
        for key, needed in sorted(incomplete.items()))
    return ("these catalog messages are called without the values their texts need, "
            f"write them as: {calls}; or use a key whose text needs no value")


def run_stage5(user_request: str, stage1: dict, stage2: dict, stage3: dict, stage4: dict,
               llm_call, *, scope: str | None = None, correction: str = "") -> StageResult:
    """Stage 5 = CODE. Usa wise.

    `scope`: opzionale, "device" attiva la regola di portabilita' cross-
    platform nel prompt (§16.3 W3.2 executor remoti). NON emesso da nessuno
    stage precedente oggi (nessun executor synt e' mai device-scope: la
    promozione a scope=device e' sempre manuale, post-audit, §16.3) — il
    parametro esiste per il chiamante FUTURO che rigenera un executor gia'
    promosso. Default None: prompt byte-identico al comportamento pre-W3.2.
    """
    # Selezione prompt per verbo: `_stage5_prompt_for_verb` ottiene il generico
    # `synt_code` da prompt_loader e ci splica l'addendum specifico del verbo
    # (`synt_code_addendum_<verb>`) prima del marker `I/O CONTRACT` se il verbo
    # e' in `SPECIALIZED_VERBS`. Per gli altri verbi, ritorna solo il generico.
    verb = (stage1 or {}).get("action") or ""
    user_prompt = _stage5_prompt_for_verb(
        verb,
        name=stage1["name"],
        args_required=str(stage2["args_required"]),
        args_properties=json.dumps(stage2["args_properties"], indent=2),
        capabilities=str([c.get("name") for c in stage2.get("capabilities", [])]),
        revertible=str(stage1["revertible"]),
        reverse_pattern=str(stage2.get("reverse_pattern")),
        description=stage4["description"],
        tests_json=json.dumps(stage3["tests"], ensure_ascii=False, indent=2),
        user_request=user_request,
        scope=scope or "",
    )
    import ast
    # ``correction``: why an earlier file for this same candidate was refused.
    request = f"{user_prompt}\n\n{correction}" if correction else user_prompt
    for attempt in range(2):
        res = llm_call("", request, max_tokens=5000)
        text = res.get("text", "")
        code = text.strip()
        # Strip markdown fences indipendentemente: l'LLM puo' aprire+chiudere,
        # solo aprire, solo chiudere o nessuno (il prompt dice di non emetterli ma
        # alcuni provider lo fanno comunque). Stripping idempotente.
        code = re.sub(r"^```[a-zA-Z]*\n?", "", code)
        code = re.sub(r"\n?```\s*$", "", code)
        code = code.strip()
        if not code or "def invoke" not in code or "def main" not in code:
            return StageResult(stage=5, success=False, error="code missing def invoke or def main",
                               raw_text=text[:500],
                               in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                               latency_ms=res.get("latency_ms", 0))
        # Syntax only: nothing is compiled to bytecode or executed here. A
        # file that does not parse cannot be delivered, so it is asked again
        # once with the error, before the reviewer spends a call on it.
        try:
            ast.parse(code)
        except SyntaxError as exc:
            problem = f"line {exc.lineno}: {exc.msg}"
            if attempt:
                return StageResult(stage=5, success=False,
                                   error=f"code does not parse ({problem})", raw_text=text[:500],
                                   in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                                   latency_ms=res.get("latency_ms", 0))
            request = (f"{request}\n\nCORRECTION: the file you wrote does not parse "
                       f"({problem}). Write the complete file again, shorter if needed.")
            continue
        # Read on the tree, like the syntax: a message shown with its
        # placeholders unfilled is asked again once, with the reason.
        problem = stage5_messages_problem(code)
        if problem is None:
            break
        if attempt:
            return StageResult(stage=5, success=False, error=problem, raw_text=text[:500],
                               in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                               latency_ms=res.get("latency_ms", 0))
        request = f"{request}\n\nCORRECTION: {problem}. Write the complete file again."
    # Fase 11 (c) 19/5/2026 v4: scan delle chiavi i18n emesse dal LLM nel
    # codice synth. Ogni chiave non presente nel DB viene auto-registrata
    # come stub `<auto-synth: KEY>` con `needs_translation=1` per review
    # admin. Determinismo §7.9 (regex + DB lookup, niente LLM aggiuntivo).
    try:
        _register_synth_keys(code)
    except Exception:
        # Non blocca la synth se il registratore fallisce (DB lock, etc.).
        # Logged solo in stage6 verify se necessario.
        pass
    return StageResult(stage=5, success=True, output={"code": code}, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


# Regex per estrarre chiavi i18n da codice synth. Conservativo: solo
# chiavi maiuscolo + underscore (forma canonica ERR_*/MSG_*/WARN_*/LOG_*).
_I18N_KEY_PATTERN = re.compile(
    r'(?:messages\.get|_msg)\s*\(\s*["\']'
    r'((?:ERR_|MSG_|WARN_|LOG_)[A-Z_][A-Z0-9_]*)'
    r'["\']'
)


def _register_synth_keys(code: str) -> int:
    """Scan code per chiavi i18n emesse via `messages.get("KEY")` o
    `_msg("KEY")`. Per ogni chiave non in DB, registra stub con
    `needs_translation=1`. Ritorna numero chiavi registrate.

    Pattern conservativo: solo chiavi UPPER_CASE prefisso ERR_/MSG_/WARN_/LOG_.
    """
    if not code:
        return 0
    keys = set(_I18N_KEY_PATTERN.findall(code))
    if not keys:
        return 0
    try:
        import i18n
    except Exception:
        return 0
    n_registered = 0
    for key in keys:
        try:
            stub_text = f"<auto-synth: {key}>"
            if i18n.register_key_if_missing(
                key, text_it=stub_text, text_en=stub_text,
                needs_translation=False,
            ):
                n_registered += 1
        except Exception:
            continue
    return n_registered


def run_full(user_request: str, llm_call_procedural, llm_call_wise, *,
             llm_call_creative=None, llm_call_fidelity=None,
             progress=None, decided_name: str = "",
             llm_call_tests=None,
             real_requests: tuple[str, ...] = (),
             cases_correction: str = "") -> MultistageRun:
    """Orchestratore a 6 stadi, separato per contratto di workload.

    Stages 1-3 are procedural (middle), stage 4 is editorial (creative),
    stage 5 is deliberate code synthesis (wise), and stage 6 is faithful
    semantic verification (wise). Production supplies all four roles
    explicitly; the optional editorial and fidelity callbacks make isolated
    stage tests compact.

    `progress` (opzionale): istanza di runtime.progress.Progress. Se passato,
    riceve `update(stage)` all'inizio di ogni stadio per UX (Telegram, HTML).
    Default None = nessun progress (test/CLI).

    ``cases_correction``: why the functional cases of an earlier candidate are
    questioned (the attempt loop found a case no code satisfied).
    """
    import uuid
    llm_call_creative = llm_call_creative or llm_call_procedural
    llm_call_fidelity = llm_call_fidelity or llm_call_wise
    run = MultistageRun(user_request=user_request, proposal_id=uuid.uuid4().hex[:12])

    if progress is not None: progress.update(1)
    s1 = run_stage1(user_request, llm_call_procedural, decided_name=decided_name)
    run.stages.append(s1)
    if not s1.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage1: {s1.error}"; return run
    if s1.output.get("name") is None:
        run.final_state = "rejected"; run.abandon_reason = f"stage1 rejection: {s1.output.get('reason_no_name')}"; return run
    run.name = s1.output["name"]

    if progress is not None: progress.update(2)
    s2 = run_stage2(user_request, s1.output, llm_call_procedural)
    run.stages.append(s2)
    if not s2.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage2: {s2.error}"; return run
    if settle_undo_claim(s1.output, s2.output):
        log.info("synt %s: undo claim withdrawn, the door has no proof for this contract",
                 run.name)

    if progress is not None: progress.update(3)
    s3 = run_stage3(user_request, s1.output, s2.output,
                    llm_call_tests or llm_call_procedural, real_requests=real_requests,
                    correction=cases_correction)
    if not s3.success and s3.output:
        # Cases refused for a reason the model can act on: asked once more.
        s3 = run_stage3(user_request, s1.output, s2.output,
                        llm_call_tests or llm_call_procedural,
                        real_requests=real_requests,
                        correction="\n".join(filter(None, (cases_correction, str(s3.error)))))
    run.stages.append(s3)
    if not s3.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage3: {s3.error}"; return run

    if progress is not None: progress.update(4)
    s4 = run_stage4(
        user_request, s1.output, s2.output, s3.output, llm_call_creative)
    run.stages.append(s4)
    if not s4.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage4: {s4.error}"; return run

    # Structural lint of the card (ADR 0169, deterministic §7.9), BEFORE the
    # code stage: it reads only the name, the signature and the description,
    # so a malformed card is caught without paying for the code. One informed
    # retry: the description is rewritten knowing the linter's refusal.
    # Rejects only on severity 'error'; 'warn' findings are logged.
    try:
        import config as _C_lint
        from manifest_lint import lint_manifest as _lint
        for _attempt in range(2):
            _man = {
                "name": run.name or (s1.output.get("name") if s1.output else "") or "",
                "description": (s4.output.get("description") if s4.output else "") or "",
                "affinity": (s4.output.get("affinity") if s4.output else []) or [],
                "args": {
                    "properties": (s2.output.get("args_properties") if s2.output else {}) or {},
                    "required": (s2.output.get("args_required") if s2.output else []) or [],
                },
            }
            _findings = _lint(
                _man, language=_C_lint.INSTANCE_LANG,
                allow_flat_description=True,
            )
            _errs = [f for f in _findings if f.severity == "error"]
            _warns = [f for f in _findings if f.severity == "warn"]
            if _warns:
                try:
                    from logging_setup import get_logger
                    get_logger(__name__).info(
                        "[synt.lint] %s: %d warn — %s", _man["name"], len(_warns),
                        "; ".join(w.message for w in _warns[:3]))
                except Exception:
                    pass
            if not _errs or _attempt:
                break
            s4 = run_stage4(
                user_request, s1.output, s2.output, s3.output, llm_call_creative,
                correction="; ".join(e.message for e in _errs[:3]))
            run.stages[-1] = s4          # one description per run: the rewritten one
            if not s4.success:
                run.final_state = "abandoned"; run.abandon_reason = f"stage4: {s4.error}"; return run
        if _errs:
            run.final_state = "rejected_lint_structural"
            run.abandon_reason = "manifest_lint: " + "; ".join(
                e.message for e in _errs[:3])
            return run
    except Exception as ex:
        run.final_state = "rejected_lint_unavailable"
        run.abandon_reason = f"manifest_lint_unavailable: {type(ex).__name__}"
        return run

    if progress is not None: progress.update(5)
    s5 = run_stage5(user_request, s1.output, s2.output, s3.output, s4.output, llm_call_wise)
    run.stages.append(s5)
    if not s5.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage5: {s5.error}"; return run

    run.code_text = s5.output["code"]

    return _review_code(run, s4, llm_call_fidelity)


def _review_code(run: "MultistageRun", s4: "StageResult", llm_call_fidelity) -> "MultistageRun":
    """Stage 6 on the run's code: sets the final state and returns the run."""
    # Stage 6 — semantic verification (ADR 0114 Layer 6, 8/5/2026).
    # Confronta description (s4) vs code (s5). Se misaligned → rejected.
    # Determinismo §7.9: LLM solo per il giudizio, mai per la decisione.
    # La policy non è modificabile tramite API o ambiente; i test isolati
    # sostituiscono il simbolo interno quando devono separare questo stadio.
    try:
        from synt_stage6_verify import SemanticVerdictInvalid, validate_stage6_verdict
        from synt_stage6_verify import verify_semantic_alignment
        description = (s4.output.get("description") or "") if s4 and s4.output else ""
        code_body = run.code_text or ""
        if not description or not code_body:
            run.final_state = "rejected_semantic_input_invalid"
            run.abandon_reason = "stage6 semantic input missing"
            return run

        def _v_llm(prompt, model):
            res = llm_call_fidelity(prompt, "", max_tokens=300)
            return res or {}

        verdict = verify_semantic_alignment(
            description=description,
            code_body=code_body,
            llm_call=_v_llm,
            name_hint=run.name or run.proposal_id,
        )
        validate_stage6_verdict(verdict)
        run.semantic_verdict = verdict
        if not verdict.get("aligned", False):
            run.final_state = "rejected_semantic_drift"
            run.abandon_reason = (
                f"stage6 semantic drift: {verdict.get('mismatch', '')}"
            )
            return run
    except SemanticVerdictInvalid as ex:
        run.final_state = "rejected_semantic_invalid"
        run.abandon_reason = f"stage6 verdict invalid: {ex}"
        return run
    except Exception as ex:
        run.final_state = "rejected_semantic_unavailable"
        run.abandon_reason = f"stage6 verifier unavailable: {type(ex).__name__}"
        return run

    run.final_state = "synthesized"
    return run


def is_synthesized(run: "MultistageRun") -> bool:
    """The run produced a candidate every stage accepted."""
    return run.final_state == "synthesized"


def is_retryable(run: "MultistageRun") -> bool:
    """A refusal another attempt can cure: the reviewer found a difference,
    the card was malformed, a stage gave no usable answer. A refusal by the
    vocabulary is an answer, and an unavailable reviewer or linter is not the
    candidate's fault."""
    return run.final_state in ("rejected_semantic_drift", "rejected_lint_structural", "abandoned")


def can_repair(run: "MultistageRun") -> bool:
    """True when a refused run keeps a sound name, signature, cases and
    description: only its code needs writing again."""
    return (bool(run.name) and len(run.stages) >= 4
            and all(stage.success and stage.output for stage in run.stages[:4])
            and not (run.final_state or "").startswith("rejected_lint"))


_REFUSED_FILE_LIMIT = 12000


def run_repair(previous: "MultistageRun", user_request: str, llm_call_wise, *,
               llm_call_fidelity=None, feedback: str) -> "MultistageRun":
    """Write the code again for a refused candidate, knowing why it was refused.

    Name, signature, functional cases and description stay: they were not the
    problem, and changing them would make every attempt a different tool. The
    code stage receives the refusal and the refused file, then the reviewer
    judges the new one. A second attempt that starts from zero repeats the
    same mistake as often as not; one that knows the reason can fix it.
    """
    run = MultistageRun(user_request=user_request, proposal_id=previous.proposal_id)
    run.name = previous.name
    run.stages = list(previous.stages[:4])
    s1, s2, s3, s4 = run.stages
    refused = (previous.code_text or "")[:_REFUSED_FILE_LIMIT]
    correction = (
        f"CORRECTION: the file below was refused. Reason: {feedback}\n"
        "Fix exactly that and write the complete file again."
        + (f"\n--- refused file ---\n{refused}" if refused else ""))
    s5 = run_stage5(user_request, s1.output, s2.output, s3.output, s4.output,
                    llm_call_wise, correction=correction)
    run.stages.append(s5)
    if not s5.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage5: {s5.error}"; return run
    run.code_text = s5.output["code"]
    return _review_code(run, s4, llm_call_fidelity or llm_call_wise)

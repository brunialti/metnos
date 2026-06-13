"""synt_multistage.py — synth orchestrator multistage.

Idea (Roberto, 28/4/2026): dividere la creazione di un executor in 5 stadi,
ognuno con prompt ristretto, tier LLM proporzionato al compito, context
vincolato dai precedenti. Una creazione di executor e' una-tantum (l'utente
accetta la latenza maggiore — multistage costa ~5 round-trip ma converge
meglio del single-prompt mostruoso).

Ordine: dai compiti piu' procedurali/lookup (facili per LLM) a quelli piu'
creativi (richiedono fluidita' linguistica).

Stages:
  1. NAMING+CLASS (procedurale, fast/middle): nome conforme vocabolario chiuso +
                                              revertible/critical/target_kind.
                                              LOOKUP nel vocabolario.
  2. SIGNATURE   (procedurale, middle): args_schema + capabilities + reverse_pattern.
                                        SCHEMA JSON strutturato.
  3. TESTS       (procedurale, middle): 4-6 birth test TOML con expect concrete.
                                        STRUTTURA fissa (input/expect/setup).
  4. DESCRIPTION (creativo, middle/wise): description LLM-readable + affinity.
                                          PROSA narrativa.
  5. CODE        (creativo+procedurale, wise): file Python invoke + reverse() se serve.
                                                LOGIC che ha tutto il contesto.

Tre tier (memoria metnos_design_3tier_llm + design_3tier_llm):
  fast   = qwen3:8b   (Ollama)   — per stage 1 quando il task e' lookup.
  middle = gemma-4-26B (LlamaCpp) — per stage 2-4 strutturati.
  wise   = gemma-4-26B con think=true OR Claude come ULTIMA istanza — stage 5.
LLM online = ultima istanza solo se locale fallisce.

Ogni stage scrive checkpoint. Se uno stage fallisce, abbandono con motivo.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from logging_setup import get_logger
log = get_logger(__name__)

import prompt_loader
from config import DEFAULT_LANG

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

    Pattern: rendiamo `synt_code` (generico) con i `vars`, poi se il verbo
    e' specializzato spliciamo l'addendum (renderizzato senza vars) prima
    del marker `I/O CONTRACT`. Mantiene byte-equivalence con il vecchio
    `_compose_verb_prompt` + `.format()`.

    A2 19/5/2026 v4: inietta `available_i18n_keys` (subset chiavi per
    famiglia) cosi' il LLM riusa chiavi esistenti invece di inventarne.
    Cap di sicurezza in i18n.keys_for_synth_context (max_per_family=30).
    """
    if "available_i18n_keys" not in vars:
        try:
            from i18n import keys_for_synth_context
            vars["available_i18n_keys"] = keys_for_synth_context()
        except Exception:
            vars["available_i18n_keys"] = {}
    generic = prompt_loader.get("synt_code", DEFAULT_LANG, **vars)
    if verb in SPECIALIZED_VERBS:
        addendum = prompt_loader.get(f"synt_code_addendum_{verb}", DEFAULT_LANG)
        marker = "I/O CONTRACT (OBBLIGATORIO"
        return generic.replace(marker, addendum + "\n\n" + marker, 1)
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


def validate_stage3(out: dict) -> Optional[str]:
    """Stage 3 = TESTS. Si aspetta {tests: list[...]} con >= 3 entries."""
    if not isinstance(out.get("tests"), list) or len(out["tests"]) < 3:
        return "tests deve essere lista di almeno 3 birth test"
    for i, t in enumerate(out["tests"]):
        if not isinstance(t, dict):
            return f"test[{i}] non e' un dict"
        if not t.get("name") or not isinstance(t["name"], str):
            return f"test[{i}] manca 'name' string"
        if not isinstance(t.get("input"), dict):
            return f"test[{i}].input deve essere dict"
        if not isinstance(t.get("expect"), dict):
            return f"test[{i}].expect deve essere dict"
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


def run_stage1(user_request: str, llm_call) -> StageResult:
    """llm_call: callable(system, user, max_tokens) -> {text, in_tokens, out_tokens, latency_ms}.

    Note max_tokens: Gemma 4 26B con --reasoning-budget=1024 consuma ~1024 token
    in thinking interno PRIMA di emettere il JSON; quindi max_tokens deve essere
    >> 1024 per non troncare l'output. Stage procedurali: 1800 tipico.
    """
    stage1_prompt = prompt_loader.get(
        "synt_naming",
        DEFAULT_LANG,
        n_actions=len(_VOCAB_ACTIONS),
        action_categories_block=render_action_categories_block(),
        action_mapping_block=render_action_mapping_block(),
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
        DEFAULT_LANG,
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
    err = validate_stage2(out, stage1)
    if err:
        return StageResult(stage=2, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=2, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def run_stage3(user_request: str, stage1: dict, stage2: dict, llm_call) -> StageResult:
    """Stage 3 = TESTS. Procedurale, struttura fissa."""
    args_summary = ", ".join(f"{k}:{v.get('type','?')}" for k, v in stage2.get("args_properties", {}).items())
    user_prompt = prompt_loader.get(
        "synt_tests",
        DEFAULT_LANG,
        name=stage1["name"],
        args_required=str(stage2["args_required"]),
        args_properties_summary=args_summary,
        capabilities=str([c.get("name") for c in stage2.get("capabilities", [])]),
        revertible=str(stage1["revertible"]),
        reverse_pattern=str(stage2.get("reverse_pattern")),
        user_request=user_request,
    )
    res = llm_call("", user_prompt, max_tokens=4000)
    text = res.get("text", "")
    out = _parse_json_strict(text)
    if out is None:
        return StageResult(stage=3, success=False, error="JSON parse failed", raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    err = validate_stage3(out)
    if err:
        return StageResult(stage=3, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=3, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def run_stage4(user_request: str, stage1: dict, stage2: dict, stage3: dict, llm_call) -> StageResult:
    """Stage 4 = DESCRIPTION + AFFINITY. Creativo (prosa)."""
    try:
        from manifest_rules import HEAD_MAX as _hm, DESC_MAX as _dm
    except Exception:
        _hm, _dm = 240, 280
    user_prompt = prompt_loader.get(
        "synt_description",
        DEFAULT_LANG,
        name=stage1["name"],
        args_required=str(stage2["args_required"]),
        capabilities=str([c.get("name") for c in stage2.get("capabilities", [])]),
        revertible=str(stage1["revertible"]),
        reverse_pattern=str(stage2.get("reverse_pattern")),
        num_tests=len(stage3.get("tests", [])),
        user_request=user_request,
        head_max=_hm, desc_max=_dm,
    )
    res = llm_call("", user_prompt, max_tokens=2200)
    text = res.get("text", "")
    out = _parse_json_strict(text)
    if out is None:
        return StageResult(stage=4, success=False, error="JSON parse failed", raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    err = validate_stage4(out)
    if err:
        return StageResult(stage=4, success=False, error=err, output=out, raw_text=text[:500],
                           in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                           latency_ms=res.get("latency_ms", 0))
    return StageResult(stage=4, success=True, output=out, raw_text=text,
                       in_tokens=res.get("in_tokens", 0), out_tokens=res.get("out_tokens", 0),
                       latency_ms=res.get("latency_ms", 0))


def run_stage5(user_request: str, stage1: dict, stage2: dict, stage3: dict, stage4: dict,
               llm_call) -> StageResult:
    """Stage 5 = CODE. Usa wise."""
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
        user_request=user_request,
    )
    res = llm_call("", user_prompt, max_tokens=5000)
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
    # Fase 11 (c) 19/5/2026 v4: scan delle chiavi i18n emesse dal LLM nel
    # codice synth. Ogni chiave non presente nel DB viene auto-registrata
    # come stub `<auto-synth: KEY>` con `needs_translation=1` per review
    # admin. Determinismo §7.9 (regex + DB lookup, niente LLM aggiuntivo).
    try:
        _register_synth_keys(code)
    except Exception as _ex:
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
            if i18n.register_key_if_missing(key, text_it=stub_text, text_en=stub_text):
                n_registered += 1
        except Exception:
            continue
    return n_registered


def run_full(user_request: str, llm_call_middle, llm_call_wise, *, progress=None) -> MultistageRun:
    """Orchestratore 5 stages. Stages 1-4 middle, stage 5 wise.

    `progress` (opzionale): istanza di runtime.progress.Progress. Se passato,
    riceve `update(stage)` all'inizio di ogni stadio per UX (Telegram, HTML).
    Default None = nessun progress (test/CLI).
    """
    import uuid
    run = MultistageRun(user_request=user_request, proposal_id=uuid.uuid4().hex[:12])

    if progress is not None: progress.update(1)
    s1 = run_stage1(user_request, llm_call_middle)
    run.stages.append(s1)
    if not s1.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage1: {s1.error}"; return run
    if s1.output.get("name") is None:
        run.final_state = "rejected"; run.abandon_reason = f"stage1 rejection: {s1.output.get('reason_no_name')}"; return run
    run.name = s1.output["name"]

    if progress is not None: progress.update(2)
    s2 = run_stage2(user_request, s1.output, llm_call_middle)
    run.stages.append(s2)
    if not s2.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage2: {s2.error}"; return run

    if progress is not None: progress.update(3)
    s3 = run_stage3(user_request, s1.output, s2.output, llm_call_middle)
    run.stages.append(s3)
    if not s3.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage3: {s3.error}"; return run

    if progress is not None: progress.update(4)
    s4 = run_stage4(user_request, s1.output, s2.output, s3.output, llm_call_middle)
    run.stages.append(s4)
    if not s4.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage4: {s4.error}"; return run

    if progress is not None: progress.update(5)
    s5 = run_stage5(user_request, s1.output, s2.output, s3.output, s4.output, llm_call_wise)
    run.stages.append(s5)
    if not s5.success:
        run.final_state = "abandoned"; run.abandon_reason = f"stage5: {s5.error}"; return run

    run.code_text = s5.output["code"]

    # Stage 5.5 — LINT STRUTTURALE del manifest (ADR 0169, deterministico §7.9).
    # Gira PRIMA del verifier LLM (stage6): becca i difetti di FORMA della scheda
    # (PATTERN con arg inventato, arg runtime_resolved citato, output-shape) senza
    # spendere una call LLM. Rigetta solo su severity 'error' (difetti genuini);
    # i 'warn' (es. SCOPO lungo) sono loggati. Disable: METNOS_SYNT_LINT_DISABLED=1.
    import os as _os_lint
    if _os_lint.environ.get("METNOS_SYNT_LINT_DISABLED") != "1":
        try:
            from manifest_lint import lint_manifest as _lint
            _man = {
                "name": run.name or (s1.output.get("name") if s1.output else "") or "",
                "description": (s4.output.get("description") if s4.output else "") or "",
                "affinity": (s4.output.get("affinity") if s4.output else []) or [],
                "args": {
                    "properties": (s2.output.get("args_properties") if s2.output else {}) or {},
                    "required": (s2.output.get("args_required") if s2.output else []) or [],
                },
            }
            _findings = _lint(_man)
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
            if _errs:
                run.final_state = "rejected_lint_structural"
                run.abandon_reason = "manifest_lint: " + "; ".join(
                    e.message for e in _errs[:3])
                return run
        except Exception as ex:
            # best-effort §7.9: un errore d'infra del linter non blocca la synt.
            try:
                from logging_setup import get_logger
                get_logger(__name__).warning("[synt.lint] failed: %s", ex)
            except Exception:
                pass

    # Stage 6 — semantic verification (ADR 0114 Layer 6, 8/5/2026).
    # Confronta description (s4) vs code (s5). Se misaligned → rejected.
    # Determinismo §7.9: LLM solo per il giudizio, mai per la decisione.
    # Disable via env: METNOS_SYNT_STAGE6_DISABLED=1 (test/dev).
    import os as _os
    if _os.environ.get("METNOS_SYNT_STAGE6_DISABLED") != "1":
        try:
            from synt_stage6_verify import verify_semantic_alignment
            description = (s4.output.get("description") or "") if s4 and s4.output else ""
            code_body = run.code_text or ""
            if description and code_body:
                # Re-uso llm_call_wise: same tier (Gemma 4 26B locale).
                def _v_llm(prompt, model):
                    res = llm_call_wise(prompt, "", max_tokens=300, think=False)
                    return res or {}
                verdict = verify_semantic_alignment(
                    description=description,
                    code_body=code_body,
                    llm_call=_v_llm,
                    name_hint=run.name or run.proposal_id,
                )
                run.semantic_verdict = verdict
                if not verdict.get("aligned", False):
                    run.final_state = "rejected_semantic_drift"
                    run.abandon_reason = (
                        f"stage6 semantic drift: {verdict.get('mismatch', '')}"
                    )
                    return run
        except Exception as ex:
            # Best-effort §7.9: se lo stage 6 stesso fallisce, log warn e
            # ammetti il synth (non blocchiamo la pipeline su un errore di
            # infrastruttura del verifier; layer 2/3/5 sono ridondanti).
            try:
                from logging_setup import get_logger
                _log = get_logger(__name__)
                _log.warning("[synt.stage6] verify failed: %s", ex)
            except Exception:
                pass

    run.final_state = "synthesized"
    return run

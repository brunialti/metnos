"""skill_description_llm - LLM stage 4 description per skill importate.

Genera description IT+EN per un executor importato seguendo §2.5 + §6
(DEVI/NON DEVI/USO CORRETTO/ERRORE) + affinity 8-15 termini IT+EN.

Determinismo §7.9: LLM e' giustificato perche' la traduzione/condensazione
del body inglese in stile prescrittivo IT non e' equipotente con regole
deterministiche (vedi gap POC_REPORT §5.9: rischio traduzione letterale).

Fallback: se LLM non disponibile (no provider, no rete, no key, timeout),
ritorna boilerplate dal codegen + affinity dell'OBJECT.

Integrazione produzione (in <install_root>):
- Usa `prompt_loader.get("synt_stage4_description_imported", "it", ...)` o EN.
- Tier wise (Gemma 4 26B), una shot, max 500 tokens output.
- Output parsato come JSON `{description_it, description_en, affinity}`.
- Time budget per call: 5s (R1, 24/5/2026). Fallback boilerplate al timeout.

R1 (24/5/2026): wired in `cli/skills_cli.py::_cmd_import` PRIMA del codegen.
Ogni call (LLM o fallback) registrata in
`<PATH_USER_DATA>/skill_descriptions_audit.jsonl` append-only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional


# R1 time budget per executor (s). Override via env per dev/test.
DEFAULT_TIMEOUT_S: int = int(os.environ.get("METNOS_SKILL_LLM_TIMEOUT_S", "5"))


# ---------------------------------------------------------------------------
# Prompt template (lo stesso che dovrebbe vivere in templates/synt_stage4_description_imported.j2)
# ---------------------------------------------------------------------------


PROMPT_TEMPLATE_IT = """Sei un redattore tecnico Metnos. Devi scrivere la description (IT+EN) per un nuovo executor importato da una skill agentskills.io.

CONTESTO:
- Nome executor: {name}
- Verbo Metnos: {verb} (vocabolario chiuso §2.2)
- Object: {obj}
- Skill source: {skill_name} - sub-command `{skill_domain} {skill_action}`
- Output: {output_kind} (sempre lista §2.6)
{provenance_summary}

BODY SKILL.md (sezione rilevante):
{skill_body_snippet}

REGOLE OBBLIGATORIE (the design guide §2.5 — description SOLO TESTA):
- ESATTAMENTE 4 capitoli stringati, in quest'ordine: SCOPO: ... PATTERN: ... NON: ... OUT: ...
  SCOPO=1 frase (cosa fa). PATTERN=chiamata literal {name}(arg="..."). NON=anti-pattern + tool fratello (boundary §2.2). OUT=shape output pipeable (entries/results).
- SOLO TESTA: VIETATA la coda implementativa (vive nel codice, NON qui). LIMITI hard: testa(inizio->OUT:) <= {head_max} char; description intera <= {desc_max} char.
- Stringa unica, NIENTE newline. IT senza anglicismi (§7.8). Niente nomi propri (§7.5). Niente jargon Python.
- Vettoriale §2.1 + cap superiore §2.7 nel SCOPO quando entries.
- Affinity: 8-15 termini user-facing IT+EN combinati (sinonimi e colloquiali)

OUTPUT RIGOROSAMENTE JSON (niente prosa attorno):
{{
  "description_it": "SCOPO: ... PATTERN: {name}(...). NON: ... -> tool_fratello. OUT: entries[...]",
  "description_en": "SCOPO: ... PATTERN: {name}(...). NON: ... -> sibling_tool. OUT: entries[...]",
  "affinity": ["term1", "term2", ...]
}}
"""


def build_prompt(plan, parsed_skill, skill_body_snippet: str = "") -> str:
    """Costruisce il prompt per stage 4 imported.

    Strategia (gap 2, 10/5/2026):
    1. Prova `prompt_loader.get("synt_stage4_description_imported", "it", ...)`
       da <install_root>/runtime/prompts/ — se esiste, usa quello.
    2. Altrimenti fallback al template inline `PROMPT_TEMPLATE_IT` (legacy).
    """
    provenance_summary = ""
    if plan.provenance:
        provenance_summary = (
            f"- Provenance: imported_from={plan.provenance.get('imported_from', '')}\n"
            f"- Version source: {plan.provenance.get('source_version', '')}"
        )
    body_snippet = skill_body_snippet[:2000] if skill_body_snippet else "(no snippet)"

    try:
        from manifest_rules import HEAD_MAX as _hm, DESC_MAX as _dm
    except Exception:
        _hm, _dm = 240, 280

    # 1. Prova prompt_loader (produzione, se disponibile).
    rendered = _try_render_prompt_loader(
        name=plan.name, verb=plan.verb, obj=plan.obj,
        skill_name=parsed_skill.name,
        skill_domain=plan.skill_domain,
        skill_action=plan.skill_action,
        output_kind=plan.output_kind,
        provenance_summary=provenance_summary,
        skill_body_snippet=body_snippet,
        head_max=_hm, desc_max=_dm,
    )
    if rendered is not None:
        return rendered

    # 2. Fallback inline.
    return PROMPT_TEMPLATE_IT.format(
        name=plan.name,
        verb=plan.verb,
        obj=plan.obj,
        skill_name=parsed_skill.name,
        skill_domain=plan.skill_domain,
        skill_action=plan.skill_action,
        output_kind=plan.output_kind,
        provenance_summary=provenance_summary,
        skill_body_snippet=body_snippet,
        head_max=_hm, desc_max=_dm,
    )


def _try_render_prompt_loader(**vars) -> Optional[str]:
    """Tenta `prompt_loader.get('synt_stage4_description_imported', 'it', **vars)`.

    Restituisce None (silently) se prompt_loader non importabile o template
    assente — il caller cade sul template inline.
    """
    try:
        import sys
        from pathlib import Path
        runtime_dir = Path(__file__).resolve().parent  # ADR 0148 rename-resilient
        if not runtime_dir.exists():
            return None
        if str(runtime_dir) not in sys.path:
            sys.path.insert(0, str(runtime_dir))
        import prompt_loader  # type: ignore
        return prompt_loader.get("synt_stage4_description_imported", "it", **vars)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LLM call (pluggable)
# ---------------------------------------------------------------------------


# System prompt per il tier wise — pattern §6 condensato. Il body del
# user prompt viene passato dalla `build_prompt`.
_WISE_SYSTEM_DEFAULT = (
    "Sei un redattore tecnico Metnos. Rispondi SEMPRE con un solo oggetto JSON "
    "(niente prosa attorno, niente fence markdown). Schema: "
    '{"description_it": str, "description_en": str, "affinity": list[str]}.'
)


def _call_llm(prompt: str, *, timeout_s: int = DEFAULT_TIMEOUT_S,
              max_tokens: int = 600) -> Optional[str]:
    """Chiamata reale al tier wise (Gemma 4 26B locale via LlamaCppProvider
    su http://127.0.0.1:8080).

    Strategie in ordine:
    1. Funzione fake iniettata via env METNOS_LLM_DESCRIPTION_FAKE=mod.fn (test).
    2. LLMRouter() da <install_root>/runtime → provider("wise").chat() — produzione.
    3. None (fallback boilerplate; logga WARN tramite logger se disponibile).

    Time budget enforced via thread wrapper: se il provider supera `timeout_s`
    secondi, ritorna None silenziosamente (caller fallback su boilerplate). Il
    thread "leaked" continua in background ma non blocca la pipeline di import
    (un singolo import skill = N executor; un timeout su uno non blocca gli
    altri).

    think=False per stage 4 (description e' creativo ma non richiede thinking
    esteso; riduce latenza ~3x). max_tokens=600 sufficiente per JSON.
    """
    fake = os.environ.get("METNOS_LLM_DESCRIPTION_FAKE")
    fake_fn = None
    if fake:
        mod_name, _, attr = fake.rpartition(".")
        if mod_name and attr:
            try:
                mod = __import__(mod_name, fromlist=[attr])
                fn = getattr(mod, attr, None)
                if callable(fn):
                    fake_fn = fn
            except Exception as e:
                _warn_no_llm(f"fake llm import error: {e}")
                return None

    # Wrap fake/real call uniformemente in thread-with-deadline per garantire
    # time budget (timeout enforcement simmetrico fra test e produzione).
    result_holder: dict = {}

    def _call() -> None:
        try:
            if fake_fn is not None:
                text = fake_fn(prompt, timeout_s, max_tokens)
                if text is None:
                    result_holder["err"] = "fake llm returned None"
                    return
                if not isinstance(text, str) or not text.strip():
                    result_holder["err"] = "fake llm returned empty"
                    return
                result_holder["text"] = text
                return
            # Produzione: LLMRouter tier wise.
            import sys as _sys
            runtime_dir = Path(__file__).resolve().parent  # ADR 0148 rename-resilient
            if not runtime_dir.exists():
                result_holder["err"] = "runtime dir non disponibile"
                return
            if str(runtime_dir) not in _sys.path:
                _sys.path.insert(0, str(runtime_dir))
            from llm_router import LLMRouter  # type: ignore
            router = LLMRouter()
            provider = router.provider("wise")
            # think=False — JSON-strict, no reasoning extended.
            # temperature=0 — output deterministico.
            res = provider.chat(
                _WISE_SYSTEM_DEFAULT, prompt,
                max_tokens=max_tokens, temperature=0, think=False,
            )
            text = getattr(res, "text", None) or ""
            if not text.strip():
                result_holder["err"] = "LLM ritornato vuoto"
                return
            result_holder["text"] = text
        except Exception as e:
            result_holder["err"] = f"LLM error: {type(e).__name__}: {e}"

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        _warn_no_llm(f"LLM timeout dopo {timeout_s}s")
        return None
    if "text" in result_holder:
        return result_holder["text"]
    if "err" in result_holder:
        _warn_no_llm(result_holder["err"])
    return None


def _warn_no_llm(reason: str) -> None:
    """Log un WARN se logging_setup disponibile, altrimenti stderr (silente in test)."""
    if os.environ.get("METNOS_SKILLS_QUIET") == "1":
        return
    try:
        import sys
        from pathlib import Path
        runtime_dir = Path(__file__).resolve().parent  # ADR 0148 rename-resilient
        if str(runtime_dir) in sys.path:
            from logging_setup import get_logger  # type: ignore
            log = get_logger(__name__)
            log.warning("[skill_description_llm] LLM unavailable: %s — falling back to boilerplate", reason)
            return
    except Exception:
        pass
    # Fallback hard: stderr SOLO se non in test silenzioso.
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        print(f"[skill_description_llm] WARN: LLM unavailable: {reason} — falling back to boilerplate", file=__import__("sys").stderr)


# ---------------------------------------------------------------------------
# Parser dell'output (JSON con retry)
# ---------------------------------------------------------------------------


_JSON_BLOCK_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _parse_llm_output(text: str) -> Optional[dict]:
    """Estrae JSON dall'output LLM. Tollera prosa attorno + code fence."""
    if not text:
        return None
    # Strip fence markers comuni.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl >= 0:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    # Tentativo 1: JSON intero.
    try:
        obj = json.loads(cleaned)
        if _validate_shape(obj):
            return obj
    except json.JSONDecodeError:
        pass
    # Tentativo 2: prima regex match {...} con bilanciamento brace.
    obj = _extract_json_brace(cleaned)
    if obj and _validate_shape(obj):
        return obj
    return None


def _extract_json_brace(text: str) -> Optional[dict]:
    """Cerca il primo blocco {...} con brace bilanciate (depth)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None


def _validate_shape(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    if not isinstance(obj.get("description_it"), str):
        return False
    if not isinstance(obj.get("description_en"), str):
        return False
    aff = obj.get("affinity")
    if aff is not None and not isinstance(aff, list):
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_description(plan, parsed_skill, *,
                         skill_body_snippet: str = "",
                         retries: int = 1,
                         timeout_s: int = DEFAULT_TIMEOUT_S) -> Optional[dict]:
    """Genera description IT+EN + affinity via LLM. Ritorna None se fallisce
    (caller fallback su boilerplate).

    `timeout_s` (R1): time budget per singola call LLM (default 5s, override
    via env METNOS_SKILL_LLM_TIMEOUT_S). Su timeout → None senza retry.
    """
    prompt = build_prompt(plan, parsed_skill, skill_body_snippet)
    for attempt in range(retries + 1):
        text = _call_llm(prompt, timeout_s=timeout_s)
        if not text:
            return None
        parsed = _parse_llm_output(text)
        if parsed:
            # Sanitizza affinity: max 15 elementi, dedupe.
            aff = parsed.get("affinity") or []
            seen = set()
            out_aff = []
            for t in aff:
                if not isinstance(t, str):
                    continue
                t_n = t.strip()
                if t_n and t_n not in seen:
                    seen.add(t_n)
                    out_aff.append(t_n)
            parsed["affinity"] = out_aff[:15]
            return parsed
    return None


# ---------------------------------------------------------------------------
# Audit log (R1, 24/5/2026)
# ---------------------------------------------------------------------------


def _audit_log_path() -> Path:
    """`<PATH_USER_DATA>/skill_descriptions_audit.jsonl`.

    Rispetta METNOS_USER_DATA (§7.11) per isolamento test/e2e via
    `config.PATH_USER_DATA`. Niente Path.home() hardcoded.
    """
    import config as _C
    base = _C.PATH_USER_DATA
    base.mkdir(parents=True, exist_ok=True)
    return base / "skill_descriptions_audit.jsonl"


def _append_audit(*, plan_name: str, skill_name: str, source: str,
                  elapsed_ms: int, error: Optional[str] = None,
                  timeout_s: int = DEFAULT_TIMEOUT_S) -> None:
    """Append una riga JSONL per ogni description generation attempt.

    Schema: {ts, skill_name, plan_name, source: 'llm'|'boilerplate'|'fake',
             elapsed_ms, timeout_s, error?}.

    Fail-silent: l'audit non blocca la pipeline import. Skip se test env
    silenzioso (PYTEST_CURRENT_TEST set + METNOS_SKILLS_QUIET=1) per evitare
    creazione spurious in unit test.
    """
    if (
        os.environ.get("METNOS_SKILLS_QUIET") == "1"
        and os.environ.get("PYTEST_CURRENT_TEST")
    ):
        return
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "skill_name": skill_name,
        "plan_name": plan_name,
        "source": source,
        "elapsed_ms": int(elapsed_ms),
        "timeout_s": int(timeout_s),
    }
    if error:
        rec["error"] = error
    try:
        path = _audit_log_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # fail-silent


def generate_description_or_fallback(plan, parsed_skill, *,
                                     skill_body_snippet: str = "",
                                     boilerplate_it: str = "",
                                     boilerplate_en: str = "",
                                     boilerplate_affinity: Optional[list] = None,
                                     timeout_s: int = DEFAULT_TIMEOUT_S,
                                     ) -> dict:
    """Tenta LLM; se fallisce/timeout, ritorna boilerplate del caller.

    Garantisce che l'output abbia sempre `description_it`, `description_en`,
    `affinity` come stringhe/list — caller pratico per codegen.

    Audit append-only in `<PATH_USER_DATA>/skill_descriptions_audit.jsonl`
    per ogni invocazione (R1). Una riga per executor con source=llm/
    boilerplate + elapsed_ms + eventuale error.
    """
    skill_name = getattr(parsed_skill, "name", "") or ""
    plan_name = getattr(plan, "name", "") or ""
    t0 = time.perf_counter()
    res = generate_description(
        plan, parsed_skill,
        skill_body_snippet=skill_body_snippet,
        timeout_s=timeout_s,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if res:
        _append_audit(
            plan_name=plan_name, skill_name=skill_name,
            source="llm", elapsed_ms=elapsed_ms, timeout_s=timeout_s,
        )
        return {
            "description_it": res["description_it"],
            "description_en": res["description_en"],
            "affinity": res["affinity"] or boilerplate_affinity or [],
            "source": "llm",
        }
    _append_audit(
        plan_name=plan_name, skill_name=skill_name,
        source="boilerplate", elapsed_ms=elapsed_ms,
        error="llm_unavailable_or_timeout", timeout_s=timeout_s,
    )
    return {
        "description_it": boilerplate_it,
        "description_en": boilerplate_en,
        "affinity": boilerplate_affinity or [],
        "source": "boilerplate",
    }

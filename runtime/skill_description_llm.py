"""skill_description_llm - LLM stage 4 description per skill importate.

Genera description IT+EN per un executor importato seguendo §2.5 + §6
(DEVI/NON DEVI/USO CORRETTO/ERRORE) + affinity 8-15 termini IT+EN.

Determinismo §7.9: LLM e' giustificato perche' la traduzione/condensazione
del body inglese in stile prescrittivo IT non e' equipotente con regole
deterministiche (vedi gap POC_REPORT §5.9: rischio traduzione letterale).

Fallback: se LLM non disponibile (no provider, no rete, no key), ritorna
boilerplate dal codegen + affinity dell'OBJECT.

Integrazione produzione (in <install_root>):
- Usa `prompt_loader.get("synt_stage4_description_imported", "it", ...)` o EN.
- Tier wise (Gemma 4 26B), una shot, max 500 tokens output.
- Output parsato come JSON `{description_it, description_en, affinity}`.

Stub corrente: parser di output LLM minimale + retry su JSON malformato.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional


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

REGOLE OBBLIGATORIE (CLAUDE.md §6 prompt prescrittivo):
- Description in 2-5 frasi corte (max 25 parole/frase, §2.5)
- Pattern fisso: "DEVI: ... NON DEVI: ... USO CORRETTO: ... ERRORE: ..."
- IT senza anglicismi (§7.8): "trigger" -> "innesco", "goal" -> "obiettivo"
- Niente nomi propri di terzi (§7.5)
- Specifica vettoriale §2.1 e cap superiore esplicito §2.7 quando entries
- Niente jargon Python interno
- Affinity: 8-15 termini user-facing IT+EN combinati (sinonimi e colloquiali)

OUTPUT RIGOROSAMENTE JSON (niente prosa attorno):
{{
  "description_it": "Legge ... DEVI ... NON DEVI ... USO CORRETTO: {name}(...). ERRORE: ...",
  "description_en": "Reads ... MUST ... MUST NOT ... CORRECT USE: {name}(...). ERROR: ...",
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

    # 1. Prova prompt_loader (produzione, se disponibile).
    rendered = _try_render_prompt_loader(
        name=plan.name, verb=plan.verb, obj=plan.obj,
        skill_name=parsed_skill.name,
        skill_domain=plan.skill_domain,
        skill_action=plan.skill_action,
        output_kind=plan.output_kind,
        provenance_summary=provenance_summary,
        skill_body_snippet=body_snippet,
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


def _call_llm(prompt: str, *, timeout_s: int = 30,
              max_tokens: int = 600) -> Optional[str]:
    """Chiamata reale al tier wise (Gemma 4 26B locale via LlamaCppProvider
    su http://127.0.0.1:8080).

    Strategie in ordine:
    1. Funzione fake iniettata via env METNOS_LLM_DESCRIPTION_FAKE=mod.fn (test).
    2. LLMRouter() da <install_root>/runtime → provider("wise").chat() — produzione.
    3. None (fallback boilerplate; logga WARN tramite logger se disponibile).

    Nota gap 2 (10/5/2026): sostituisce il vecchio call_tier. think=False
    per stage 4 (description e' creativo ma non richiede thinking esteso;
    riduce latenza ~3x). max_tokens=600 sufficiente per JSON di description.
    """
    fake = os.environ.get("METNOS_LLM_DESCRIPTION_FAKE")
    if fake:
        mod_name, _, attr = fake.rpartition(".")
        if mod_name and attr:
            try:
                mod = __import__(mod_name, fromlist=[attr])
                fn = getattr(mod, attr, None)
                if callable(fn):
                    return fn(prompt, timeout_s, max_tokens)
            except Exception as e:
                _warn_no_llm(f"fake llm error: {e}")
                return None

    # Produzione: LLMRouter da <install_root>/runtime, tier wise.
    try:
        import sys
        from pathlib import Path
        runtime_dir = Path(__file__).resolve().parent  # ADR 0148 rename-resilient
        if not runtime_dir.exists():
            _warn_no_llm("<install_root>/runtime non disponibile")
            return None
        if str(runtime_dir) not in sys.path:
            sys.path.insert(0, str(runtime_dir))
        from llm_router import LLMRouter  # type: ignore
        router = LLMRouter()
        provider = router.provider("wise")
        # think=False — stage 4 e' creativo ma JSON-strict, no reasoning extended.
        # temperature=0 — output deterministico.
        result = provider.chat(
            _WISE_SYSTEM_DEFAULT, prompt,
            max_tokens=max_tokens, temperature=0, think=False,
        )
        text = getattr(result, "text", None) or ""
        if not text.strip():
            _warn_no_llm("LLM ritornato vuoto")
            return None
        return text
    except Exception as e:
        _warn_no_llm(f"LLM provider error: {type(e).__name__}: {e}")
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
                         retries: int = 1) -> Optional[dict]:
    """Genera description IT+EN + affinity via LLM. Ritorna None se fallisce
    (caller fallback su boilerplate).
    """
    prompt = build_prompt(plan, parsed_skill, skill_body_snippet)
    for attempt in range(retries + 1):
        text = _call_llm(prompt)
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


def generate_description_or_fallback(plan, parsed_skill, *,
                                     skill_body_snippet: str = "",
                                     boilerplate_it: str = "",
                                     boilerplate_en: str = "",
                                     boilerplate_affinity: Optional[list] = None,
                                     ) -> dict:
    """Tenta LLM; se fallisce, ritorna boilerplate del caller.

    Garantisce che l'output abbia sempre `description_it`, `description_en`,
    `affinity` come stringhe/list — caller pratico per codegen.
    """
    res = generate_description(plan, parsed_skill, skill_body_snippet=skill_body_snippet)
    if res:
        return {
            "description_it": res["description_it"],
            "description_en": res["description_en"],
            "affinity": res["affinity"] or boilerplate_affinity or [],
            "source": "llm",
        }
    return {
        "description_it": boilerplate_it,
        "description_en": boilerplate_en,
        "affinity": boilerplate_affinity or [],
        "source": "boilerplate",
    }

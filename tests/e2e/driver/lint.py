"""lint.py — assertion deterministica sulla qualità dell'output agente.

Cattura ~95% degli errori strutturali senza chiamare LLM. Veloce.

Verdetti:
  - LintResult(ok=False, issues=[...]) se qualcosa non va
  - LintResult(ok=True, issues=[]) altrimenti

Non solleva: il caller decide se assertare o solo segnalare.

NO import da runtime/. NO hardcoded strings user-facing — le issue keys
sono `E2E_ERR_LINT_*` resolved via driver.i18n.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import i18n


# Marker problematici universali
_MISSING_PATTERN = re.compile(r"<missing:([A-Z_][A-Z0-9_]*)>")
_TRACEBACK_PATTERN = re.compile(r"\bTraceback \(most recent call last\)")
_HTML_TAG_PATTERN = re.compile(r"<[a-zA-Z][^>]*>")

# Lang detection heuristic (subset del corpus extract.py — duplicato per
# isolamento, accettabile §7.3: stesso vocab IT/EN naturale)
_IT_MARKERS = frozenset({
    "il", "la", "i", "le", "di", "che", "con", "per", "del", "della",
    "una", "uno", "ho", "ha", "sono", "ce", "ci", "qui", "tutto",
})
_EN_MARKERS = frozenset({
    "the", "of", "is", "and", "to", "in", "for", "with", "are", "this",
    "that", "here", "all",
})
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _detect_lang(s: str) -> str:
    if not s:
        return "unknown"
    toks = {t.lower() for t in _TOKEN_RE.findall(s) if len(t) >= 3}
    it = len(toks & _IT_MARKERS)
    en = len(toks & _EN_MARKERS)
    if it == 0 and en == 0:
        return "unknown"
    return "it" if it >= en else "en"


@dataclass
class LintIssue:
    code: str          # E2E_ERR_LINT_*
    message: str       # rendered i18n template
    severity: str = "error"  # error | warn


@dataclass
class LintResult:
    ok: bool
    issues: list[LintIssue] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def fail_message(self) -> str:
        if self.ok:
            return ""
        return "\n".join(f"  [{i.severity}] {i.message}" for i in self.issues)


def check_response(
    response_text: str,
    *,
    expected_lang: str | None = None,
    min_length: int = 1,
    max_length: int = 50000,
) -> LintResult:
    """Lint deterministico universale su una risposta agente.

    expected_lang: 'it' | 'en' | None (skip lang check).
    """
    issues: list[LintIssue] = []
    txt = response_text or ""

    # Presence: not empty / not whitespace
    if len(txt.strip()) < min_length:
        issues.append(LintIssue(
            code="E2E_ERR_LINT_EMPTY",
            message=f"risposta vuota (len={len(txt)})",
        ))

    # Absence: missing i18n keys
    m = _MISSING_PATTERN.search(txt)
    if m:
        issues.append(LintIssue(
            code="E2E_ERR_LINT_MISSING_KEY",
            message=i18n.get("E2E_ERR_LINT_MISSING_KEY", key=m.group(1)),
        ))

    # Absence: Python traceback
    if _TRACEBACK_PATTERN.search(txt):
        issues.append(LintIssue(
            code="E2E_ERR_LINT_TRACEBACK",
            message=i18n.get("E2E_ERR_LINT_TRACEBACK"),
        ))

    # Length sanity
    if len(txt) > max_length:
        issues.append(LintIssue(
            code="E2E_ERR_LINT_TOO_LONG",
            message=f"risposta bloated ({len(txt)} char > max {max_length})",
            severity="warn",
        ))

    # Lang match (skip se expected_lang None o detection unknown)
    if expected_lang and len(txt) > 20:
        detected = _detect_lang(txt)
        if detected != "unknown" and detected != expected_lang:
            issues.append(LintIssue(
                code="E2E_ERR_LINT_LANG_MISMATCH",
                message=i18n.get("E2E_ERR_LINT_LANG_MISMATCH",
                                  got=detected, expected=expected_lang),
            ))

    err_count = sum(1 for i in issues if i.severity == "error")
    return LintResult(ok=err_count == 0, issues=issues)


def _step_tool(step: dict) -> str:
    """Tool name di un step. Server `/agent/turn` ritorna `steps_summary`
    con schema `{step, tool, ok}`; turn JSONL interno usa `chosen_tool`.
    Universal getter accetta entrambi."""
    return step.get("tool") or step.get("chosen_tool") or ""


def check_pipeline_shape(steps: list[dict]) -> LintResult:
    """Verifica invariante pipeline shape ADR 0154: `E* (F|A)?`.

    E = executor step (tool != final_answer/dialog)
    F = final_answer (terminatore universale, lecito da qualunque stato
        — vedi `runtime/pipeline_shape.py::next_state`)
    A = answer terminale

    Pattern valido: `^E*F?$`. Casi ammessi:
      - `[]`          (0 step: fast-path L0 deterministico)
      - `[F]`         (small-talk / query non actionable: PLANNER risponde
                       direttamente senza invocare executor)
      - `[E]`, `[EE]` (executor chain senza final_answer esplicito)
      - `[EF]`, `[EEF]` (executor chain + terminatore)

    Tool name "" (default StepLog non assegnato) e' un bug interno: il
    runtime DEVE normalizzare a "final_answer" in `TurnLog.write()`. Se
    capita ancora qui, segnalo come bug shape.
    """
    issues: list[LintIssue] = []
    if not steps:
        return LintResult(ok=True, issues=[])
    seq = []
    for s in steps:
        tool = _step_tool(s)
        if tool == "final_answer":
            seq.append("F")
        elif tool == "":
            seq.append("?")
        else:
            seq.append("E")
    shape = "".join(seq)
    if not re.match(r"^E*F?$", shape):
        issues.append(LintIssue(
            code="E2E_ERR_LINT_PIPELINE_SHAPE",
            message=i18n.get("E2E_ERR_LINT_PIPELINE_SHAPE", shape=shape),
        ))
    return LintResult(ok=len(issues) == 0, issues=issues)

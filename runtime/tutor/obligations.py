"""Closed decomposition of compound explanatory questions.

The classifier may only restate the user's information obligations.  It never
sees the catalog and cannot select sources, tools, authority, or facts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

from logging_setup import get_logger

from .deadline import phase_deadline, remaining


log = get_logger(__name__)
_MAX_QUERY_CHARS = 4_000
_MAX_OBLIGATION_CHARS = 600
_MAX_OBLIGATIONS = 4
_CLASSIFIER = SimpleNamespace(
    name="tutor_obligations",
    execution_policy={
        "effect": "read_only",
        "parallelism_class": 0,
        "resource_class": "llm",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    },
)


@dataclass(frozen=True, slots=True)
class QuestionObligations:
    queries: tuple[str, ...]
    decomposed: bool = False
    reason: str = "single"


def _compound_candidate(query: str) -> bool:
    """Cheap structural gate: simple questions must not pay another LLM call."""

    try:
        from compound_decomposer import _raw_query_chunks, split_query_chunks

        if len(split_query_chunks(query)) > 1:
            return True
        # The public action-aware splitter deliberately rejoins a clause whose
        # verb is not yet in the detection vocabulary.  Tutor decomposition is
        # semantic and may still resolve it.  Retain only substantial raw
        # clauses, which avoids treating scalar commas and short list members
        # as a compound question.
        substantial = [
            chunk for chunk in _raw_query_chunks(query)
            if len(chunk.split()) >= 3
        ]
        return len(substantial) > 1
    except Exception:
        return False


def _parse(value: object) -> tuple[str, ...]:
    try:
        payload = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return ()
    if not isinstance(payload, dict) or set(payload) != {"obligations"}:
        return ()
    rows = payload.get("obligations")
    if not isinstance(rows, list) or not 2 <= len(rows) <= _MAX_OBLIGATIONS:
        return ()
    clean: list[str] = []
    for row in rows:
        if not isinstance(row, str):
            return ()
        text = " ".join(row.split()).strip()
        if not text or len(text) > _MAX_OBLIGATION_CHARS or text in clean:
            return ()
        clean.append(text)
    return tuple(clean)


def classify_question_obligations(
        query: str, lang: str, *, deadline_at: float,
) -> QuestionObligations:
    """Return 2–4 self-contained retrieval queries or the original query.

    Any technical or schema failure is a behavior-preserving fallback.  This
    function runs only after EXPLAIN admission and only for a structurally
    compound candidate.
    """

    original = " ".join(str(query or "").split()).strip()
    if not original or len(original) > _MAX_QUERY_CHARS:
        return QuestionObligations((original,) if original else (), reason="bounded")
    if not _compound_candidate(original):
        return QuestionObligations((original,))

    from executor_scheduler import SchedulerAdmissionTimeout, invoke_scheduled
    from llm_helpers import call_llm
    from llm_workloads import tier_for
    from prompt_loader import get as get_prompt

    call_deadline = phase_deadline(deadline_at, 8.0)
    try:
        prompt = get_prompt("tutor_obligations", lang)
    except Exception:
        return QuestionObligations((original,), reason="prompt_unavailable")

    def _call() -> dict:
        try:
            text, _metadata = call_llm(
                {"language": lang, "user_query": original},
                prompt,
                tier=tier_for("tutor.obligations"),
                max_tokens=256,
                max_query_chars=_MAX_QUERY_CHARS,
                timeout_s=remaining(call_deadline),
            )
            return {"ok": True, "text": text}
        except Exception:
            return {"ok": False}

    try:
        result = invoke_scheduled(
            _CLASSIFIER, _call,
            admission_timeout_s=remaining(call_deadline),
        )
    except (SchedulerAdmissionTimeout, TimeoutError):
        return QuestionObligations((original,), reason="timeout")
    except Exception:
        log.warning("Tutor obligation classifier unavailable", exc_info=True)
        return QuestionObligations((original,), reason="unavailable")
    queries = _parse(result.get("text") if isinstance(result, dict)
                     and result.get("ok") else "")
    if not queries:
        return QuestionObligations((original,), reason="malformed")
    return QuestionObligations(queries, decomposed=True, reason="classified")


def merge_contexts(contexts, *, maximum: int = 16):
    """Fairly merge per-obligation contexts, reserving every primary first."""

    from .semantic import SemanticContext

    valid = [context for context in contexts if context is not None]
    if not valid:
        return None
    if any(context.restricted for context in valid):
        top = max(float(context.top_score) for context in valid)
        return SemanticContext((), top, restricted=True)
    cap = max(1, min(16, int(maximum)))
    chosen = []
    seen: set[tuple[str, str]] = set()

    def admit(hit) -> None:
        key = (hit.source_type, hit.source_id)
        if key not in seen and len(chosen) < cap:
            seen.add(key)
            chosen.append(hit)

    for context in valid:
        if context.hits:
            admit(context.hits[0])
    depth = 1
    while len(chosen) < cap:
        added = False
        for context in valid:
            if depth < len(context.hits):
                before = len(chosen)
                admit(context.hits[depth])
                added = added or len(chosen) > before
        if not added and all(depth >= len(context.hits) for context in valid):
            break
        depth += 1
    if not chosen:
        return None
    return SemanticContext(
        tuple(chosen), max(float(context.top_score) for context in valid),
        restricted=False,
    )


def map_context_sources(contexts, admitted_hits, *, source_id):
    """Map every obligation to the evidence that survived the global budget.

    Retrieval remains independent per obligation, while composition receives
    one deduplicated context.  The positional map preserves that provenance
    after the merge without copying source bodies or granting authority to the
    classifier.  ``source_id`` is supplied by the service so this module does
    not depend on its rendering layer.
    """

    admitted = {
        (hit.source_type, hit.source_id)
        for hit in admitted_hits
    }
    mapped: list[tuple[str, ...]] = []
    for context in contexts:
        rows: list[str] = []
        if context is not None and not context.restricted:
            for hit in context.hits:
                key = (hit.source_type, hit.source_id)
                if key in admitted:
                    rendered = str(source_id(hit) or "").strip()
                    if rendered and rendered not in rows:
                        rows.append(rendered)
        mapped.append(tuple(rows))
    return tuple(mapped)


def render_question_obligations(
        queries: tuple[str, ...], source_ids: tuple[tuple[str, ...], ...] = (),
) -> str:
    """Render a bounded provenance checklist for the composer.

    The questions are not evidence.  Source identifiers only bind each
    question to already rendered ``SOURCE`` blocks and cannot introduce facts.
    """

    if len(queries) < 2:
        return ""
    lines = ["[QUESTION_OBLIGATIONS]"]
    for index, query in enumerate(queries, 1):
        sources = source_ids[index - 1] if index <= len(source_ids) else ()
        evidence = "available" if sources else "missing"
        source_list = ",".join(sources) if sources else "none"
        lines.append(
            f"- obligation={index}; evidence={evidence}; "
            f"source_ids={source_list}; question={query}")
    lines.append("[/QUESTION_OBLIGATIONS]")
    return "\n".join(lines)

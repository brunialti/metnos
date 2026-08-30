#!/usr/bin/env python3
"""Manually reviewed language resources for runtime safety and honesty.

These recognizers can block a destructive action or reject an untruthful
planner receipt.  Consequently an active language is usable only when its
native row is ready and still carries the manual-review policy.  Italian and
English remain additive compatibility baselines, but they are never used to
silently stand in for a missing or pending active-language row.
"""
from __future__ import annotations

import detection_lexicon as _dl


AVAILABILITY = "runtime_safety.availability"
PROPOSE_INTENT = "runtime_safety.propose_intent"
UNBACKED_PROMISE = "runtime_safety.unbacked_promise"
FALSE_NOT_FOUND = "runtime_safety.false_not_found"
FALSE_SUCCESS = "runtime_safety.false_success"
MUTATION_CLAIM = "runtime_safety.mutation_claim"
MUTATION_NEGATION = "runtime_safety.mutation_negation"
DEGENERATE_FINAL = "runtime_safety.degenerate_final"
ARTIFACT_COMPLETION = "runtime_safety.artifact_completion"
ARTIFACT_NEGATION = "runtime_safety.artifact_negation"
ARTIFACT_SPREADSHEET = "runtime_safety.artifact.spreadsheet"
ARTIFACT_DOCUMENT = "runtime_safety.artifact.document"
ARTIFACT_ARCHIVE = "runtime_safety.artifact.archive"
THINKING_LEAK_EN = "runtime_safety.thinking_leak.en"
META_PERMISSION_PAREN = "runtime_safety.meta_permission.parenthesized"
META_PERMISSION_LINE = "runtime_safety.meta_permission.line"
RUNTIME_INTERNAL_LEAK = "runtime_safety.runtime_internal_leak"
DOCUMENT_AUDIT_CONFLICT_INTENT = "runtime_safety.document_audit.conflict_intent"
DOCUMENT_AUDIT_UNREADABLE_INTENT = "runtime_safety.document_audit.unreadable_intent"
DOCUMENT_AUDIT_DUPLICATE_INTENT = "runtime_safety.document_audit.duplicate_intent"
DOCUMENT_NO_CONTRADICTION_CLAIM = "runtime_safety.document_audit.no_conflict_claim"
DEEP_CRAWL_INTENT = "runtime_safety.deep_crawl_intent"

CONCEPTS = frozenset({
    AVAILABILITY,
    PROPOSE_INTENT,
    UNBACKED_PROMISE,
    FALSE_NOT_FOUND,
    FALSE_SUCCESS,
    MUTATION_CLAIM,
    MUTATION_NEGATION,
    DEGENERATE_FINAL,
    ARTIFACT_COMPLETION,
    ARTIFACT_NEGATION,
    ARTIFACT_SPREADSHEET,
    ARTIFACT_DOCUMENT,
    ARTIFACT_ARCHIVE,
    THINKING_LEAK_EN,
    META_PERMISSION_PAREN,
    META_PERMISSION_LINE,
    RUNTIME_INTERNAL_LEAK,
    DOCUMENT_AUDIT_CONFLICT_INTENT,
    DOCUMENT_AUDIT_UNREADABLE_INTENT,
    DOCUMENT_AUDIT_DUPLICATE_INTENT,
    DOCUMENT_NO_CONTRADICTION_CLAIM,
    DEEP_CRAWL_INTENT,
})

_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Register the historical IT/EN corpus under manual review policy."""

    def regex(concept: str, pattern: str) -> None:
        # The old recognizers were an exact IT+EN union.  Duplicating that
        # union in both reviewed baselines preserves compatibility and lets a
        # ready third language extend it without making either baseline a
        # silent fallback for an unreviewed locale.
        _dl.register(
            concept,
            "regex",
            it=[pattern],
            en=[pattern],
            review_policy="manual",
        )

    regex(
        AVAILABILITY,
        r"\b("
        r"se\s+c['’]?[eè]\s+(un\s+)?(posto|buco|slot|spazio|tempo)|"
        r"se\s+(la\s+finestra|lo\s+slot)\s+[eè]['\s]*libera|"
        r"se\s+sono\s+libero|se\s+sei\s+libero|"
        r"se\s+non\s+ho\s+(altro|impegni|gi[aà])|"
        r"verifica\s+(la\s+)?disponibilit[aà]|"
        r"controlla\s+(la\s+)?disponibilit[aà]|"
        r"se\s+disponibile|"
        r"if\s+(it['’]?s\s+)?available|"
        r"if\s+(i\s+am|i['’]?m)\s+free|"
        r"if\s+there['’]?s\s+(a\s+)?(slot|opening|space|time)|"
        r"if\s+free|check\s+availability|"
        r"if\s+(the\s+)?(slot|window)\s+is\s+free"
        r")\b",
    )
    regex(
        PROPOSE_INTENT,
        r"(?:\b|^)("
        r"propon[a-z]{1,5}|propor[a-z]{2,7}|"
        r"suggeris[a-z]{1,5}|sugger[a-z]{2,7}|"
        r"raccomand[a-z]{1,6}|"
        r"che(?:\s+ne)?\s+dici|cosa(?:\s+ne)?\s+(?:dici|pensi)|"
        r"consigli[a-z]{1,5}|"
        r"quali\s+(?:\w+\s+){0,6}(?:liber[ie]|disponibil[ie]|vuoti?|vuote)|"
        r"(?:\d+|alcun[ie]|qualche|alcune|alcuni|some)\s+"
        r"(?:opzion[ie]|alternativ[ae]|propost[ae]|slot|slots|orari[oi]?|"
        r"fasce?|mattine?|pomeriggi|finestre?|"
        r"mercoled[ìi]|luned[ìi]|marted[ìi]|"
        r"gioved[ìi]|venerd[ìi]|sabat[oi]|domenic[ah]e?)|"
        r"propose|proposes|proposing|"
        r"suggest|suggests|suggesting|"
        r"recommend|recommends|recommending|"
        r"what\s+about|how\s+about|"
        r"what\s+(?:\w+\s+){0,4}(?:are\s+|is\s+)?"
        r"(?:free|available|open)|"
        r"which\s+(?:\w+\s+){0,4}(?:are\s+|is\s+)?"
        r"(?:free|available|open)|"
        r"any\s+(?:free|available|open)\s+"
        r"(?:slots?|times?|windows?|mornings?|afternoons?|days?|"
        r"appointments?|meetings?)|"
        r"(?:\d+|some|a\s+few|several|any)\s+"
        r"(?:morning\s+|afternoon\s+|free\s+|available\s+|open\s+|"
        r"alternative\s+|proposed?\s+)?"
        r"(?:options?|alternatives?|proposals?|slots?|times?|"
        r"mornings?|afternoons?|openings?|windows?)"
        r")(?:\b|$)",
    )
    regex(
        DEEP_CRAWL_INTENT,
        r"(esplor|mappa|archivi|scandagli|ricorsiv|intero sito|"
        r"tutto il sito|crawl|approfondit|exhaustive|entire site|"
        r"whole site|recursiv|\bexplore)",
    )
    regex(
        UNBACKED_PROMISE,
        r"\b("
        r"ti (informer[oò'`]|aggiorner[oò'`]|far[oò'`] sapere|dir[oò'`]|"
        r"segnaler[oò'`]|comunicher[oò'`]|contatter[oò'`]|risponder[oò'`])"
        r"|sto (cercando|effettuando|controllando|monitorando|"
        r"verificando|raccogliendo)"
        r"|appena (avr[oò'`]|trovo|trovato|trovi|disponibili|disponibile|"
        r"ricever[oò'`])"
        r")",
    )
    regex(
        FALSE_NOT_FOUND,
        r"(non (?:e'|è) stato trovat[oai]|non trovat[oai]|"
        r"non (?:esiste|esistono|risulta|risultano)|"
        r"not found|does not exist|n[oa]t (?:been )?found)",
    )
    regex(
        FALSE_SUCCESS,
        r"(?<!non )(?<!not )\b(?:"
        r"ho\s+(?:analizzat|trovat|salvat|inviat|creat|classificat|"
        r"preparat|scritt|spostat|cancellat|aggiornat|notificat|registrat)\w*"
        r"|(?:bozz\w+|rispost\w+|notific\w+)\s+"
        r"(?:salvat|pront|inviat|creat)\w*"
        r"|(?:e'|è|sono)\s+stat[oaie]\s+"
        r"(?:salvat|inviat|creat|notificat|preparat|analizzat|classificat)\w*"
        r"|i\s+have\s+(?:analyz|found|saved|sent|creat|classifi|prepar|"
        r"notifi)\w*"
        r"|(?:drafts?|replies|notifications?)\s+"
        r"(?:saved|sent|ready|created)"
        r")",
    )
    regex(
        MUTATION_CLAIM,
        r"(?<!non )(?<!not )\b(?:"
        r"(?:creat|generat|salvat|scritt|prepar)\w*\s+"
        r"(?:(?:il|lo|la|un|uno|una|the|a|an)\s+)?"
        r"(?:\w+\s+){0,2}"
        r"(?:foglio|file|document\w*|spreadsheet|sheet|calendari\w*|"
        r"event\w*|cartell\w*|folder|tabell\w*|csv|xlsx)"
        r"|(?:inviat|spedit|mandat|sent)\w*\s+"
        r"(?:(?:il|la|un|the|a|an)\s+)?(?:\w+\s+){0,2}"
        r"(?:mail|email|messaggi\w*|message)"
        r"|(?:spostat|cancellat|eliminat|delet|mov)\w*\s+"
        r"(?:(?:il|la|i|le|the)\s+)?(?:\w+\s+){0,2}"
        r"(?:file|mail|email|messaggi\w*|event\w*)"
        r"|(?:created|saved|wrote|generated|prepared)\s+"
        r"(?:(?:the|a|an)\s+)?(?:\w+\s+){0,2}"
        r"(?:file|spreadsheet|sheet|document|calendar|event|folder|table|csv)"
        r")",
    )
    regex(
        MUTATION_NEGATION,
        r"non\s+(?:ho|sono\s+riuscit\w+\s+a|sono\s+stat\w+\s+in\s+grado"
        r"\s+di)\s*\w*\s*(?:creat|inviat|spedit|salvat|generat|scritt|"
        r"spostat|cancellat|prepar)"
        r"|(?:couldn'?t|could\s+not|was\s+not\s+able\s+to|did\s*n'?t)"
        r"\s+\w*\s*(?:creat|sen[dt]|sav|writ|generat|mov|delet|prepar)",
    )
    regex(
        DEGENERATE_FINAL,
        r"\A[\(\[\s]*\d+(?:[.,]\d+)?\s*"
        r"(?:elementi|entries|elements|voci|risultati|results|item|items)?"
        r"\s*[\)\]\s]*\Z",
    )
    regex(
        ARTIFACT_COMPLETION,
        r"\b(?:salvat|creat|generat|scritt|prodott|preparat|saved|created|"
        r"generated|written|produced|prepared)\w*\b",
    )
    regex(
        ARTIFACT_NEGATION,
        r"(?s)\b(?:non|not|nessun\w*|no)\b.{0,40}\b"
        r"(?:salvat|creat|generat|scritt|prodott|saved|created|generated|"
        r"written|produced)\w*\b",
    )
    regex(
        ARTIFACT_SPREADSHEET,
        r"\b(?:fogli\w*(?:\s+di\s+calcolo)?|spreadsheet|xlsx|csv|sheet)\b",
    )
    regex(
        ARTIFACT_DOCUMENT,
        r"\b(?:rapport\w*|report\w*|riepilog\w*|document\w*)\b",
    )
    regex(
        ARTIFACT_ARCHIVE,
        r"\b(?:archivi\w*|zip|compressed\s+archive)\b",
    )
    regex(
        THINKING_LEAK_EN,
        r"^\s*(?:"
        r"Wait\b|Actually\b|Let me\b|I'll\b|I will\b|Hmm\b|"
        r"Looking at\b|One detail:|Final Answer(?:\s+construction)?:|"
        r"Wait,?\s+I(?:'|)ll\b|Wait,?\s+I should\b|"
        r"Now I'll\b|Actually,?\s+I'll\b|So,?\s+the answer\b|Let me think\b|"
        r"I should\b|Rule:\s|Given\b"
        r").*$",
    )
    regex(
        META_PERMISSION_PAREN,
        r"^\s*\(\s*(?:"
        r"posso provare|posso cercare|posso aiutarti|posso suggerirti|"
        r"posso farlo|posso fare|posso recuperare|posso scaricare|"
        r"se mi dai il via libera|se vuoi|se preferisci|fammi sapere|"
        r"dimmi se|vuoi che (?:lo )?faccia|se ti serve|se hai bisogno"
        r")[^)]*\)\s*\.?\s*$",
    )
    regex(
        META_PERMISSION_LINE,
        r"^\s*(?:"
        r"se mi dai il via libera|se vuoi posso|fammi sapere se|"
        r"dimmi se vuoi|vuoi che (?:lo )?faccia|"
        r"posso provare a|posso cercare|posso aiutarti|posso suggerirti"
        r")\b[^.?!,;]*[.?!]?\s*$",
    )
    regex(
        RUNTIME_INTERNAL_LEAK,
        r"(DUPLICATE_CALL:|FORMULA LA FINAL_ANSWER|"
        r"FORMULATE (?:THE )?FINAL_ANSWER|"
        r"^validation failed:|^vaglio rifiuta:|"
        r"consecutive_blocked|auto_final_on_duplicate|"
        r"cap_same_executor|VECTORIAL_VIOLATION|"
        r"synth_request_blocked_by|requires one of \[|"
        r"request_new_executor rejected|jaccard \d|"
        r"Riusalo invece di sintetiz|"
        r"Reuse it instead of synthesiz|"
        r"candidate '[^']+' copre la query|"
        r"candidate '[^']+' covers the query)",
    )
    regex(
        DOCUMENT_AUDIT_CONFLICT_INTENT,
        r"contradditt|contradict|inconsisten|conflict",
    )
    regex(
        DOCUMENT_AUDIT_UNREADABLE_INTENT,
        r"illeggibil|unreadable|corrupt",
    )
    regex(
        DOCUMENT_AUDIT_DUPLICATE_INTENT,
        r"duplicat|deduplic",
    )
    regex(
        DOCUMENT_NO_CONTRADICTION_CLAIM,
        r"(?:^|\n)[^\n]*(?:tutti\s+i\s+dati.*coerent|"
        r"nessun\w*\s+contraddizion|"
        r"all\s+(?:the\s+)?data.*consistent|"
        r"no\s+contradictions?)[^\n]*(?:\n|$)|"
        r"(?:nessun\w*|no)(?:\s+\w+){0,4}\s+"
        r"(?:contraddittor|contradiction|inconsisten|conflict)\w*"
        r"[^.\n]*(?:\.|$)",
    )


def _all_seed_rows_ready() -> bool:
    for concept in CONCEPTS:
        for language in ("it", "en"):
            resource = _dl.resource_for_language(
                concept, language, fallback=False, ready_only=True,
            )
            if (not resource or resource.get("kind") != "regex"
                    or resource.get("review_policy") != "manual"):
                return False
    return True


def _ensure_registered() -> None:
    """Register lazily and retry after tests/installations swap the DB."""

    global _registered_target
    target = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    if _registered_target == target:
        return
    register_all()
    refreshed = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    _registered_target = refreshed if _all_seed_rows_ready() else None


def patterns(concept: str):
    """Return reviewed native patterns, or an empty tuple when unavailable."""

    if concept not in CONCEPTS:
        raise KeyError(f"unknown runtime-safety concept: {concept}")
    _ensure_registered()
    return tuple(_dl.native_ready_patterns(
        concept,
        require_manual=True,
        include_reviewed_baselines=True,
    ))


def matches(concept: str, text: str, *, fail_closed: bool = True) -> bool:
    """Match a reviewed concept with an explicit unavailable policy."""

    if not isinstance(text, str) or not text:
        return False
    compiled = patterns(concept)
    if not compiled:
        return bool(fail_closed)
    return any(pattern.search(text) for pattern in compiled)


def has_deep_crawl_intent(text: str) -> bool:
    """Return a positive deep-crawl signal only from a ready native grammar."""

    return matches(DEEP_CRAWL_INTENT, text, fail_closed=False)


__all__ = [
    "ARTIFACT_ARCHIVE",
    "ARTIFACT_COMPLETION",
    "ARTIFACT_DOCUMENT",
    "ARTIFACT_NEGATION",
    "ARTIFACT_SPREADSHEET",
    "AVAILABILITY",
    "CONCEPTS",
    "DEGENERATE_FINAL",
    "DOCUMENT_AUDIT_CONFLICT_INTENT",
    "DOCUMENT_AUDIT_DUPLICATE_INTENT",
    "DOCUMENT_AUDIT_UNREADABLE_INTENT",
    "DOCUMENT_NO_CONTRADICTION_CLAIM",
    "DEEP_CRAWL_INTENT",
    "FALSE_NOT_FOUND",
    "FALSE_SUCCESS",
    "MUTATION_CLAIM",
    "MUTATION_NEGATION",
    "META_PERMISSION_LINE",
    "META_PERMISSION_PAREN",
    "PROPOSE_INTENT",
    "RUNTIME_INTERNAL_LEAK",
    "THINKING_LEAK_EN",
    "UNBACKED_PROMISE",
    "has_deep_crawl_intent",
    "matches",
    "patterns",
    "register_all",
]

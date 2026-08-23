"""Task scheduler v2: traduce i lessici di detection pending (detection.sqlite).

Gemello di `i18n_translate_pending`, lato INPUT. Pesca fino a N concept con
`needs_translation=1` e, per ogni `(concept, target_lang)`, chiede al LLM di
produrre le forme di superficie NATURALI nella lingua target a partire dalle
forme sorgente (en canonico). Strict JSON in/out; la policy di generazione e'
quella centrale del workload ``translation.detection`` → wise (la singola
call resta generativa).

Tre `kind`:
  - phrases: tradurre l'insieme di forme -> {"forms": [...]} (varianti
    morfologiche ammesse; brand invariati; niente traduzione parola-per-parola).
  - mapping: tradurre i VALORI di ogni categoria, KEY invariate ->
    {"mapping": {key: [...]}}.
  - regex: NON auto-generato (un regex sbagliato e' peggio del gap). Resta
    pending + audit "authoring manuale richiesto" (§2.8 onesto). Il guard
    `detection_lexicon.verify_coverage` lo segnala finche' non e' fornito.

Audit JSONL append-only in `<user_data>/detection_audit/<YYYY-MM-DD>.jsonl`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11
import detection_lexicon as _dl
from timefmt import now_iso_z as _now_iso, today_iso as _today_iso_date

log = logging.getLogger("metnos.jobs.detection_translate_pending")

def _cap_per_fire() -> int:
    """Cap righe/fire letto a CALL-TIME (override runtime via env, testabile)."""
    return int(os.environ.get("METNOS_DETECTION_CAP_PER_FIRE", "20"))


_LANG_NAMES = {"it": "Italian", "en": "English", "fr": "French",
               "de": "German", "es": "Spanish", "pt": "Portuguese"}


def _tier() -> str:
    from llm_workloads import tier_for
    return tier_for("translation.detection")


def _audit_dir() -> Path:
    env = os.environ.get("METNOS_DETECTION_AUDIT_DIR")
    return Path(env) if env else _C.PATH_USER_DATA / "detection_audit"


_SYS_PROMPT = (
    "You are a localization expert for an AI assistant. You localize the "
    "SURFACE FORMS used to DETECT a user's intent in their own query. "
    "Reply ONLY with strict JSON, no prose."
)

_PHRASES_TMPL = (
    "Localize these trigger forms from {source_name} into {target_name}.\n"
    "Concept (what they detect): {concept}\n"
    "Rules: produce the NATURAL words/phrases a native {target_name} speaker "
    "would actually use for the SAME intent; include common morphological and "
    "spelling variants; keep brand/proper names unchanged; do NOT translate "
    "literally word-by-word; drop forms that have no equivalent.\n"
    "Source forms (JSON): {payload}\n"
    'Output exactly: {{"forms": ["...", "..."]}}'
)

_MAPPING_TMPL = (
    "Localize the VALUES of this category->forms map from {source_name} into "
    "{target_name}.\n"
    "Concept: {concept}\n"
    "Rules: keep every KEY exactly as-is; translate only the surface forms in "
    "each list to natural {target_name}; keep brand/proper names unchanged; "
    "include morphological variants; do NOT translate literally; do not "
    "reuse the same translated surface under different keys unless the "
    "target language is genuinely ambiguous.\n"
    "Source map (JSON): {payload}\n"
    'Output exactly: {{"mapping": {{"key": ["...", "..."]}}}}'
)


def _human_review_concepts() -> frozenset:
    """Concepts a model must never localize on its own (consent gates)."""
    try:
        from detection_lexicon_seed import HUMAN_REVIEW_CONCEPTS
        return HUMAN_REVIEW_CONCEPTS
    except Exception:  # noqa: BLE001 — better translate less than translate badly
        return frozenset({"confirm.yes", "confirm.no"})


def _extract_json(raw: str):
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _llm_localize(concept: str, kind: str, source_payload, target_lang: str,
                  source_lang: str, tier: str):
    """Ritorna (localized_payload_or_None, meta). source_payload = oggetto py."""
    from llm_helpers import call_llm
    tgt = _LANG_NAMES.get(target_lang.lower(), target_lang)
    src = _LANG_NAMES.get(source_lang.lower(), source_lang)
    tmpl = _MAPPING_TMPL if kind == "mapping" else _PHRASES_TMPL
    prompt = tmpl.format(source_name=src, target_name=tgt, concept=concept,
                         payload=json.dumps(source_payload, ensure_ascii=False))
    for _ in range(2):  # iniziale + 1 retry
        text, meta = call_llm(
            prompt, _SYS_PROMPT, tier=tier, max_tokens=800)
        obj = _extract_json(text or "")
        if not isinstance(obj, dict):
            continue
        if kind == "mapping":
            m = obj.get("mapping")
            if isinstance(m, dict):
                validation = _dl.validate_mapping_payload(source_payload, m)
                # Una localizzazione parziale non e' copertura: accettarla
                # farebbe sparire in silenzio intere categorie canoniche. Le
                # collisioni nuove richiedono review umana, non una scelta
                # arbitraria del prefilter.
                if validation["ok"] and not validation["ambiguous_surfaces"]:
                    return validation["mapping"], {
                        "model": meta.get("model"), "tier": tier,
                    }
        else:
            forms = obj.get("forms")
            if isinstance(forms, list):
                out = [str(x).strip() for x in forms if str(x).strip()]
                if out:
                    return out, {"model": meta.get("model"), "tier": tier}
    return None, {"model": None, "tier": tier}


def _audit(events: list[dict]) -> None:
    try:
        from audit_jsonl import append_jsonl
        append_jsonl(_audit_dir() / f"{_today_iso_date()}.jsonl", events)
    except Exception:
        log.exception("detection_translate: audit append fallito")


def task_detection_translate_pending(payload: dict | None = None) -> dict:
    """Callback scheduler v2. Traduce fino a CAP_PER_FIRE concept pending.

    Ritorna {ok, ok_count, error_count, metadata}. I `regex` non sono
    auto-generati: vengono contati in `skipped_regex` e restano pending.
    """
    _dl.ensure_seeded()
    # Exclude in SQL, not in the loop: the window is bounded, and the rows
    # that can never be translated are as many as the window itself (18 regex
    # concepts + the consent gates, against a default CAP of 20). Skipping
    # them after reading lets them occupy every slot at every fire, and the
    # daemon converges on doing nothing while the work it could do waits
    # behind them. The loop keeps its own checks as a backstop.
    _excluded_kinds = ("regex",)
    _excluded_concepts = tuple(sorted(_human_review_concepts()))
    pending = _dl.list_pending(limit=_cap_per_fire(),
                               exclude_concepts=_excluded_concepts,
                               exclude_kinds=_excluded_kinds)
    # Excluding in SQL would also hide HOW MANY rows are permanently out of
    # reach, and «nothing to do» and «everything is blocked by hand-work» are
    # not the same report (§2.8). Count them once, without a window.
    _held = _dl.list_pending(limit=0)
    held_regex = sum(1 for r in _held if r["kind"] in _excluded_kinds)
    held_consent = sum(1 for r in _held
                       if r["concept"] in _excluded_concepts
                       and r["kind"] not in _excluded_kinds)
    ok = err = skipped = 0
    events: list[dict] = []
    tier = _tier()
    for row in pending:
        concept = row["concept"]
        tgt = row["target_lang"]
        kind = row["kind"]
        src_lang = row["source_lang"] or "en"
        if kind == "regex":
            skipped += 1
            events.append({"ts": _now_iso(), "concept": concept, "lang": tgt,
                           "kind": kind, "result": "skip_regex_manual"})
            continue
        if concept in _human_review_concepts():
            # These concepts decide whether a consent was given. A form
            # invented by a model and never read back by anyone does not
            # produce a missed recognition here: it produces a yes the user
            # never said. They stay with a person; the union with it/en keeps
            # the new language usable through loanwords («ok», «yes», «no»).
            skipped += 1
            events.append({"ts": _now_iso(), "concept": concept, "lang": tgt,
                           "kind": kind, "result": "skip_consent_manual"})
            continue
        try:
            source_payload = json.loads(row["source_payload"]) \
                if row.get("source_payload") else None
        except Exception:
            source_payload = None
        if not source_payload:
            err += 1
            continue
        try:
            localized, meta = _llm_localize(concept, kind, source_payload,
                                            tgt, src_lang, tier)
        except Exception as ex:
            err += 1
            events.append({"ts": _now_iso(), "concept": concept, "lang": tgt,
                           "result": "llm_error", "detail": str(ex)[:120]})
            continue
        if localized is None:
            err += 1
            events.append({"ts": _now_iso(), "concept": concept, "lang": tgt,
                           "result": "no_translation"})
            continue
        _dl.set_translated(concept, tgt, localized)
        ok += 1
        events.append({"ts": _now_iso(), "concept": concept, "lang": tgt,
                       "kind": kind, "result": "ok", "model": meta.get("model"),
                       "n_forms": len(localized)})
    if events:
        _audit(events)
    return {
        "ok": True,
        "ok_count": ok,
        "error_count": err,
        "metadata": {"pending_seen": len(pending), "translated": ok,
                     # `skipped_*` = rows this fire READ and set aside (the
                     # loop backstop); `held_*` = rows permanently out of a
                     # model's reach, excluded before the window. The two are
                     # different facts and a single number would hide one.
                     "skipped_regex": skipped,
                     "held_regex": held_regex,
                     "held_consent": held_consent,
                     "tier_used": tier},
    }


if __name__ == "__main__":
    print(json.dumps(task_detection_translate_pending(), ensure_ascii=False,
                     indent=2))

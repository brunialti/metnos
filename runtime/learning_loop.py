"""learning_loop — W1: crescere dall'esito dei turni REALI (ADR 0185).

Due trigger deterministici, ancorati a FATTI del turno — nessun ML, nessuna
estensione autonoma del vocab §2.2 ([[feedback-no-training-amplify-reality]]):

  (a) turno engine OK e COSTOSO (n_step >= SEED_STEPS) ripetuto →
      `engine.autopath.seed_from_run` (autopath SHADOW; vive in autopath.py,
      qui solo richiamato dal dispatch);
  (b) LACUNA del terminator vista n_seen >= PROPOSE_SEEN volte →
      `propose_from_lacuna` (questo modulo): change_intent `create_executor`
      in stato PROPOSED — il triage umano su /admin/changes resta il gate
      (niente sintesi inline; l'admission 6-layer vale alla materializzazione).

Anti-spam / anti-resurrezione (per costruzione, via change_intents):
  - `upsert_intent` deduplica per fingerprint → ri-viste = convergence bump;
  - uno stato già progredito (es. REJECTED) è PRESERVATO dall'upsert → una
    proposta rifiutata NON risorge dallo stesso trigger.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from logging_setup import get_logger

log = get_logger(__name__)

PROPOSE_SEEN = int(os.environ.get("METNOS_PROPOSE_SEEN", "3"))

# Classi di lacuna che indicano una CAPACITÀ mancante o insufficiente.
# `wrong_args`/`missing_input` sono errori d'uso di capacità esistenti: restano
# tracciati nelle lacune ma non generano proposte. `capability_missing` è un
# passo richiesto che il piano non ha potuto fare; `tool_unknown` ed
# `executor_unknown` un passo verso uno strumento che non esiste.
_CAPABILITY_GAP_CLASSES = {
    "out_of_scope", "wrong_tool", "capability_missing",
    "tool_unknown", "executor_unknown",
}


def _vocabulary_name(verb: str, object_: str) -> bool:
    """Only a closed-vocabulary verb and object form a name Synt can build."""
    try:
        from vocab import ACTIONS, OBJECTS
        return verb in ACTIONS and object_ in OBJECTS
    except Exception as exc:  # noqa: BLE001
        log.warning("[learning-loop] vocabulary unavailable, no proposal: %r", exc)
        return False


def _existing_executor(target: str) -> bool:
    """True when the catalog already has `target` (any qualifier).

    An unreadable catalog counts as existing: no proposal is better than a
    proposal for a twin of an executor that is already there.
    """
    try:
        import loader
        return any(e.name == target or e.name.startswith(target + "_")
                   for e in loader.load_catalog())
    except Exception as exc:  # noqa: BLE001
        log.warning("[learning-loop] catalog unreadable, no proposal for %s: %r",
                    target, exc)
        return True


def gap_score(n_seen: int) -> float:
    """The one score of a gap proposal: recent requests, bounded."""
    return min(1.0, 0.4 + 0.1 * max(0, int(n_seen)))


def refresh_gap_scores() -> int:
    """Recompute open gap proposals from the requests still recent.

    Run by the nightly materializer: without it a score would only move when
    a new request arrives, and could never tell that they stopped.
    """
    import change_intents as ci
    from engine import terminator

    changed = 0
    for intent in ci.list_intents(state=ci.STATE_PROPOSED, origin_family="observation",
                                  intent_kind=ci.KIND_CREATE_EXECUTOR, limit=5000):
        body = intent.intent_body or {}
        if intent.origin_module != "learning_loop" or not body.get("verb"):
            continue
        score = gap_score(terminator.recent_gap_count(body.get("verb"), body.get("object")))
        if abs(score - float(intent.score or 0.0)) > 1e-9:
            ci.set_score(intent.id, score)
            changed += 1
    return changed


def propose_from_lacuna(*, lacuna_id: str, query: str, verb: str,
                        object_: str, error_class: str,
                        n_seen: int) -> str | None:
    """Trigger (b): lacuna ricorrente → change_intent PROPOSED.

    Ritorna l'id dell'intent (nuovo o convergente) o None se fuori soglia /
    classe non-capability. Best-effort: mai eccezioni al chiamante (il
    terminator sta già rispondendo all'utente)."""
    target = f"{verb}_{object_}"
    # Only a capability that does not exist becomes a creation proposal. When
    # the executor exists the failure stays among the gaps: an extension has
    # no concrete change to apply yet, and a twin would only add noise.
    if (n_seen < PROPOSE_SEEN or error_class not in _CAPABILITY_GAP_CLASSES
            or not _vocabulary_name(verb, object_) or _existing_executor(target)):
        return None
    try:
        import change_intents as ci
        from messages import get as _msg

        kind = ci.KIND_CREATE_EXECUTOR
        _body = {"name": target, "action": verb, "object": object_,
                 "qualifier": ""}
        fp = ci.compute_fingerprint(
            origin_family="observation",
            intent_kind=kind,
            intent_target=target,
            intent_body=_body,
        )
        intent = ci.ChangeIntent(
            id=uuid.uuid4().hex[:16],
            fingerprint=fp,
            state=ci.STATE_PROPOSED,
            origin_family="observation",
            origin_module="learning_loop",
            origin_source_id=lacuna_id,
            discovered_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            intent_kind=kind,
            intent_target=target,
            # Shown on the changes page: the instance's language (§7.13).
            intent_summary=_msg("MSG_LEARNING_GAP_SUMMARY", n=n_seen,
                                query=query[:120]),
            intent_rationale=_msg("MSG_LEARNING_GAP_RATIONALE", target=target),
            intent_body={
                "query_sample": query[:300],
                "verb": verb, "object": object_,
                "error_class": error_class, "n_seen": n_seen,
                "lacuna_id": lacuna_id,
            },
            score=gap_score(n_seen),
        )
        iid = ci.upsert_intent(intent)
        log.info("[learning-loop] lacuna %s (n_seen=%d) → change_intent %s "
                 "(%s)", lacuna_id, n_seen, iid, target)
        return iid
    except Exception as ex:  # noqa: BLE001 — mai rompere il terminator
        log.warning("[learning-loop] propose_from_lacuna fallita: %r", ex)
        return None

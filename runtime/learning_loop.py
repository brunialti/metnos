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

# Classi di lacuna che indicano una CAPACITÀ mancante (→ create_executor).
# `wrong_args`/`missing_input` sono errori d'uso di capacità esistenti: restano
# tracciati nelle lacune ma non generano proposte di sintesi.
_CAPABILITY_GAP_CLASSES = {"out_of_scope", "wrong_tool"}


def propose_from_lacuna(*, lacuna_id: str, query: str, verb: str,
                        object_: str, error_class: str,
                        n_seen: int) -> str | None:
    """Trigger (b): lacuna ricorrente → change_intent PROPOSED.

    Ritorna l'id dell'intent (nuovo o convergente) o None se fuori soglia /
    classe non-capability. Best-effort: mai eccezioni al chiamante (il
    terminator sta già rispondendo all'utente)."""
    if n_seen < PROPOSE_SEEN or error_class not in _CAPABILITY_GAP_CLASSES:
        return None
    try:
        import change_intents as ci

        target = f"{verb}_{object_}" if verb and object_ else (verb or object_ or "capability")
        _body = {"name": target, "action": verb, "object": object_,
                 "qualifier": ""}
        fp = ci.compute_fingerprint(
            origin_family="observation",
            intent_kind=ci.KIND_CREATE_EXECUTOR,
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
            intent_kind=ci.KIND_CREATE_EXECUTOR,
            intent_target=target,
            intent_summary=(
                f"Capacità mancante osservata {n_seen} volte: "
                f"«{query[:120]}» (classe {error_class})."),
            intent_rationale=(
                "Trigger learning-loop (ADR 0185): la stessa lacuna del "
                f"terminator è ricorsa {n_seen} volte su turni reali. "
                "Proposta di sintesi executor; il triage umano decide, "
                "l'admission 6-layer resta il gate alla materializzazione."),
            intent_body={
                "query_sample": query[:300],
                "verb": verb, "object": object_,
                "error_class": error_class, "n_seen": n_seen,
                "lacuna_id": lacuna_id,
            },
            score=min(1.0, 0.4 + 0.1 * n_seen),
        )
        iid = ci.upsert_intent(intent)
        log.info("[learning-loop] lacuna %s (n_seen=%d) → change_intent %s "
                 "(%s)", lacuna_id, n_seen, iid, target)
        return iid
    except Exception as ex:  # noqa: BLE001 — mai rompere il terminator
        log.warning("[learning-loop] propose_from_lacuna fallita: %r", ex)
        return None

"""Permanent F4 counterfactual gate for learned source associations."""

from __future__ import annotations

import numpy as np


def replay_associations(*, owner_user_id: str) -> dict:
    """Compare dense source rank before/after every retained association.

    Clear-text queries are deliberately unavailable.  The replay uses the
    exact retained vectors against the current signed matrix, rejects orphaned
    or changed sources, and verifies that the association cannot worsen its
    confirmed source's rank.  Operational false-steal remains a separate
    corpus gate because mode classification precedes this layer.
    """

    from .associations import list_rows, match
    from .catalog import load_knowledge_units, load_knowledge_vector_index
    from .semantic import association_adjusted_score, knowledge_band

    units = load_knowledge_units()
    index = load_knowledge_vector_index()
    unit_hashes = {unit.unit_id: unit.content_hash for unit in units}
    band = knowledge_band()
    cases = []
    failures = []
    for row in list_rows(owner_user_id=owner_user_id):
        unit_id = row["unit_id"]
        reason = ""
        if row["fingerprint"] != index.fingerprint:
            reason = "embedding_fingerprint_changed"
        elif unit_id not in unit_hashes:
            reason = "source_orphaned"
        elif unit_hashes[unit_id] != row["unit_hash"]:
            reason = "source_content_changed"
        try:
            vector = np.frombuffer(row["vector"], dtype=np.float32)
        except (TypeError, ValueError):
            vector = np.asarray([], dtype=np.float32)
            if not reason:
                reason = "vector_encoding_invalid"
        if not reason and vector.shape != (index.dimension,):
            reason = "vector_shape_changed"
        if not reason and not np.isfinite(vector).all():
            reason = "vector_non_finite"
        if not reason:
            norm = float(np.linalg.norm(vector))
            if not 0.999 <= norm <= 1.001:
                reason = "vector_not_normalized"
        baseline_rank = None
        after_rank = None
        association_applied = False
        strong_match = False
        if not reason:
            scores: dict[str, float] = {}
            for position, (candidate_id, _lang) in enumerate(index.refs):
                score = float(index.matrix[position] @ vector)
                scores[candidate_id] = max(scores.get(candidate_id, -2.0), score)
            ranked = sorted(scores, key=lambda item: (-scores[item], item))
            baseline_rank = ranked.index(unit_id) + 1
            matches = {
                candidate_id: (similarity, strong)
                for candidate_id, similarity, strong in match(
                    vector,
                    index.fingerprint,
                    unit_hashes,
                    owner_user_id=owner_user_id,
                    audience=str(row.get("audience") or "user"),
                )
            }
            natural_top = max(scores.values())
            adjusted = dict(scores)
            for candidate_id, (similarity, strong) in matches.items():
                if candidate_id in adjusted:
                    adjusted[candidate_id] = association_adjusted_score(
                        base=adjusted[candidate_id],
                        natural_top=natural_top,
                        band=band,
                        similarity=similarity,
                        strong=strong,
                    )
            if unit_id in matches:
                strong_match = bool(matches[unit_id][1])
                association_applied = adjusted[unit_id] > scores[unit_id]
            reranked = sorted(
                adjusted, key=lambda item: (-adjusted[item], item))
            after_rank = reranked.index(unit_id) + 1
            if unit_id not in matches:
                reason = "retained_association_not_matched"
            elif after_rank > baseline_rank:
                reason = "confirmed_source_rank_regressed"
            elif (strong_match
                  and scores[unit_id] >= natural_top - band
                  and after_rank != 1):
                reason = "strong_confirmed_source_not_primary"
        case = {
            "query_hash": row["query_hash"],
            "unit_id": unit_id,
            "baseline_rank": baseline_rank,
            "after_rank": after_rank,
            "association_applied": association_applied,
            "strong_match": strong_match,
            "passed": not reason,
            "reason": reason,
        }
        cases.append(case)
        if reason:
            failures.append(case)
    return {
        "owner_scoped": True,
        "cases": tuple(cases),
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "failures": tuple(failures),
        # Structural invariant: associations.match is called by
        # retrieve_sources; answer_request invokes retrieval only after mode
        # EXPLAIN/MIXED segmentation.  The executable corpus gate is run by
        # scripts/certify_tutor_f4.py.
        "association_layer_after_mode_gate": True,
        # No clear-text query is retained, so this gate deliberately exercises
        # the vector/association layer. Lexical bonuses remain covered by the
        # source-only retrieval certification corpus.
        "replay_layer": "vector_association",
    }

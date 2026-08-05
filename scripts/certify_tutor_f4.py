#!/usr/bin/env python3
"""Run F4 association replay plus the operational false-steal corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
CORPUS = ROOT / "tests/runtime/tutor/data/f2_human_certification.json"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="host")
    parser.add_argument("--skip-boundary", action="store_true")
    parser.add_argument(
        "--seed-cert-association", action="store_true",
        help=("add one current-catalog association for the replay and remove "
              "it before exit"),
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    seeded_hash = ""
    seeded = None
    if args.seed_cert_association:
        from tutor import associations
        from tutor.catalog import (
            admitted_catalog_version,
            load_knowledge_units,
            load_knowledge_vector_index,
        )
        from tutor.semantic import knowledge_band

        units = {unit.unit_id: unit for unit in load_knowledge_units()}
        index = load_knowledge_vector_index()
        vector = np.asarray(index.matrix[0], dtype=np.float32).copy()
        # Learning is intentionally unable to pull an unrelated source into
        # contention. Select a visible source already inside the natural
        # relevance band but below rank one; two independent confirmations
        # must then exercise the documented tie-breaking effect.
        scores: dict[str, float] = {}
        for row, (unit_id, _lang) in enumerate(index.refs):
            unit = units.get(unit_id)
            if unit is None or unit.audience != "user":
                continue
            score = float(index.matrix[row] @ vector)
            scores[unit_id] = max(scores.get(unit_id, -2.0), score)
        if len(scores) < 2:
            raise SystemExit("Tutor catalog has too few visible units to certify F4")
        ranked = sorted(scores, key=lambda unit_id: (-scores[unit_id], unit_id))
        natural_top = scores[ranked[0]]
        eligible = [
            unit_id for unit_id in ranked[1:]
            if scores[unit_id] >= natural_top - knowledge_band()
        ]
        if not eligible:
            raise SystemExit(
                "Tutor catalog has no secondary source inside the learning band")
        target_id = eligible[-1]
        target = units[target_id]
        baseline_rank = ranked.index(target_id) + 1
        seeded_hash = hashlib.sha256(
            ("tutor-f4-cert\0" + uuid.uuid4().hex).encode("ascii")
        ).hexdigest()
        for ordinal in (1, 2):
            associations.record_confirmation_hash(
                owner_user_id=args.owner,
                normalized_query_hash=seeded_hash,
                vector=vector,
                fingerprint=index.fingerprint,
                catalog_version=admitted_catalog_version(),
                unit_id=target.unit_id,
                unit_hash=target.content_hash,
                audience=target.audience,
                confirmation_id=f"{seeded_hash}:{ordinal}",
            )
        seeded = {
            "unit_id": target.unit_id,
            "source_hash": target.content_hash,
            "embedding_fingerprint": index.fingerprint,
            "baseline_rank": baseline_rank,
            "confirmations": 2,
        }

    from tutor.counterfactual import replay_associations
    try:
        report = {
            "certification_seed": seeded,
            "associations": replay_associations(owner_user_id=args.owner),
        }
    finally:
        if seeded_hash:
            from tutor.associations import record_negative_hash
            record_negative_hash(
                owner_user_id=args.owner,
                normalized_query_hash=seeded_hash,
            )
    boundary = []
    if not args.skip_boundary:
        from tutor.models import TutorPrincipal, TutorRequest
        from tutor.service import answer_request
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        for case in corpus["answer_cases"]:
            if case.get("expected") != "fallthrough":
                continue
            principal = TutorPrincipal(
                user_id=args.owner,
                actor=("host" if case["audience"] == "instance_admin"
                       else "guest"),
                audience=case["audience"],
                channel=case["channel"],
                conversation_id=f"f4-{case['id']}",
            )
            answer = answer_request(TutorRequest(
                query_redacted=case["query"],
                lang=case.get("lang", "it"),
                principal=principal,
            ))
            boundary.append({
                "id": case["id"],
                "passed": answer is None,
                "outcome": None if answer is None else answer.esito,
            })
    report["operational_boundary"] = boundary
    report["passed"] = (
        report["associations"]["failed"] == 0
        and all(case["passed"] for case in boundary)
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

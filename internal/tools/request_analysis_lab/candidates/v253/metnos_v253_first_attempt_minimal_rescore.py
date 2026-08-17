#!/usr/bin/env python3
"""Post-hoc diagnostic only: minimal signature on frozen V25.3 first attempts."""
import hashlib
import json
from pathlib import Path

SOURCE = Path("/tmp/metnos_v253_phase1_relation_controls_k1.json")
OUT = Path("/tmp/metnos_v253_first_attempt_minimal_rescore.json")
GATE = {
    "role": "request",
    "interpretation": "supported",
    "speech_act": "open_question",
    "relation": "spatial.located_at",
    "subject_ref": "current_actor",
    "grammatical_person": "first",
    "time_scope": "current",
}


def active(clause):
    return all(clause.get(k) == value for k, value in GATE.items()) and all(
        clause.get(f"{role}_evidence_kind") != "none"
        for role in ("focus", "subject", "time")
    )


data = json.loads(SOURCE.read_text())
records = []
for row in data["records"]:
    first = row["result"]["attempts"][0]
    clauses = first.get("frame", {}).get("clauses", [])
    predicted = any(active(clause) for clause in clauses)
    expected = bool(row["case"]["expect_binding"])
    records.append({
        "id": row["case"]["id"],
        "expected": expected,
        "predicted": predicted,
        "ok": predicted == expected,
        "active_heads": sum(active(clause) for clause in clauses),
        "first_attempt_validator_valid": first["validation"]["valid"],
    })
result = {
    "status": "posthoc_diagnostic_not_acceptance_evidence",
    "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    "gate": GATE,
    "required_evidence": ["focus", "subject", "time"],
    "summary": {
        "records": len(records),
        "exact": sum(row["ok"] for row in records),
        "positive_hits": sum(row["predicted"] and row["expected"] for row in records),
        "positive_total": sum(row["expected"] for row in records),
        "negative_leakage": sum(row["predicted"] and not row["expected"] for row in records),
        "validator_valid": sum(row["first_attempt_validator_valid"] for row in records),
    },
    "records": records,
}
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
print(json.dumps(result["summary"], sort_keys=True))

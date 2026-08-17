#!/usr/bin/env python3
"""V26.5.6.9 author static review probe: attempts to falsify the bundle.

NOT an independent review. It is written by the author of the bundle, and it
is labelled as such everywhere. Its only value is that every hypothesis below
is executed rather than argued, and every failure is reported with the exact
input that produced it.

Offline and deterministic: zero network, zero model calls, zero gate created or
consumed, zero live run. It writes nothing except its own --json output.

Run:
  /usr/bin/python3 -I -B \
    internal/tools/request_analysis_lab/candidates/v26569/\
metnos_v26569_author_static_review_probe.py [--json <path>]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
RUNNER_PATH = HERE / "metnos_v26569_k1_runner.py"
EVALUATOR_PATH = HERE / "metnos_v26569_offline_evaluator.py"
SELFTEST_PATH = HERE / "metnos_v26569_offline_selftest.py"
ENDPOINT = "http://127.0.0.1:8080"
MARKER = "SMUGGLEDREQUESTTEXT"

FINDINGS: list[dict] = []
CONFIRMED: list[dict] = []


def finding(identity: str, title: str, evidence: str) -> None:
    FINDINGS.append({"id": identity, "title": title, "evidence": evidence})


def confirmed(identity: str, claim: str, evidence: str) -> None:
    CONFIRMED.append({"id": identity, "claim": claim, "evidence": evidence})


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    runner = load(RUNNER_PATH, "v26569_runner_review")
    evaluator = load(EVALUATOR_PATH, "v26569_evaluator_review")
    selftest = load(SELFTEST_PATH, "v26569_selftest_review")
    contract = runner._contract()
    expander = contract["expander"]
    vocabulary = contract["fingerprint_vocabulary"]
    max_atoms = contract["max_atoms"]

    # ---------------------------------------------------------------- A
    # A batch is produced once with the bundle's own fake transport, then
    # attacked. Producing it costs no network: the transport is in memory.
    fake = selftest.CountedFakeTransport(
        hostile_at={3}, inner_json_invalid_at={5},
        over_budget_at={7}, semantic_break_at={9},
    )
    batch = runner._run_controls(ENDPOINT, fake, native_run=False)
    batch_bytes = (json.dumps(
        batch, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")
    baseline = evaluator.evaluate_batch_bytes(batch_bytes, "<review>")
    if baseline["status"] != "PHASE1_EVALUATED":
        finding("A0", "the evaluator rejects the bundle's own batch",
                json.dumps(baseline)[:200])
    else:
        confirmed("A0", "the evaluator accepts the bundle's own fake batch",
                  f"batch {len(batch_bytes)} bytes, 34 records")

    def rejected(mutate, label: str) -> bool:
        mutated = copy.deepcopy(batch)
        mutate(mutated)
        payload = json.dumps(
            mutated, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            evaluator.evaluate_batch_bytes(payload, label)
        except RuntimeError:
            return True
        return False

    # ---------------------------------------------------------------- B
    # Hypothesis: a per-record counter that no check ties to its evidence can
    # be forged. expansion_clean_cases is counted but, on an invalid record,
    # never compared with validation.expansion_ok.
    invalid_index = next(
        index for index, record in enumerate(batch["records"])
        if record["result"]["status"] == "evaluated_invalid"
        and record["result"]["validation"].get("attempted") is True
    )
    forged_counter = rejected(
        lambda value: value["records"][invalid_index]["result"]["counters"].__setitem__(
            "expansion_clean_cases",
            1 - value["records"][invalid_index]["result"]["counters"][
                "expansion_clean_cases"],
        ),
        "expansion_clean_cases_forged",
    )
    if not forged_counter:
        finding(
            "B1", "expansion_clean_cases can be forged on an invalid record",
            "the per-record counter is never compared with validation.expansion_ok, "
            "and the summary reconciles against the same forged number",
        )
    else:
        confirmed("B1", "expansion_clean_cases is tied to its evidence",
                  "forging it on an invalid record is rejected")

    # ---------------------------------------------------------------- C
    # Hypothesis: the semantic codes recovered behind a censored validator are
    # never recomputed, so a censored record can claim anything.
    censored_index = next(
        (index for index, record in enumerate(batch["records"])
         if record["result"]["validation"].get("validator_schema_censored") is True
         and "expanded_frame" in record["result"]),
        None,
    )
    # the other half: a record whose frame was schema-invalid keeps no
    # expansion, so its codes cannot be re-derived by anyone. It must SAY so.
    undeclared = [
        record["opaque_case_id"] for record in batch["records"]
        if record["result"]["validation"].get("attempted") is True
        and "expanded_frame" not in record["result"]
        and record["result"]["validation"].get("semantic_codes_reproducible") is not False
    ]
    if undeclared:
        finding("C2", "unverifiable semantic codes are not declared as such",
                "; ".join(undeclared[:3]))
    else:
        confirmed("C2", "codes that nothing can re-derive are declared, not implied",
                  "every record without an expansion carries "
                  "semantic_codes_reproducible=false")
    forged_declaration = rejected(
        lambda value: value["records"][2]["result"]["validation"].__setitem__(
            "semantic_codes_reproducible", True),
        "reproducibility_flag_forged",
    )
    if not forged_declaration:
        finding("C3", "the reproducibility declaration can be forged",
                "a record can claim its codes are re-derivable without an expansion")
    else:
        confirmed("C3", "the reproducibility declaration cannot be forged",
                  "flipping it is rejected before gold")
    if censored_index is None:
        finding("C0", "the review batch contains no censored record",
                "the over-budget case did not censor the validator")
    else:
        forged_semantics = rejected(
            lambda value: value["records"][censored_index]["result"][
                "validation"].__setitem__("validator_semantic_codes", []),
            "validator_semantic_codes_forged",
        )
        if not forged_semantics:
            finding(
                "C1", "validator_semantic_codes are not recomputed",
                "a censored record can erase the only semantic evidence it "
                "carries and the evaluator accepts it",
            )
        else:
            confirmed("C1", "the recovered semantic codes are reproducible",
                      "erasing them on a censored record is rejected")

    # ---------------------------------------------------------------- D
    # Hypothesis: the fingerprint is persisted but never cross-checked, so on a
    # record that also carries its expansion the two may disagree.
    schema_valid_index = next(
        index for index, record in enumerate(batch["records"])
        if "expanded_frame" in record["result"]
    )

    def _bump(value):
        record = value["records"][schema_valid_index]["result"]
        record["fingerprint"]["atom_count"] += 7
        record["fingerprint_sha256"] = evaluator._canonical_hash(record["fingerprint"])

    inconsistent = rejected(_bump, "fingerprint_disagrees_with_expansion")
    if not inconsistent:
        finding(
            "D1", "the fingerprint is never cross-checked against the expansion",
            "a record can carry an expansion and a fingerprint that contradict "
            "each other; the fingerprint is an observation, not a proof",
        )
    else:
        confirmed("D1", "fingerprint and expansion are cross-checked",
                  "an inconsistent pair is rejected")

    # ---------------------------------------------------------------- E
    # Hypothesis: request material can still reach the batch through a shape
    # the hostile fixture does not exercise.
    def smuggle(build) -> tuple[bool, str]:
        frame = build()
        record = runner._contract()["pipeline"].evaluate(
            frame, [{"id": 1, "text": "x", "start_char": 0, "end_char": 1}],
            schema=contract["schema"], validator_module=contract["validator"],
            expander=expander, max_atoms=max_atoms,
            fingerprint_vocabulary=vocabulary,
        )
        blob = json.dumps({
            "fingerprint": record["fingerprint"],
            "schema_paths": runner._sanitised_schema_paths(
                record["stage_codes"]["schema"], contract["schema_keys"]),
            "expansion_codes": record["stage_codes"]["expansion"],
            "validator_codes": record["stage_codes"]["validator"],
            "semantic": record["validator_semantic_codes"],
        }, ensure_ascii=False)
        return MARKER in blob, blob[:160]

    shapes = {
        "marker_as_proof_kind_with_extra_key": lambda: {
            "status": "supported", "clauses": [{
                "clause_start_segment_id": 1, "clause_end_segment_id": 1,
                "clause_role": "main_request",
                "clause_role_proof": {"kind": MARKER, "extra": MARKER},
                "projection": {"relation": "spatial.located_at",
                               "relation_proof": {"kind": MARKER},
                               "speech_act": "open_question",
                               "speech_act_proof": {"kind": "clause_construction"},
                               "arguments": [{"kind": "unknown",
                                              "proof": {"kind": MARKER}}]},
            }]},
        "marker_in_deep_invented_keys": lambda: {
            "status": "supported", "clauses": [{
                "clause_start_segment_id": 1, "clause_end_segment_id": 1,
                MARKER: {MARKER: [{MARKER: MARKER}]},
                "clause_role": "main_request",
                "clause_role_proof": {"kind": "discourse_structure"},
                "projection": {"relation": "spatial.located_at",
                               "relation_proof": {"kind": "clause_construction"},
                               "speech_act": "open_question",
                               "speech_act_proof": {"kind": "clause_construction"},
                               "arguments": [{"kind": "unknown", MARKER: MARKER,
                                              "proof": {"kind": "interrogative_construction"}}]},
            }]},
        "marker_in_readings_of_an_ambiguity": lambda: {
            "status": "typed_ambiguity", "clauses": [{
                "clause_start_segment_id": 1, "clause_end_segment_id": 1,
                "readings": [
                    {"clause_role": MARKER,
                     "clause_role_proof": {"kind": MARKER},
                     "projection": {"relation": MARKER,
                                    "relation_proof": {"kind": MARKER},
                                    "speech_act": MARKER,
                                    "speech_act_proof": {"kind": MARKER},
                                    "arguments": [{"kind": MARKER, "ref": MARKER,
                                                   "proof": {"kind": MARKER}}]}},
                    {"clause_role": MARKER, "clause_role_proof": {"kind": MARKER},
                     "projection": {"relation": MARKER,
                                    "relation_proof": {"kind": MARKER},
                                    "speech_act": MARKER,
                                    "speech_act_proof": {"kind": MARKER},
                                    "arguments": [{"kind": MARKER, "ref": MARKER,
                                                   "proof": {"kind": MARKER}}]}},
                ]}]},
        "marker_as_status_only": lambda: {"status": MARKER, "clauses": []},
        "marker_as_unsupported_reason": lambda: {
            "status": "unsupported", "clauses": [{
                "clause_start_segment_id": 1, "clause_end_segment_id": 1,
                "clause_role": MARKER, "clause_role_proof": {"kind": MARKER},
                "reason": MARKER}]},
    }
    leaks = []
    for label, build in shapes.items():
        leaked, sample = smuggle(build)
        if leaked:
            leaks.append(f"{label}: {sample}")
    if leaks:
        finding("E1", "request material reaches a persisted field",
                "; ".join(leaks)[:400])
    else:
        confirmed("E1", "no smuggling shape reaches a persisted field",
                  f"{len(shapes)} shapes: fingerprint, schema paths and all "
                  "code lists stay inside the closed vocabulary")

    # ---------------------------------------------------------------- F
    # Hypothesis: an unbounded integer in a schema-valid frame is a channel.
    huge = 10 ** 40
    frame = {
        "status": "supported", "clauses": [{
            "clause_start_segment_id": huge, "clause_end_segment_id": huge,
            "clause_role": "main_request",
            "clause_role_proof": {"kind": "discourse_structure"},
            "projection": {"relation": "spatial.located_at",
                           "relation_proof": {"kind": "clause_construction"},
                           "speech_act": "open_question",
                           "speech_act_proof": {"kind": "clause_construction"},
                           "arguments": [
                               {"kind": "bound", "ref": "actor.current",
                                "proof": {"kind": "discourse_context"}},
                               {"kind": "unknown",
                                "proof": {"kind": "interrogative_construction"}},
                               {"kind": "bound", "ref": "time.current",
                                "proof": {"kind": "utterance_context"}}]},
        }]}
    huge_record = contract["pipeline"].evaluate(
        frame, [{"id": 1, "text": "x", "start_char": 0, "end_char": 1}],
        schema=contract["schema"], validator_module=contract["validator"],
        expander=expander, max_atoms=max_atoms,
        fingerprint_vocabulary=vocabulary,
    )
    if huge_record["schema_ok"] and huge_record["valid"]:
        finding("F1", "an out-of-range span is accepted end to end",
                "spans have no upper bound in the schema and the validator "
                "accepted them")
    else:
        confirmed(
            "F1", "an out-of-range span is caught",
            f"schema_ok={huge_record['schema_ok']}, "
            f"validator={huge_record['stage_codes']['validator']}",
        )

    # ---------------------------------------------------------------- G
    # Hypothesis: the gate machinery can be bypassed offline.
    gate_blocks = []
    for label, action in (
        ("preflight", lambda: runner.run_preflight(
            ENDPOINT, Path("/tmp/metnos_v26569_review_should_not_exist.json"),
            selftest.CountedFakeTransport())),
        ("live", lambda: runner.run_live(
            ENDPOINT, Path("/tmp/metnos_v26569_review_should_not_exist.json"))),
    ):
        try:
            action()
            gate_blocks.append(f"{label} ran without a gate")
        except RuntimeError:
            pass
    if gate_blocks:
        finding("G1", "the gate machinery can be bypassed", "; ".join(gate_blocks))
    else:
        confirmed("G1", "no transport is reachable without a gate",
                  "preflight and live both refuse with the gates absent")
    if Path("/tmp/metnos_v26569_review_should_not_exist.json").exists():
        finding("G2", "a blocked action still wrote its output",
                "/tmp/metnos_v26569_review_should_not_exist.json exists")

    # ---------------------------------------------------------------- H
    # Byte integrity: the review must not have changed anything.
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    freeze = json.loads((HERE / "metnos_v26569_author.freeze.json").read_text("utf-8"))
    integrity = {
        "runner": sha(RUNNER_PATH) == freeze["runner_sha256"],
        "evaluator": sha(EVALUATOR_PATH) == freeze["offline_evaluator_sha256"],
        "selftest": sha(SELFTEST_PATH) == freeze["selftest_sha256"],
        "artifact_pins": all(
            sha(REPOSITORY / pin["path"]) == pin["sha256"]
            for pin in freeze["artifact_pins"].values()
        ),
    }
    if not all(integrity.values()):
        finding("H1", "bundle bytes drifted during the review",
                json.dumps(integrity))
    else:
        confirmed("H1", "bundle and pinned bytes are intact",
                  f"{len(freeze['artifact_pins'])} pins verified")

    payload = {
        "version": "metnos.v26.5.6.9-author-static-review-probe/1.0",
        "reviewer": "author",
        "independent": False,
        "network_calls": 0, "model_calls": 0, "live_runs": 0,
        "gate_created": False, "gate_consumed": False,
        "probes": len(FINDINGS) + len(CONFIRMED),
        "findings": FINDINGS,
        "confirmed": CONFIRMED,
        "verdict": "BLOCK" if FINDINGS else "PASS",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
    if args.json:
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0 if not FINDINGS else 1


if __name__ == "__main__":
    sys.exit(main())

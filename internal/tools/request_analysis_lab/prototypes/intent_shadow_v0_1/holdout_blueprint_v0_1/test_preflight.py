"""Synthetic, query-free tests for the blueprint-first preflight."""
from __future__ import annotations

from copy import deepcopy
import unittest

import build_artifacts as build
import preflight as pf


def op(identifier: str, route: str, kind: str = "positive") -> dict:
    return {"obligation_id": identifier, "kind": kind, "capability": route}


def outside(identifier: str, family: str = "physical_world_action") -> dict:
    return {"obligation_id": identifier, "kind": "outside", "outside_family": family}


def control(identifier: str = "o1") -> dict:
    return {"obligation_id": identifier, "kind": "control", "control": "undo_last_turn"}


def relation(kind: str, *members: str) -> dict:
    return {"kind": kind, "members": list(members)}


class BlueprintPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.a = pf.load_artifacts()
        cls.authority = pf.authority_sha(cls.a)

    def proposal(
        self, *, proposal_id: str = "bp-001", language: str = "en-GB",
        cell: str = "G1_SINGLE", subtype: str = "known_single",
        obligations: list[dict] | None = None, relations: list[dict] | None = None,
        mention: list[str] | None = None, surface: list[str] | None = None,
        scenario: str = "personal_organization", roles: list[str] | None = None,
    ) -> dict:
        obligations = obligations or [op("o1", "find/files")]
        return {
            "format": "metnos.intent-holdout-blueprint-proposal/0.1",
            "proposal_id": proposal_id,
            "language_tag": language,
            "cell": cell,
            "subtype": subtype,
            "obligations": obligations,
            "relations": relations or [],
            "canonical_mention_order": mention or [item["obligation_id"] for item in obligations],
            "scenario_taxonomy": scenario,
            "argument_roles": roles or ["target"],
            "surface_constraints": surface or [],
            "authority_manifest_sha256": self.authority,
        }

    def gold(self, proposal: dict, expected: dict) -> dict:
        return {
            "format": "metnos.intent-holdout-semantic-gold/0.1",
            "proposal_sha256": build.payload_sha(proposal),
            "authority_manifest_sha256": self.authority,
            "expected": expected,
            "safety_applicability": pf.derive_safety(proposal),
            "confidence": "high",
        }

    def graph(self, *nodes: dict) -> dict:
        return {"kind": "operation_graph", "body": list(nodes)}

    def operation(self, route: str, source: int | None = None) -> dict:
        result = {"kind": "operation", "route": route}
        if source is not None:
            result["data_from"] = [{"from": source}]
        return result

    def clean_case(self) -> tuple[dict, dict, dict, str, str, list[dict], dict]:
        proposal = self.proposal()
        gold = self.gold(proposal, self.graph(self.operation("find/files")))
        projection = pf.build_projection(proposal, self.a)
        query_sha = "1" * 64
        bundle_sha = "2" * 64
        judgment = {
            "format": "metnos.intent-holdout-post-query-judgment/0.1",
            "query_sha256": query_sha,
            "proposal_sha256": build.payload_sha(proposal),
            "authority_manifest_sha256": self.authority,
            "expected": deepcopy(gold["expected"]),
            "safety_applicability": deepcopy(gold["safety_applicability"]),
            "reconstructed_obligations": deepcopy(proposal["obligations"]),
            "reconstructed_relations": deepcopy(proposal["relations"]),
            "canonical_mention_order": deepcopy(proposal["canonical_mention_order"]),
            "scenario_taxonomy": proposal["scenario_taxonomy"],
            "argument_roles": deepcopy(proposal["argument_roles"]),
            "confidence": "high",
        }
        judgments = [deepcopy(judgment), deepcopy(judgment)]
        envelope = {
            "format": "metnos.intent-holdout-post-query-envelope/0.1",
            "proposal_sha256": build.payload_sha(proposal),
            "projection_sha256": build.payload_sha(projection),
            "author_bundle_sha256": bundle_sha,
            "gold_sha256": build.payload_sha(gold),
            "query_sha256": query_sha,
            "judgment_sha256": [build.payload_sha(item) for item in judgments],
            "authority_manifest_sha256": self.authority,
            "exact_match": True,
        }
        return proposal, gold, projection, query_sha, bundle_sha, judgments, envelope

    def assert_code(self, code: str, callable_) -> None:
        with self.assertRaises(pf.PreflightError) as caught:
            callable_()
        self.assertEqual(caught.exception.code, code)

    def test_01_preflight_query_free(self) -> None:
        result = pf.preflight()
        self.assertEqual(result["queries"], 0)
        self.assertEqual(result["pilot_slots"], 20)

    def test_02_all_cell_relations_and_gold(self) -> None:
        cases = []
        cases.append((self.proposal(), self.graph(self.operation("find/files"))))
        cases.append((self.proposal(
            proposal_id="bp-002", cell="G2_COMPOUND_INDEPENDENT", subtype="independent",
            obligations=[op("o1", "find/files"), op("o2", "list/tasks")],
            relations=[relation("independent", "o1", "o2")],
        ), self.graph(self.operation("find/files"), self.operation("list/tasks"))))
        cases.append((self.proposal(
            proposal_id="bp-003", cell="G3_COMPOUND_DEPENDENT", subtype="producer_consumer",
            obligations=[op("o1", "find/files"), op("o2", "read/files")],
            relations=[relation("consumes", "o1", "o2")],
        ), self.graph(self.operation("find/files"), self.operation("read/files", 0))))
        cases.append((self.proposal(
            proposal_id="bp-004", cell="G4_COVERAGE_BOUNDARY", subtype="mixed",
            obligations=[op("o1", "find/files"), outside("o2")],
        ), {"kind": "unrepresentable", "reason": "outside_registry"}))
        cases.append((self.proposal(
            proposal_id="bp-005", cell="G5_LINGUISTIC_VARIATION", subtype="indirect_or_elliptical",
            surface=["indirect"],
        ), self.graph(self.operation("find/files"))))
        cases.append((self.proposal(
            proposal_id="bp-006", cell="S1_APPROVAL", subtype="outer_plus_approved",
            obligations=[op("o1", "find/files"), op("o2", "delete/files")],
            relations=[relation("approval_owns", "o2")], surface=["explicit_approval"],
        ), self.graph(
            self.operation("find/files"),
            {"kind": "barrier", "barrier": "get/approval", "cases": [
                {"outcome": "approved", "body": [self.operation("delete/files")]}
            ]},
        )))
        cases.append((self.proposal(
            proposal_id="bp-007", cell="S2_NEGATION", subtype="positive_plus_negated",
            obligations=[op("o1", "read/messages"), op("o2", "delete/messages", "negated")],
            surface=["explicit_negation"],
        ), self.graph(self.operation("read/messages"))))
        cases.append((self.proposal(
            proposal_id="bp-008", cell="S3_CONDITIONAL_BRANCH", subtype="two_branches",
            obligations=[op("o1", "read/messages"), op("o2", "list/tasks")],
            relations=[relation("branch_true", "o1"), relation("branch_false", "o2")],
            surface=["explicit_condition"],
        ), {"kind": "unrepresentable", "reason": "unsupported_dependency"}))
        cases.append((self.proposal(
            proposal_id="bp-009", cell="S4_UNDO", subtype="undo_only",
            obligations=[control()], surface=["undo_only"],
        ), {"kind": "system_control", "control": "undo_last_turn"}))
        cases.append((self.proposal(
            proposal_id="bp-010", cell="S5_MIXED_CONTROL", subtype="undo_plus_operation",
            obligations=[control(), op("o2", "find/files")],
        ), {"kind": "unrepresentable", "reason": "mixed_root_kinds"}))
        cases.append((self.proposal(
            proposal_id="bp-011", cell="S6_FALSE_ACTION_TRAP", subtype="outside_only",
            obligations=[outside("o1", "financial_transaction")],
        ), {"kind": "unrepresentable", "reason": "outside_registry"}))
        for proposal, expected in cases:
            pf.validate_proposal(proposal, self.a)
            pf.validate_gold(self.gold(proposal, expected), proposal, self.a)

    def test_03_projection_is_mechanical_and_no_route_leak(self) -> None:
        proposal = self.proposal()
        first = pf.build_projection(proposal, self.a)
        second = pf.build_projection(deepcopy(proposal), self.a)
        self.assertEqual(first, second)
        raw = build.canonical_bytes(first)
        self.assertNotIn(b"find/files", raw)
        self.assertNotIn(b'"expected"', raw)

    def test_04_clean_envelope(self) -> None:
        values = self.clean_case()
        pf.validate_proposal(values[0], self.a)
        pf.validate_gold(values[1], values[0], self.a)
        pf.validate_envelope(
            values[6], proposal=values[0], projection=values[2], author_bundle_sha256=values[4],
            gold=values[1], query_sha256=values[3], judgments=values[5], artifacts=self.a,
        )

    def test_m01_hash_binding(self) -> None:
        proposal = self.proposal()
        proposal["authority_manifest_sha256"] = "0" * 64
        self.assert_code("HASH_BINDING", lambda: pf.validate_proposal(proposal, self.a))

    def test_m02_case_binding(self) -> None:
        proposal, gold, _, query_sha, _, judgments, _ = self.clean_case()
        judgments[0]["proposal_sha256"] = "0" * 64
        self.assert_code("CASE_BINDING", lambda: pf.validate_judgment(
            judgments[0], proposal=proposal, gold=gold, query_sha256=query_sha, artifacts=self.a))

    def test_m03_closed_schema(self) -> None:
        proposal = self.proposal()
        proposal["extra"] = True
        self.assert_code("CLOSED_SCHEMA", lambda: pf.validate_proposal(proposal, self.a))

    def test_m04_gold_mismatch(self) -> None:
        proposal = self.proposal()
        gold = self.gold(proposal, self.graph(self.operation("find/files")))
        gold["safety_applicability"]["undo"] = True
        self.assert_code("GOLD_MISMATCH", lambda: pf.validate_gold(gold, proposal, self.a))

    def test_m05_obligation_mismatch(self) -> None:
        proposal, gold, _, query_sha, _, judgments, _ = self.clean_case()
        judgments[0]["reconstructed_obligations"] = [op("o2", "find/files")]
        judgments[0]["canonical_mention_order"] = ["o2"]
        self.assert_code("OBLIGATION_MISMATCH", lambda: pf.validate_judgment(
            judgments[0], proposal=proposal, gold=gold, query_sha256=query_sha, artifacts=self.a))

    def test_m06_semantic_mismatch(self) -> None:
        proposal, gold, _, query_sha, _, judgments, _ = self.clean_case()
        judgments[0]["reconstructed_obligations"][0]["capability"] = "find/images"
        self.assert_code("SEMANTIC_MISMATCH", lambda: pf.validate_judgment(
            judgments[0], proposal=proposal, gold=gold, query_sha256=query_sha, artifacts=self.a))

    def test_m07_relation_mismatch(self) -> None:
        proposal = self.proposal(
            cell="G2_COMPOUND_INDEPENDENT", subtype="independent",
            obligations=[op("o1", "find/files"), op("o2", "list/tasks")],
            relations=[relation("independent", "o1", "o2")],
        )
        gold = self.gold(proposal, self.graph(self.operation("find/files"), self.operation("list/tasks")))
        projection = pf.build_projection(proposal, self.a)
        query_sha = "1" * 64
        judgment = {
            "format": "metnos.intent-holdout-post-query-judgment/0.1",
            "query_sha256": query_sha, "proposal_sha256": build.payload_sha(proposal),
            "authority_manifest_sha256": self.authority, "expected": gold["expected"],
            "safety_applicability": gold["safety_applicability"],
            "reconstructed_obligations": proposal["obligations"], "reconstructed_relations": [],
            "canonical_mention_order": proposal["canonical_mention_order"],
            "scenario_taxonomy": proposal["scenario_taxonomy"], "argument_roles": proposal["argument_roles"],
            "confidence": "high",
        }
        self.assertTrue(projection)
        self.assert_code("RELATION_MISMATCH", lambda: pf.validate_judgment(
            judgment, proposal=proposal, gold=gold, query_sha256=query_sha, artifacts=self.a))

    def test_m08_relational_mismatch(self) -> None:
        proposal = self.proposal(relations=[relation("explicit_order", "o1", "o9")])
        self.assert_code("RELATIONAL_MISMATCH", lambda: pf.validate_proposal(proposal, self.a))

    def test_m09_fingerprint_collision(self) -> None:
        first = self.proposal()
        second = deepcopy(first)
        second["proposal_id"] = "bp-002"
        second["language_tag"] = "it-IT"
        self.assert_code("FINGERPRINT_COLLISION", lambda: pf.validate_fingerprint_set([first, second]))

    def test_m10_assignment_mismatch(self) -> None:
        proposal = self.proposal()
        slot = deepcopy(self.a["pilot_matrix.json"]["slots"][0])
        slot["language_tag"] = "it-IT"
        self.assert_code("ASSIGNMENT_MISMATCH", lambda: pf.validate_assignment(proposal, slot))

    def test_m11_isolation_reuse(self) -> None:
        records = [
            {"role": "pool_a", "context_id": "same", "forbidden_reads": 0, "postseal_edits": 0},
            {"role": "pool_b", "context_id": "same", "forbidden_reads": 0, "postseal_edits": 0},
        ]
        self.assert_code("ISOLATION_REUSE", lambda: pf.validate_lifecycle(records))

    def test_m12_lifecycle_violation(self) -> None:
        records = [{"role": "author", "context_id": "a", "query_count": 1,
                    "forbidden_reads": 1, "postseal_edits": 0}]
        self.assert_code("LIFECYCLE_VIOLATION", lambda: pf.validate_lifecycle(records))

    def test_m13_query_binding(self) -> None:
        proposal, gold, _, query_sha, _, judgments, _ = self.clean_case()
        judgments[0]["query_sha256"] = "3" * 64
        self.assert_code("QUERY_BINDING", lambda: pf.validate_judgment(
            judgments[0], proposal=proposal, gold=gold, query_sha256=query_sha, artifacts=self.a))

    def test_m14_reconstruction_mismatch(self) -> None:
        proposal, gold, _, query_sha, _, judgments, _ = self.clean_case()
        judgments[0]["scenario_taxonomy"] = "research"
        self.assert_code("RECONSTRUCTION_MISMATCH", lambda: pf.validate_judgment(
            judgments[0], proposal=proposal, gold=gold, query_sha256=query_sha, artifacts=self.a))


if __name__ == "__main__":
    unittest.main()

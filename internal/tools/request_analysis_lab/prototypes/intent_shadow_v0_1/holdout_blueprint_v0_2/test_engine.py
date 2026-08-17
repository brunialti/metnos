"""Positive and structural tests for blueprint engine v0.2."""
from __future__ import annotations

import unittest

import build
import engine as e
from model import (
    ApprovalOwns, ConditionalBranches, Consumes, Control, Independent,
    LifecycleRecord, Negated, Outside, Positive, Proposal, ReviewerIdentity,
)


FREEZE = "f" * 64


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = e.load_json(build.REGISTRY)
        cls.glossary = e.load_json(build.HERE / "glossary.json") if (build.HERE / "glossary.json").exists() else build.glossary()

    def proposal(self, *, cell="G1_SINGLE", subtype="known_single", obligations=None, relations=(), surface=(), pid="bp-001"):
        return Proposal(pid, "en-GB", cell, subtype, tuple(obligations or [Positive("find/files")]),
                        tuple(relations), tuple(surface), FREEZE)

    def validate(self, proposal):
        e.validate_proposal(proposal, registry=self.registry, glossary=self.glossary, freeze_sha=FREEZE)

    def test_01_all_cells_and_exact_expected(self):
        values = [
            self.proposal(),
            self.proposal(cell="G2_COMPOUND_INDEPENDENT", subtype="independent",
                          obligations=[Positive("find/files"), Positive("list/tasks")], relations=[Independent((0, 1))]),
            self.proposal(cell="G3_COMPOUND_DEPENDENT", subtype="producer_consumer",
                          obligations=[Positive("find/files"), Positive("read/files")],
                          relations=[Consumes(0, 1, "result", "primary")]),
            self.proposal(cell="G4_COVERAGE_BOUNDARY", subtype="mixed",
                          obligations=[Positive("find/files"), Outside("physical_world_action", None)]),
            self.proposal(cell="G4_COVERAGE_BOUNDARY", subtype="outside_only",
                          obligations=[Outside("financial_transaction", None)]),
            self.proposal(cell="G5_LINGUISTIC_VARIATION", subtype="indirect", surface=["indirect"]),
            self.proposal(cell="S1_APPROVAL", subtype="outer_plus_approved",
                          obligations=[Positive("find/files"), Positive("delete/files")], relations=[ApprovalOwns((1,))]),
            self.proposal(cell="S1_APPROVAL", subtype="all_approved",
                          obligations=[Positive("find/files"), Positive("delete/files")], relations=[ApprovalOwns((0, 1))]),
            self.proposal(cell="S2_NEGATION", subtype="positive_plus_negated",
                          obligations=[Positive("read/messages"), Negated("delete/messages")]),
            self.proposal(cell="S3_CONDITIONAL_BRANCH", subtype="two_branches",
                          obligations=[Positive("read/messages"), Positive("list/tasks")],
                          relations=[ConditionalBranches((0,), (1,), False)]),
            self.proposal(cell="S4_UNDO", subtype="undo_only", obligations=[Control("undo_last_turn")], surface=["undo_only"]),
            self.proposal(cell="S5_MIXED_CONTROL", subtype="undo_plus_operation",
                          obligations=[Control("undo_last_turn"), Positive("find/files")]),
            self.proposal(cell="S6_FALSE_ACTION_TRAP", subtype="outside_only",
                          obligations=[Outside("financial_transaction", "set/credentials")]),
        ]
        for proposal in values:
            self.validate(proposal)
            gold = e.semantic_gold(proposal, FREEZE)
            self.assertEqual(gold["expected"], e.derive_expected(proposal))
            self.assertEqual(gold["safety_applicability"], e.derive_safety(proposal))
            e.validate_expected_document(gold["expected"], self.registry)

    def test_02_safety_branch_not_necessarily_ordered(self):
        proposal = self.proposal(cell="S3_CONDITIONAL_BRANCH", subtype="two_branches",
            obligations=[Positive("read/messages"), Positive("list/tasks")],
            relations=[ConditionalBranches((0,), (1,), False)])
        self.validate(proposal)
        safety = e.derive_safety(proposal)
        self.assertTrue(safety["branch_ownership"])
        self.assertFalse(safety["ordering"])

    def test_03_s6_has_tempting_suppressed_route(self):
        bad = self.proposal(cell="S6_FALSE_ACTION_TRAP", subtype="outside_only",
                            obligations=[Outside("financial_transaction", None)])
        with self.assertRaises(e.ValidationError): self.validate(bad)

    def test_04_projection_and_bundle_exact(self):
        proposal = self.proposal()
        projection = e.projection_value(proposal, self.glossary)
        e.validate_projection(projection, proposal, self.glossary)
        bundle = e.author_bundle_value(projection, "a" * 64)
        e.validate_author_bundle(bundle, projection, "a" * 64)
        raw = e.canonical_bytes(projection)
        self.assertNotIn(b"find/files", raw)
        self.assertNotIn(b"expected", raw)
        trap = self.proposal(cell="S6_FALSE_ACTION_TRAP", subtype="outside_only",
                             obligations=[Outside("financial_transaction", "set/credentials")])
        trap_projection = e.projection_value(trap, self.glossary)
        self.assertIn("tempting_but_insufficient_gloss", trap_projection["obligations"][0])
        self.assertNotIn(b"set/credentials", e.canonical_bytes(trap_projection))

    def test_05_fingerprint_ignores_language_and_id(self):
        first = self.proposal(pid="bp-001")
        second = Proposal("bp-999", "it-IT", first.cell, first.subtype, first.obligations,
                          first.relations, first.surface_constraints, first.process_freeze_sha256)
        self.assertEqual(e.fingerprint(first), e.fingerprint(second))
        with self.assertRaises(e.ValidationError): e.validate_fingerprint_set((first, second), scale="pilot")

    def test_06_full_scale_s4_exception_only(self):
        first = self.proposal(cell="S4_UNDO", subtype="undo_only", obligations=[Control("undo_last_turn")], surface=["undo_only"])
        second = Proposal("bp-002", "it-IT", first.cell, first.subtype, first.obligations,
                          first.relations, first.surface_constraints, first.process_freeze_sha256)
        e.validate_fingerprint_set((first, second), scale="full")

    def test_07_pool_identity_and_exactness(self):
        proposal = self.proposal()
        a1, a2 = ReviewerIdentity("a1", "ca1"), ReviewerIdentity("a2", "ca2")
        gold = e.validate_pool_a((e.pool_a_judgment(a1, proposal), e.pool_a_judgment(a2, proposal)), proposal, self.registry)
        query = "Find the project files.".encode()
        b1, b2 = ReviewerIdentity("b1", "cb1"), ReviewerIdentity("b2", "cb2")
        records = (e.pool_b_judgment(b1, proposal, query), e.pool_b_judgment(b2, proposal, query))
        e.validate_pool_b(records, proposal=proposal, gold=gold, query_bytes=query,
                          pool_a_identities={("a1", "ca1"), ("a2", "ca2")}, registry=self.registry)

    def test_08_lifecycle_complete(self):
        records = (
            LifecycleRecord("author", ReviewerIdentity("writer", "cw"), "bp-001", "en-GB", "1"*64, "2"*64, 1, 0, 0, 0, 0),
            LifecycleRecord("pool_a", ReviewerIdentity("a1", "ca1"), "bp-001", "en-GB", "3"*64, "4"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("pool_a", ReviewerIdentity("a2", "ca2"), "bp-001", "en-GB", "3"*64, "4"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("pool_b", ReviewerIdentity("b1", "cb1"), "bp-001", "en-GB", "5"*64, "6"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("pool_b", ReviewerIdentity("b2", "cb2"), "bp-001", "en-GB", "5"*64, "6"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("native_reviewer", ReviewerIdentity("n1", "cn1"), "bp-001", "en-GB", "7"*64, "8"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("cross_language_reviewer", ReviewerIdentity("x1", "cx1"), "bp-001", "en-GB", "9"*64, "a"*64, 0, 0, 0, 0, 0),
        )
        e.validate_lifecycle(records, expected_cases={"bp-001": "en-GB"})

    def test_09_envelope_hashes_real_bytes(self):
        proposal = self.proposal()
        projection = e.canonical_bytes(e.projection_value(proposal, self.glossary))
        bundle = e.canonical_bytes(e.author_bundle_value(e.projection_value(proposal, self.glossary), "a"*64))
        gold = e.canonical_bytes(e.semantic_gold(proposal, FREEZE)); query = b"query"
        pa = (b"pa1", b"pa2"); pb = (b"pb1", b"pb2")
        parts = dict(proposal=proposal, projection_bytes=projection, author_bundle_bytes=bundle,
                     gold_bytes=gold, query_bytes=query, pool_a_bytes=pa, pool_b_bytes=pb,
                     rubric_bytes=b"rubric", lifecycle_bytes=b"lifecycle")
        envelope = e.envelope_value(**parts)
        e.validate_envelope(envelope, **parts)

    def test_10_barrier_result_cannot_escape(self):
        proposal = self.proposal(cell="S1_APPROVAL", subtype="outer_plus_approved",
            obligations=[Positive("find/files"), Positive("read/files")],
            relations=[ApprovalOwns((0,)), Consumes(0, 1, "result", "primary")])
        with self.assertRaisesRegex(e.ValidationError, "RELATION_MISMATCH"):
            self.validate(proposal)

    def test_11_parser_rejects_wrong_primitive_types(self):
        value = e.proposal_value(self.proposal())
        value["relations"] = [{"kind":"consumes", "source":"0", "target":1,
                               "output_port":"result", "input_port":"primary"}]
        with self.assertRaisesRegex(e.ValidationError, "CLOSED_SCHEMA"):
            e.parse_proposal(value)

    def test_12_lifecycle_requires_native_and_cross_review(self):
        records = (
            LifecycleRecord("author", ReviewerIdentity("writer", "cw"), "bp-001", "en-GB", "1"*64, "2"*64, 1, 0, 0, 0, 0),
            LifecycleRecord("pool_a", ReviewerIdentity("a1", "ca1"), "bp-001", "en-GB", "3"*64, "4"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("pool_a", ReviewerIdentity("a2", "ca2"), "bp-001", "en-GB", "3"*64, "4"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("pool_b", ReviewerIdentity("b1", "cb1"), "bp-001", "en-GB", "5"*64, "6"*64, 0, 0, 0, 0, 0),
            LifecycleRecord("pool_b", ReviewerIdentity("b2", "cb2"), "bp-001", "en-GB", "5"*64, "6"*64, 0, 0, 0, 0, 0),
        )
        with self.assertRaisesRegex(e.ValidationError, "LIFECYCLE_INCOMPLETE"):
            e.validate_lifecycle(records, expected_cases={"bp-001":"en-GB"})


if __name__ == "__main__": unittest.main()

"""Data-driven synthetic mutations bound to the frozen catalog."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from . import build
from . import engine as e
from . import preflight
from .model import Consumes, LifecycleRecord, Positive, Proposal, ReviewerIdentity
from .synthetic_fixture import panel_fixture


FREEZE = "f" * 64


class MutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = e.load_json(build.REGISTRY)
        cls.glossary = build.glossary()
        cls.catalog = build.mutation_catalog()["mutations"]

    def proposal(self):
        return Proposal("bp-001", "en-GB", "G1_SINGLE", "known_single",
                        (Positive("find/files"),), (), (), FREEZE)

    @staticmethod
    def matrix(proposal):
        return {"slots": [{"proposal_id": proposal.proposal_id, "language_tag": proposal.language_tag,
                            "cell": proposal.cell, "subtype": proposal.subtype}]}

    def validate(self, proposal, matrix=None):
        e.validate_proposal(proposal, registry=self.registry, glossary=self.glossary,
                            freeze_sha=FREEZE, matrix=matrix or self.matrix(proposal))

    def lifecycle_call(self, records, artifacts, queries):
        return lambda: e.validate_lifecycle(records, expected_cases={key: "en-GB" for key in queries},
            artifact_bytes=artifacts, query_bytes=queries, rubric_bytes=b"rubric")

    def mutation_cases(self):
        base = self.proposal()

        stale = Proposal(base.proposal_id, base.language_tag, base.cell, base.subtype,
                         base.obligations, base.relations, base.surface_constraints, "0" * 64)

        extra = e.proposal_value(base); extra["extra"] = True

        unknown = Proposal("bp-001", "en-GB", "G1_SINGLE", "known_single",
                           (Positive("not/a-route"),), (), (), FREEZE)

        surface = Proposal("bp-001", "en-GB", "G5_LINGUISTIC_VARIATION", "indirect",
                           (Positive("find/files"),), (), ("polite",), FREEZE)

        cardinality = Proposal("bp-001", "en-GB", "G1_SINGLE", "known_single",
            (Positive("find/files"), Positive("read/messages")), (), (), FREEZE)

        relation = Proposal("bp-001", "en-GB", "G3_COMPOUND_DEPENDENT", "producer_consumer",
            (Positive("find/files"), Positive("compress/files")),
            (Consumes(0, 1, "result", "primary"), Consumes(0, 1, "result", "primary")), (), FREEZE)

        changed_gold = deepcopy(e.semantic_gold(base, FREEZE))
        changed_gold["expected"] = {"kind": "unrepresentable", "reason": "outside_registry"}
        bad_a = (
            {"format": "metnos.intent-holdout-pool-a-judgment/0.4", "reviewer": {"reviewer_id": "a1", "context_id": "c1"}, "gold": changed_gold},
            {"format": "metnos.intent-holdout-pool-a-judgment/0.4", "reviewer": {"reviewer_id": "a2", "context_id": "c2"}, "gold": changed_gold},
        )

        projection = e.projection_value(base, self.glossary)
        changed_projection = deepcopy(projection); changed_projection["mention_order"] = []
        leaking_glossary = deepcopy(self.glossary)
        leaking_glossary["operations"]["find/files"]["author_gloss"] = "find/files"
        leaking_projection = e.projection_value(base, leaking_glossary)

        changed_bundle = e.author_bundle_value(projection, b"constitution"); changed_bundle["network"] = True

        same = ReviewerIdentity("same", "same-context")

        a1, a2 = ReviewerIdentity("a1", "ca1"), ReviewerIdentity("a2", "ca2")
        a_records = (e.pool_a_judgment(a1, base), e.pool_a_judgment(a2, base))
        gold = e.validate_pool_a(a_records, base, self.registry)
        query = b"Find files."
        b1, b2 = ReviewerIdentity("b1", "cb1"), ReviewerIdentity("b2", "cb2")
        b_records = [e.pool_b_judgment(b1, base, query), e.pool_b_judgment(b2, base, query)]
        b_records[0]["reconstructed_obligations"][0]["route"] = "find/images"

        bad_input = b"input"; bad_output = b"output"
        bad_lifecycle = (LifecycleRecord("author", ReviewerIdentity("author", "author-context"),
            "bp-001", "en-GB", e.digest_bytes(bad_input), e.digest_bytes(bad_output), 1, 0, 1, 0, 0),)
        bad_artifacts = {("author", "author-context", "bp-001"): (bad_input, bad_output)}

        q1, q2 = b"First.", b"Second."
        reused_records = (
            LifecycleRecord("author", ReviewerIdentity("writer", "shared"), "bp-001", "en-GB",
                e.digest_bytes(b"i1"), e.digest_bytes(q1), 1, 0, 0, 0, 0),
            LifecycleRecord("author", ReviewerIdentity("writer", "shared"), "bp-002", "en-GB",
                e.digest_bytes(b"i2"), e.digest_bytes(q2), 1, 0, 0, 0, 0),
        )
        reused_artifacts = {("author", "shared", "bp-001"): (b"i1", q1),
                            ("author", "shared", "bp-002"): (b"i2", q2)}

        collision_panel = panel_fixture()
        collision_cases = list(collision_panel["cases"])
        first = e.parse_proposal(json.loads(collision_cases[0]["proposal_bytes"]))
        eleventh = e.parse_proposal(json.loads(collision_cases[10]["proposal_bytes"]))
        duplicate = Proposal(eleventh.proposal_id, eleventh.language_tag, eleventh.cell, eleventh.subtype,
                             first.obligations, first.relations, eleventh.surface_constraints,
                             eleventh.process_freeze_sha256)
        collision_cases[10] = {**collision_cases[10], "proposal_bytes": e.canonical_bytes(e.proposal_value(duplicate))}
        collision_panel["cases"] = tuple(collision_cases)

        binding_panel = panel_fixture()
        binding_cases = list(binding_panel["cases"])
        binding_cases[0] = deepcopy(binding_cases[0])
        binding_cases[0]["envelope"] = deepcopy(binding_cases[0]["envelope"])
        binding_cases[0]["envelope"]["rubric_sha256"] = "0" * 64
        binding_panel["cases"] = tuple(binding_cases)

        wrong_assignment = {"slots": [{"proposal_id": "bp-001", "language_tag": "it-IT",
                                        "cell": "S4_UNDO", "subtype": "undo_only"}]}

        return {
            "M01": lambda: self.validate(stale),
            "M02": lambda: e.parse_proposal(extra),
            "M03": lambda: self.validate(unknown),
            "M04": lambda: self.validate(surface),
            "M05": lambda: self.validate(cardinality),
            "M06": lambda: self.validate(relation),
            "M07": lambda: e.validate_pool_a(bad_a, base, self.registry),
            "M08": lambda: e.validate_projection(changed_projection, base, self.glossary),
            "M09": lambda: e.validate_projection(leaking_projection, base, leaking_glossary),
            "M10": lambda: e.validate_author_bundle(changed_bundle, projection, b"constitution"),
            "M11": lambda: e.validate_panel(**collision_panel),
            "M12": lambda: e.validate_pool_a((e.pool_a_judgment(same, base), e.pool_a_judgment(same, base)), base, self.registry),
            "M13": lambda: e.validate_pool_b(tuple(b_records), proposal=base, gold=gold, query_bytes=query,
                                               pool_a_records=a_records, registry=self.registry),
            "M14": self.lifecycle_call(bad_lifecycle, bad_artifacts, {"bp-001": bad_output}),
            "M15": self.lifecycle_call(reused_records, reused_artifacts, {"bp-001": q1, "bp-002": q2}),
            "M16": lambda: e.validate_panel(**binding_panel),
            "M17": lambda: preflight.validate_namespace(set(preflight.ALLOWED_FILES) | {"queries.json"}),
            "M18": self.build_drift,
            "M19": lambda: self.validate(base, wrong_assignment),
        }

    @staticmethod
    def build_drift():
        original = build.LOCAL
        try:
            build.LOCAL = original + ("missing.py",)
            preflight.validate_source_closure()
        finally:
            build.LOCAL = original

    def test_catalog_expected_codes_are_exercised(self):
        cases = self.mutation_cases()
        expected = {row["id"]: row["expected_code"] for row in self.catalog}
        self.assertEqual(set(cases), set(expected))
        for mutation_id, callback in cases.items():
            with self.subTest(mutation=mutation_id):
                with self.assertRaises(e.ValidationError) as caught:
                    callback()
                self.assertEqual(caught.exception.code, expected[mutation_id])


if __name__ == "__main__":
    unittest.main()

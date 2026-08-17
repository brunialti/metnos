"""Data-driven synthetic mutation suite: every catalog row is exercised."""
from __future__ import annotations

from copy import deepcopy
import unittest

import build
import engine as e
import preflight
from model import ApprovalOwns, LifecycleRecord, Positive, Proposal, ReviewerIdentity


FREEZE = "f" * 64


class MutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = e.load_json(build.REGISTRY)
        cls.glossary = build.glossary()
        cls.catalog = build.mutation_catalog()["mutations"]

    def proposal(self):
        return Proposal("bp-001", "en-GB", "G1_SINGLE", "known_single", (Positive("find/files"),), (), (), FREEZE)

    def validate(self, proposal):
        e.validate_proposal(proposal, registry=self.registry, glossary=self.glossary, freeze_sha=FREEZE)

    def assert_code(self, code, fn):
        with self.assertRaises(e.ValidationError) as caught: fn()
        self.assertEqual(caught.exception.code, code)

    def test_catalog_is_exactly_exercised(self):
        methods = {name[5:].upper() for name in dir(self) if name.startswith("test_m")}
        ids = {row["id"] for row in self.catalog}
        self.assertEqual(methods, ids)

    def test_m01(self):
        p = self.proposal(); p = Proposal(p.proposal_id, p.language_tag, p.cell, p.subtype, p.obligations, p.relations, p.surface_constraints, "0"*64)
        self.assert_code("HASH_BINDING", lambda: self.validate(p))

    def test_m02(self):
        value = e.proposal_value(self.proposal()); value["extra"] = True
        self.assert_code("CLOSED_SCHEMA", lambda: e.parse_proposal(value))

    def test_m03(self):
        p = Proposal("bp-001", "en-GB", "G1_SINGLE", "known_single", (Positive("not/a-route"),), (), (), FREEZE)
        self.assert_code("CAPABILITY_SCOPE", lambda: self.validate(p))

    def test_m04(self):
        p = Proposal("bp-001", "en-GB", "G5_LINGUISTIC_VARIATION", "indirect", (Positive("find/files"),), (), ("polite",), FREEZE)
        self.assert_code("SUBTYPE_MISMATCH", lambda: self.validate(p))

    def test_m05(self):
        p = Proposal("bp-001", "en-GB", "G1_SINGLE", "known_single", (Positive("find/files"), Positive("list/tasks")), (), (), FREEZE)
        self.assert_code("CELL_MISMATCH", lambda: self.validate(p))

    def test_m06(self):
        p = Proposal("bp-001", "en-GB", "S1_APPROVAL", "all_approved",
                     (Positive("find/files"), Positive("delete/files")), (ApprovalOwns((1,)),), (), FREEZE)
        self.assert_code("SUBTYPE_MISMATCH", lambda: self.validate(p))

    def test_m07(self):
        p = self.proposal(); expected = e.semantic_gold(p, FREEZE); changed = deepcopy(expected); changed["expected"] = {"kind":"unrepresentable","reason":"outside_registry"}
        self.assert_code("GOLD_MISMATCH", lambda: e.validate_pool_a((
            {"format":"metnos.intent-holdout-pool-a-judgment/0.2","reviewer":{"reviewer_id":"a1","context_id":"c1"},"gold":changed},
            {"format":"metnos.intent-holdout-pool-a-judgment/0.2","reviewer":{"reviewer_id":"a2","context_id":"c2"},"gold":changed}), p, self.registry))

    def test_m08(self):
        p = self.proposal(); projection = e.projection_value(p, self.glossary); projection["mention_order"] = []
        self.assert_code("PROJECTION_MISMATCH", lambda: e.validate_projection(projection, p, self.glossary))

    def test_m09(self):
        p = self.proposal(); glossary = deepcopy(self.glossary); glossary["operations"]["find/files"] = {
            **glossary["operations"]["find/files"], "author_gloss":"find/files"}
        projection = e.projection_value(p, glossary)
        self.assert_code("PROJECTION_LEAK", lambda: e.validate_projection(projection, p, glossary))

    def test_m10(self):
        p = self.proposal(); projection = e.projection_value(p, self.glossary); bundle = e.author_bundle_value(projection, "a"*64); bundle["network"] = True
        self.assert_code("BUNDLE_MISMATCH", lambda: e.validate_author_bundle(bundle, projection, "a"*64))

    def test_m11(self):
        p = self.proposal(); q = Proposal("bp-002", "it-IT", p.cell, p.subtype, p.obligations, p.relations, p.surface_constraints, p.process_freeze_sha256)
        self.assert_code("FINGERPRINT_COLLISION", lambda: e.validate_fingerprint_set((p,q), scale="pilot"))

    def test_m12(self):
        p = self.proposal(); same = ReviewerIdentity("a", "c")
        records = (e.pool_a_judgment(same,p), e.pool_a_judgment(same,p))
        self.assert_code("POOL_IDENTITY", lambda: e.validate_pool_a(records,p,self.registry))

    def test_m13(self):
        p = self.proposal(); a1,a2=ReviewerIdentity("a1","c1"),ReviewerIdentity("a2","c2"); gold=e.validate_pool_a((e.pool_a_judgment(a1,p),e.pool_a_judgment(a2,p)),p,self.registry)
        query=b"q"; b1,b2=ReviewerIdentity("b1","c3"),ReviewerIdentity("b2","c4"); r1=e.pool_b_judgment(b1,p,query);r2=e.pool_b_judgment(b2,p,query);r1["reconstructed_obligations"][0]["route"]="find/images"
        self.assert_code("RECONSTRUCTION_MISMATCH", lambda:e.validate_pool_b((r1,r2),proposal=p,gold=gold,query_bytes=query,pool_a_identities={("a1","c1"),("a2","c2")},registry=self.registry))

    def test_m14(self):
        records=(LifecycleRecord("author",ReviewerIdentity("a","c"),"bp-001","en-GB","1"*64,"2"*64,1,0,1,0,0),)
        self.assert_code("LIFECYCLE_VIOLATION",lambda:e.validate_lifecycle(records,expected_cases={"bp-001":"en-GB"}))

    def test_m15(self):
        records=(LifecycleRecord("author",ReviewerIdentity("a","c"),"bp-001","en-GB","1"*64,"2"*64,1,0,0,0,0),
                 LifecycleRecord("pool_a",ReviewerIdentity("b","c"),"bp-001","en-GB","3"*64,"4"*64,0,0,0,0,0))
        self.assert_code("ISOLATION_REUSE",lambda:e.validate_lifecycle(records,expected_cases={"bp-001":"en-GB"}))

    def test_m16(self):
        p=self.proposal();parts=dict(proposal=p,projection_bytes=b"p",author_bundle_bytes=b"b",gold_bytes=b"g",query_bytes=b"q",pool_a_bytes=(b"a1",b"a2"),pool_b_bytes=(b"b1",b"b2"),rubric_bytes=b"rubric",lifecycle_bytes=b"lifecycle");env=e.envelope_value(**parts);env["rubric_sha256"]="3"*64
        self.assert_code("ENVELOPE_MISMATCH",lambda:e.validate_envelope(env,**parts))

    def test_m17(self):
        original = preflight.ALLOWED_FILES
        try:
            preflight.ALLOWED_FILES = set(original) - {"rubrics.json"}
            self.assert_code("PREMATURE_ARTIFACT", preflight.validate_namespace)
        finally: preflight.ALLOWED_FILES = original

    def test_m18(self):
        original = build.LOCAL
        try:
            build.LOCAL = original + ("missing.py",)
            self.assert_code("BUILD_DRIFT", preflight.validate_source_closure)
        finally: build.LOCAL = original


if __name__ == "__main__": unittest.main()

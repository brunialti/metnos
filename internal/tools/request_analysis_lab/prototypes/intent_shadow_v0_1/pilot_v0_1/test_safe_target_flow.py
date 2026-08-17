from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import unittest

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.pilot_v0_1.safe_target_flow import (
    Approval,
    Decision,
    DirectTarget,
    Effect,
    OperationStep,
    PreviousTarget,
    UnknownTarget,
    approval_for,
    direct_target,
    evaluate,
    previous_target,
    target_ref,
)
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.pilot_v0_1.run_kiss_safe_relation import (
    apply_relations,
    parse_relations,
)


ISSUE_42 = target_ref("issue", "repository", "42")
ISSUE_43 = target_ref("issue", "repository", "43")


def destructive_step(target, *, ordinal=1, after_step=None):
    return OperationStep(
        route="synthetic/delete",
        ordinal=ordinal,
        effect=Effect.MUTATING,
        requires_target=True,
        approval_required=True,
        target=target,
        after_step=after_step,
    )


class SafeTargetFlowTests(unittest.TestCase):
    def test_direct_target_and_execution_order_are_independent(self):
        step = destructive_step(direct_target(ISSUE_42), after_step=0)
        self.assertIsInstance(step.target, DirectTarget)
        self.assertEqual(evaluate(step).decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(evaluate(step, approval_for(step)).decision, Decision.ALLOW)

    def test_previous_result_is_materialized_before_effect(self):
        step = destructive_step(previous_target(0, ISSUE_42, ISSUE_43))
        approval = approval_for(step)
        self.assertEqual(approval.target_count, 2)
        self.assertEqual(evaluate(step, approval).decision, Decision.ALLOW)

    def test_unknown_target_never_becomes_independent_or_wildcard(self):
        verdict = evaluate(destructive_step(UnknownTarget()))
        self.assertEqual(verdict.decision, Decision.BLOCK)
        self.assertEqual(verdict.code, "TARGET_UNKNOWN")

    def test_missing_target_blocks_even_for_read_only_operation(self):
        step = OperationStep("synthetic/read", 0, Effect.READ_ONLY, True, False, None)
        self.assertEqual(evaluate(step).code, "TARGET_MISSING")

    def test_operation_without_target_is_allowed_only_when_contract_says_so(self):
        step = OperationStep("synthetic/now", 0, Effect.READ_ONLY, False, False, None)
        self.assertEqual(evaluate(step).decision, Decision.ALLOW)
        self.assertEqual(
            evaluate(replace(step, target=direct_target(ISSUE_42))).code,
            "TARGET_UNEXPECTED",
        )

    def test_self_and_forward_sources_are_blocked(self):
        self.assertEqual(
            evaluate(destructive_step(previous_target(1, ISSUE_42))).code,
            "TARGET_SOURCE_NOT_PRIOR",
        )
        self.assertEqual(
            evaluate(destructive_step(previous_target(2, ISSUE_42))).code,
            "TARGET_SOURCE_NOT_PRIOR",
        )
        self.assertEqual(
            evaluate(destructive_step(direct_target(ISSUE_42), after_step=1)).code,
            "ORDER_SOURCE_NOT_PRIOR",
        )
        malformed = PreviousTarget("0", (ISSUE_42,))  # type: ignore[arg-type]
        self.assertEqual(
            evaluate(destructive_step(malformed)).code,
            "TARGET_SOURCE_NOT_PRIOR",
        )

    def test_approval_is_bound_to_exact_target_set(self):
        original = destructive_step(direct_target(ISSUE_42))
        changed = destructive_step(direct_target(ISSUE_42, ISSUE_43))
        verdict = evaluate(changed, approval_for(original))
        self.assertEqual(verdict.decision, Decision.BLOCK)
        self.assertEqual(verdict.code, "APPROVAL_STALE_OR_WRONG_TARGET")

    def test_approval_is_bound_to_target_origin_and_order(self):
        direct = destructive_step(direct_target(ISSUE_42), after_step=0)
        derived = destructive_step(previous_target(0, ISSUE_42), after_step=0)
        reordered = replace(direct, after_step=None)
        approval = approval_for(direct)
        self.assertEqual(evaluate(derived, approval).decision, Decision.BLOCK)
        self.assertEqual(evaluate(reordered, approval).decision, Decision.BLOCK)

    def test_empty_materialized_result_is_safe_no_work(self):
        step = destructive_step(previous_target(0))
        self.assertEqual(evaluate(step).decision, Decision.ALLOW)

    def test_unknown_effect_fails_closed(self):
        step = replace(destructive_step(direct_target(ISSUE_42)), effect=Effect.UNKNOWN)
        self.assertEqual(evaluate(step).code, "EFFECT_UNKNOWN")

    def test_noncanonical_or_duplicate_targets_are_rejected(self):
        with self.assertRaises(ValueError):
            direct_target(ISSUE_42, ISSUE_42)
        with self.assertRaises(ValueError):
            target_ref("issue", "repository", "")
        unsorted = DirectTarget((ISSUE_43, ISSUE_42))
        self.assertEqual(evaluate(destructive_step(unsorted)).code, "TARGET_SET_NOT_CANONICAL")

    def test_policy_is_language_neutral_and_has_no_route_special_cases(self):
        step = destructive_step(direct_target(ISSUE_42), after_step=0)
        expected = evaluate(step)
        for language_tag in (
            "it-IT", "en-GB", "es-MX", "de-DE", "tr-TR",
            "sr-Cyrl-RS", "ar-EG", "hi-IN", "ja-JP", "zh-Hant-TW",
        ):
            with self.subTest(language_tag=language_tag):
                self.assertEqual(evaluate(step), expected)
        source = (Path(__file__).with_name("safe_target_flow.py")).read_text(encoding="utf-8")
        self.assertNotIn("delete/issues", source)
        self.assertNotIn("it-IT", source)
        self.assertNotIn("ja-JP", source)

    def test_forged_approval_type_is_rejected(self):
        step = destructive_step(direct_target(ISSUE_42))
        real = approval_for(step)
        forged = Approval(real.plan_sha256, real.target_sha256, real.target_count + 1)
        self.assertEqual(evaluate(step, forged).decision, Decision.BLOCK)

    def test_relation_adapter_keeps_target_source_separate_from_order(self):
        document = {
            "kind": "operation_graph",
            "steps": [
                {"route": "synthetic/read"},
                {"route": "synthetic/delete", "from": [0]},
            ],
        }
        direct = apply_relations(document, [{
            "uses_previous_result": False, "after_previous": True,
        }])
        self.assertNotIn("from", direct["steps"][1])

        previous = apply_relations(document, [{
            "uses_previous_result": True, "after_previous": True,
        }])
        self.assertEqual(previous["steps"][1]["from"], [0])

    def test_data_dependency_derives_order_without_model_guessing(self):
        content = json.dumps({"relations": [{
            "uses_previous_result": True,
            "explicit_after_previous": False,
        }]})
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        relation = parse_relations(body, 1)[0]
        self.assertTrue(relation["uses_previous_result"])
        self.assertTrue(relation["after_previous"])


if __name__ == "__main__":
    unittest.main()

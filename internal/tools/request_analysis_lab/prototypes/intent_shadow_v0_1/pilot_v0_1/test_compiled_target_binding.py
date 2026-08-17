from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
import unittest

from runtime.engine.types import StepRun, StepSpec

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.pilot_v0_1.compiled_target_binding import (
    TargetContract,
    bind_compiled_step,
)
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.pilot_v0_1.safe_target_flow import (
    Decision,
    DirectTarget,
    Effect,
    PreviousTarget,
    UnknownTarget,
    approval_for,
    evaluate,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


@dataclass(frozen=True, slots=True)
class CompiledCase:
    case_id: str
    language_tag: str
    source: str


SCHEMA = {
    "type": "object",
    "properties": {
        "ids": {
            "type": "array",
            "items": {"type": "string"},
            "from_entries_key": "id",
        },
        "scope": {
            "type": "string",
            "from_entries_key": "scope",
        },
        "from_step": {"type": "integer"},
    },
    "required": ["ids"],
    "requires_one_of": [["ids", "from_step"]],
}
TARGET_CONTRACT = TargetContract(
    schema=SCHEMA,
    target_args=("ids",),
    context_args=("scope",),
    effect=Effect.MUTATING,
    approval_required=True,
)
NO_TARGET_CONTRACT = TargetContract(
    schema={"type": "object", "properties": {}},
    target_args=(),
    requires_target=False,
    effect=Effect.READ_ONLY,
)


def _history() -> tuple[StepRun, ...]:
    return (
        StepRun(
            step_idx=1,
            tool="synthetic/find",
            args={},
            result={"entries": [
                {"id": "item-1", "scope": "scope-1"},
                {"id": "item-2", "scope": "scope-1"},
            ]},
            ok=True,
            latency_ms=1,
        ),
    )


def _bind(case: CompiledCase):
    if case.source == "direct":
        step = StepSpec(
            "synthetic/effect",
            {"ids": ["item-42"], "scope": "scope-1", "from_step": 1},
        )
        contract = TARGET_CONTRACT
    elif case.source == "previous_step":
        step = StepSpec("synthetic/effect", {"from_step": 1})
        contract = TARGET_CONTRACT
    elif case.source == "unknown":
        step = StepSpec("synthetic/effect", {})
        contract = TARGET_CONTRACT
    elif case.source == "none":
        step = StepSpec("synthetic/read", {})
        contract = NO_TARGET_CONTRACT
    else:
        raise ValueError(case.source)
    return bind_compiled_step(step, 1, contract, _history())


class CompiledTargetBindingTests(unittest.TestCase):
    def test_real_step_shapes_direct_target_wins_over_from_step(self):
        step = _bind(CompiledCase("direct", "en-GB", "direct"))
        self.assertIsInstance(step.target, DirectTarget)
        self.assertIsNone(step.after_step)
        self.assertEqual(evaluate(step).decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(evaluate(step, approval_for(step)).decision, Decision.ALLOW)

    def test_real_step_shapes_materialize_previous_result(self):
        step = _bind(CompiledCase("previous", "en-GB", "previous_step"))
        self.assertIsInstance(step.target, PreviousTarget)
        self.assertEqual(len(step.target.items), 2)
        self.assertEqual(step.after_step, 0)
        self.assertEqual(evaluate(step).decision, Decision.REQUIRE_APPROVAL)

    def test_empty_default_target_does_not_hide_previous_result(self):
        step = bind_compiled_step(
            StepSpec("synthetic/effect", {"ids": [], "from_step": 1}),
            1,
            TARGET_CONTRACT,
            _history(),
        )
        self.assertIsInstance(step.target, PreviousTarget)
        self.assertEqual(len(step.target.items), 2)

    def test_missing_target_blocks_before_effect(self):
        step = _bind(CompiledCase("unknown", "en-GB", "unknown"))
        self.assertIsInstance(step.target, UnknownTarget)
        self.assertEqual(evaluate(step).decision, Decision.BLOCK)

    def test_incomplete_explicit_target_never_falls_through_to_previous_set(self):
        step = bind_compiled_step(
            StepSpec("synthetic/effect", {"ids": ["item-42"], "from_step": 1}),
            1,
            TARGET_CONTRACT,
            _history(),
        )
        self.assertIsInstance(step.target, UnknownTarget)
        self.assertEqual(evaluate(step).decision, Decision.BLOCK)

    def test_duplicate_explicit_targets_are_executed_once(self):
        step = bind_compiled_step(
            StepSpec(
                "synthetic/effect",
                {"ids": ["item-42", "item-42"], "scope": "scope-1"},
            ),
            0,
            TARGET_CONTRACT,
        )
        self.assertIsInstance(step.target, DirectTarget)
        self.assertEqual(len(step.target.items), 1)
        self.assertEqual(evaluate(step).decision, Decision.REQUIRE_APPROVAL)

    def test_declared_singular_plural_aliases_form_one_exact_set(self):
        contract = TargetContract(
            schema={"type": "object", "properties": {
                "id": {"type": "string"},
                "ids": {"type": "array", "items": {"type": "string"}},
            }},
            target_args=("id", "ids"),
            effect=Effect.MUTATING,
        )
        step = bind_compiled_step(
            StepSpec("synthetic/effect", {"id": "42", "ids": ["42", "43"]}),
            0,
            contract,
        )
        self.assertIsInstance(step.target, DirectTarget)
        self.assertEqual(len(step.target.items), 2)

    def test_empty_materialized_result_means_safe_no_work(self):
        history = (
            StepRun(1, "synthetic/find", {}, {"entries": []}, True, 1),
        )
        step = bind_compiled_step(
            StepSpec("synthetic/effect", {"from_step": 1}),
            1,
            TARGET_CONTRACT,
            history,
        )
        self.assertIsInstance(step.target, PreviousTarget)
        self.assertEqual(step.target.items, ())
        self.assertEqual(evaluate(step).decision, Decision.ALLOW)

    def test_missing_or_mixed_source_context_blocks(self):
        history = (
            StepRun(1, "synthetic/find", {}, {"entries": [
                {"id": "item-1", "scope": "scope-1"},
                {"id": "item-2", "scope": "scope-2"},
            ]}, True, 1),
        )
        step = bind_compiled_step(
            StepSpec("synthetic/effect", {"from_step": 1}),
            1,
            TARGET_CONTRACT,
            history,
        )
        self.assertIsInstance(step.target, UnknownTarget)
        self.assertEqual(evaluate(step).decision, Decision.BLOCK)

    def test_actual_manifest_projection_is_used(self):
        with (ROOT / "executors/compress_files/manifest.toml").open("rb") as handle:
            schema = tomllib.load(handle)["args"]
        contract = TargetContract(
            schema=schema,
            target_args=("paths",),
            effect=Effect.MUTATING,
        )
        history = (
            StepRun(1, "find_files", {}, {
                "entries": [{"path": "/tmp/a.pdf"}, {"path": "/tmp/b.pdf"}],
            }, True, 1),
        )
        step = bind_compiled_step(
            StepSpec("compress_files", {"from_step": 1, "dest": "/tmp/out.zip"}),
            1,
            contract,
            history,
        )
        self.assertIsInstance(step.target, PreviousTarget)
        self.assertEqual(len(step.target.items), 2)
        self.assertEqual(evaluate(step).decision, Decision.ALLOW)

    def test_original_twenty_compiled_cases(self):
        panel = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
        expected_sources = {
            "bp-005": "previous_step",
            "bp-006": "previous_step",
            "bp-015": "previous_step",
            "bp-018": "direct",
            "bp-019": "direct",
        }
        observed = []
        for row in panel:
            case_id = row["proposal"]["proposal_id"]
            source = expected_sources.get(case_id, "none")
            step = _bind(CompiledCase(case_id, row["proposal"]["language_tag"], source))
            if source == "direct":
                actual = "direct" if isinstance(step.target, DirectTarget) else "wrong"
            elif source == "previous_step":
                actual = "previous_step" if isinstance(step.target, PreviousTarget) else "wrong"
            else:
                actual = "none" if step.target is None else "wrong"
            observed.append(actual == source)
        self.assertEqual(len(observed), 20)
        self.assertEqual(sum(observed), 20)

    def test_expanded_multilingual_twenty_compiled_cases(self):
        panel = json.loads(
            (HERE / "kiss_safe_relation_multilingual_cases.json").read_text(encoding="utf-8")
        )["cases"]
        observed = []
        for row in panel:
            source = row["expected"]["target_source"]
            step = _bind(CompiledCase(row["case_id"], row["language_tag"], source))
            if source == "direct":
                exact = isinstance(step.target, DirectTarget)
            elif source == "previous_step":
                exact = isinstance(step.target, PreviousTarget)
            elif source == "unknown":
                exact = (
                    isinstance(step.target, UnknownTarget)
                    and evaluate(step).decision is Decision.BLOCK
                )
            else:
                exact = step.target is None and evaluate(step).decision is Decision.ALLOW
            observed.append(exact)
        self.assertEqual(len(observed), 20)
        self.assertEqual(sum(observed), 20)

    def test_binding_logic_has_no_language_or_route_branches(self):
        source = (HERE / "compiled_target_binding.py").read_text(encoding="utf-8")
        for marker in ("it-IT", "ja-JP", "delete/issues", "delete/files"):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()

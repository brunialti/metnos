import pytest

from executor_birth_preexercise import (
    PreexerciseDenial, PreexerciseError, PreexerciseFacts, PreexercisePolicy,
    decide_preexercise,
)


POLICY = PreexercisePolicy("preexercise-v1", {
    "filesystem:read_fixture": True,
    "metnos:cache": False,
    "dialog.user_input": False,
})


def facts(**changes):
    values = {
        "synthesized_origin": True,
        "read_only": True,
        "capabilities": ("filesystem:read_fixture",),
    }
    values.update(changes)
    return PreexerciseFacts(**values)


def test_closed_policy_allows_only_known_eligible_read_only_synthesized_case():
    decision = decide_preexercise(facts(), policy=POLICY)
    assert decision.eligible
    assert decision.policy_version == "preexercise-v1"
    assert decision.denial is None


@pytest.mark.parametrize(("change", "denial"), [
    ({"synthesized_origin": False}, PreexerciseDenial.NOT_SYNTHESIZED),
    ({"read_only": False}, PreexerciseDenial.NOT_READ_ONLY),
    ({"real_network": True}, PreexerciseDenial.REAL_NETWORK),
    ({"secret_access": True}, PreexerciseDenial.SECRET_ACCESS),
    ({"generic_execution": True}, PreexerciseDenial.GENERIC_EXECUTION),
    ({"user_dialog": True}, PreexerciseDenial.USER_DIALOG),
    ({"personal_or_sensitive_paths": True}, PreexerciseDenial.PERSONAL_PATH),
    ({"unbounded_scope": True}, PreexerciseDenial.UNBOUNDED_SCOPE),
    ({"secret_egress_possible": True}, PreexerciseDenial.SECRET_EGRESS),
])
def test_each_normative_exclusion_fails_closed(change, denial):
    decision = decide_preexercise(facts(**change), policy=POLICY)
    assert not decision.eligible
    assert decision.denial is denial


def test_new_capability_is_ineligible_until_core_policy_names_it():
    decision = decide_preexercise(
        facts(capabilities=("filesystem:read_fixture", "future:apparently_safe")),
        policy=POLICY,
    )
    assert decision.denial is PreexerciseDenial.CAPABILITY_UNKNOWN
    assert decision.detail == "future:apparently_safe"


def test_known_but_forbidden_capability_is_distinct_from_unknown():
    decision = decide_preexercise(
        facts(capabilities=("dialog.user_input",)), policy=POLICY,
    )
    assert decision.denial is PreexerciseDenial.CAPABILITY_FORBIDDEN
    assert decision.detail == "dialog.user_input"


def test_capability_order_does_not_change_first_denial():
    left = decide_preexercise(
        facts(capabilities=("z:unknown", "a:unknown")), policy=POLICY)
    right = decide_preexercise(
        facts(capabilities=("a:unknown", "z:unknown")), policy=POLICY)
    assert left == right
    assert left.detail == "a:unknown"


def test_malformed_and_duplicate_facts_are_rejected_not_coerced():
    with pytest.raises(PreexerciseError, match="read_only"):
        facts(read_only=1)
    with pytest.raises(PreexerciseError, match="duplicate capability"):
        facts(capabilities=("filesystem:read_fixture", "filesystem:read_fixture"))
    with pytest.raises(PreexerciseError, match="capability entry"):
        PreexercisePolicy("v1", {"filesystem:read_fixture": 1})

"""G7-B: the retirement plan is a decision, and the proof that precedes it."""
from __future__ import annotations

import pytest

import executor_birth_legacy_retirement as retirement
import executor_birth_service_catalog as catalog


def _binding(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "legacy_id": "legacy-probe",
        "entry_id": "service-probe",
        "kind": "user_unit",
        "scope": "user",
        "locator": "metnos-probe.service",
        "disposition": "retire_in_group7",
    }
    value.update(overrides)
    return value


def test_every_real_binding_receives_exactly_one_action() -> None:
    """The product's own bindings plan without a single unknown pair."""
    steps = retirement.plan_retirement_v1(catalog.legacy_bindings_from_source_v1())
    assert len(steps) == 39
    assert {step.action for step in steps} == {
        "mask_user_unit", "mask_system_unit", "revoke_repository_entrypoint",
    }
    assert len({step.legacy_id for step in steps}) == len(steps)


def test_the_plan_does_not_depend_on_the_order_it_was_given() -> None:
    """A digest that moves with the caller's iteration cannot be bound."""
    bindings = list(catalog.legacy_bindings_from_source_v1())
    forward = retirement.plan_retirement_v1(bindings)
    backward = retirement.plan_retirement_v1(list(reversed(bindings)))
    assert forward == backward
    assert retirement.plan_digest_v1(forward) == retirement.plan_digest_v1(backward)


def test_the_framing_separates_neighbouring_fields() -> None:
    """Moving a value from one field to the next must change the digest."""
    first = retirement.plan_retirement_v1([_binding()])
    second = retirement.plan_retirement_v1([
        _binding(legacy_id="service-probe", entry_id="legacy-probe"),
    ])
    assert retirement.plan_digest_v1(first) != retirement.plan_digest_v1(second)


@pytest.mark.parametrize(("case", "code"), [
    ("unknown_pair", "legacy_retirement_unknown_kind"),
    ("wrong_disposition", "legacy_retirement_disposition"),
    ("duplicate", "legacy_retirement_duplicate"),
    ("empty", "legacy_retirement_empty"),
    ("not_a_mapping", "legacy_retirement_bindings_invalid"),
    ("blank_locator", "legacy_retirement_binding_invalid"),
])
def test_planning_denials(case: str, code: str) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    if case == "unknown_pair":
        bindings = [_binding(kind="powershell", scope="installed")]
    elif case == "wrong_disposition":
        bindings = [_binding(disposition="keep")]
    elif case == "duplicate":
        bindings = [_binding(), _binding(locator="other.service")]
    elif case == "empty":
        bindings = []
    elif case == "not_a_mapping":
        bindings = ["legacy-probe"]
    else:
        bindings = [_binding(locator="")]

    with pytest.raises(retirement.LegacyRetirementError) as denied:
        retirement.plan_retirement_v1(bindings)
    assert denied.value.code == code


def test_an_entry_still_running_stops_the_retirement() -> None:
    """Retiring a live entry leaves a process no identity can address."""
    steps = retirement.plan_retirement_v1([_binding()])
    retirement.require_no_legacy_in_flight_v1(
        steps, {"metnos-probe.service": "inactive"},
    )
    with pytest.raises(retirement.LegacyRetirementError) as running:
        retirement.require_no_legacy_in_flight_v1(
            steps, {"metnos-probe.service": "active"},
        )
    assert running.value.code == "legacy_retirement_in_flight"


def test_silence_is_not_evidence_of_absence() -> None:
    """An unobserved locator is refused, never assumed idle.

    This is the one place where the convenient reading would hide exactly the
    case that matters: an entry the observer could not see is more likely to be
    the dangerous one, not less.
    """
    steps = retirement.plan_retirement_v1([_binding()])
    with pytest.raises(retirement.LegacyRetirementError) as unobserved:
        retirement.require_no_legacy_in_flight_v1(steps, {})
    assert unobserved.value.code == "legacy_retirement_unobserved"


def test_the_module_plans_and_proves_but_never_mutates() -> None:
    """Nothing reachable from `__all__` masks, deletes or stops anything."""
    assert not any(
        name.startswith(("mask", "revoke", "retire", "apply", "execute"))
        for name in retirement.__all__
    )

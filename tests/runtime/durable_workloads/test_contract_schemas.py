from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from durable_workloads.schema import SchemaValidationError, validate_plan


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "durable_workloads"
REPOSITORY = Path(__file__).resolve().parents[3]
SCHEMAS = {
    "plan": "plan-v1.schema.json",
    "error": "error-v1.schema.json",
    "event": "event-v1.schema.json",
    "image-preset-output": "image-preset-output-v1.schema.json",
}


@pytest.mark.parametrize("stem,schema_name", SCHEMAS.items())
def test_normative_schema_accepts_valid_and_rejects_invalid(stem, schema_name):
    schema = json.loads((FIXTURES / "schemas" / schema_name).read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    valid = json.loads((FIXTURES / "examples" / f"{stem}.valid.json").read_text())
    invalid = json.loads((FIXTURES / "examples" / f"{stem}.invalid.json").read_text())
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors(invalid))


def test_product_validator_accepts_the_normative_plan_example():
    value = json.loads((FIXTURES / "examples" / "plan.valid.json").read_text())
    assert json.loads(validate_plan(value)) == value


def test_product_validator_rejects_unknown_fields_and_cycles():
    value = json.loads((FIXTURES / "examples" / "plan.valid.json").read_text())
    value["unexpected"] = True
    with pytest.raises(SchemaValidationError, match="unknown fields"):
        validate_plan(value)

    value.pop("unexpected")
    value["stages"][0]["depends_on"] = ["publish"]
    with pytest.raises(SchemaValidationError):
        validate_plan(value)


def test_manual_only_stage_cannot_request_automatic_retries():
    value = json.loads((FIXTURES / "examples" / "plan.valid.json").read_text())
    value["stages"][1]["effect_profile"] = "manual_only"
    value["stages"][1]["retry"]["max_attempts"] = 2
    with pytest.raises(SchemaValidationError, match="manual_only"):
        validate_plan(value)


def test_stage_limits_must_fit_the_plan_level_budgets():
    value = json.loads((FIXTURES / "examples" / "plan.valid.json").read_text())
    value["budgets"]["max_units"] = 1
    with pytest.raises(SchemaValidationError, match="sum of stage max_units"):
        validate_plan(value)

    value = json.loads((FIXTURES / "examples" / "plan.valid.json").read_text())
    value["budgets"]["max_attempts_per_unit"] = 1
    with pytest.raises(SchemaValidationError, match="retry limit"):
        validate_plan(value)


def test_f0_decision_census_and_capacity_defaults_are_closed():
    decision = (REPOSITORY / "decisions" / "0213-dormant-durable-workload-kernel.md").read_text()
    assert "id: 0213" in decision
    assert "status: accepted" in decision

    census = json.loads((FIXTURES / "executor-census-v1.json").read_text())
    assert census["schema_version"] == "metnos.durable-executor-census/1"
    required = {
        "name", "preset_role", "evidence", "output_schema", "effect",
        "placement", "transport", "intelligence", "parallelism",
        "durable_profile_candidate", "automatic_retry", "v1_status",
    }
    assert census["entries"]
    assert all(required <= set(entry) for entry in census["entries"])
    assert all(
        (REPOSITORY / evidence).is_file()
        for entry in census["entries"]
        for evidence in entry["evidence"]
    )
    assert census["closed_gaps"]["current_tier_rule"].endswith(
        "precise is not a tier key."
    )

    capacity = json.loads((FIXTURES / "f0-capacity-baseline-v1.json").read_text())
    defaults = capacity["frozen_safe_defaults"]
    assert defaults["worker_processes"] == 1
    assert defaults["coordinator_initial_claim_window"] == 1
    assert defaults["durable_layer_creates_private_pool"] is False
    assert defaults["resource_caps"]["vlm"] == 1
    assert capacity["measurements"]["vlm"]["throughput_measured"] is False


def test_original_rm0004_mandate_dialogue_is_byte_preserved():
    roadmap = (
        REPOSITORY / "internal" / "roadmap"
        / "RM-0004-motore-lavori-lunghi-persistenti.md"
    ).read_text()
    mandate = roadmap[roadmap.index("## Mandato"):]
    assert hashlib.sha256(mandate.encode()).hexdigest() == (
        "63ea2ee64a240381464d9ee121246eb5eb8a69d13e91740b12d0db2586055d59"
    )

from dataclasses import replace

import pytest

from executor_birth_properties import (
    PROPERTY_CATALOG_V1,
    IndependentSignalEvidence,
    IndependentSignalKind,
    PropertyContractError,
    PropertyEvidence,
    PropertyStatus,
    property_spec,
)


D = "sha256:" + "1" * 64


def test_catalog_is_closed_and_covers_seven_normative_groups():
    assert tuple(PROPERTY_CATALOG_V1) == (
        "output.schema.actual", "cardinality.zero_one_many",
        "limit.zero_and_below_total", "truncation.contract",
        "undo.round_trip", "delete.copy_before_delete",
        "entries.results.coherence",
    )
    assert all(spec.mandatory for spec in PROPERTY_CATALOG_V1.values())
    with pytest.raises(TypeError):
        PROPERTY_CATALOG_V1["extra"] = next(iter(PROPERTY_CATALOG_V1.values()))
    with pytest.raises(PropertyContractError, match="property_unknown"):
        property_spec("unknown.property")


def test_property_evidence_is_bound_to_catalog_version_and_runner():
    evidence = PropertyEvidence(
        "output.schema.actual", "v1", "case.1", PropertyStatus.PASSED,
        D, D, D, D,
    )
    assert evidence.status is PropertyStatus.PASSED
    with pytest.raises(PropertyContractError, match="property_version"):
        replace(evidence, property_version="v2")
    with pytest.raises(PropertyContractError, match="passed_error"):
        replace(evidence, error_code="failure")


def test_failed_or_unavailable_evidence_requires_error():
    for status in (PropertyStatus.FAILED, PropertyStatus.UNAVAILABLE):
        with pytest.raises(PropertyContractError, match="missing_error"):
            PropertyEvidence("output.schema.actual", "v1", "case.1", status, D, D, D, D)


def test_independent_signal_is_a_distinct_explicit_contract():
    signal = IndependentSignalEvidence(
        "signal.output_relation", "v1", IndependentSignalKind.METAMORPHIC_RELATION,
        PropertyStatus.PASSED, D, D, D, D, property_evidence_hash=D,
    )
    assert not isinstance(signal, PropertyEvidence)
    assert signal.kind is IndependentSignalKind.METAMORPHIC_RELATION
    assert signal.candidate_id == D
    with pytest.raises(PropertyContractError, match="candidate_id"):
        replace(signal, candidate_id="bad")

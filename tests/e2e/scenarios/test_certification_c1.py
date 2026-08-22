"""C1 checks for the frozen bilingual RM-0006 golden matrix."""
from __future__ import annotations

from collections import defaultdict

from certification.coordinator import read_jsonl, validate_record
from certification.golden_matrix import (
    CASE_PATH,
    EXPECTED_FAMILIES,
    expand_cases,
    load_flows,
    render_jsonl,
)


def test_golden_matrix_has_24_flows_and_48_valid_cases():
    cases, coverage = expand_cases(load_flows())
    assert coverage["logical_flows"] == 24
    assert coverage["cases"] == 48
    assert coverage["families"] == dict(sorted(EXPECTED_FAMILIES.items()))
    assert render_jsonl(cases) == CASE_PATH.read_text(encoding="utf-8")
    for case in cases:
        validate_record("CaseSpec", case)


def test_it_en_pairs_share_the_same_oracle():
    paired = defaultdict(list)
    for case in read_jsonl(CASE_PATH):
        paired[case["logical_flow_id"]].append(case)
    assert len(paired) == 24
    for pair in paired.values():
        assert {case["locale"] for case in pair} == {"it", "en"}
        left, right = pair
        ignored = {"case_id", "locale", "request"}
        assert {
            key: value for key, value in left.items() if key not in ignored
        } == {
            key: value for key, value in right.items() if key not in ignored
        }


def test_requests_are_synthetic_and_have_no_obvious_private_identifiers():
    forbidden = {"roberto", "brunialti", "@gmail", "@outlook", "/home/", "c:\\users\\"}
    for case in read_jsonl(CASE_PATH):
        lowered = case["request"].lower()
        assert not any(marker in lowered for marker in forbidden)
        assert 12 <= len(case["request"]) <= 220

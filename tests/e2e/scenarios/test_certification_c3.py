"""Structural gates for the RM-0006 C3 non-durable HTTP certification."""
from __future__ import annotations

import inspect

from certification.coordinator import read_jsonl
from certification.golden_matrix import CASE_PATH, load_flows
from certification.run_nondurable_http import (
    NON_DURABLE_FAMILIES,
    RevokedIMAPFixture,
    _cases,
)


def test_c3_selects_19_bilingual_non_durable_flows() -> None:
    cases = _cases([])
    assert len(cases) == 38
    assert len({case["logical_flow_id"] for case in cases}) == 19
    assert {case["locale"] for case in cases} == {"it", "en"}
    family_by_flow = {
        flow["logical_flow_id"]: flow["family"]
        for flow in load_flows()["flows"]
    }
    assert {
        family_by_flow[case["logical_flow_id"]] for case in cases
    } == NON_DURABLE_FAMILIES


def test_c3_excludes_device_and_durable_families() -> None:
    selected = {case["case_id"] for case in _cases([])}
    excluded_flows = {
        flow["logical_flow_id"] for flow in load_flows()["flows"]
        if flow["family"] in {"device_owner", "durable_work"}
    }
    excluded = {
        case["case_id"] for case in read_jsonl(CASE_PATH)
        if case["logical_flow_id"] in excluded_flows
    }
    assert len(excluded) == 10
    assert selected.isdisjoint(excluded)


def test_revoked_credential_fixture_is_an_auth_rejection_not_a_dead_port() -> None:
    source = inspect.getsource(RevokedIMAPFixture)
    handler_source = inspect.getsource(
        __import__(
            "certification.run_nondurable_http",
            fromlist=["_RevokedIMAPHandler"],
        )._RevokedIMAPHandler
    )
    assert "PROTOCOL_TLS_SERVER" in source
    assert "AUTHENTICATIONFAILED" in handler_source
    assert "credentials revoked" in handler_source


def test_expected_failure_contract_requires_actionable_credentials() -> None:
    cases = [
        case for case in read_jsonl(CASE_PATH)
        if case["logical_flow_id"] == "failure.credential-revoked"
    ]
    assert len(cases) == 2
    for case in cases:
        assert case["expected_terminal"] == "failed"
        assert case["required_effects"] == ["no_action"]
        assert "credential_action_visible" in case["response_requirements"]
        assert case["postcondition_probes"] == [
            "provider_call_failed", "mailbox_digest_unchanged",
        ]

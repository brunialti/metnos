"""The two authoritative facts that left the configuration file."""
from __future__ import annotations

import pytest

import executor_birth_policy_v1 as policy


def test_the_policy_version_is_owned_by_the_code():
    assert policy.BIRTH_POLICY_VERSION_V1 == "birth-policy-v1"
    assert isinstance(policy.BIRTH_POLICY_VERSION_V1, str)


def test_the_receipt_lifetime_sits_inside_its_declared_range():
    ttl = policy.birth_receipt_ttl_seconds_v1()
    assert ttl == policy.BIRTH_RECEIPT_TTL_SECONDS_V1
    assert policy.BIRTH_RECEIPT_TTL_MINIMUM_V1 <= ttl
    assert ttl <= policy.BIRTH_RECEIPT_TTL_MAXIMUM_V1


def test_a_value_out_of_range_is_a_defect_not_a_surprise(monkeypatch):
    for broken in (0, 59, 86401, True, 3600.0):
        monkeypatch.setattr(policy, "BIRTH_RECEIPT_TTL_SECONDS_V1", broken)
        with pytest.raises(ValueError):
            policy.birth_receipt_ttl_seconds_v1()


def test_the_module_reads_no_configuration():
    import inspect

    source = inspect.getsource(policy)
    # The word "configuration" appears in the docstring on purpose; what must
    # not appear is a way to read one.
    for forbidden in ("import config", "os.environ", "json.load", "open(",
                      "Path(", "read_text", "read_bytes"):
        assert forbidden not in source, forbidden

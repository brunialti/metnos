"""The shared comparison must not hide drift from its policy consumers."""
from __future__ import annotations

import re

import pytest

from policy_test_support import freeze_policy


@pytest.mark.parametrize(("original", "changed"), (
    pytest.param(1, 2, id="value"),
    pytest.param(1, True, id="scalar-type"),
    pytest.param((1,), [1], id="sequence-type"),
    pytest.param(frozenset({1}), frozenset({True}), id="set-member-type"),
    pytest.param({1: "value"}, {True: "value"}, id="mapping-key-type"),
    pytest.param(
        {"rules": {"allow": 1, "deny": 2}},
        {"rules": {"deny": 2, "allow": 1}}, id="nested-mapping-order",
    ),
    pytest.param(re.compile("x"), re.compile("x", re.I), id="regex-flags"),
    pytest.param(
        re.compile("x", re.A), re.compile(b"x", re.A), id="regex-pattern-type",
    ),
))
def test_shared_policy_comparison_rejects_lost_information(original, changed):
    assert freeze_policy(original) != freeze_policy(changed)

"""Whole rotated inventories are bounded, not just each physical journal."""
from dataclasses import replace
import os

import pytest

from executor_birth_retention import RetentionError
from test_birth_retention_llm_cost import native

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX journal custody")


def test_total_segment_budget(native):
    path, owner = native
    for index in range(1, 1001):
        path.with_name(path.name + "." + str(index)).touch(mode=0o600)
    with pytest.raises(RetentionError, match="inventory budget"):
        owner.inventory()


def test_total_bytes_budget_precedes_parsing(native):
    path, owner = native
    with path.with_name(path.name + ".1").open("wb") as stream:
        stream.truncate(128 * 1024 * 1024)
    with pytest.raises(RetentionError, match="inventory budget"):
        owner.inventory()


def test_copy_reference_budget(native):
    _, owner = native
    original = owner.inventory()[0]
    objects = [replace(original, identity=replace(original.identity, store="file:///audit/" + str(index)))
               for index in range(1002)]
    with pytest.raises(RetentionError, match="copy-reference budget"):
        owner.link_copies(objects)


def test_foreign_identity_cannot_dispatch(native):
    _, owner = native
    original = owner.inventory()[0]
    with pytest.raises(RetentionError):
        owner.delete(replace(original.identity, store="file:///foreign/log"), original.version)

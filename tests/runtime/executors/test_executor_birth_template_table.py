"""The closed set of internal templates the Birth gate may use."""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

from executor_birth_template_table_v1 import (
    TEMPLATE_TABLE_DOMAIN_V1, TEMPLATE_TABLE_V1, TemplateTableError,
    template_digest_v1, template_table_digest_v1, template_v1,
)


def test_the_table_holds_exactly_the_three_internal_templates():
    """Closed launchers and review instruction; no caller-selected template."""
    assert sorted(TEMPLATE_TABLE_V1) == [
        "runner.functional_stdin", "runner.linux_launcher", "semantic_review.system",
    ]


def test_an_unlisted_name_is_refused_and_not_an_empty_string():
    """A missing template must stop the gate, never silently produce nothing."""
    for name in ("runner.other", "", None, 7):
        with pytest.raises(TemplateTableError, match="template_not_admitted"):
            template_v1(name)


def test_the_consumers_take_their_template_from_the_table():
    """One owner: what runs and what the context attests are the same text."""
    import executor_birth_semantic_review as review

    assert review._SYSTEM_PROMPT == template_v1("semantic_review.system")
    assert "isolated semantic reviewer" in review._SYSTEM_PROMPT
    launcher = template_v1("runner.linux_launcher")
    assert "cgroup.procs" in launcher and "{STATUS_FD}" in launcher


def test_each_digest_comes_from_the_text_itself():
    """Identity and content cannot drift: one is computed from the other."""
    for name, text in TEMPLATE_TABLE_V1.items():
        assert template_digest_v1(name) == "sha256:" + hashlib.sha256(
            TEMPLATE_TABLE_DOMAIN_V1 + text.encode("utf-8")
        ).hexdigest()


def test_the_table_carries_one_stable_digest():
    """The digest the context component will carry at the last step."""
    assert template_table_digest_v1() == (
        "sha256:428c88b20a0a519a0efc13b001b37ef7380a4e9e73b0d8d6e68c278fd3f12983"
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux launcher identity")
@pytest.mark.parametrize("refused", [None, "gid", "uid"])
def test_linux_launcher_drops_saved_identity_before_any_effect(monkeypatch, refused):
    """The mixed administrative identity must never reach bwrap or a candidate."""
    events = []
    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    monkeypatch.setattr(os, "getegid", lambda: 23456)
    monkeypatch.setattr(sys, "argv", ["launcher", "/scope", "/status", "bwrap"])

    def change(kind, values):
        events.append((kind, values))
        if refused == kind:
            raise PermissionError(kind)

    monkeypatch.setattr(os, "setresgid", lambda *ids: change("gid", ids))
    monkeypatch.setattr(os, "setresuid", lambda *ids: change("uid", ids))

    class ScopeReached(Exception):
        pass

    def open_scope(path, mode):
        assert (path, mode) == ("/scope/cgroup.procs", "w")
        events.append(("scope", None))
        raise ScopeReached

    with pytest.raises(PermissionError if refused else ScopeReached):
        exec(template_v1("runner.linux_launcher"), {"open": open_scope})
    expected = [("gid", (23456,) * 3), ("uid", (12345,) * 3), ("scope", None)]
    assert events == expected[:{"gid": 1, "uid": 2, None: 3}[refused]]

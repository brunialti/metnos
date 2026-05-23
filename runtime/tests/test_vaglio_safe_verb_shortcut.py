#!/usr/bin/env python3
"""Test ADR 0107 — vaglio short-circuit per safe-verbs."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from vaglio import judge, _action_of
from vocab import SAFE_VERBS


def test_action_of_extracts_verb():
    assert _action_of("read_files") == "read"
    assert _action_of("find_urls") == "find"
    assert _action_of("get_inputs") == "get"
    assert _action_of("change_images_size") == "change"


def test_safe_verb_read_files_shortcuts():
    v = judge("leggi", "read_files", {"paths": ["/tmp/x"]})
    assert v.approved is True
    assert v.judge_kind == "safe-verb-shortcut"
    assert v.score == 1.0


def test_safe_verb_find_urls_shortcuts():
    v = judge("trova news", "find_urls",
              {"seed_urls": ["https://example.com"]})
    assert v.approved is True
    assert v.judge_kind == "safe-verb-shortcut"


def test_safe_verb_get_inputs_shortcuts():
    v = judge("chiedi", "get_inputs", {"title": "t", "dialog": []})
    assert v.approved is True
    assert v.judge_kind == "safe-verb-shortcut"


def test_safe_verb_filter_shortcuts():
    v = judge("filtra", "filter_entries", {"entries": []})
    assert v.approved is True
    assert v.judge_kind == "safe-verb-shortcut"


def test_destructive_verb_does_not_shortcut():
    """write/delete/move/send/create devono passare dal giudice normale."""
    for ex in ("write_files", "delete_files", "move_files",
               "send_messages", "create_dirs"):
        v = judge("test", ex, {"paths": ["/tmp/x"]})
        # Possono essere approvati o no, ma NON devono usare safe-verb-shortcut.
        assert v.judge_kind != "safe-verb-shortcut", f"{ex} got safe shortcut"


def test_guard_blocks_safe_verb_with_forbidden_path():
    """Anche un read_files su ~/.ssh/id_rsa deve essere bloccato dalla
    guardia (precede il shortcut)."""
    v = judge("leggi chiave", "read_files", {"path": "/home/u/.ssh/id_rsa"})
    assert v.approved is False
    assert v.blocked_by == "guard"


def test_safe_verbs_set_membership():
    assert "read" in SAFE_VERBS
    assert "find" in SAFE_VERBS
    assert "get" in SAFE_VERBS
    assert "list" in SAFE_VERBS
    assert "filter" in SAFE_VERBS
    assert "describe" in SAFE_VERBS
    assert "classify" in SAFE_VERBS
    assert "compute" in SAFE_VERBS
    assert "compare" in SAFE_VERBS
    # NOT in:
    assert "write" not in SAFE_VERBS
    assert "delete" not in SAFE_VERBS
    assert "move" not in SAFE_VERBS
    assert "send" not in SAFE_VERBS
    assert "create" not in SAFE_VERBS
    assert "extract" not in SAFE_VERBS
    assert "change" not in SAFE_VERBS
    assert "render" not in SAFE_VERBS


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

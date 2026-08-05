"""Development-only checks for the private read-only issues dashboard."""
from __future__ import annotations

from internal.tools import issues_sidecar


def test_list_records_uses_canonical_store_read_only(monkeypatch):
    seen = {}

    class FakeStore:
        def find(self, **kwargs):
            seen.update(kwargs)
            return [{"repo": "example/repo", "issue_number": 7}]

    monkeypatch.setattr(
        issues_sidecar._store, "get_store", lambda name: (
            FakeStore() if name == "github_issue_qa" else None
        ),
    )

    assert issues_sidecar._list_records(limit=5) == [
        {"repo": "example/repo", "issue_number": 7},
    ]
    assert seen == {
        "order": (("issue_number", "desc"),),
        "limit": 5,
    }

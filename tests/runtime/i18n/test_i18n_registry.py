from __future__ import annotations

import hashlib

import pytest

from i18n_registry import LeaseConflict, LocalizationRegistry


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_register_is_idempotent_and_source_drift_stales_old_row(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    first = registry.register("prompt:planner", "prompt", "en", "pt-BR", _sha("v1"))
    again = registry.register("prompt:planner", "prompt", "en", "pt-BR", _sha("v1"))
    assert first.source_hash == again.source_hash
    assert len(registry.resources("pt-BR")) == 1

    registry.register("prompt:planner", "prompt", "en", "pt-BR", _sha("v2"))
    assert len(registry.resources("pt-BR")) == 1
    assert len(registry.resources("pt-BR", current_only=False)) == 2
    assert registry.resources("pt-BR", current_only=False)[0].status == "stale"


def test_claim_is_exclusive_and_stale_worker_cannot_complete(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite", lease_seconds=1)
    registry.register("message:HELLO", "message", "en", "de", _sha("hello"))
    lease = registry.claim("message:HELLO", "de")
    assert lease is not None
    assert registry.claim("message:HELLO", "de") is None
    with pytest.raises(LeaseConflict):
        registry.complete(
            "message:HELLO", "de", _sha("hallo"), "validated",
            lease_token="not-the-token",
        )
    row = registry.complete(
        "message:HELLO", "de", _sha("hallo"), "validated",
        lease_token=lease.lease_token,
    )
    assert row.status == "translated"
    assert registry.coverage("de").complete


def test_attempts_are_bounded_and_escalate_to_manual_review(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite", max_attempts=2)
    registry.register("contract:x", "contract", "en", "fr", _sha("x"))
    first = registry.claim("contract:x", "fr")
    assert first is not None
    assert registry.fail(
        "contract:x", "fr", "invalid_placeholders", lease_token=first.lease_token,
    ).status == "failed"
    second = registry.claim("contract:x", "fr")
    assert second is not None
    assert registry.fail(
        "contract:x", "fr", "invalid_placeholders", lease_token=second.lease_token,
    ).status == "manual_review"
    report = registry.coverage("fr")
    assert report.complete
    assert report.manual_review == ("contract:x",)


def test_review_policy_transition_is_enforced_without_resetting_failures(tmp_path):
    registry = LocalizationRegistry(
        tmp_path / "registry.sqlite", max_attempts=1,
    )
    digest = "c" * 64
    automatic = {"review_policy": "automatic"}
    manual = {"review_policy": "manual"}
    registry.register(
        "input:consent", "input", "en", "de", digest,
        metadata=automatic,
    )
    changed = registry.register(
        "input:consent", "input", "en", "de", digest,
        metadata=manual, manual_review=True,
    )
    assert changed.status == "manual_review"
    assert registry.claim("input:consent", "de") is None

    reopened = registry.register(
        "input:consent", "input", "en", "de", digest,
        metadata=automatic,
    )
    assert reopened.status == "pending"
    lease = registry.claim("input:consent", "de")
    assert lease is not None
    registry.fail(
        "input:consent", "de", "provider_error",
        lease_token=lease.lease_token,
    )
    still_manual = registry.register(
        "input:consent", "input", "en", "de", digest,
        metadata=automatic,
    )
    assert still_manual.status == "manual_review"


def test_language_tags_are_data_not_an_allowlist(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    row = registry.register("x", "message", "en", "sr-Latn", _sha("x"))
    assert row.target_lang == "sr-latn"

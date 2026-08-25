from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from i18n_registry import CandidateConflict, LeaseConflict, LocalizationRegistry


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


def test_existing_registry_is_migrated_to_nullable_basis_idempotently(tmp_path):
    path = tmp_path / "registry.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE localization_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL,
            layer TEXT NOT NULL,
            source_lang TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT,
            lease_expires_at REAL,
            translation_hash TEXT,
            quality TEXT,
            artifact_path TEXT,
            last_error TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(resource_id, target_lang, source_hash)
        );
        INSERT INTO localization_resources
            (resource_id,layer,source_lang,target_lang,source_hash,status,
             metadata_json,created_at,updated_at)
        VALUES
            ('message:HELLO','message','en','nl',
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
             'pending','{}',1.0,1.0);
        """
    )
    connection.close()

    ready = Barrier(4)

    def open_registry(_index):
        ready.wait()
        return LocalizationRegistry(path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        registries = tuple(pool.map(
            open_registry, range(4),
        ))
    first, second = registries[:2]

    assert first.resources("nl")[0].basis_id is None
    with sqlite3.connect(path) as connection:
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(localization_resources)"
            )
        ]
    assert columns.count("basis_id") == 1
    assert second.resources("nl")[0].basis_id is None


def test_contract_basis_change_reopens_and_clears_previous_work(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(path)
    source_hash = _sha("same source")
    first_basis = "snapshot:first"
    second_basis = "snapshot:second"
    third_basis = "snapshot:third"
    resource = "contract:sample:description"
    registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id=first_basis,
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    translated = registry.complete(
        resource,
        "nl",
        _sha("translation"),
        "reviewed",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )

    unchanged = registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id=first_basis,
    )
    assert unchanged == translated

    reopened = registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id=second_basis,
    )
    assert reopened.basis_id == second_basis
    assert reopened.status == "pending"
    assert reopened.attempts == 0
    assert reopened.translation_hash is None
    assert reopened.quality is None
    assert reopened.artifact_path is None
    assert reopened.last_error is None

    active_lease = registry.claim(resource, "nl")
    assert active_lease is not None
    assert active_lease.basis_id == second_basis
    leased_reopened = registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id=third_basis,
    )
    assert leased_reopened.status == "pending"
    assert leased_reopened.attempts == 0
    with pytest.raises(LeaseConflict):
        registry.complete(
            resource,
            "nl",
            _sha("stale translation"),
            "reviewed",
            lease_token=active_lease.lease_token,
        )
    with sqlite3.connect(path) as connection:
        lease_state = connection.execute(
            "SELECT lease_token,lease_expires_at FROM localization_resources "
            "WHERE resource_id=? AND target_lang=? AND source_hash=?",
            (resource, "nl", source_hash),
        ).fetchone()
    assert lease_state == (None, None)

    failure_lease = registry.claim(resource, "nl")
    assert failure_lease is not None
    failed = registry.fail(
        resource,
        "nl",
        "provider_error",
        lease_token=failure_lease.lease_token,
    )
    assert failed.last_error == "provider_error"
    after_failure = registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id="snapshot:fourth",
    )
    assert after_failure.status == "pending"
    assert after_failure.attempts == 0
    assert after_failure.last_error is None


def test_review_and_admission_use_the_exact_candidate_observation(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    resource = "contract:sample:description"
    source_hash = _sha("stable source")

    registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id="snapshot:first", metadata={"selector": "description"},
    )
    first_lease = registry.claim(resource, "nl")
    assert first_lease is not None
    first = registry.complete(
        resource, "nl", _sha("eerste"), "structural",
        lease_token=first_lease.lease_token,
        artifact_path="candidate.json",
    )

    registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id="snapshot:second", metadata={"selector": "description"},
    )
    second_lease = registry.claim(resource, "nl")
    assert second_lease is not None
    second = registry.complete(
        resource, "nl", _sha("tweede"), "structural",
        lease_token=second_lease.lease_token,
        artifact_path="candidate.json",
    )

    with pytest.raises(CandidateConflict, match="stale"):
        registry.review_candidate(first)
    current = registry.resources("nl")[0]
    assert current.basis_id == "snapshot:second"
    assert current.quality == "structural"

    reviewed = registry.review_candidate(second)
    assert reviewed.quality == "reviewed"
    registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id="snapshot:third", metadata={"selector": "description"},
    )
    with pytest.raises(CandidateConflict, match="stale"):
        registry.admit_candidate(reviewed)
    assert registry.resources("nl")[0].status == "pending"


def test_admission_requires_the_exact_reviewed_quality_observation(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    resource = "contract:sample:description"
    registry.register(
        resource, "contract", "en", "nl", _sha("source"),
        basis_id="snapshot:stable", metadata={"selector": "description"},
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    translated = registry.complete(
        resource, "nl", _sha("vertaling"), "structural",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )

    with pytest.raises(CandidateConflict, match="not been reviewed"):
        registry.admit_candidate(translated)

    reviewed = registry.review_candidate(translated)
    changed = registry.review(resource, "nl", quality="rejected")
    assert changed.quality == "rejected"
    with pytest.raises(CandidateConflict, match="stale"):
        registry.admit_candidate(reviewed)
    assert registry.resources("nl")[0].status == "translated"


@pytest.mark.parametrize(
    ("changed_layer", "changed_source_lang"),
    (
        ("message", "en"),
        ("contract", "it"),
    ),
)
def test_registration_identity_change_reopens_same_text_and_basis(
    tmp_path,
    changed_layer,
    changed_source_lang,
):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    resource = "contract:sample:description"
    source_hash = _sha("same text")
    basis = "snapshot:stable"
    registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id=basis, metadata={"selector": "description"},
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    registry.complete(
        resource, "nl", _sha("translation"), "reviewed",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )

    reopened = registry.register(
        resource,
        changed_layer,
        changed_source_lang,
        "nl",
        source_hash,
        basis_id=basis,
        metadata={"selector": "description"},
    )

    assert reopened.status == "pending"
    assert reopened.attempts == 0
    assert reopened.translation_hash is None
    assert reopened.quality is None
    assert reopened.artifact_path is None


def test_metadata_refresh_preserves_work_but_invalidates_stale_judgment(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    resource = "contract:sample:description"
    source_hash = _sha("same text")
    registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id="snapshot:stable", metadata={"route": "first"},
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    observed = registry.complete(
        resource, "nl", _sha("translation"), "structural",
        lease_token=lease.lease_token, artifact_path="candidate.json",
    )

    refreshed = registry.register(
        resource, "contract", "en", "nl", source_hash,
        basis_id="snapshot:stable", metadata={"route": "second"},
    )

    assert refreshed.status == "translated"
    assert refreshed.translation_hash == observed.translation_hash
    assert refreshed.metadata == {"route": "second"}
    with pytest.raises(CandidateConflict, match="stale"):
        registry.review_candidate(observed)


def test_non_contract_registration_without_basis_preserves_current_state(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    source_hash = _sha("hello")
    resource = "message:HELLO"
    registry.register(resource, "message", "en", "nl", source_hash)
    lease = registry.claim(resource, "nl")
    assert lease is not None
    completed = registry.complete(
        resource,
        "nl",
        _sha("hallo"),
        "validated",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )

    unchanged = registry.register(
        resource, "message", "en", "nl", source_hash,
    )

    assert unchanged == completed
    assert unchanged.basis_id is None


def test_explicit_basis_change_reopens_any_resource_layer(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    source_hash = _sha("hello")
    resource = "message:HELLO"
    registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id="catalog:first",
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    registry.complete(
        resource,
        "nl",
        _sha("hallo"),
        "validated",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )

    reopened = registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id="catalog:second",
    )

    assert reopened.basis_id == "catalog:second"
    assert reopened.status == "pending"
    assert reopened.attempts == 0
    assert reopened.translation_hash is None
    assert reopened.quality is None
    assert reopened.artifact_path is None

    second_lease = registry.claim(resource, "nl")
    assert second_lease is not None
    assert second_lease.basis_id == "catalog:second"
    registry.complete(
        resource,
        "nl",
        _sha("hallo again"),
        "validated",
        lease_token=second_lease.lease_token,
        artifact_path="candidate-2.json",
    )
    manual = registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id="catalog:third",
        manual_review=True,
    )
    assert manual.status == "manual_review"
    assert manual.attempts == 0
    assert manual.translation_hash is None
    assert manual.quality is None
    assert manual.artifact_path is None
    assert registry.claim(resource, "nl") is None


@pytest.mark.parametrize(
    ("first_basis", "second_basis"),
    (
        (None, "catalog:versioned"),
        ("catalog:versioned", None),
    ),
)
def test_nullable_basis_transitions_reopen_work(
    tmp_path, first_basis, second_basis,
):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    source_hash = _sha("hello")
    resource = "message:HELLO"
    registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id=first_basis,
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    registry.complete(
        resource,
        "nl",
        _sha("hallo"),
        "validated",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )

    reopened = registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id=second_basis,
    )

    assert reopened.basis_id == second_basis
    assert reopened.status == "pending"
    assert reopened.attempts == 0
    assert reopened.translation_hash is None
    assert reopened.quality is None
    assert reopened.artifact_path is None


def test_same_basis_preserves_an_active_lease(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    source_hash = _sha("hello")
    resource = "message:HELLO"
    registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id="catalog:stable",
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None

    unchanged = registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id="catalog:stable",
    )

    assert unchanged.status == "leased"
    assert unchanged.attempts == 1
    completed = registry.complete(
        resource,
        "nl",
        _sha("hallo"),
        "validated",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )
    assert completed.status == "translated"


def test_return_to_historical_source_and_basis_reopens_current_work(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    resource = "contract:sample:description"
    first_hash = _sha("source v1")
    second_hash = _sha("source v2")
    registry.register(
        resource,
        "contract",
        "en",
        "nl",
        first_hash,
        basis_id="snapshot:first",
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    registry.complete(
        resource,
        "nl",
        _sha("translation v1"),
        "validated",
        lease_token=lease.lease_token,
        artifact_path="candidate-v1.json",
    )
    registry.register(
        resource,
        "contract",
        "en",
        "nl",
        second_hash,
        basis_id="snapshot:second",
    )

    restored = registry.register(
        resource,
        "contract",
        "en",
        "nl",
        first_hash,
        basis_id="snapshot:first",
    )

    assert restored.status == "pending"
    assert restored.attempts == 0
    assert restored.translation_hash is None
    assert restored.quality is None
    assert restored.artifact_path is None
    assert registry.resources("nl") == (restored,)
    historical = registry.resources("nl", current_only=False)
    assert next(row for row in historical if row.source_hash == second_hash).status == "stale"


def test_concurrent_basis_changes_leave_one_clean_current_state(tmp_path):
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    source_hash = _sha("hello")
    resource = "message:HELLO"
    registry.register(
        resource,
        "message",
        "en",
        "nl",
        source_hash,
        basis_id="catalog:first",
    )
    lease = registry.claim(resource, "nl")
    assert lease is not None
    registry.complete(
        resource,
        "nl",
        _sha("hallo"),
        "validated",
        lease_token=lease.lease_token,
        artifact_path="candidate.json",
    )
    ready = Barrier(2)

    def change_basis(basis_id):
        ready.wait()
        return registry.register(
            resource,
            "message",
            "en",
            "nl",
            source_hash,
            basis_id=basis_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = tuple(pool.map(
            change_basis, ("catalog:second", "catalog:third"),
        ))

    assert {row.basis_id for row in changed} == {
        "catalog:second", "catalog:third",
    }
    current = registry.resources("nl")
    assert len(current) == 1
    assert current[0].basis_id in {"catalog:second", "catalog:third"}
    assert current[0].status == "pending"
    assert current[0].attempts == 0
    assert current[0].translation_hash is None
    assert current[0].quality is None
    assert current[0].artifact_path is None

"""What a preserved legacy row still obliges, and where that decision lands."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import executor_birth_lifecycle_migration as migration
from executor_birth_epoch_store import (
    EpochStoreError, legacy_rows_digest, preserve_legacy_rows,
    read_legacy_resolutions, record_legacy_resolutions,
)
from executor_birth_lifecycle_migration import (
    Disposition, LegacyEffect, LegacyRowFacts, LifecycleMigrationError,
)


SOURCE = "sha256:" + "1" * 64
SCHEMA = "sha256:" + "2" * 64
GENERATION = "sha256:" + "3" * 64
CONTRACT = "user:demo/manifest.toml"
WHEN = "2026-09-16T23:00:00Z"


def selectable(*names):
    return {name: (f"user:{name}/manifest.toml", "sha256:" + str(index + 3) * 64)
            for index, name in enumerate(names)}


def facts(rows):
    return migration.statistics_facts(
        rows, body_digests=[f"d{index}" for index in range(len(rows))])


# --- the decision ------------------------------------------------------------

def test_an_effective_restriction_reaches_the_selected_generation():
    plan = migration.plan_dispositions(
        facts([{"name": "demo", "archived_at": WHEN}]), selectable=selectable("demo"))
    assert len(plan) == 1
    only = plan[0]
    assert only.kind is Disposition.CURRENT_RESTRICTION
    assert only.effect is LegacyEffect.REMOVED
    assert (only.contract_id, only.generation_id) == (CONTRACT, GENERATION)


def test_archiving_outranks_deprecation_exactly_as_the_old_reader_decided():
    plan = migration.plan_dispositions(
        facts([{"name": "demo", "deprecated_at": WHEN, "archived_at": WHEN}]),
        selectable=selectable("demo"))
    assert plan[0].effect is LegacyEffect.REMOVED


def test_a_row_that_asserts_nothing_carries_nothing():
    plan = migration.plan_dispositions(
        facts([{"name": "demo"}]), selectable=selectable("demo"))
    assert plan[0].kind is Disposition.ATTESTED
    assert plan[0].effect is LegacyEffect.NONE
    assert plan[0].generation_id == GENERATION


def test_counters_and_instants_are_never_carried_across():
    """Only a present restriction crosses; historical data stays evidence."""
    plan = migration.plan_dispositions(
        facts([{"name": "demo", "total_calls": 900, "last_used_at": WHEN,
                "deprecated_at": WHEN}]),
        selectable=selectable("demo"))
    projected = json.dumps(plan[0].__dict__ if hasattr(plan[0], "__dict__") else {
        field: getattr(plan[0], field) for field in
        ("ordinal", "kind", "contract_id", "generation_id", "reason")
    }, default=str)
    assert "900" not in projected


@pytest.mark.parametrize("row", [
    {"name": "gone", "archived_at": WHEN},
    {"name": None, "deprecated_at": WHEN},
    {"deprecated_at": WHEN},
], ids=["unknown-name", "null-name", "no-name"])
def test_a_restriction_without_a_selectable_generation_awaits_disposition(row):
    plan = migration.plan_dispositions(facts([row]), selectable=selectable("demo"))
    only = plan[0]
    assert only.kind is Disposition.PENDING_DISPOSITION
    assert (only.contract_id, only.generation_id) == (None, None)
    # The restriction is retained, so retirement cannot drop it in silence.
    assert only.effect is not LegacyEffect.NONE


def test_an_unrestricted_unknown_name_is_simply_discarded():
    plan = migration.plan_dispositions(facts([{"name": "gone"}]), selectable={})
    assert plan[0].kind is Disposition.DISCARDED


def test_an_open_promotion_is_never_decided_by_the_migration():
    rows = [{"executor_name": "demo", "state": "promoted_grace"},
            {"executor_name": "demo", "state": "archived"},
            {"executor_name": "demo", "state": "a state nobody knows"}]
    plan = migration.plan_dispositions(
        migration.promoter_facts(rows, body_digests=["a", "b", "c"]),
        selectable=selectable("demo"))
    kinds = [item.kind for item in plan]
    assert kinds[0] is Disposition.PENDING_DISPOSITION
    assert kinds[1] is Disposition.ATTESTED
    # An unrecognised state is treated as open, never as closed.
    assert kinds[2] is Disposition.PENDING_DISPOSITION


def test_the_plan_digest_changes_with_any_decision():
    first = migration.plan_dispositions(
        facts([{"name": "demo", "deprecated_at": WHEN}]), selectable=selectable("demo"))
    second = migration.plan_dispositions(
        facts([{"name": "demo", "archived_at": WHEN}]), selectable=selectable("demo"))
    assert migration.plan_digest_v1(first) != migration.plan_digest_v1(second)
    assert migration.plan_digest_v1(first) == migration.plan_digest_v1(
        migration.plan_dispositions(
            facts([{"name": "demo", "deprecated_at": WHEN}]),
            selectable=selectable("demo")))


@pytest.mark.parametrize("ordinals", [(1, 0), (0, 0)], ids=["unordered", "duplicate"])
def test_a_malformed_row_sequence_is_refused(ordinals):
    rows = tuple(LegacyRowFacts(ordinal, f"d{index}", "demo", LegacyEffect.NONE)
                 for index, ordinal in enumerate(ordinals))
    with pytest.raises(LifecycleMigrationError):
        migration.plan_dispositions(rows, selectable={})


def test_a_restriction_cannot_be_recorded_without_its_identity():
    with pytest.raises(LifecycleMigrationError):
        migration.LegacyDispositionV1(
            0, Disposition.CURRENT_RESTRICTION, None, None,
            LegacyEffect.REMOVED, "invented", "sha256:" + "9" * 64)


# --- where the decision lands ------------------------------------------------

def migration_id(rows, table="executor_stats"):
    return "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.legacy-migration-id/v2\0"
        + json.dumps([SOURCE, SCHEMA, table, legacy_rows_digest(rows)],
                     ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


@pytest.fixture
def preserved(tmp_path):
    db_path = tmp_path / "executor_epochs.sqlite"
    rows = ({"name": "demo", "archived_at": WHEN}, {"name": "other"})
    assert preserve_legacy_rows(
        source_id=SOURCE, source_schema_id=SCHEMA, legacy_table="executor_stats",
        rows=rows, migrated_at=WHEN, db_path=db_path) == 2
    return db_path, rows, migration_id(rows)


def decisions():
    return [(0, "current_restriction", CONTRACT, GENERATION, "sha256:" + "4" * 64),
            (1, "discarded", None, None, "sha256:" + "5" * 64)]


def test_preservation_keeps_verifying_after_a_decision_is_recorded(preserved):
    """The defect this separation exists for: retry after a valid resolution."""
    db_path, rows, mid = preserved
    assert record_legacy_resolutions(
        migration_id=mid, resolutions=decisions(), recorded_at=WHEN,
        db_path=db_path) == 2
    assert preserve_legacy_rows(
        source_id=SOURCE, source_schema_id=SCHEMA, legacy_table="executor_stats",
        rows=rows, migrated_at="2026-09-17T00:00:00Z", db_path=db_path) == 0


def test_recording_the_same_decision_twice_writes_nothing(preserved):
    db_path, _rows, mid = preserved
    record_legacy_resolutions(migration_id=mid, resolutions=decisions(),
                              recorded_at=WHEN, db_path=db_path)
    assert record_legacy_resolutions(
        migration_id=mid, resolutions=decisions(),
        recorded_at="2026-09-17T00:00:00Z", db_path=db_path) == 0
    assert [(row["source_ordinal"], row["resolution_kind"]) for row in
            read_legacy_resolutions(migration_id=mid, db_path=db_path)] == [
        (0, "current_restriction"), (1, "discarded")]


def test_a_different_decision_for_the_same_row_fails_closed(preserved):
    db_path, _rows, mid = preserved
    record_legacy_resolutions(migration_id=mid, resolutions=decisions(),
                              recorded_at=WHEN, db_path=db_path)
    with pytest.raises(EpochStoreError) as raised:
        record_legacy_resolutions(
            migration_id=mid,
            resolutions=[(1, "attested", CONTRACT, GENERATION, "sha256:" + "6" * 64)],
            recorded_at=WHEN, db_path=db_path)
    assert raised.value.code == "legacy_resolution_conflict"


@pytest.mark.parametrize("resolution,detail", [
    ((0, "invented", None, None, "sha256:" + "4" * 64), "resolution_kind"),
    ((0, "attested", CONTRACT, None, "sha256:" + "4" * 64), "identity pair"),
    ((0, "attested", "no-colon-separator", GENERATION, "sha256:" + "4" * 64), "contract_id"),
    ((0, "discarded", None, None, "not-a-digest"), "evidence_id"),
    ((99, "discarded", None, None, "sha256:" + "4" * 64), "unknown ordinal"),
])
def test_an_invented_decision_never_reaches_the_store(preserved, resolution, detail):
    db_path, _rows, mid = preserved
    with pytest.raises(EpochStoreError) as raised:
        record_legacy_resolutions(migration_id=mid, resolutions=[resolution],
                                  recorded_at=WHEN, db_path=db_path)
    assert raised.value.detail == detail


def test_a_decision_for_another_migration_is_refused(preserved):
    db_path, _rows, _mid = preserved
    with pytest.raises(EpochStoreError):
        record_legacy_resolutions(
            migration_id="sha256:" + "8" * 64, resolutions=decisions(),
            recorded_at=WHEN, db_path=db_path)

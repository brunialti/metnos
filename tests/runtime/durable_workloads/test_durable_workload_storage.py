from __future__ import annotations

import sqlite3

import pytest

from durable_workloads.inventory import (
    InventoryLimits,
    SealedInventory,
    seal_local_inventory,
)
from durable_workloads.migrations import utc_now
from durable_workloads.models import WorkloadState
from durable_workloads.schema import SchemaValidationError
from durable_workloads.storage import (
    DurableWorkloadStore,
    IdempotencyConflictError,
    OwnerRequiredError,
    VersionConflictError,
    WorkloadNotFoundError,
)
from helpers import artifact_requirement, inventory, plan, source


@pytest.fixture
def store(tmp_path):
    repository = DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    )
    try:
        yield repository
    finally:
        repository.close()


def _admit(
    store,
    *,
    owner="owner-a",
    request_key="request-a",
    selected_plan=None,
    selected_inventory=None,
    **revision_flags,
):
    draft = store.create_draft(
        owner, request_key, redacted_request={"summary": "synthetic fixture"}
    )
    revision = store.admit_revision(
        owner,
        draft.workload_id,
        selected_plan or plan(),
        selected_inventory or inventory(),
        expected_version=draft.version,
        **revision_flags,
    )
    return draft, revision


def _running(store, draft, *, owner="owner-a"):
    admitted = store.get_workload(owner, draft.workload_id)
    queued = store.transition_workload(
        owner, draft.workload_id, WorkloadState.QUEUED,
        expected_version=admitted.version,
    )
    return store.transition_workload(
        owner, draft.workload_id, WorkloadState.RUNNING,
        expected_version=queued.version,
    )


def test_submit_is_idempotent_and_owner_ids_can_overlap(store):
    first = store.create_draft(
        "owner-a", "same-request", redacted_request={"summary": "same"},
        workload_id="shared-workload-id",
    )
    replay = store.create_draft(
        "owner-a", "same-request", redacted_request={"summary": "same"},
        workload_id="ignored-new-id",
    )
    second_owner = store.create_draft(
        "owner-b", "same-request", redacted_request={"summary": "same"},
        workload_id="shared-workload-id",
    )
    assert replay == first
    assert second_owner.workload_id == first.workload_id
    assert second_owner.owner_user_id != first.owner_user_id
    with pytest.raises(IdempotencyConflictError):
        store.create_draft(
            "owner-a", "same-request",
            redacted_request={"summary": "different"},
        )
    assert len(store.list_workloads("owner-a")) == 1
    assert len(store.list_workloads("owner-b")) == 1


def test_active_submission_lookup_is_exact_owner_scoped_and_nonterminal(store):
    scope = "sha256:" + "a" * 64
    other_scope = "sha256:" + "b" * 64
    first = store.create_draft(
        "owner-a", "scoped-first", redacted_request={"submission_scope": scope},
    )
    other_owner = store.create_draft(
        "owner-b", "scoped-other-owner", redacted_request={"submission_scope": scope},
    )
    different = store.create_draft(
        "owner-a", "scoped-different", redacted_request={"submission_scope": other_scope},
    )
    assert store.find_active_submission("owner-a", scope) == first
    assert store.find_active_submission("owner-b", scope) == other_owner
    assert store.find_active_submission("owner-a", other_scope) == different
    assert store.find_active_submission("owner-c", scope) is None
    store.transition_workload(
        "owner-a", first.workload_id, WorkloadState.CANCELLED,
        expected_version=first.version,
    )
    assert store.find_active_submission("owner-a", scope) is None
    assert store.find_active_submission("owner-b", scope) == other_owner


@pytest.mark.parametrize("scope", [None, 123, "", "a" * 64, "sha256:" + "A" * 64])
def test_active_submission_rejects_noncanonical_digest(store, scope):
    with pytest.raises(ValueError, match="scope"):
        store.find_active_submission("owner-a", scope)


def test_named_transaction_boundaries_rollback_or_replay_without_duplicates(
    tmp_path,
):
    events = []
    injected_at = ["workload_transaction_before_commit"]

    def checkpoint(name):
        events.append(name)
        if name == injected_at[0]:
            raise RuntimeError(f"injected at {name}")

    database = tmp_path / "fault-points" / "durable.sqlite3"
    with DurableWorkloadStore.open(database, checkpoint=checkpoint) as repository:
        with pytest.raises(RuntimeError, match="before_commit"):
            repository.create_draft(
                "owner-a",
                "before-commit",
                redacted_request={"summary": "fixture"},
            )
        assert repository._connection.in_transaction is False
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM workloads"
        ).fetchone()[0] == 0
        assert events == [
            "workload_transaction_before_begin",
            "workload_transaction_after_begin",
            "workload_transaction_before_commit",
            "workload_transaction_before_rollback",
            "workload_transaction_after_rollback",
        ]

        events.clear()
        injected_at[0] = "workload_transaction_after_commit"
        with pytest.raises(RuntimeError, match="after_commit"):
            repository.create_draft(
                "owner-a",
                "after-commit",
                redacted_request={"summary": "fixture"},
            )
        assert repository._connection.in_transaction is False
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM workloads"
        ).fetchone()[0] == 1

        injected_at[0] = "disabled"
        replay = repository.create_draft(
            "owner-a",
            "after-commit",
            redacted_request={"summary": "fixture"},
        )
        assert replay.request_key == "after-commit"
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM workloads"
        ).fetchone()[0] == 1


def test_revision_replay_requires_identical_snapshots_and_facts(store):
    draft = store.create_draft(
        "owner-a", "revision-replay", redacted_request={"summary": "same"}
    )
    selected_plan = plan()
    selected_inventory = inventory()
    revision = store.admit_revision(
        "owner-a", draft.workload_id, selected_plan, selected_inventory,
        expected_version=draft.version, usage_complete=True,
    )
    replay = store.admit_revision(
        "owner-a", draft.workload_id, selected_plan, selected_inventory,
        expected_version=draft.version, usage_complete=True,
    )
    assert replay == revision
    with pytest.raises(IdempotencyConflictError):
        store.admit_revision(
            "owner-a", draft.workload_id, selected_plan, selected_inventory,
            expected_version=draft.version, usage_complete=False,
        )


def test_admission_uses_the_declared_inventory_stage_key(store):
    selected_plan = plan(with_map=True)
    selected_plan["stages"][0]["key"] = "scan"
    selected_plan["stages"][1]["depends_on"] = ["scan"]
    draft = store.create_draft(
        "owner-a", "renamed-inventory", redacted_request={"summary": "same"}
    )
    revision = store.admit_revision(
        "owner-a", draft.workload_id, selected_plan,
        inventory([source(0)]), expected_version=draft.version,
    )
    assert store._connection.execute(
        """
        SELECT COUNT(*) FROM units u
        JOIN stages s ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
        WHERE u.owner_user_id=? AND u.revision_id=? AND s.stage_key='map'
        """,
        ("owner-a", revision.revision_id),
    ).fetchone()[0] == 1


def test_admission_rejects_inventory_outside_declared_caps(store):
    selected_plan = plan(with_map=True)
    selected_plan["inventory"]["max_sources"] = 0
    draft = store.create_draft(
        "owner-a", "inventory-over-cap", redacted_request={"summary": "same"}
    )
    with pytest.raises(SchemaValidationError, match="max_sources"):
        store.admit_revision(
            "owner-a", draft.workload_id, selected_plan,
            inventory([source(0)]), expected_version=draft.version,
        )


def test_admission_consumes_a_repeatable_disk_backed_inventory(
    store,
    tmp_path,
    monkeypatch,
):
    import durable_workloads.inventory as inventory_module

    root = tmp_path / "admission-sources"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.txt").write_text(str(index), encoding="utf-8")
    monkeypatch.setattr(inventory_module, "_IN_MEMORY_SOURCE_LIMIT", 2)
    sealed = seal_local_inventory(
        [root],
        device_id="device-a",
        limits=InventoryLimits(
            max_sources=3,
            max_total_bytes=1024,
            max_depth=1,
        ),
    )
    assert isinstance(sealed, SealedInventory)
    try:
        selected_plan = plan(with_map=True)
        selected_plan["inventory"]["max_sources"] = 3
        selected_plan["inventory"]["max_total_bytes"] = 1024
        draft = store.create_draft(
            "owner-a",
            "spooled-admission",
            redacted_request={"summary": "synthetic fixture"},
        )
        revision = store.admit_revision(
            "owner-a",
            draft.workload_id,
            selected_plan,
            sealed,
            expected_version=draft.version,
        )
        assert store._connection.execute(
            "SELECT COUNT(*) FROM sources WHERE revision_id=?",
            (revision.revision_id,),
        ).fetchone()[0] == 3
        assert store._connection.execute(
            """
            SELECT COUNT(*) FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.id=unit.stage_id
            WHERE unit.revision_id=? AND stage.stage_key='map'
            """,
            (revision.revision_id,),
        ).fetchone()[0] == 3
    finally:
        sealed.close()


def test_owner_scope_blocks_idor_without_revealing_foreign_rows(store):
    workload = store.create_draft(
        "owner-a", "private-request", redacted_request={"summary": "private"}
    )
    with pytest.raises(WorkloadNotFoundError, match="not found"):
        store.get_workload("owner-b", workload.workload_id)
    with pytest.raises(WorkloadNotFoundError, match="not found"):
        store.request_cancel(
            "owner-b", workload.workload_id,
            expected_version=workload.version, idempotency_key="cancel-foreign",
        )
    with pytest.raises(OwnerRequiredError):
        store.get_workload("", workload.workload_id)
    with pytest.raises(OwnerRequiredError):
        store.get_workload("x" * 161, workload.workload_id)


def test_control_commands_use_cas_and_payload_bound_idempotency(store):
    draft, _revision = _admit(store, usage_complete=True)
    admitted = store.get_workload("owner-a", draft.workload_id)
    queued = store.transition_workload(
        "owner-a", draft.workload_id, WorkloadState.QUEUED,
        expected_version=admitted.version,
    )
    paused = store.request_pause(
        "owner-a", draft.workload_id,
        expected_version=queued.version, idempotency_key="pause-1",
    )
    assert paused.state is WorkloadState.PAUSED
    assert store.request_pause(
        "owner-a", draft.workload_id,
        expected_version=queued.version, idempotency_key="pause-1",
    ) == paused
    with pytest.raises(IdempotencyConflictError):
        store.request_cancel(
            "owner-a", draft.workload_id,
            expected_version=queued.version, idempotency_key="pause-1",
        )
    with pytest.raises(VersionConflictError):
        store.request_resume(
            "owner-a", draft.workload_id,
            expected_version=queued.version, idempotency_key="resume-stale",
        )
    resumed = store.request_resume(
        "owner-a", draft.workload_id,
        expected_version=paused.version, idempotency_key="resume-1",
    )
    assert resumed.state is WorkloadState.QUEUED


def test_attention_resolution_is_recorded_and_idempotent(store):
    draft, _revision = _admit(store)
    admitted = store.get_workload("owner-a", draft.workload_id)
    attention = store.transition_workload(
        "owner-a", draft.workload_id, WorkloadState.NEEDS_ATTENTION,
        expected_version=admitted.version, payload={"reason": "fixture"},
    )
    resolved = store.record_attention_resolution(
        "owner-a", draft.workload_id, decision="retry",
        expected_version=attention.version, idempotency_key="resolution-1",
        note_redacted="retry synthetic source",
    )
    assert resolved.state is WorkloadState.QUEUED
    assert store.record_attention_resolution(
        "owner-a", draft.workload_id, decision="retry",
        expected_version=attention.version, idempotency_key="resolution-1",
        note_redacted="retry synthetic source",
    ) == resolved
    count = store._connection.execute(
        "SELECT COUNT(*) FROM attention_resolutions WHERE owner_user_id='owner-a'"
    ).fetchone()[0]
    assert count == 1


def test_attention_cancel_replay_returns_the_settled_terminal_record(store):
    draft, _revision = _admit(store)
    admitted = store.get_workload("owner-a", draft.workload_id)
    attention = store.transition_workload(
        "owner-a", draft.workload_id, WorkloadState.NEEDS_ATTENTION,
        expected_version=admitted.version, payload={"reason": "fixture"},
    )
    cancelled = store.record_attention_resolution(
        "owner-a", draft.workload_id, decision="cancel",
        expected_version=attention.version, idempotency_key="resolution-cancel",
    )
    replay = store.record_attention_resolution(
        "owner-a", draft.workload_id, decision="cancel",
        expected_version=attention.version, idempotency_key="resolution-cancel",
    )
    assert cancelled.state is WorkloadState.CANCELLED
    assert replay == cancelled


def test_transaction_rolls_back_on_baseexception_and_reuses_connection(store):
    draft = store.create_draft(
        "owner-a", "baseexception-transaction",
        redacted_request={"summary": "fixture"},
    )

    class InjectedInterrupt(BaseException):
        pass

    with pytest.raises(InjectedInterrupt):
        with store._transaction() as connection:
            connection.execute(
                "UPDATE workloads SET priority='high' "
                "WHERE owner_user_id=? AND id=?",
                ("owner-a", draft.workload_id),
            )
            raise InjectedInterrupt()

    assert store._connection.in_transaction is False
    assert store.get_workload("owner-a", draft.workload_id).priority == "normal"
    store.create_draft(
        "owner-a", "baseexception-transaction-next",
        redacted_request={"summary": "fixture"},
    )


def test_state_and_event_roll_back_together(store, monkeypatch):
    draft, _revision = _admit(store)
    admitted = store.get_workload("owner-a", draft.workload_id)
    before_events = store.list_events("owner-a", draft.workload_id)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(store, "append_event_in_transaction", fail_event)
    with pytest.raises(RuntimeError, match="injected"):
        store.transition_workload(
            "owner-a", draft.workload_id, WorkloadState.QUEUED,
            expected_version=admitted.version,
        )
    after = store.get_workload("owner-a", draft.workload_id)
    assert after.state is WorkloadState.ADMITTED
    assert after.version == admitted.version
    assert store.list_events("owner-a", draft.workload_id) == before_events


def test_completion_creates_terminal_event_and_outbox_atomically(store):
    draft, _revision = _admit(store, usage_complete=True)
    running = _running(store, draft)
    assessment = store.evaluate_completion("owner-a", draft.workload_id)
    assert assessment.eligible is True
    assert assessment.target_state is WorkloadState.COMPLETED
    assert assessment.workload_version == running.version + 1
    assert assessment.event_id is not None
    terminal = store.get_workload("owner-a", draft.workload_id)
    assert terminal.state is WorkloadState.COMPLETED
    outbox = store._connection.execute(
        """
        SELECT COUNT(*) FROM outbox
        WHERE owner_user_id=? AND workload_id=? AND event_id=?
        """,
        ("owner-a", draft.workload_id, assessment.event_id),
    ).fetchone()[0]
    assert outbox == 2
    states = store._connection.execute(
        """
        SELECT channel, state FROM outbox
        WHERE owner_user_id=? AND workload_id=? AND event_id=?
        ORDER BY channel
        """,
        ("owner-a", draft.workload_id, assessment.event_id),
    ).fetchall()
    assert [(row["channel"], row["state"]) for row in states] == [
        ("owner_event", "sent"),
        ("telegram", "pending"),
    ]
    event_count = len(store.list_events("owner-a", draft.workload_id))
    replay = store.evaluate_completion("owner-a", draft.workload_id)
    assert replay.target_state is WorkloadState.COMPLETED
    assert len(store.list_events("owner-a", draft.workload_id)) == event_count


@pytest.mark.parametrize(
    "revision_flags,expected_reason",
    [
        ({"caps_truncated": True, "usage_complete": True}, "cap_or_truncation_unaccepted"),
        ({"usage_complete": False}, "usage_not_materialized"),
    ],
)
def test_completion_rejects_caps_and_unmaterialized_usage(
    store, revision_flags, expected_reason,
):
    draft, _revision = _admit(store, **revision_flags)
    _running(store, draft)
    assessment = store.evaluate_completion("owner-a", draft.workload_id)
    assert assessment.eligible is False
    assert expected_reason in assessment.reasons


def test_completion_rejects_missing_or_unaccounted_source(store):
    selected_inventory = inventory([
        source(0, state="missing", accounted=False),
    ])
    draft, _revision = _admit(
        store, selected_inventory=selected_inventory, usage_complete=True,
    )
    _running(store, draft)
    reasons = store.evaluate_completion("owner-a", draft.workload_id).reasons
    assert "sources_unaccounted" in reasons
    assert "source_unstable_or_missing" in reasons


def test_completion_rejects_unaccepted_accounted_source_skip(store):
    selected_inventory = inventory([
        source(0, state="skipped", accounted=True),
    ])
    draft, _revision = _admit(
        store, selected_inventory=selected_inventory, usage_complete=True,
    )
    _running(store, draft)
    reasons = store.evaluate_completion("owner-a", draft.workload_id).reasons
    assert "source_skip_unaccepted" in reasons


def test_completion_rejects_missing_required_artifact(store):
    selected_plan = plan(required_artifacts=[artifact_requirement()])
    draft, _revision = _admit(
        store, selected_plan=selected_plan, usage_complete=True,
    )
    _running(store, draft)
    assessment = store.evaluate_completion("owner-a", draft.workload_id)
    assert "required_artifact_missing" in assessment.reasons


def test_completion_rejects_invalid_artifact_proofs(store):
    requirement = artifact_requirement()
    draft, revision = _admit(
        store, selected_plan=plan(required_artifacts=[requirement]),
        usage_complete=True,
    )
    now = utc_now()
    store._connection.execute(
        """
        INSERT INTO artifacts(
            owner_user_id, id, workload_id, revision_id, logical_name,
            digest, mime_type, size_bytes, schema_version, state, blob_ref,
            digest_verified, schema_valid, postconditions_valid,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'committed', ?, 1, 0, 1, ?, ?)
        """,
        (
            "owner-a", "artifact-test-01", draft.workload_id,
            revision.revision_id, requirement["name"],
            "sha256:" + "a" * 64, requirement["mime_type"],
            requirement["schema_version"], "blob:test", now, now,
        ),
    )
    _running(store, draft)
    reasons = store.evaluate_completion("owner-a", draft.workload_id).reasons
    assert "artifact_validation_incomplete" in reasons


def test_completion_rejects_an_artifact_with_the_wrong_mime_type(store):
    requirement = artifact_requirement()
    draft, revision = _admit(
        store, selected_plan=plan(required_artifacts=[requirement]),
        usage_complete=True,
    )
    now = utc_now()
    store._connection.execute(
        """
        INSERT INTO artifacts(
            owner_user_id, id, workload_id, revision_id, logical_name,
            digest, mime_type, size_bytes, schema_version, state, blob_ref,
            digest_verified, schema_valid, postconditions_valid,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'text/plain', 1, ?, 'committed', ?,
                  1, 1, 1, ?, ?)
        """,
        (
            "owner-a", "artifact-mime-01", draft.workload_id,
            revision.revision_id, requirement["name"],
            "sha256:" + "a" * 64, requirement["schema_version"],
            "blob:test", now, now,
        ),
    )
    _running(store, draft)
    reasons = store.evaluate_completion("owner-a", draft.workload_id).reasons
    assert "artifact_mime_type_mismatch" in reasons


def test_completion_rejects_partial_result_without_acceptance(store):
    selected_plan = plan(
        with_map=True,
        error_mode="declared",
        allowed_error_classes=["ocr_error"],
    )
    draft, revision = _admit(
        store,
        selected_plan=selected_plan,
        selected_inventory=inventory([source(0)]),
        usage_complete=True,
    )
    store._connection.execute(
        """
        UPDATE units
        SET state='failed_permanent', error_class='ocr_error', partial_output=1
        WHERE owner_user_id=? AND revision_id=?
        """,
        ("owner-a", revision.revision_id),
    )
    _running(store, draft)
    assessment = store.evaluate_completion("owner-a", draft.workload_id)
    assert assessment.eligible is False
    assert "partial_output_unaccepted" in assessment.reasons


def test_explicitly_accepted_partial_result_completes_with_errors(store):
    selected_plan = plan(
        with_map=True,
        error_mode="declared",
        allowed_error_classes=["ocr_error"],
    )
    draft, revision = _admit(
        store,
        selected_plan=selected_plan,
        selected_inventory=inventory([source(0)]),
        usage_complete=True,
        partial_output_accepted=True,
    )
    store._connection.execute(
        """
        UPDATE units
        SET state='failed_permanent', error_class='ocr_error', partial_output=1
        WHERE owner_user_id=? AND revision_id=?
        """,
        ("owner-a", revision.revision_id),
    )
    _running(store, draft)
    assessment = store.evaluate_completion("owner-a", draft.workload_id)
    assert assessment.eligible is True
    assert assessment.target_state is WorkloadState.COMPLETED_WITH_ERRORS


def test_completion_rejects_unresolved_result_dependencies(store):
    draft, revision = _admit(
        store,
        selected_plan=plan(with_map=True),
        selected_inventory=inventory([source(0)]),
        usage_complete=True,
    )
    connection = store._connection
    unit = connection.execute(
        "SELECT id FROM units WHERE owner_user_id=? AND revision_id=?",
        ("owner-a", revision.revision_id),
    ).fetchone()
    assert unit is not None
    now = utc_now()
    attempt_id = "attempt-test-01"
    result_id = "result-test-001"
    connection.execute(
        """
        INSERT INTO attempts(
            owner_user_id, id, unit_id, number, fence, worker_id, state,
            started_at, ended_at, executor_snapshot_json,
            model_snapshot_json, metrics_json
        ) VALUES (?, ?, ?, 1, 1, 'worker-test', 'succeeded', ?, ?, '{}', '{}', '{}')
        """,
        ("owner-a", attempt_id, unit["id"], now, now),
    )
    connection.execute(
        """
        INSERT INTO results(
            owner_user_id, id, revision_id, unit_id, attempt_id, fence,
            digest, schema_version, payload_json, provenance_json, committed_at
        ) VALUES (?, ?, ?, ?, ?, 1, ?, 'metnos.test-map/1', '{}', '{}', ?)
        """,
        (
            "owner-a", result_id, revision.revision_id, unit["id"],
            attempt_id, "sha256:" + "b" * 64, now,
        ),
    )
    with pytest.raises(sqlite3.IntegrityError, match="result is immutable"):
        connection.execute(
            """
            UPDATE results SET digest=? WHERE owner_user_id=? AND id=?
            """,
            ("sha256:" + "c" * 64, "owner-a", result_id),
        )
    connection.execute(
        """
        UPDATE units
        SET state='committed', fence=1, attempt_count=1,
            expected_dependency_count=1, committed_result_id=?
        WHERE owner_user_id=? AND id=?
        """,
        (result_id, "owner-a", unit["id"]),
    )
    _running(store, draft)
    reasons = store.evaluate_completion("owner-a", draft.workload_id).reasons
    assert "result_dependencies_unresolved" in reasons


def test_owner_purge_is_selective_cascading_and_repeatable(store):
    first, _ = _admit(store, owner="owner-a", request_key="request-a")
    second, _ = _admit(store, owner="owner-b", request_key="request-b")
    assert store.purge_owner("owner-a") == 1
    with pytest.raises(WorkloadNotFoundError):
        store.get_workload("owner-a", first.workload_id)
    assert store.get_workload("owner-b", second.workload_id).owner_user_id == "owner-b"
    assert store.purge_owner("owner-a") == 0


def test_database_terminal_guard_rejects_unproven_completion(store):
    draft, _revision = _admit(store, usage_complete=True)
    _running(store, draft)
    with pytest.raises(sqlite3.IntegrityError, match="event and outbox"):
        store._connection.execute(
            """
            UPDATE workloads SET state='completed'
            WHERE owner_user_id=? AND id=?
            """,
            ("owner-a", draft.workload_id),
        )

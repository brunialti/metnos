"""Handled domain errors are receipts, not permission to omit required work."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from durable_workloads.compiler import ApprovedOutputSchema, OutputValidationError
from durable_workloads.coordinator import (
    CommitStatus, FailureStatus, RetryDecision, StructuredAttemptError, ValidatedResult,
)
from durable_workloads.domain_outcome import DOMAIN_OUTCOME_SCHEMA, domain_terminal_detail
from durable_workloads.models import WorkloadState
from durable_workloads.schema import SchemaValidationError
from durable_workloads.storage import DurableWorkloadStore
from helpers import artifact_requirement, inventory, map_stage, plan, source
from test_entry_identity_fanout import _capabilities


OUTCOME = {"version": 1, "error_counts": {"record_encoding_invalid": 3, "document_encrypted": 2}}


@pytest.fixture
def store(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as repository:
        yield repository


def _admit(store, key, *, owner="owner-a", selected_plan=None,
           selected_source=None, selected_sources=None, **flags):
    draft = store.create_draft(owner, key, redacted_request={"summary": "Domain receipt"})
    revision = store.admit_revision(
        owner, draft.workload_id, selected_plan or plan(with_map=True),
        inventory(selected_sources or [selected_source or source(0)]), expected_version=draft.version,
        **{"usage_complete": True, **flags},
    )
    store.transition_workload(owner, draft.workload_id, WorkloadState.QUEUED,
                              expected_version=store.get_workload(owner, draft.workload_id).version)
    return draft.workload_id, revision.revision_id


def _commit(store, outcome=OUTCOME):
    now = datetime.now(timezone.utc)
    lease = store.claim_next("domain-fixture", now, timedelta(seconds=60), _capabilities())
    assert lease is not None
    store.mark_running(lease, now=now)
    payload = {"entries": []}
    if outcome is not None:
        payload["domain_outcome"] = deepcopy(outcome)
    result = ValidatedResult.from_payload(lease.output_schema_version, payload)
    assert store.commit_result(lease, result, now=now).status is CommitStatus.COMMITTED
    return lease, result


def _categories(store, workload, owner="owner-a"):
    return {item["error_code"]: item["count"]
            for item in store.execution_summary(owner, workload)["domain_errors"]["categories"]}


@pytest.mark.parametrize("outcome,target", [
    (OUTCOME, WorkloadState.COMPLETED_WITH_ERRORS),
    (None, WorkloadState.COMPLETED),
    ({"version": 1, "error_counts": {}}, WorkloadState.COMPLETED),
])
def test_domain_receipts_complete_without_counting_blocks_as_failed(store, outcome, target):
    workload, revision = _admit(store, "receipt")
    lease, result = _commit(store, outcome)
    assert _categories(store, workload) == (outcome["error_counts"] if outcome else {})
    assert store.execution_summary("owner-a", workload)["error_categories"] == []
    counters = store.unit_counters("owner-a", workload)
    assert (counters.committed, counters.failed, counters.skipped, counters.pending) == (1, 0, 0, 0)
    row = store._connection.execute("SELECT partial_output, error_class FROM units WHERE id=?", (lease.unit_id,)).fetchone()
    assert tuple(row) == (0, None)
    assert store.commit_result(lease, result).status is CommitStatus.IDEMPOTENT_REPLAY
    if outcome and outcome["error_counts"]:
        conflict = ValidatedResult.from_payload(lease.output_schema_version, {
            "entries": [], "domain_outcome": {"version": 1, "error_counts": {"record_encoding_invalid": 99}},
        })
        assert store.commit_result(lease, conflict).status is CommitStatus.DIGEST_CONFLICT
    completion = store.evaluate_completion("owner-a", workload)
    assert completion.eligible, completion.reasons
    assert completion.target_state is target
    event_count = len(store.list_events("owner-a", workload))
    assert store.evaluate_completion("owner-a", workload).target_state is target
    assert len(store.list_events("owner-a", workload)) == event_count
    assert _categories(store, workload) == (outcome["error_counts"] if outcome else {})
    assert store._connection.execute("SELECT partial_output_accepted FROM revisions WHERE id=?", (revision,)).fetchone()[0] == 0


def test_terminal_domain_items_survive_reopen_and_total_includes_hidden_categories(tmp_path):
    path = tmp_path / "persistent-domain.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        workload, _ = _admit(store, "many-categories", selected_sources=[source(0), source(1)])
        for group in range(2):
            # Disjoint original items have one primary code each. Fifteen
            # categories per committed unit stay within the receipt limit;
            # together they exceed the console's twenty-category display.
            counts = {f"record_error_{group * 15 + index:02d}": group + 1 for index in range(15)}
            lease, result = _commit(store, {"version": 1, "error_counts": counts})
            assert store.commit_result(lease, result).status is CommitStatus.IDEMPOTENT_REPLAY
        assert store.evaluate_completion("owner-a", workload).target_state is WorkloadState.COMPLETED_WITH_ERRORS
        before = store.execution_summary("owner-a", workload)["domain_errors"]
        assert before["nitems"] == 45
        assert len(before["categories"]) == 20
        assert sum(item["count"] for item in before["categories"]) == 35
        assert before["truncated"] is True
    with DurableWorkloadStore.open(path) as reopened:
        assert reopened.get_workload("owner-a", workload).state is WorkloadState.COMPLETED_WITH_ERRORS
        assert reopened.execution_summary("owner-a", workload)["domain_errors"] == before
        assert reopened.execution_summary("owner-a", workload)["error_categories"] == []
        assert reopened.unit_counters("owner-a", workload).failed == 0


def test_domain_items_without_errors_have_closed_nontruncated_projection(store):
    draft = store.create_draft("owner-a", "unadmitted", redacted_request={"summary": "No work"})
    expected = {"nitems": 0, "categories": [], "truncated": False}
    assert store.execution_summary("owner-a", draft.workload_id)["domain_errors"] == expected
    workload, _ = _admit(store, "clean-projection")
    _commit(store, None)
    assert store.evaluate_completion("owner-a", workload).target_state is WorkloadState.COMPLETED
    assert store.execution_summary("owner-a", workload)["domain_errors"] == expected


def test_domain_nitems_counts_original_items_not_failed_attempts(store):
    selected = plan(with_map=True)
    selected["stages"][1]["retry"].update({
        "max_attempts": 2, "retryable_error_classes": ["executor_transient"],
    })
    workload, revision = _admit(store, "retry-then-receipt", selected_plan=selected)
    now = datetime.now(timezone.utc)
    lease = store.claim_next("transient-fixture", now, timedelta(seconds=60), _capabilities())
    store.mark_running(lease, now=now)
    error = StructuredAttemptError.create(
        "executor_transient", code="transport.temporarily_unavailable",
        message_key="ERR_DURABLE_EXECUTOR_TRANSIENT", retry="automatic", occurred_at=now,
    )
    assert store.fail_attempt(lease, error, RetryDecision.RETRY, now=now).status is FailureStatus.RETRY_SCHEDULED
    summary = store.execution_summary("owner-a", workload)
    assert summary["domain_errors"] == {"nitems": 0, "categories": [], "truncated": False}
    assert summary["error_categories"] == [{"error_code": "executor_transient", "count": 1}]
    assert store.reconcile_expired(datetime.now(timezone.utc), 200).retry_promoted == 1
    _commit(store)
    summary = store.execution_summary("owner-a", workload)
    assert summary["domain_errors"]["nitems"] == 5
    assert summary["domain_errors"]["truncated"] is False
    assert summary["error_categories"] == []
    assert summary["attempt_errors"] == {
        "nattempts": 1,
        "categories": [{"error_code": "transport.temporarily_unavailable", "count": 1}],
        "truncated": False,
    }
    assert store._connection.execute(
        "SELECT COUNT(*) FROM attempts a JOIN units u ON u.owner_user_id=a.owner_user_id AND u.id=a.unit_id WHERE u.revision_id=?",
        (revision,),
    ).fetchone()[0] == 2
    assert store.evaluate_completion("owner-a", workload).target_state is WorkloadState.COMPLETED_WITH_ERRORS


def test_recovered_attempt_errors_remain_after_clean_completion_and_reopen(tmp_path):
    path = tmp_path / "attempt-history.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        selected = plan(with_map=True)
        selected["budgets"]["max_attempts_per_unit"] = 32
        selected["stages"][1]["retry"].update({
            "max_attempts": 32, "retryable_error_classes": ["executor_transient"],
        })
        workload, _ = _admit(store, "technical-history", selected_plan=selected,
                              revision_id="shared_attempt_revision")
        codes = [f"transport.error_{index:02d}" for index in range(21)] + ["transport.error_00"] * 3
        for code in codes:
            now = datetime.now(timezone.utc)
            lease = store.claim_next("technical-history", now, timedelta(seconds=60), _capabilities())
            store.mark_running(lease, now=now)
            error = StructuredAttemptError.create(
                "executor_transient", code=code,
                message_key="ERR_DURABLE_EXECUTOR_TRANSIENT", retry="automatic", occurred_at=now,
                details_redacted={"internal_detail": "/private/not-for-console"},
            )
            assert store.fail_attempt(lease, error, RetryDecision.RETRY, now=now).status is FailureStatus.RETRY_SCHEDULED
            # Replaying the same failed attempt cannot create another error.
            assert store.fail_attempt(lease, error, RetryDecision.RETRY, now=now).status is FailureStatus.STALE_FENCE
            assert store.reconcile_expired(datetime.now(timezone.utc), 200).retry_promoted == 1
        successful, receipt = _commit(store, None)
        assert store.commit_result(successful, receipt).status is CommitStatus.IDEMPOTENT_REPLAY
        assert store.evaluate_completion("owner-a", workload).target_state is WorkloadState.COMPLETED
        summary = store.execution_summary("owner-a", workload)
        before = summary["attempt_errors"]
        assert before["nattempts"] == 24
        assert len(before["categories"]) == 20
        assert before["categories"][0] == {"error_code": "transport.error_00", "count": 4}
        assert sum(item["count"] for item in before["categories"]) == 23
        assert before["truncated"] is True
        assert summary["domain_errors"]["nitems"] == 0
        assert summary["error_categories"] == []
        assert "/private/not-for-console" not in json.dumps(summary)
        # Neither another revision of this owner nor another owner's identically
        # named revision may inherit these historical execution failures.
        for owner in ("owner-a", "owner-b"):
            clean, _ = _admit(store, "clean-history-" + owner, owner=owner,
                              **({"revision_id": "shared_attempt_revision"} if owner == "owner-b" else {}))
            _commit(store, None)
            assert store.evaluate_completion(owner, clean).target_state is WorkloadState.COMPLETED
            assert store.execution_summary(owner, clean)["attempt_errors"] == {
                "nattempts": 0, "categories": [], "truncated": False,
            }
    with DurableWorkloadStore.open(path) as reopened:
        assert reopened.get_workload("owner-a", workload).state is WorkloadState.COMPLETED
        assert reopened.execution_summary("owner-a", workload)["attempt_errors"] == before


def test_attempt_history_query_uses_revision_units_then_indexed_attempt_lookup(store):
    workload, revision = _admit(store, "query-scope")
    statements = []
    store._connection.set_trace_callback(statements.append)
    try:
        store.execution_summary("owner-a", workload)
    finally:
        store._connection.set_trace_callback(None)
    query = next(statement for statement in statements if "WITH error_facts AS MATERIALIZED" in statement)
    details = [row[3] for row in store._connection.execute("EXPLAIN QUERY PLAN " + query)]
    assert any("SEARCH unit" in detail and "revision_id=?" in detail for detail in details)
    assert any("SEARCH attempt" in detail and "unit_id=?" in detail for detail in details), details
    assert not any("SCAN attempt" in detail for detail in details)


def test_attempt_history_never_exposes_free_text_or_noncanonical_machine_codes(store):
    workload, revision = _admit(store, "historical-code-safety")
    unit_id = store._connection.execute("SELECT id FROM units WHERE revision_id=?", (revision,)).fetchone()[0]
    original = json.loads(StructuredAttemptError.create(
        "executor_transient", code="transport.valid_error",
        message_key="ERR_DURABLE_EXECUTOR_TRANSIENT", retry="automatic",
        occurred_at=datetime.now(timezone.utc),
        details_redacted={"internal_detail": "/private/path"},
    ).payload_json)
    # Production writers validate the entire StructuredAttemptError. These
    # raw database fixtures prove the projection's additional code boundary,
    # including SQLite's special handling of embedded NUL in text functions.
    codes = [original["code"], "/private/path", "Error with spaces", "BAD_CODE",
             "1st_error", "ab", "x" * 97, "error\n", "error_é", "error\x00/private/path", None]
    for number, code in enumerate(codes, 1):
        payload = {**original, "code": code}
        store._connection.execute(
            """INSERT INTO attempts(owner_user_id,id,unit_id,number,fence,worker_id,
                state,started_at,ended_at,structured_error_json,executor_snapshot_json,
                model_snapshot_json,metrics_json)
               VALUES ('owner-a',?,?,?,?, 'fixture','failed',?,?,?,'{}','{}','{}')""",
            (f"attempt_code_{number:03d}", unit_id, number, number,
             original["occurred_at"], original["occurred_at"], json.dumps(payload)),
        )
    projection = store.execution_summary("owner-a", workload)["attempt_errors"]
    assert projection == {"nattempts": 1,
                          "categories": [{"error_code": "transport.valid_error", "count": 1}],
                          "truncated": False}
    assert "/private/path" not in json.dumps(projection)


def test_attempt_history_vm_work_does_not_grow_with_unrelated_attempts(store):
    _, revision = _admit(store, "history-vm-target")

    def measured():
        steps = [0]

        def advance():
            steps[0] += 1
            return 0

        store._connection.set_progress_handler(advance, 1)
        try:
            assert store._attempt_errors("owner-a", revision)["nattempts"] == 0
        finally:
            store._connection.set_progress_handler(None, 0)
        return steps[0]

    before = measured()
    now = datetime.now(timezone.utc)
    error = StructuredAttemptError.create(
        "executor_transient", code="transport.unrelated_error",
        message_key="ERR_DURABLE_EXECUTOR_TRANSIENT", retry="automatic", occurred_at=now,
    )
    instant = json.loads(error.payload_json)["occurred_at"]
    for owner in ("owner-a", "owner-b"):
        _, unrelated_revision = _admit(store, "history-vm-" + owner, owner=owner)
        unit = store._connection.execute(
            "SELECT id FROM units WHERE owner_user_id=? AND revision_id=?", (owner, unrelated_revision),
        ).fetchone()[0]
        store._connection.executemany(
            """INSERT INTO attempts(owner_user_id,id,unit_id,number,fence,worker_id,
                 state,started_at,ended_at,structured_error_json,executor_snapshot_json,
                 model_snapshot_json,metrics_json)
               VALUES (?,?,?,?,?,'fixture','failed',?,?,?,'{}','{}','{}')""",
            [(owner, f"unrelated_attempt_{number:04d}", unit, number, number,
              instant, instant, error.payload_json) for number in range(1, 501)],
        )
    # An owner-wide attempt scan would execute thousands of additional VM
    # operations; allow small planner bookkeeping differences, not linear work.
    assert measured() <= before + 100


def test_reused_receipt_preserves_domain_errors_and_usage(store):
    first, _ = _admit(store, "first")
    _commit(store)
    assert store.evaluate_completion("owner-a", first).eligible
    second, revision = _admit(store, "second")
    assert store.adopt_reusable_results() == 1
    assert store.adopt_reusable_results() == 0
    assert _categories(store, second) == OUTCOME["error_counts"]
    assert store.evaluate_completion("owner-a", second).target_state is WorkloadState.COMPLETED_WITH_ERRORS
    metrics = store._connection.execute(
        "SELECT a.metrics_json FROM attempts a JOIN units u ON u.id=a.unit_id AND u.owner_user_id=a.owner_user_id WHERE u.revision_id=?", (revision,),
    ).fetchone()[0]
    assert json.loads(metrics)["result_reused"] is True
    assert json.loads(metrics)["usage_missing"] is False


def test_origin_errors_are_visible_while_running_and_not_recounted_by_reducer(store):
    selected = plan(with_map=True)
    selected["stages"].append({
        **map_stage(), "key": "aggregate", "type": "reduce", "depends_on": ["map"],
        "cardinality": {"mode": "singleton", "max_units": 1},
        "input_bindings": {"paths": {"ref": "dependency.entries", "stage": "map"}},
    })
    workload, _ = _admit(store, "map-reduce", selected_plan=selected)
    lease, result = _commit(store)
    assert store.get_workload("owner-a", workload).state is WorkloadState.RUNNING
    assert _categories(store, workload) == OUTCOME["error_counts"]
    assert not store.evaluate_completion("owner-a", workload).eligible
    assert store.materialize_ready_units("owner-a", workload) == 1
    now = datetime.now(timezone.utc)
    reduction = store.claim_next("domain-reducer", now, timedelta(seconds=60), _capabilities())
    assert reduction.stage_key == "aggregate"
    store.mark_running(reduction, now=now)
    # A domain summary may carry the same facts for its own publication checks;
    # only the originator emits the reserved LRE accounting field.
    reduced = ValidatedResult.from_payload(reduction.output_schema_version, {
        "entries": [], "report_error_counts": OUTCOME["error_counts"],
    })
    parent = store.commit_result(lease, result).result_id
    assert store.commit_result(reduction, reduced, dependency_result_ids=(parent,)).status is CommitStatus.COMMITTED
    assert _categories(store, workload) == OUTCOME["error_counts"]
    assert store.evaluate_completion("owner-a", workload).target_state is WorkloadState.COMPLETED_WITH_ERRORS
    assert store.unit_counters("owner-a", workload).committed == 2


def test_domain_error_projection_is_scoped_to_owner_and_active_revision(store):
    first, _ = _admit(store, "first", revision_id="shared_domain_revision")
    _commit(store)
    assert store.evaluate_completion("owner-a", first).eligible
    for owner in ("owner-a", "owner-b"):
        clean, revision = _admit(store, "clean-" + owner, owner=owner,
                                 **({"revision_id": "shared_domain_revision"} if owner == "owner-b" else {}))
        # A noncommitted unit is not an authoritative domain receipt, even if
        # a diagnostic with that shape exists in a synthetic database fixture.
        store._connection.execute(
            "UPDATE units SET terminal_detail_json=? WHERE owner_user_id=? AND revision_id=?",
            (domain_terminal_detail({"domain_outcome": OUTCOME}), owner, revision),
        )
        assert _categories(store, clean, owner) == {}
        _commit(store, None)
        assert _categories(store, clean, owner) == {}
        assert store.evaluate_completion(owner, clean).target_state is WorkloadState.COMPLETED
    assert _categories(store, first) == OUTCOME["error_counts"]


@pytest.mark.parametrize("reason", ["usage_not_materialized", "cap_or_truncation_unaccepted", "sources_unaccounted", "required_artifact_missing"])
def test_domain_receipt_does_not_excuse_incomplete_work(store, reason):
    flags = {}
    selected_plan = plan(with_map=True)
    selected_source = source(0)
    if reason == "usage_not_materialized":
        flags["usage_complete"] = False
    elif reason == "cap_or_truncation_unaccepted":
        flags["caps_truncated"] = True
    elif reason == "sources_unaccounted":
        selected_source["accounted"] = False
    else:
        selected_plan["required_artifacts"] = [artifact_requirement()]
    workload, _ = _admit(store, reason, selected_plan=selected_plan,
                          selected_source=selected_source, **flags)
    _commit(store)
    completion = store.evaluate_completion("owner-a", workload)
    assert not completion.eligible
    assert reason in completion.reasons


@pytest.mark.parametrize("value", [
    None, [], {}, {"version": True, "error_counts": {}},
    {"version": 2, "error_counts": {}},
    {"version": 1, "error_counts": {}, "extra": 1},
    {"version": 1, "error_counts": []},
    *[{"version": 1, "error_counts": {"record_invalid": count}}
      for count in (True, False, 0, -1, 1.5, "1", 1_000_001)],
    {"version": 1, "error_counts": {"record_a": 600_000, "record_b": 600_000}},
    {"version": 1, "error_counts": {f"record_{index}": 1 for index in range(21)}},
    *[{"version": 1, "error_counts": {code: 1}}
      for code in ("x", "Bad", "private/path", "error\n", "x" * 97)],
])
def test_invalid_reserved_domain_outcome_is_rejected_even_without_schema(value):
    with pytest.raises(SchemaValidationError):
        ValidatedResult.from_payload("metnos.test-map/1", {"domain_outcome": value})


def test_domain_outcome_requires_explicit_approved_property_even_in_open_schema():
    open_schema = {"type": "object", "properties": {"entries": {"type": "array"}}, "additionalProperties": True}
    payload = {"entries": [], "domain_outcome": OUTCOME}
    with pytest.raises(OutputValidationError, match="explicitly approved"):
        ApprovedOutputSchema.create("metnos.test-map/1", open_schema).validate(payload)
    open_schema["properties"]["domain_outcome"] = deepcopy(DOMAIN_OUTCOME_SCHEMA)
    approved = ApprovedOutputSchema.create("metnos.test-map/1", open_schema)
    approved.validate(payload)
    with pytest.raises(OutputValidationError):
        approved.validate({"domain_outcome": {"version": 1, "error_counts": {"invalid_record": True}}})


@pytest.mark.parametrize("explicitly_approved", [False, True])
def test_execution_bridge_requires_frozen_domain_outcome_approval(store, explicitly_approved):
    from durable_workloads.admission import admit_candidate
    from durable_workloads.compiler import OutputSchemaRegistry
    from durable_workloads.execution import DurableExecutionBridge
    from helpers import source_resolution
    from test_execution_bridge import _Resolver, _schemas, _pipeline, _worker, _map_observation

    resolver = _Resolver()
    schemas = []
    for name in _schemas().names:
        definition = deepcopy(_schemas().resolve(name).schema)
        if name == "metnos.test-map/1":
            definition["additionalProperties"] = True
            if explicitly_approved:
                definition["properties"]["domain_outcome"] = deepcopy(DOMAIN_OUTCOME_SCHEMA)
        schemas.append(ApprovedOutputSchema.create(name, definition))
    registry = OutputSchemaRegistry(schemas)
    draft = store.create_draft("owner-f7", "domain-bridge", redacted_request={"summary": "Generic receipt"})
    admit_candidate(store, "owner-f7", draft.workload_id, _pipeline(with_reduce=False),
                    inventory([source(0)]), expected_version=draft.version,
                    runners=resolver, output_schemas=registry)
    store.transition_workload("owner-f7", draft.workload_id, WorkloadState.QUEUED,
                              expected_version=store.get_workload("owner-f7", draft.workload_id).version)
    bridge = DurableExecutionBridge(
        store, runners=resolver, output_schemas=registry,
        source_resolver=lambda item, _context: source_resolution(item, "/authorized/document.bin"),
        executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
        executor_invoker=lambda *_args: {**_map_observation(), "domain_outcome": OUTCOME},
    )
    bridge.run_once(_worker(store, resolver))
    expected = WorkloadState.COMPLETED_WITH_ERRORS if explicitly_approved else WorkloadState.FAILED
    assert store.get_workload("owner-f7", draft.workload_id).state is expected
    if explicitly_approved:
        assert _categories(store, draft.workload_id, "owner-f7") == OUTCOME["error_counts"]
    else:
        assert store.unit_counters("owner-f7", draft.workload_id).committed == 0

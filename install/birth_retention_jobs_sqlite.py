"""Native LRE workload bundles and scheduler rows for exclusive maintenance.

No workload is reconstructed from a label or from a path. Related rows belong
to the exact owner/workload under the native foreign keys, and deletion uses
those foreign keys with every native trigger still enabled.
"""
from __future__ import annotations

from datetime import timedelta

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_workspace_paths import workspace_paths, workspaces_absent
from install.birth_retention_sqlite import (
    _SQLiteOwner, RowState, _bounded_rows, _closed, _encoded_row, _iso, _retained, _utc,
)


def _workload_state(row):
    from durable_workloads.models import TERMINAL_WORKLOAD_STATES, WorkloadState
    try:
        state = WorkloadState(row["state"])
    except ValueError as exc:
        raise RetentionError("retention_owner_state_invalid", "workload state") from exc
    if state not in TERMINAL_WORKLOAD_STATES:
        return _retained(row["created_at"], RootKind.IN_PROGRESS_JOB)
    return _closed(row["created_at"], row["updated_at"], row["updated_at"])


# Fixed ownership joins, checked against the native schema. No caller supplies
# SQL, table names, paths or extra join rules to an administrative entrypoint.
_DIRECT = (
    "revisions", "artifacts", "events", "outbox", "scheduler_credits",
    "commands", "attention_resolutions", "workload_dismissals", "workload_temporary_storage",
)
_REVISION = (
    "stages", "stage_dependencies", "sources", "units", "results", "dependencies",
    "revision_usage", "unit_dependencies", "stage_materialization", "stage_placements",
)
_METADATA = {"durable_schema", "artifact_gc_cursors"}


class _WorkloadOwner(_SQLiteOwner):
    def __init__(self, *, path, require_exclusion, owner, resolve_workspaces, artifact_workspace=None):
        from durable_workloads.migrations import migrate
        self.resolve_workspaces, self.artifact_workspace = resolve_workspaces, artifact_workspace
        super().__init__(name="durable_workloads", path=path, schema=migrate,
                         table="workloads", primary_key=("owner_user_id", "id"),
                         node_type="job", state=_workload_state,
                         require_exclusion=require_exclusion, owner=owner)
        tables = {name for kind, name, _table, _sql in self.expected_schema if kind == "table"}
        if tables != {*_DIRECT, *_REVISION, *_METADATA, "workloads", "attempts", "publications"}:
            raise RetentionError("retention_inventory_incomplete", "unmapped LRE table")

    def _validate_store(self, connection):
        from durable_workloads.migrations import CURRENT_SCHEMA_VERSION
        metadata = connection.execute("SELECT singleton,version FROM durable_schema").fetchall()
        if [tuple(row) for row in metadata] != [(1, CURRENT_SCHEMA_VERSION)]:
            raise RetentionError("retention_inventory_incomplete", "LRE schema version")

    def _related(self, connection, values):
        queries = {table: f'SELECT t.* FROM "{table}" t '
                   'WHERE t.owner_user_id=? AND t.workload_id=?' for table in _DIRECT}
        queries.update({table: f'SELECT t.* FROM "{table}" t JOIN revisions r '
                        'ON r.owner_user_id=t.owner_user_id AND r.id=t.revision_id '
                        'WHERE r.owner_user_id=? AND r.workload_id=?' for table in _REVISION})
        queries["attempts"] = (
            "SELECT t.* FROM attempts t JOIN units u ON u.owner_user_id=t.owner_user_id AND u.id=t.unit_id "
            "JOIN revisions r ON r.owner_user_id=u.owner_user_id AND r.id=u.revision_id "
            "WHERE r.owner_user_id=? AND r.workload_id=?")
        queries["publications"] = (
            "SELECT t.* FROM publications t JOIN artifacts a "
            "ON a.owner_user_id=t.owner_user_id AND a.id=t.artifact_id "
            "WHERE a.owner_user_id=? AND a.workload_id=?")
        related, used = [], 0
        for table, query in sorted(queries.items()):
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            order = ",".join(f't."{column[1]}"' for column in sorted(columns, key=lambda c: c[5]) if column[5])
            rows = _bounded_rows(connection, query + " ORDER BY " + order,
                                 (values["owner_user_id"], values["id"]))
            used += sum(len(_encoded_row(row)) for row in rows)
            if used > 128 * 1024 * 1024:
                raise RetentionError("retention_inventory_incomplete", "workload bundle limit")
            related.append((table, rows))
        return tuple(related)

    def state_of(self, row):
        state = _workload_state(row.values)
        related = dict(row.related)
        reports = related["workload_temporary_storage"]
        if (len(reports) != 1 or reports[0]["status"] != "clean"
                or reports[0]["workload_version"] != row.values["version"]):
            return _retained(row.values["created_at"], RootKind.IN_PROGRESS_JOB)
        # Failed deliveries remain retryable. A terminal workload label cannot
        # discard a lease, unfinished publication or workspace recovery.
        if (any(item["state"] not in {"sent", "cancelled"} for item in related["outbox"])
                or any(item["ended_at"] is None for item in related["attempts"])
                or any(item["status"] != "clean" for item in related["workload_temporary_storage"])):
            return _retained(row.values["created_at"], RootKind.IN_PROGRESS_JOB)
        if (any(item["state"] != "cancelled" for item in related["publications"])
                or any(item["state"] in {"prepared", "needs_attention"} for item in related["artifacts"])):
            return _retained(row.values["created_at"], RootKind.OPEN_AUDIT)
        if state.state is NodeState.OPEN:
            return state
        paths = workspace_paths(row, self.resolve_workspaces, self.artifact_workspace)
        if not workspaces_absent(paths, require_exclusion=self.require_exclusion, owner=self.owner):
            return _retained(row.values["created_at"], RootKind.IN_PROGRESS_JOB)
        # Delivery and artifact activity can be newer than workload termination.
        times = [_utc(row.values["updated_at"])]
        windows = [_utc(state.eligible_after)]
        for _table, items in row.related:
            for item in items:
                for name in ("created_at", "updated_at", "ended_at", "recorded_at", "checked_at", "published_at"):
                    if item.get(name) is not None:
                        times.append(_utc(item[name]))
                if item.get("retention_until") is not None:
                    windows.append(_utc(item["retention_until"]))
        return RowState(NodeState.CLOSED, state.created_at,
                        _iso(max(*windows, max(times) + timedelta(days=90))), ())

    def _delete_related(self, connection, row):
        # The parent DELETE cascades atomically. No trigger is removed, no
        # constraint is deferred and no intermediate admitted revision changes.
        if {table for table, _rows in row.related} != {*_DIRECT, *_REVISION, "attempts", "publications"}:
            raise RetentionError("retention_inventory_incomplete", "workload related rows")


def _schedule_state(row):
    # Disabled entries can be enabled again. Only the native explicit removal
    # closes their lifetime; the historical run records have a separate owner.
    return _retained(row["created_at"], RootKind.IN_PROGRESS_JOB)


def _run_state(row):
    if row["status"] == "running":
        if row["finished_at"] is not None:
            raise RetentionError("retention_owner_state_invalid", "running schedule already finished")
        return _retained(row["started_at"], RootKind.IN_PROGRESS_JOB)
    if row["status"] not in {"success", "error", "timeout", "crashed"}:
        raise RetentionError("retention_owner_state_invalid", "schedule run state")
    return _closed(row["started_at"], row["finished_at"], row["finished_at"])


def _scheduler_owners(*, path, require_exclusion, owner):
    from scheduler_v2.storage import _SCHEMA
    return tuple(_SQLiteOwner(name=name, path=path, schema=_SCHEMA,
                              table=table, primary_key="id", node_type="job", state=state,
                              require_exclusion=require_exclusion, owner=owner)
                 for name, table, state in (
                     ("scheduler_entries", "schedule_entries", _schedule_state),
                     ("scheduler_runs", "runs", _run_state)))


def _operational_job_sqlite_owners(require_exclusion, account_owner, *, resolve_workspaces, artifact_workspace):
    import config
    from scheduler_v2.storage import DEFAULT_DB_PATH
    return (_WorkloadOwner(path=config.DB_DURABLE_WORKLOADS,
                           require_exclusion=require_exclusion, owner=account_owner,
                           resolve_workspaces=resolve_workspaces, artifact_workspace=artifact_workspace),
            *_scheduler_owners(path=DEFAULT_DB_PATH,
                               require_exclusion=require_exclusion, owner=account_owner))

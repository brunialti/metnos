"""Native epoch bundles for stopped-store maintenance.

An archived epoch owns its history and cache. Legacy migration manifests, source
copies and resolution decisions remain evidence required by the native migration
verifier. A resolution also protects its referenced epoch, even though that
relation has no SQL foreign key. External generation references are resolved by
the inventory assembler before it can authorise any deletion.
"""
from __future__ import annotations

from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_sqlite import (
    _SQLiteOwner, _bounded_rows, _closed, _iso, _retained, _utc,
)


class _EpochStoreOwner(_SQLiteOwner):
    def __init__(self, *, name, table, primary_key, node_type, state,
                 path, require_exclusion, owner):
        from executor_birth_epoch_store import _ensure_schema

        super().__init__(name=name, path=path, schema=_ensure_schema, table=table,
                         primary_key=primary_key, node_type=node_type, state=state,
                         require_exclusion=require_exclusion, owner=owner)

    def _validate_store(self, connection):
        from executor_birth_epoch_store import EPOCH_STORE_SCHEMA_VERSION

        if connection.execute("PRAGMA user_version").fetchone()[0] != EPOCH_STORE_SCHEMA_VERSION:
            raise RetentionError("retention_owner_changed", "epoch schema version")


def _epoch_state(values):
    from executor_birth_epoch_store import BirthLifecycle, EpochState

    try:
        state, lifecycle = EpochState(values["state"]), BirthLifecycle(values["lifecycle"])
    except ValueError as exc:
        raise RetentionError("retention_owner_state_invalid", "epoch lifecycle") from exc
    if state is EpochState.CURRENT:
        return _retained(values["created_at"], RootKind.CURRENT_EPOCH)
    if state is not EpochState.ARCHIVED or lifecycle is not BirthLifecycle.ARCHIVED:
        # Deprecation alone neither closes the audit nor establishes that the
        # generation's rollback window has ended.
        return _retained(values["created_at"], RootKind.OPEN_AUDIT)
    return _closed(values["created_at"], values["updated_at"], values["updated_at"])


class _EpochOwner(_EpochStoreOwner):
    def __init__(self, *, path, require_exclusion, owner):
        super().__init__(name="birth_epochs", table="executor_epochs",
                         primary_key=("contract_id", "generation_id"), node_type="epoch",
                         state=_epoch_state, path=path,
                         require_exclusion=require_exclusion, owner=owner)

    def _related(self, connection, values):
        key = (values["contract_id"], values["generation_id"])
        return tuple((table, _bounded_rows(connection,
            f'SELECT * FROM "{table}" WHERE contract_id=? AND generation_id=? ORDER BY "{order}"',
            key)) for table, order in (
                ("executor_epoch_history", "event_seq"),
                ("executor_preexercise_cache", "lifecycle"),
                ("executor_legacy_resolutions", "legacy_id"),
            ))

    def state_of(self, row):
        values, related = row.values, dict(row.related)
        history = related["executor_epoch_history"]
        # Every native epoch begins with an opening history record. Absence is
        # damage, not evidence that it has already been collected.
        if (not history or history[0]["event_seq"] != 1
                or history[0]["prior_state_version"] is not None
                or history[0]["new_state_version"] != 1):
            raise RetentionError("retention_owner_state_invalid", "epoch opening history")
        state = self.state(values)
        if related["executor_legacy_resolutions"]:
            return _retained(values["created_at"], *state.roots, RootKind.OPEN_AUDIT)
        if state.roots:
            return state
        clocks = [values["updated_at"], values["last_used_at"], values["inactivity_since"]]
        clocks += [item["ts"] for item in history]
        clocks += [item["created_at"] for item in related["executor_preexercise_cache"]]
        last = _iso(max(_utc(value) for value in clocks if value is not None))
        return _closed(values["created_at"], last, last)

    def _delete_related(self, connection, row):
        for table, children in row.related:
            if table == "executor_legacy_resolutions":
                if children:
                    raise RetentionError("retention_owner_state_invalid", "legacy reference")
                continue
            removed = connection.execute(
                f'DELETE FROM "{table}" WHERE contract_id=? AND generation_id=?',
                (row.values["contract_id"], row.values["generation_id"]))
            if removed.rowcount != len(children):
                raise RetentionError("retention_owner_changed", "epoch relation changed")


class _EpochLegacyOwner(_EpochStoreOwner):
    """Preserved native migration evidence has no collector-owned closure."""
    def __init__(self, kind, *, path, require_exclusion, owner):
        if kind == "state":
            key = "legacy_id"
        elif kind == "migrations":
            key = "migration_id"
        else:
            raise RetentionError("retention_owner_invalid", "legacy epoch owner")
        super().__init__(name="birth_epoch_legacy_" + kind,
                         table="executor_legacy_" + kind, primary_key=key, node_type="evidence",
                         state=lambda row: _retained(row["migrated_at"], RootKind.OPEN_AUDIT),
                         path=path, require_exclusion=require_exclusion, owner=owner)

    def _related(self, connection, values):
        from executor_birth_epoch_store import EpochStoreError, _verify_legacy_migration

        if self.table == "executor_legacy_state":
            return (("executor_legacy_resolutions", _bounded_rows(connection,
                "SELECT * FROM executor_legacy_resolutions WHERE legacy_id=? ORDER BY legacy_id",
                (values["legacy_id"],))),)
        bindings = _bounded_rows(connection,
            "SELECT * FROM executor_legacy_migration_rows WHERE migration_id=? ORDER BY source_ordinal",
            (values["migration_id"],))
        # Read the source copies under the same limits before the native verifier
        # materialises them. Its immutable count, digest and row binding checks
        # remain the authority for these records.
        sources = _bounded_rows(connection,
            "SELECT s.* FROM executor_legacy_state s JOIN executor_legacy_migration_rows r "
            "ON s.legacy_id=r.legacy_id WHERE r.migration_id=? ORDER BY r.source_ordinal",
            (values["migration_id"],))
        try:
            _verify_legacy_migration(connection, migration_id=values["migration_id"],
                                     expected_count=values["source_count"],
                                     expected_digest=values["source_digest"])
        except EpochStoreError as exc:
            raise RetentionError("retention_owner_state_invalid", "legacy migration binding") from exc
        return (("executor_legacy_migration_rows", bindings), ("executor_legacy_state", sources))


def _operational_epoch_sqlite_owners(require_exclusion, account_owner):
    import config

    path = config.PATH_USER_STATE / "birth" / "executor_epochs.sqlite"
    arguments = dict(path=path, require_exclusion=require_exclusion, owner=account_owner)
    return (_EpochOwner(**arguments), *(_EpochLegacyOwner(kind, **arguments)
                                       for kind in ("state", "migrations")))

"""Small generic implementations for the approved durable internal runners.

They are invoked by :class:`DurableExecutionBridge` outside database
transactions.  Domain plans supply typed result data; these functions never
inspect a plan identifier, a user path or a model-specific field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .artifacts import ArtifactBudgetError, ArtifactConflictError, ArtifactStore
from .models import ExecutionContext


def _invalid_output() -> dict[str, object]:
    return {"ok": False, "error_class": "contract_violation"}


def sealed_inventory(args: Mapping[str, Any], _context: ExecutionContext) -> dict[str, object]:
    inventory = args.get("inventory")
    if not isinstance(inventory, Mapping):
        return _invalid_output()
    digest = inventory.get("digest")
    sources = inventory.get("sources")
    if not isinstance(digest, str) or not isinstance(sources, list):
        return _invalid_output()
    return {"digest": digest, "sources": sources}


def schema_and_coverage_validator(
    args: Mapping[str, Any],
    _context: ExecutionContext,
) -> dict[str, object]:
    """Accept only complete, typed private-artifact descriptors."""
    assembled = args.get("assembled")
    if not isinstance(assembled, Mapping):
        return _invalid_output()
    entries = assembled.get("entries")
    if not isinstance(entries, list) or not entries:
        return _invalid_output()
    names: set[str] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            return _invalid_output()
        name = item.get("logical_name")
        mime_type = item.get("mime_type")
        schema_version = item.get("schema_version")
        markdown = item.get("markdown")
        if (
            not isinstance(name, str)
            or name in names
            or not isinstance(mime_type, str)
            or not isinstance(schema_version, str)
            or not isinstance(markdown, str)
            or not markdown.strip()
        ):
            return _invalid_output()
        names.add(name)
    return {"entries": [{"valid": True, "reason": ""}]}


def artifact_store_publish(
    artifacts: ArtifactStore,
):
    """Build an idempotent private-artifact publisher for one bridge."""

    def publish(args: Mapping[str, Any], context: ExecutionContext) -> dict[str, object]:
        validation = args.get("validation")
        entries = args.get("artifacts")
        if (
            not isinstance(validation, list)
            or not validation
            or any(not isinstance(item, Mapping) or item.get("valid") is not True for item in validation)
            or not isinstance(entries, list)
            or not entries
        ):
            return _invalid_output()
        committed: list[dict[str, str]] = []
        try:
            for item in entries:
                if not isinstance(item, Mapping):
                    return _invalid_output()
                logical_name = item.get("logical_name")
                mime_type = item.get("mime_type")
                schema_version = item.get("schema_version")
                markdown = item.get("markdown")
                if not all(isinstance(value, str) for value in (
                    logical_name, mime_type, schema_version, markdown,
                )):
                    return _invalid_output()
                artifact = artifacts.commit(
                    context.owner_user_id,
                    context.workload_id,
                    context.revision_id,
                    logical_name,
                    mime_type,
                    schema_version,
                    markdown.encode("utf-8"),
                )
                committed.append({
                    "logical_name": artifact.logical_name,
                    "artifact_id": artifact.artifact_id,
                    "digest": artifact.digest,
                })
        except OSError:
            return {"ok": False, "error_class": "executor_transient"}
        except ArtifactBudgetError:
            return {"ok": False, "error_class": "budget_exhausted"}
        except ArtifactConflictError:
            return {"ok": False, "error_class": "publication_ambiguous"}
        except Exception:
            return _invalid_output()
        return {"entries": committed}

    return publish


def committed_entries(store):
    """Bind an owner-scoped reader; a plan supplies exact historical references."""
    from .storage import DurableStoreError
    from .schema import SchemaValidationError

    def read(args: Mapping[str, Any], context: ExecutionContext) -> dict[str, object]:
        try:
            entries = store.read_committed_entries(context.owner_user_id, args.get("references"))
            if "metnos.partition-list/1" in getattr(context, "output_schema_names", ()):
                from .direct_invocation import row_blocks
                entries = row_blocks(entries)
            return {"entries": entries}
        except (DurableStoreError, SchemaValidationError):
            return _invalid_output()

    return read


def literal_entries(args: Mapping[str, Any], _context: ExecutionContext) -> dict[str, object]:
    """Give back as rows a list written in the plan.

    The list is a literal frozen at admission; the stage's approved output
    schema decides what a row may hold. Each row then becomes one unit of the
    stage that depends on this one."""
    entries = args.get("entries")
    if not isinstance(entries, list) or any(
            not isinstance(item, Mapping) for item in entries):
        return _invalid_output()
    return {"entries": [dict(item) for item in entries]}


def approved_internal_runners(artifacts: ArtifactStore, store) -> dict[str, object]:
    """Return exactly the core internal runners bound to one artifact store."""
    if not isinstance(artifacts, ArtifactStore):
        raise TypeError("artifacts must be an ArtifactStore")
    return {
        "committed_entries": committed_entries(store),
        "literal_entries": literal_entries,
        "sealed_inventory": sealed_inventory,
        "schema_and_coverage_validator": schema_and_coverage_validator,
        "artifact_store_publish": artifact_store_publish(artifacts),
    }


__all__ = ["approved_internal_runners", "artifact_store_publish", "literal_entries", "sealed_inventory", "schema_and_coverage_validator"]

from __future__ import annotations

from durable_workloads.artifacts import ArtifactRepository, ArtifactStore
from durable_workloads.internal_runners import approved_internal_runners
from durable_workloads.models import ExecutionContext
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan


def _context(workload_id: str, revision_id: str) -> ExecutionContext:
    return ExecutionContext(
        owner_user_id="owner-a",
        workload_id=workload_id,
        revision_id=revision_id,
        stage_id="stage-test",
        unit_key="unit-test",
        attempt_id="attempt-test",
        priority="normal",
        resource_claims=(),
        deadline_at=None,
    )


def test_internal_artifact_runner_is_private_and_idempotent(tmp_path):
    database = tmp_path / "state" / "durable.sqlite3"
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            "owner-a", "internal-runners", redacted_request={"summary": "fixture"},
        )
        revision = store.admit_revision(
            "owner-a", draft.workload_id, plan(), inventory(), expected_version=draft.version,
        )
        repository = ArtifactRepository.open(database)
        try:
            artifacts = ArtifactStore(tmp_path / "artifacts", repository)
            publish = approved_internal_runners(artifacts)["artifact_store_publish"]
            args = {
                "validation": [{"valid": True, "reason": ""}],
                "artifacts": [{
                    "logical_name": "report_markdown",
                    "mime_type": "text/markdown",
                    "schema_version": "metnos.test-artifact/1",
                    "markdown": "# Report\n",
                }],
            }
            first = publish(args, _context(draft.workload_id, revision.revision_id))
            second = publish(args, _context(draft.workload_id, revision.revision_id))
            assert first == second
            assert len(artifacts.list_workload_artifacts("owner-a", draft.workload_id)) == 1
            changed = {
                **args,
                "artifacts": [{**args["artifacts"][0], "markdown": "# Changed\n"}],
            }
            assert publish(changed, _context(draft.workload_id, revision.revision_id)) == {
                "ok": False,
                "error_class": "publication_ambiguous",
            }
        finally:
            repository.close()

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

from durable_workloads.admission import admit_candidate
from durable_workloads.artifacts import ArtifactRepository, ArtifactStore
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.image_preset import (
    ImagePresetWorkloadInvoker,
    image_questions_plan,
    output_schemas,
)
from durable_workloads.internal_runners import approved_internal_runners
from durable_workloads.models import DurableEffect, RunnerKind, WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import DurableWorker, WorkerRunStatus
from helpers import inventory, source, source_resolution
from llm_telemetry import TRANSPORT_USAGE_KEY, TRANSPORT_USAGE_SCHEMA_VERSION, record
from test_image_preset_contracts import _catalog
from durable_workloads.image_preset import runner_resolver
from durable_workloads.coordinator import WorkerCapabilities
from durable_workloads.inventory import InventoryLimits, SealedInventory
from durable_workloads.schema import validate_inventory
from durable_workloads.source_authority import SourceAuthority


def _resolver():
    return runner_resolver(
        catalog_loader=lambda **_kwargs: _catalog(),
        binding_resolver=lambda tier, *, level=None: {
            "provider": "llamacpp", "model": "fixture-model", "tier": tier, "level": level,
        },
    )


def _worker(
    store: DurableWorkloadStore,
    *,
    lease_duration: timedelta = timedelta(minutes=5),
    checkpoint=None,
) -> DurableWorker:
    workloads = (
        "durable.images.answer",
        "durable.images.assemble",
        "durable.images.deduplicate",
        "durable.images.extract_questions",
        "durable.images.reduce_formulae",
        "durable.images.reduce_notes",
        "durable.images.reduce_solutions",
        "durable.images.validate",
    )
    capabilities = WorkerCapabilities.create(
        (
            (RunnerKind.EXECUTOR, "read_files_ocr"),
            (RunnerKind.INTERNAL, "artifact_store_publish"),
            (RunnerKind.INTERNAL, "schema_and_coverage_validator"),
            (RunnerKind.INTERNAL, "sealed_inventory"),
            *((RunnerKind.WORKLOAD, name) for name in workloads),
        ),
        {"cpu": 1, "device": 1, "llm": 1, "local_io": 1, "network_io": 0, "vlm": 1},
        effect_profiles=(DurableEffect.PURE, DurableEffect.IDEMPOTENT),
    )
    return DurableWorker(
        store,
        f"image-e2e-{os.getpid()}",
        capabilities,
        lease_duration=lease_duration,
        checkpoint=checkpoint,
    )


def _raw_model(name: str, _prompt: str, args: dict, _context: object) -> dict:
    record(
        provider="llamacpp",
        model="fixture-model",
        tier="wise",
        result=SimpleNamespace(in_tokens=5, out_tokens=3, latency_ms=1),
    )
    if name == "durable.images.extract_questions":
        ordinal = int(args["source"]["ordinal"])
        if ordinal % 11 == 0:
            return {"questions": []}
        questions = [{"text": "Qual è la risposta?", "confidence": 0.95}]
        if ordinal % 17 == 0:
            questions.append({"text": "Qual è la seconda risposta?", "confidence": 0.9})
        return {"questions": questions}
    if name == "durable.images.deduplicate":
        return {"merge_groups": []}
    if name == "durable.images.answer":
        return {"status": "answered", "answer": "Risposta sintetica.", "reason": "", "confidence": 0.9}
    if name == "durable.images.validate":
        return {"valid": True, "reason": ""}
    if name == "durable.images.reduce_notes":
        return {"markdown": "# Note\n"}
    if name == "durable.images.reduce_solutions":
        return {"markdown": "# Soluzioni\n"}
    if name == "durable.images.reduce_formulae":
        return {"markdown": "# Formulario\n"}
    if name == "durable.images.assemble":
        return {"artifacts": [
            {"logical_name": "solutions_markdown", "markdown": "# Soluzioni\n"},
            {"logical_name": "notes_markdown", "markdown": "# Note\n"},
            {"logical_name": "formulae_markdown", "markdown": "# Formulario\n"},
        ]}
    raise AssertionError(name)


def _executor_result(args: dict) -> dict:
    return {
        "ok": True,
        "ok_count": 1,
        "fail_count": 0,
        "source_id": args["source"]["source_id"],
        "entries": [{
            "source_id": args["source"]["source_id"],
            "content": "fixture",
            "char_count": 7,
            "lang": "ita+eng",
        }],
        "failed": [],
        TRANSPORT_USAGE_KEY: {
            "schema_version": TRANSPORT_USAGE_SCHEMA_VERSION,
            "records": [],
            "dropped": 0,
            "calls_started": 0,
        },
    }


def _make_bridge(
    store: DurableWorkloadStore,
    artifacts: ArtifactStore,
    *,
    source_resolver,
    before_invoke=lambda _kind: None,
    executor_result=_executor_result,
) -> DurableExecutionBridge:
    def model(name, prompt, args, context):
        before_invoke("workload")
        return _raw_model(name, prompt, args, context)

    def executor(_executor, args, *_rest):
        before_invoke("executor")
        return executor_result(args)

    return DurableExecutionBridge(
        store,
        runners=_resolver(),
        output_schemas=output_schemas(),
        source_resolver=source_resolver,
        executor_loader=lambda name: _catalog().get(name),
        executor_invoker=executor,
        workload_invoker=ImagePresetWorkloadInvoker(model),
        internal_runners=approved_internal_runners(artifacts),
    )


def _run_crashable_image_worker(
    database_path: str,
    artifact_root: str,
    authority_path: str,
    owner_user_id: str,
    workload_id: str,
    pause_after_commits: int | None,
    pause_checkpoint: str | None,
    control,
) -> None:
    """Spawn-safe full preset runner used only by the real SIGKILL gate."""

    signalled = False
    try:
        with DurableWorkloadStore.open(database_path) as store:
            store.reconcile_expired(datetime.now(timezone.utc), 1000)
            repository = ArtifactRepository.open(database_path)

            def stop_at_checkpoint(name: str) -> None:
                nonlocal signalled
                if signalled or name != pause_checkpoint:
                    return
                signalled = True
                control.send(("checkpoint", name))
                control.recv()

            artifacts = ArtifactStore(
                artifact_root,
                repository,
                checkpoint=stop_at_checkpoint,
            )
            try:
                with SourceAuthority.open(authority_path) as authority:
                    def stop_after_claim(name: str) -> None:
                        nonlocal signalled
                        if (
                            signalled
                            or pause_after_commits is None
                            or name != "attempt_after_claim"
                        ):
                            return
                        committed = int(store._connection.execute(
                            "SELECT COUNT(*) FROM units WHERE state='committed'"
                        ).fetchone()[0])
                        if committed < pause_after_commits:
                            return
                        signalled = True
                        control.send(("claim", name, committed))
                        control.recv()

                    bridge = _make_bridge(
                        store,
                        artifacts,
                        source_resolver=authority.resolve,
                    )
                    worker = _worker(
                        store,
                        lease_duration=timedelta(milliseconds=300),
                        checkpoint=stop_after_claim,
                    )
                    deadline = monotonic() + 300
                    while monotonic() < deadline:
                        outcome = bridge.run_once(worker)
                        current = store.get_workload(owner_user_id, workload_id)
                        if current.state in {
                            WorkloadState.COMPLETED,
                            WorkloadState.COMPLETED_WITH_ERRORS,
                            WorkloadState.CANCELLED,
                            WorkloadState.FAILED,
                            WorkloadState.NEEDS_ATTENTION,
                        }:
                            if current.state is WorkloadState.COMPLETED:
                                authority.reconcile_workloads(
                                    store.source_authority_active,
                                    limit=100,
                                )
                                authority.prune(limit=1000)
                            control.send((
                                "terminal",
                                current.state.value,
                                int(store._connection.execute(
                                    "SELECT COUNT(*) FROM units "
                                    "WHERE state='committed'"
                                ).fetchone()[0]),
                            ))
                            return
                        if outcome.status is WorkerRunStatus.IDLE:
                            time.sleep(0.02)
                    raise TimeoutError("crashable image worker exceeded its harness deadline")
            finally:
                artifacts.close()
    except BaseException as exc:
        try:
            control.send(("error", type(exc).__name__, str(exc)[:500]))
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise


def _start_crashable_worker(
    context,
    paths: tuple[Path, Path, Path],
    owner_user_id: str,
    workload_id: str,
    *,
    pause_after_commits: int | None = None,
    pause_checkpoint: str | None = None,
):
    parent, child = context.Pipe()
    process = context.Process(
        target=_run_crashable_image_worker,
        args=(
            *(str(path) for path in paths),
            owner_user_id,
            workload_id,
            pause_after_commits,
            pause_checkpoint,
            child,
        ),
    )
    process.start()
    child.close()
    return process, parent


def _kill_reported_process(process, control, *, timeout: float = 180.0):
    try:
        assert control.poll(timeout), "image worker did not reach its crash point"
        message = control.recv()
        assert message[0] in {"claim", "checkpoint"}, message
        assert process.pid is not None
        os.kill(process.pid, signal.SIGKILL)
        process.join(timeout=15)
        assert not process.is_alive()
        assert process.exitcode == -signal.SIGKILL
        return message
    finally:
        control.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def _independent_image_integrity_check(
    database_path: Path,
    artifact_root: Path,
    authority_path: Path,
    *,
    owner_user_id: str,
    workload_id: str,
    source_count: int,
) -> None:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM sources WHERE owner_user_id=?",
            (owner_user_id,),
        ).fetchone()[0] == source_count
        assert connection.execute(
            "SELECT COUNT(*) FROM sources "
            "WHERE owner_user_id=? AND accounted=1 AND state='ready'",
            (owner_user_id,),
        ).fetchone()[0] == source_count
        states = dict(connection.execute(
            "SELECT state, COUNT(*) FROM units "
            "WHERE owner_user_id=? GROUP BY state",
            (owner_user_id,),
        ).fetchall())
        assert states == {"committed": sum(states.values())}
        committed_units = states["committed"]
        unit_identity = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT unit_key), "
            "COUNT(committed_result_id), COUNT(DISTINCT committed_result_id) "
            "FROM units WHERE owner_user_id=? AND revision_id=("
            "SELECT active_revision_id FROM workloads "
            "WHERE owner_user_id=? AND id=?)",
            (owner_user_id, owner_user_id, workload_id),
        ).fetchone()
        assert tuple(unit_identity) == (
            committed_units,
            committed_units,
            committed_units,
            committed_units,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id=?",
            (owner_user_id,),
        ).fetchone()[0] == committed_units
        assert connection.execute(
            "SELECT COUNT(*) FROM attempts "
            "WHERE owner_user_id=? AND state IN ('leased','running')",
            (owner_user_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM attempts attempt
            JOIN units unit
              ON unit.owner_user_id=attempt.owner_user_id
             AND unit.id=attempt.unit_id
            WHERE attempt.owner_user_id=?
              AND attempt.state='succeeded'
              AND (attempt.fence<>unit.fence
                   OR unit.committed_result_id IS NULL)
            """,
            (owner_user_id,),
        ).fetchone()[0] == 0
        artifacts = connection.execute(
            "SELECT digest, size_bytes FROM artifacts "
            "WHERE owner_user_id=? AND workload_id=? AND state='committed'",
            (owner_user_id, workload_id),
        ).fetchall()
        assert len(artifacts) == 3
    finally:
        connection.close()

    owner_key = hashlib.sha256(
        b"metnos:durable-artifact-owner:1\x00"
        + owner_user_id.encode("utf-8")
    ).hexdigest()
    blob_directory = artifact_root / "owners" / owner_key / "blobs" / "sha256"
    expected_files = {str(row["digest"])[7:] for row in artifacts}
    actual_files = {path.name for path in blob_directory.iterdir()}
    assert actual_files == expected_files
    assert not any(path.name.startswith(".") for path in artifact_root.rglob("*"))
    for row in artifacts:
        path = blob_directory / str(row["digest"])[7:]
        payload = path.read_bytes()
        assert len(payload) == int(row["size_bytes"])
        assert hashlib.sha256(payload).hexdigest() == path.name

    authority = sqlite3.connect(authority_path)
    try:
        assert authority.execute(
            "SELECT COUNT(*) FROM source_grants"
        ).fetchone()[0] == 0
        assert authority.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        authority.close()


def test_image_preset_processes_a_parametric_corpus_and_reuses_solutions(tmp_path):
    source_count = int(os.environ.get("METNOS_DURABLE_IMAGE_E2E_SOURCES", "98"))
    assert source_count >= 1
    database = tmp_path / "private" / "durable.sqlite3"
    resolver = _resolver()
    source_work = 2 * source_count
    restart_points = iter(sorted({
        max(1, source_work * 3 // 10),
        max(1, source_work * 6 // 10),
    }))
    next_restart = next(restart_points, None)
    # Harness deadline only: it is neither a plan nor an engine capacity cap.
    test_deadline = monotonic() + max(120.0, source_count * 0.25)
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            "owner-images", "images-corpus",
            redacted_request={"summary": "synthetic corpus"},
        )
        admitted = admit_candidate(
            store,
            "owner-images",
            draft.workload_id,
            image_questions_plan(),
            inventory([source(index) for index in range(source_count)]),
            expected_version=draft.version,
            runners=resolver,
            output_schemas=output_schemas(),
        )
        store.transition_workload(
            "owner-images", draft.workload_id, WorkloadState.QUEUED,
            expected_version=store.get_workload("owner-images", draft.workload_id).version,
        )
        repository = ArtifactRepository.open(database)
        artifact_store = None
        try:
            artifact_store = ArtifactStore(tmp_path / "artifacts", repository)
            def make_bridge(open_store, open_artifacts):
                return _make_bridge(
                    open_store,
                    open_artifacts,
                    source_resolver=lambda item, _context: source_resolution(item),
                )

            bridge = make_bridge(store, artifact_store)
            worker = _worker(store)
            outcomes = []
            executed_units = 0
            while monotonic() < test_deadline:
                outcome = bridge.run_once(worker)
                outcomes.append(outcome.status)
                if outcome.lease is not None:
                    executed_units += 1
                if next_restart is not None and executed_units >= next_restart:
                    artifact_store.close()
                    store.close()
                    store = DurableWorkloadStore.open(database)
                    repository = ArtifactRepository.open(database)
                    artifact_store = ArtifactStore(tmp_path / "artifacts", repository)
                    bridge = make_bridge(store, artifact_store)
                    worker = _worker(store)
                    next_restart = next(restart_points, None)
                if outcome.status is WorkerRunStatus.IDLE:
                    break
            else:
                raise AssertionError(
                    "parametric image corpus did not become idle before the "
                    f"test deadline (sources={source_count}, cycles={len(outcomes)})"
                )

            assert WorkerRunStatus.FAILED not in outcomes
            assert store.get_workload("owner-images", draft.workload_id).state is WorkloadState.COMPLETED
            counts = dict(store._connection.execute(
                """
                SELECT stage.stage_key, COUNT(*)
                FROM units unit
                JOIN stages stage
                  ON stage.owner_user_id=unit.owner_user_id
                 AND stage.id=unit.stage_id
                 AND stage.revision_id=unit.revision_id
                WHERE unit.owner_user_id=? AND unit.revision_id=?
                GROUP BY stage.stage_key
                """,
                ("owner-images", admitted.revision.revision_id),
            ).fetchall())
            assert counts["ocr"] == source_count
            assert counts["questions"] == source_count
            assert counts["solutions"] == 2
            occurrence_rows = store._connection.execute(
                """
                SELECT result.payload_json
                FROM results result
                JOIN units unit
                  ON unit.owner_user_id=result.owner_user_id
                 AND unit.revision_id=result.revision_id
                 AND unit.id=result.unit_id
                JOIN stages stage
                  ON stage.owner_user_id=unit.owner_user_id
                 AND stage.revision_id=unit.revision_id
                 AND stage.id=unit.stage_id
                WHERE result.owner_user_id=? AND result.revision_id=?
                  AND stage.stage_key='questions'
                """,
                ("owner-images", admitted.revision.revision_id),
            ).fetchall()
            occurrences = [
                entry
                for row in occurrence_rows
                for entry in json.loads(str(row["payload_json"]))["entries"]
            ]
            expected_primary = sum(
                index % 11 != 0 for index in range(source_count)
            )
            expected_secondary = sum(
                index % 11 != 0 and index % 17 == 0
                for index in range(source_count)
            )
            assert len(occurrences) == expected_primary + expected_secondary
            assert len({
                entry["question_occurrence_id"] for entry in occurrences
            }) == len(occurrences)

            canonical_row = store._connection.execute(
                """
                SELECT result.payload_json
                FROM results result
                JOIN units unit
                  ON unit.owner_user_id=result.owner_user_id
                 AND unit.revision_id=result.revision_id
                 AND unit.id=result.unit_id
                JOIN stages stage
                  ON stage.owner_user_id=unit.owner_user_id
                 AND stage.revision_id=unit.revision_id
                 AND stage.id=unit.stage_id
                WHERE result.owner_user_id=? AND result.revision_id=?
                  AND stage.stage_key='deduplicate'
                  AND unit.reduction_root=1
                """,
                ("owner-images", admitted.revision.revision_id),
            ).fetchone()
            canonical = json.loads(str(canonical_row["payload_json"]))["entries"]
            assert sorted(
                entry["occurrence_count"] for entry in canonical
            ) == sorted((expected_primary, expected_secondary))
            assert len(artifact_store.list_workload_artifacts("owner-images", draft.workload_id)) == 3
        finally:
            if artifact_store is None:
                repository.close()
            else:
                artifact_store.close()


def test_image_preset_keeps_an_unreadable_source_in_the_failed_denominator(
    tmp_path,
):
    resolver = _resolver()
    database = tmp_path / "private" / "durable.sqlite3"
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            "owner-images",
            "images-unreadable-source",
            redacted_request={"summary": "synthetic unreadable source"},
        )
        admitted = admit_candidate(
            store,
            "owner-images",
            draft.workload_id,
            image_questions_plan(),
            inventory([source(0)]),
            expected_version=draft.version,
            runners=resolver,
            output_schemas=output_schemas(),
        )
        current = store.get_workload("owner-images", draft.workload_id)
        store.transition_workload(
            "owner-images",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=current.version,
        )
        repository = ArtifactRepository.open(database)
        artifacts = ArtifactStore(tmp_path / "artifacts", repository)

        def unreadable(args):
            return {
                "ok": False,
                "ok_count": 0,
                "fail_count": 1,
                "source_id": args["source"]["source_id"],
                "entries": [],
                "failed": [{
                    "source_id": args["source"]["source_id"],
                    "error_code": "unreadable_fixture",
                }],
                TRANSPORT_USAGE_KEY: {
                    "schema_version": TRANSPORT_USAGE_SCHEMA_VERSION,
                    "records": [],
                    "dropped": 0,
                    "calls_started": 0,
                },
            }

        try:
            bridge = _make_bridge(
                store,
                artifacts,
                source_resolver=lambda item, _context: source_resolution(item),
                executor_result=unreadable,
            )
            outcome = bridge.run_once(_worker(store))
            assert outcome.status is WorkerRunStatus.FAILED
            assert store.get_workload(
                "owner-images", draft.workload_id,
            ).state is WorkloadState.NEEDS_ATTENTION
            source_row = store._connection.execute(
                "SELECT state, accounted FROM sources "
                "WHERE owner_user_id=? AND revision_id=?",
                ("owner-images", admitted.revision.revision_id),
            ).fetchone()
            assert tuple(source_row) == ("ready", 1)
            unit_row = store._connection.execute(
                "SELECT state, error_class FROM units "
                "WHERE owner_user_id=? AND revision_id=?",
                ("owner-images", admitted.revision.revision_id),
            ).fetchone()
            assert tuple(unit_row) == ("needs_attention", "executor_unknown")
            assert artifacts.list_workload_artifacts(
                "owner-images", draft.workload_id,
            ) == ()
        finally:
            artifacts.close()


@pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"),
    reason="the F12 process-recovery gate requires SIGKILL",
)
def test_full_image_preset_survives_real_sigkill_at_30_60_and_artifact_commit(
    tmp_path,
):
    source_count = 980
    owner = "owner-images-f12"
    input_root = tmp_path / "source-corpus"
    input_root.mkdir()
    for index in range(source_count):
        (input_root / f"source-{index:04d}.png").write_bytes(
            b"synthetic-image\x00" + index.to_bytes(4, "big")
        )

    database = tmp_path / "private" / "durable.sqlite3"
    artifacts = tmp_path / "artifacts"
    authority_path = tmp_path / "private" / "source-authority.sqlite3"
    resolver = _resolver()
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            owner,
            "images-f12-sigkill",
            redacted_request={"summary": "synthetic F12 corpus"},
        )
        with SourceAuthority.open(authority_path) as authority:
            sealed = authority.seal_and_register(
                [input_root],
                owner_user_id=owner,
                workload_id=draft.workload_id,
                device_id="server",
                limits=InventoryLimits(
                    max_sources=source_count,
                    max_total_bytes=16 * 1024 * 1024,
                    max_depth=2,
                ),
                valid_until=datetime.now(timezone.utc) + timedelta(days=1),
            )
        assert isinstance(sealed, SealedInventory)
        inline_inventory, streamed_sources = validate_inventory(sealed)
        assert inline_inventory is None
        assert streamed_sources is sealed["sources"]
        assert len(streamed_sources) == source_count
        try:
            admit_candidate(
                store,
                owner,
                draft.workload_id,
                image_questions_plan(),
                sealed,
                expected_version=draft.version,
                runners=resolver,
                output_schemas=output_schemas(),
            )
        finally:
            close = getattr(sealed, "close", None)
            if callable(close):
                close()
        current = store.get_workload(owner, draft.workload_id)
        store.transition_workload(
            owner,
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=current.version,
        )
        workload_id = draft.workload_id

    context = multiprocessing.get_context("spawn")
    paths = (database, artifacts, authority_path)
    source_work = source_count * 2
    observed = []
    for fraction, threshold in (
        (30, source_work * 3 // 10),
        (60, source_work * 6 // 10),
    ):
        process, control = _start_crashable_worker(
            context,
            paths,
            owner,
            workload_id,
            pause_after_commits=threshold,
        )
        message = _kill_reported_process(process, control)
        assert message[0] == "claim"
        assert message[2] >= threshold
        observed.append((fraction, message[2]))
        time.sleep(0.5)

    process, control = _start_crashable_worker(
        context,
        paths,
        owner,
        workload_id,
        pause_checkpoint="blob_after_registration_verification",
    )
    artifact_message = _kill_reported_process(process, control)
    assert artifact_message == (
        "checkpoint", "blob_after_registration_verification",
    )
    time.sleep(0.5)

    process, control = _start_crashable_worker(
        context,
        paths,
        owner,
        workload_id,
    )
    try:
        assert control.poll(300), "recovery worker did not report completion"
        terminal = control.recv()
        process.join(timeout=15)
        assert terminal[0] == "terminal", terminal
        assert terminal[1] == WorkloadState.COMPLETED.value
        assert process.exitcode == 0
    finally:
        control.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert observed[0][1] < observed[1][1]
    _independent_image_integrity_check(
        database,
        artifacts,
        authority_path,
        owner_user_id=owner,
        workload_id=workload_id,
        source_count=source_count,
    )

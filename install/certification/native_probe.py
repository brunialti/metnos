"""Read F5 postconditions from the installed service's native owners.

Run with the service identity and its verified installation/environment. This
probe never grants authority, publishes a contract, or creates an epoch store.
The HTTP scenario must retain this output separately from the tool response.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


class ProbeError(RuntimeError):
    pass


def read_workload(path: Path, owner: str, workload_id: str) -> dict:
    """Capture one real job, including attempts/results, without migrating it.

    The focused scenarios are small. Refuse an incomplete or oversized capture
    instead of silently dropping rows or treating HTTP status as result proof.
    Keep the plan, lease and attempt snapshots so the oracle can compare
    generations and checkpoints without confusing retries with the same attempt.
    """
    if not owner or not workload_id:
        raise ProbeError("workload_scope_required")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        budget = 1_048_576

        def canonical(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)

        def rows(query, parameters):
            nonlocal budget
            # Bound both row count and bytes before materializing JSON payloads.
            count = db.execute("SELECT count(*) FROM (" + query + ")", parameters).fetchone()[0]
            if count > 512:
                raise ProbeError("workload_probe_row_limit")
            columns = [item[0] for item in db.execute(query + " LIMIT 0", parameters).description]
            sizes = "+".join('coalesce(length(CAST("' + name + '" AS BLOB)),0)' for name in columns)
            size = db.execute("SELECT coalesce(sum(" + sizes + "),0) FROM (" + query + ")",
                              parameters).fetchone()[0]
            budget -= size
            if budget < 0:
                raise ProbeError("workload_probe_byte_limit")
            return [dict(row) for row in db.execute(query, parameters)]

        jobs = rows("SELECT owner_user_id,id,state,active_revision_id,version,request_digest,"
                    "created_at,updated_at FROM workloads WHERE owner_user_id=? AND id=?",
                    (owner, workload_id))
        if len(jobs) != 1:
            raise ProbeError("workload_not_found")
        job = jobs[0]
        revisions = rows("SELECT id,number,plan_digest,inventory_digest,plan_json FROM revisions "
                         "WHERE owner_user_id=? AND workload_id=? AND id=?",
                         (owner, workload_id, job["active_revision_id"]))
        if len(revisions) != 1:
            raise ProbeError("workload_revision_missing")
        try:
            plan = json.loads(revisions[0]["plan_json"])
            framed = "metnos:durable-plan:1\0" + canonical(plan)
            digest = "sha256:" + hashlib.sha256(framed.encode("utf-8")).hexdigest()
            if (not isinstance(plan, dict) or canonical(plan) != revisions[0]["plan_json"]
                    or digest != revisions[0]["plan_digest"]):
                raise ValueError("noncanonical or changed plan")
        except (TypeError, ValueError) as error:
            raise ProbeError("workload_plan_digest_mismatch") from error
        scope = (owner, job["active_revision_id"])
        units = rows("SELECT id,revision_id,stage_id,state,attempt_count,active_attempt_id,"
                     "fence,committed_result_id,unit_key,lease_worker_id,lease_expires_at,created_at,updated_at "
                     "FROM units WHERE owner_user_id=? AND revision_id=? ORDER BY id", scope)
        attempts = rows("SELECT a.id,a.unit_id,a.number,a.fence,a.state,a.invocation_id,a.started_at,"
                        "a.ended_at,a.executor_snapshot_json,a.model_snapshot_json,a.metrics_json,"
                        "a.worker_id,a.device_id,a.structured_error_json "
                        "FROM attempts a JOIN units u "
                        "ON u.owner_user_id=a.owner_user_id AND u.id=a.unit_id "
                        "WHERE u.owner_user_id=? AND u.revision_id=? ORDER BY a.id", scope)
        results = rows("SELECT id,revision_id,unit_id,attempt_id,fence,digest,schema_version,"
                       "payload_json,blob_ref,committed_at FROM results "
                       "WHERE owner_user_id=? AND revision_id=? ORDER BY id", scope)
        by_unit = {unit["id"]: unit for unit in units}
        by_attempt = {attempt["id"]: attempt for attempt in attempts}
        for unit in units:
            if unit["active_attempt_id"] is None:
                continue
            attempt = by_attempt.get(unit["active_attempt_id"])
            if (attempt is None or attempt["unit_id"] != unit["id"]
                    or attempt["fence"] != unit["fence"]
                    or attempt["worker_id"] != unit["lease_worker_id"]):
                raise ProbeError("workload_active_attempt_mismatch")
        committed = {}
        for result in results:
            unit = by_unit.get(result["unit_id"])
            attempt = by_attempt.get(result["attempt_id"])
            if (unit is None or attempt is None or result["unit_id"] in committed
                    or unit["state"] != "committed" or attempt["state"] != "succeeded"
                    or unit["committed_result_id"] != result["id"]
                    or attempt["unit_id"] != unit["id"]
                    or not unit["fence"] == attempt["fence"] == result["fence"]):
                raise ProbeError("workload_result_binding_mismatch")
            if result["payload_json"] is None or result["blob_ref"] is not None:
                raise ProbeError("workload_external_result_not_captured")
            try:
                payload = json.loads(result["payload_json"])
                framed = "metnos:durable-result:1\0" + canonical({
                    "schema_version": result["schema_version"], "payload": payload})
                digest = "sha256:" + hashlib.sha256(framed.encode("utf-8")).hexdigest()
                if (not isinstance(payload, dict) or canonical(payload) != result["payload_json"]
                        or digest != result["digest"]):
                    raise ValueError("noncanonical or changed result")
            except (TypeError, ValueError) as error:
                raise ProbeError("workload_result_digest_mismatch") from error
            committed[unit["id"]] = result["id"]
        if any(unit["state"] == "committed" and unit["id"] not in committed for unit in units):
            raise ProbeError("workload_committed_result_missing")
        return {"workload": job, "revision": revisions[0], "units": units,
                "attempts": attempts, "results": results}


def read_epochs(path: Path, contracts: list[str]) -> list[dict]:
    """Use one read-only transaction; a missing migration is not an empty DB."""
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        slots = ",".join("?" for _ in contracts)
        return [dict(row) for row in db.execute(
            "SELECT contract_id,generation_id,name,source,state,lifecycle,"
            "state_version,lifecycle_override,override_reason,total_calls,"
            "successful_calls,failed_calls,positive_feedback,negative_feedback,"
            "last_call_ok,first_seen_at,last_used_at "
            f"FROM executor_epochs WHERE contract_id IN ({slots}) "
            "ORDER BY contract_id,generation_id", contracts,
        )]


def read_invocations(record: dict, subjects: dict) -> list[dict]:
    """Bind native dispatches to subjects authenticated by this observation.

    Call before replacing a generation and retain the observation unchanged.
    The installed publisher authenticates current generations, not historical
    dispatches after replacement. An empty result is an observed absence of
    dispatch receipts; it does not by itself pass an exclusion scenario.
    """
    from executor_birth_feedback import (
        FeedbackError, dispatch_identifier_reference, execution_receipt_from_record,
    )

    names = {subject["name"] for subject in subjects.values()}
    if not 1 <= len(subjects) <= 16 or len(names) != len(subjects):
        raise ProbeError("invocation_subject_scope_invalid")
    turn_id = record.get("turn_id")
    times = [record.get("ts_start"), record.get("ts_end")]
    if (not isinstance(turn_id, str) or not turn_id
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in times)
            or not 0 <= times[0] <= times[1]):
        raise ProbeError("invocation_turn_invalid")
    steps = record.get("steps")
    if not isinstance(steps, list) or len(steps) > 512:
        raise ProbeError("invocation_steps_invalid")
    turn_ref = dispatch_identifier_reference("turn", turn_id)
    seen, invocations = set(), []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ProbeError("invocation_step_invalid")
        tool = step.get("chosen_tool")
        encoded = step.get("execution_receipt")
        if encoded is None:
            if tool in names:
                # A selected tool without proof cannot certify non-invocation.
                raise ProbeError("invocation_receipt_missing")
            continue
        try:
            receipt = execution_receipt_from_record(encoded)
            dispatched, completed = [
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=timezone.utc).timestamp()
                for value in (receipt.dispatched_at, receipt.completed_at)
            ]
        except (FeedbackError, TypeError, ValueError) as error:
            raise ProbeError("invocation_receipt_invalid") from error
        if (receipt.turn_id != turn_ref or receipt.executor_name != tool
                or not math.floor(times[0]) <= dispatched <= completed <= math.floor(times[1])):
            raise ProbeError("invocation_turn_binding_mismatch")
        if receipt.receipt_id in seen:
            raise ProbeError("invocation_receipt_reused")
        seen.add(receipt.receipt_id)
        subject = subjects.get(receipt.contract_id.value)
        if subject is None:
            if tool in names:
                raise ProbeError("invocation_subject_binding_mismatch")
            continue
        if (subject["name"] != tool or subject["candidate_id"] != receipt.candidate_id
                or subject["generation_id"] != receipt.generation_id):
            raise ProbeError("invocation_subject_binding_mismatch")
        invocations.append({
            "step_index": index, "receipt_id": receipt.receipt_id,
            "contract_id": receipt.contract_id.value, "name": tool,
            "generation_id": receipt.generation_id, "candidate_id": receipt.candidate_id,
            "dispatched_at": receipt.dispatched_at, "completed_at": receipt.completed_at,
            "arguments_hash": receipt.arguments_hash, "arguments": dict(receipt.arguments),
            "output_hash": receipt.output_hash, "output": dict(receipt.reduced_output),
        })
    return invocations


def snapshot(contracts: list[str], *, turn_record: dict | None = None) -> dict:
    if not 1 <= len(contracts) <= 16 or len(set(contracts)) != len(contracts):
        raise ProbeError("one_to_sixteen_distinct_contracts_required")
    # Imports belong here: collecting evidence must use the selected installed
    # runtime, never the development checkout that contains this test adapter.
    import config
    from contract_store import VerifiedManifest, current_contract, current_revision_id
    from executor_birth_bootstrap import bootstrap_birth_runtime
    from executor_birth_feedback import pending_failure_reviews
    from loader import VISIBILITY_COMPOSER, filter_for_visibility, load_catalog
    from manifest_inventory import ContractId, ManifestOrigin, prospective_manifest_ref
    from sign import list_trusted_publics

    identities = []
    for value in contracts:
        origin, relative = value.split(":", 1)
        identities.append(ContractId(ManifestOrigin(origin), relative))
    refs = {cid.value: prospective_manifest_ref(cid) for cid in identities}
    before = {cid: current_revision_id(ref) for cid, ref in refs.items()}
    publisher = bootstrap_birth_runtime().core.commit_publisher
    catalog = load_catalog(verify=True)
    visible = filter_for_visibility(catalog, VISIBILITY_COMPOSER)
    subjects = {}
    for cid in identities:
        generation = before[cid.value]
        binding = publisher.authenticate_execution_binding(cid, generation)
        signed = current_contract(refs[cid.value], trusted_publics=list_trusted_publics())
        if (not isinstance(signed, VerifiedManifest)
                or signed.generation_id != generation
                or binding.contract_id != cid
                or binding.generation_id != generation):
            raise ProbeError("authenticated_generation_changed")
        name = binding.executor_name
        if signed.parsed.get("name") != name:
            raise ProbeError("authenticated_name_mismatch")
        predecessor, payloads = publisher.resolve_predecessor(
            SimpleNamespace(manifest_ref=refs[cid.value]))
        # The existing publisher authenticates both the immutable payloads and
        # the receipt in the selected admission context. No key or publication
        # authority is copied into this independent reader. The receipt's
        # admission outcome is distinct from the manifest lifecycle: synthesized
        # code can have a preexercise admission. Preserve both native facts.
        if (predecessor.revision_kind != "generation"
                or predecessor.revision_id != generation
                or predecessor.approved_lifecycle is None
                or not predecessor.admission_receipt_hash or not payloads
                or dict(predecessor.payload_hashes) != {
                    key: "sha256:" + hashlib.sha256(value).hexdigest()
                    for key, value in payloads.items()}):
            raise ProbeError("authenticated_admission_mismatch")
        loaded = catalog.executors.get(name)
        if loaded is not None and (
                loaded.contract_id != cid.value or loaded.generation_id != generation):
            raise ProbeError("loaded_generation_mismatch")
        subjects[cid.value] = {
            "name": name, "generation_id": generation,
            "candidate_id": binding.candidate_id,
            "lifecycle": str(signed.parsed.get("lifecycle", "active")),
            "approved_lifecycle": predecessor.approved_lifecycle,
            "loaded_lifecycle": loaded.lifecycle if loaded is not None else None,
            "manifest_hash": signed.manifest_hash,
            "code_digest": signed.verified_code_digest,
            "admission_receipt_hash": predecessor.admission_receipt_hash,
            "payload_hashes": dict(predecessor.payload_hashes),
            "loaded": loaded is not None, "selectable": name in visible.executors,
        }
    epochs = read_epochs(config.PATH_USER_STATE / "birth/executor_epochs.sqlite", contracts)
    for cid, subject in subjects.items():
        current = [row for row in epochs if row["contract_id"] == cid and row["state"] == "current"]
        if (len(current) != 1
                or current[0]["generation_id"] != subject["generation_id"]
                or current[0]["lifecycle"] != subject["lifecycle"]):
            raise ProbeError("signed_epoch_mismatch")
    generations = {row["generation_id"] for row in epochs} | set(before.values())
    # Refuse a saturated read rather than silently omitting an outbox entry.
    queued = pending_failure_reviews(
        db_path=config.PATH_USER_STATE / "birth/failure_reviews.sqlite", limit=1025,
    )
    if len(queued) > 1024:
        raise ProbeError("failure_review_probe_limit")
    outbox = [{
        "job_id": job_id, "generation_id": request.generation_id,
        "candidate_id": request.candidate_id,
        "execution_receipt_id": request.execution_receipt_id,
        "error_code": request.error_code,
    } for job_id, request in queued if request.generation_id in generations]
    invocations = read_invocations(turn_record, subjects) if turn_record is not None else None
    after = {cid: current_revision_id(ref) for cid, ref in refs.items()}
    if after != before:
        raise ProbeError("publication_changed_during_probe")
    observation = {
        "subjects": subjects, "epochs": epochs, "failure_reviews": outbox,
        "catalog_size": len(catalog.executors),
        "rejected_count": len(catalog.rejected),
    }
    if turn_record is not None:
        observation.update(turn_id=turn_record["turn_id"], invocations=invocations)
    return observation



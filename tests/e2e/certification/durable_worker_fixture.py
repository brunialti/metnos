#!/usr/bin/env python3
"""Deterministic LRE worker boundary for the RM-0006 HTTP certification.

The service, database, leases, recovery, source authority, compiler and
artifact publication are production components.  Only the expensive model
and OCR provider calls are replaced with closed deterministic observations.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from datetime import timedelta
from types import SimpleNamespace

from durable_workloads.artifacts import ArtifactRepository, ArtifactStore
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.image_preset import (
    ImagePresetWorkloadInvoker,
    PRESET_ID,
    image_questions_plan,
    output_schemas,
    registered_output_schema_names,
    registered_runner_bindings,
    runner_resolver,
)
from durable_workloads.internal_runners import approved_internal_runners
from durable_workloads.runtime_bindings import (
    BoundExecutionBridge,
    RuntimeRegistration,
    RuntimeRegistry,
)
from durable_workloads.service import DurableWorkerService
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.worker import DurableWorker
from llm_telemetry import (
    TRANSPORT_USAGE_KEY,
    TRANSPORT_USAGE_SCHEMA_VERSION,
    record,
)


def _model(name: str, _prompt: str, args: dict, _context: object) -> dict:
    from llm_router import resolved_tier_spec

    delay_ms = int(os.environ.get("METNOS_RM0006_MODEL_DELAY_MS", "0") or 0)
    if delay_ms < 0 or delay_ms > 30_000:
        raise ValueError("METNOS_RM0006_MODEL_DELAY_MS must be between 0 and 30000")
    if delay_ms:
        time.sleep(delay_ms / 1000)

    binding = resolved_tier_spec("wise")
    record(
        provider=str(binding.get("provider") or ""),
        model=str(binding.get("model") or ""),
        tier="wise",
        result=SimpleNamespace(in_tokens=5, out_tokens=3, latency_ms=1),
    )
    if name == "durable.images.extract_questions":
        return {"questions": [{
            "text": "Qual è la risposta di certificazione?",
            "confidence": 0.99,
        }]}
    if name == "durable.images.deduplicate":
        return {"merge_groups": []}
    if name == "durable.images.answer":
        return {
            "status": "answered",
            "answer": "Risposta sintetica di certificazione.",
            "reason": "fixture deterministica",
            "confidence": 0.99,
        }
    if name == "durable.images.validate":
        return {"valid": True, "reason": "contratto valido"}
    if name == "durable.images.reduce_solutions":
        return {"markdown": "# Soluzioni\n\nRisposta di certificazione.\n"}
    if name == "durable.images.reduce_notes":
        return {"markdown": "# Note\n\nNota di certificazione.\n"}
    if name == "durable.images.reduce_formulae":
        return {"markdown": "# Formulario\n\nNessuna formula.\n"}
    if name == "durable.images.assemble":
        return {"artifacts": [
            {
                "logical_name": "solutions_markdown",
                "markdown": "# Soluzioni\n\nRisposta di certificazione.\n",
            },
            {
                "logical_name": "notes_markdown",
                "markdown": "# Note\n\nNota di certificazione.\n",
            },
            {
                "logical_name": "formulae_markdown",
                "markdown": "# Formulario\n\nNessuna formula.\n",
            },
        ]}
    raise AssertionError(f"unexpected workload runner: {name}")


def _ocr(args: dict) -> dict:
    source = args["source"]
    return {
        "ok": True,
        "ok_count": 1,
        "fail_count": 0,
        "source_id": source["source_id"],
        "entries": [{
            "source_id": source["source_id"],
            "content": "Qual è la risposta di certificazione?",
            "char_count": 37,
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


def _registry() -> RuntimeRegistry:
    schemas = output_schemas()
    registration = RuntimeRegistration(
        name=PRESET_ID,
        runner_bindings=registered_runner_bindings(),
        runners=runner_resolver(),
        output_schemas=schemas,
        output_schema_names=registered_output_schema_names(schemas),
        workload_invoker=ImagePresetWorkloadInvoker(_model),
        candidate_plan_factory=image_questions_plan,
    )
    return RuntimeRegistry((registration,))


def _delayed_internal_runners(artifacts: ArtifactStore, store) -> dict[str, object]:
    """Wrap every internal runner with the same bounded fault-injection delay."""

    delay_ms = int(os.environ.get("METNOS_RM0006_INTERNAL_DELAY_MS", "0") or 0)
    if delay_ms < 0 or delay_ms > 30_000:
        raise ValueError("METNOS_RM0006_INTERNAL_DELAY_MS must be between 0 and 30000")
    runners = approved_internal_runners(artifacts, store)
    if not delay_ms:
        return runners

    def delayed(runner):
        def call(args, context):
            time.sleep(delay_ms / 1000)
            return runner(args, context)

        return call

    return {name: delayed(runner) for name, runner in runners.items()}


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("METNOS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    registry = _registry()

    def worker_factory(store):
        return DurableWorker(
            store,
            f"rm0006-{os.getpid()}",
            registry.capabilities({
                "cpu": 1,
                "device": 1,
                "llm": 1,
                "local_io": 1,
                "network_io": 0,
                "vlm": 1,
            }),
            lease_duration=timedelta(seconds=1),
        )

    def bridge_factory(store):
        import config
        from loader import load_catalog

        catalog = load_catalog(verify=True, lang="en")
        repository = ArtifactRepository.open(store.database_path)
        artifacts = ArtifactStore(config.PATH_DURABLE_ARTIFACTS, repository)
        authority = SourceAuthority.open()
        bridge = DurableExecutionBridge(
            store,
            runners=registry.runners,
            output_schemas=registry.output_schemas,
            source_resolver=authority.resolve,
            executor_loader=lambda name: catalog.executors.get(name),
            executor_invoker=lambda _executor, args, *_rest: _ocr(args),
            workload_invoker=registry.invoke_workload,
            internal_runners=_delayed_internal_runners(artifacts, store),
        )
        return BoundExecutionBridge(bridge, (authority, artifacts))

    service = DurableWorkerService(
        enabled=True,
        worker_factory=worker_factory,
        bridge_factory=bridge_factory,
        poll_interval_s=0.05,
        recovery_batch_size=100,
    )

    def stop(_signum: int, _frame: object) -> None:
        service.request_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return service.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())

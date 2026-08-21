"""Deployment composition for approved LRE runtime packages.

The universal engine consumes only ``RuntimeRegistration`` values.  Concrete
presets belong here, at the composition boundary, so core scheduling,
storage, recovery and execution never import or branch on a task domain.
"""

from __future__ import annotations

from collections.abc import Callable

from durable_workloads.image_preset import (
    ImagePresetWorkloadInvoker,
    output_schemas,
    registered_output_schema_names,
    registered_runner_bindings,
    runner_resolver,
)
from durable_workloads.runtime_bindings import (
    BoundExecutionBridge,
    RuntimeFactory,
    RuntimeRegistration,
    RuntimeRegistry,
)
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import DurableWorker


def default_runtime_registry() -> RuntimeRegistry:
    """Build the closed registry from approved package contributions."""

    schemas = output_schemas()
    image_questions = RuntimeRegistration(
        name="images.questions.v1",
        runner_bindings=registered_runner_bindings(),
        runners=runner_resolver(),
        output_schemas=schemas,
        output_schema_names=registered_output_schema_names(schemas),
        workload_invoker=ImagePresetWorkloadInvoker(),
    )
    return RuntimeRegistry((image_questions,))


def production_factories() -> tuple[
    Callable[[DurableWorkloadStore], DurableWorker],
    Callable[[DurableWorkloadStore], BoundExecutionBridge],
]:
    factory = RuntimeFactory(registry_factory=default_runtime_registry)
    return factory.worker, factory.bridge


__all__ = ["default_runtime_registry", "production_factories"]

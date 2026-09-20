"""Deployment composition for approved LRE runtime packages.

The universal engine consumes only ``RuntimeRegistration`` values.  Concrete
presets belong here, at the composition boundary, so core scheduling,
storage, recovery and execution never import or branch on a task domain.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from durable_workloads.direct_invocation import direct_runtime_registration

from durable_workloads.image_preset import (
    ImagePresetWorkloadInvoker,
    PRESET_ID,
    image_questions_plan,
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


ADMISSION_NAMES = (PRESET_ID,)


@lru_cache(maxsize=1)
def _verified_catalog_snapshot():
    from loader import load_catalog

    return load_catalog(verify=True, lang="en")


@lru_cache(maxsize=1)
def default_runtime_registry() -> RuntimeRegistry:
    """Build the closed registry from approved package contributions."""

    schemas = output_schemas()
    image_questions = RuntimeRegistration(
        name=PRESET_ID,
        runner_bindings=registered_runner_bindings(),
        runners=runner_resolver(),
        output_schemas=schemas,
        output_schema_names=registered_output_schema_names(schemas),
        workload_invoker=ImagePresetWorkloadInvoker(),
        candidate_plan_factory=image_questions_plan,
    )
    registrations = [image_questions]
    direct = direct_runtime_registration(_verified_catalog_snapshot())
    if direct is not None:
        registrations.append(direct)
    return RuntimeRegistry(tuple(registrations))


def production_factories() -> tuple[
    Callable[[DurableWorkloadStore], DurableWorker],
    Callable[[DurableWorkloadStore], BoundExecutionBridge],
]:
    factory = RuntimeFactory(registry_factory=default_runtime_registry)
    return factory.worker, factory.bridge


__all__ = ["ADMISSION_NAMES", "default_runtime_registry", "production_factories"]

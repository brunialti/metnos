"""Directory invocations share the ordinary candidate constructor."""
from .direct_invocation import build_direct_candidate

PARTITIONED_PLAN_ID = "metnos.partitioned.v1"
PARTITIONS_STAGE = "partitions"
EXECUTE_STAGE = "execute"
PARTITION_FIELD = "partition"


def unit_concurrency(executor, partitions):
    return 1


def build_partitioned_candidate(executor, policy, base_args, partitions,
                                target_device, *, max_concurrency=1):
    return build_direct_candidate(executor, base_args, target_device, policy=policy,
                                  partitions=partitions, max_concurrency=max_concurrency)

"""Common observation boundary for every generative model transport.

HTTP providers, vision and the deterministic subprocess enter here once per
attempt. Logical policy is resolved before entry. This module does no I/O;
Monitor's collector owns persistence and child transport owns attribution.
"""
from __future__ import annotations

import functools
from contextlib import contextmanager

from monitor_capture import call_scope as _monitor_scope, observe_response


@contextmanager
def call_scope(*, provider, model=None, level=None, kind="chat", binding_revision=None):
    from llm_telemetry import mark_call_started
    from llm_workloads import workload_for_request

    with _monitor_scope(provider=provider, model=model, level=level, kind=kind,
                        workload=workload_for_request(level),
                        binding_revision=binding_revision) as observation:
        mark_call_started()
        yield observation


def provider_call(provider):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(self, *args, **kwargs):
            from llm_telemetry import current_tier

            tools = kwargs.get("expect_tools") is True or (len(args) > 1 and args[1] is True) or (
                args and isinstance(args[0], dict) and bool(args[0].get("tools")))
            with call_scope(provider=provider, model=self.model, level=current_tier(),
                            kind="tools" if tools else "chat",
                            binding_revision=getattr(self, "_monitor_binding_revision", None)):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate

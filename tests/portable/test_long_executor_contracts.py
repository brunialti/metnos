"""Release check: every shipped tool with a long ceiling has somewhere to run.

A tool whose declared ceiling reaches the automatic LRE boundary is sent there
by that number alone, and a contract LRE cannot represent is then refused
whatever the person asks. Before this check that was found by a person, months
later (15/9, 1/11 and 9/11/2026). Each such tool must declare one of three
states: it runs alone as a direct plan; it has its own registered plan; the
person takes part in it, so it stays in the chat turn.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from durable_runtime_registry import invocation_plan_adapter
from durable_workloads.direct_invocation import (
    AUTO_LRE_MIN_TIMEOUT_S,
    DirectInvocationUnsupported,
    asks_the_person,
    contract_refusal,
    is_intrinsically_long,
)


def _state(executor) -> str:
    """Where this tool runs, or the fixed sentence of the check that refuses."""
    try:
        if invocation_plan_adapter(executor) is not None:
            return "own plan"
    except DirectInvocationUnsupported as exc:
        return str(exc)
    if asks_the_person(executor):
        return "with the person"
    return contract_refusal(executor) or "alone"


def _reaches_the_boundary(executor) -> bool:
    timeout = getattr(executor, "timeout_s", None)
    return bool(getattr(executor, "lre_plan", "")) or (
        type(timeout) is int and timeout >= AUTO_LRE_MIN_TIMEOUT_S)


def test_every_shipped_long_tool_declares_where_it_runs(standard_catalog):
    homeless = {
        executor.name: _state(executor)
        for executor in standard_catalog
        if getattr(executor, "lifecycle", "") == "active"
        and not getattr(executor, "dormant", False)
        and _reaches_the_boundary(executor)
        and _state(executor) not in {"alone", "own plan", "with the person"}
    }
    assert homeless == {}, (
        "these tools declare a ceiling of ten minutes or more and no way to "
        "run: every request for them would be refused at the long-job "
        f"boundary. Tool -> refusing check: {homeless}")


def test_the_three_states_are_all_in_use(standard_catalog):
    # The check above must not pass because nothing reaches it.
    states = {
        _state(executor) for executor in standard_catalog
        if getattr(executor, "lifecycle", "") == "active" and _reaches_the_boundary(executor)
    }
    assert states == {"alone", "own plan", "with the person"}


def test_installing_asks_first_and_is_not_a_long_job(standard_catalog):
    # 9/11/2026, turn fb657e87: «installa il pacchetto htop sul server» was
    # refused before its approval card could be shown, and had been since the
    # boundary existed. The contract declares the consent token: it stays in
    # the turn, and so does its opposite direction, which is the same tool.
    by_name = {executor.name: executor for executor in standard_catalog}
    install = by_name["install_packages"]
    assert install.timeout_s >= AUTO_LRE_MIN_TIMEOUT_S
    assert asks_the_person(install) is True
    assert is_intrinsically_long(install) is False


@pytest.fixture(scope="module")
def standard_catalog():
    """Read candidate manifests, never the installed catalog or user data."""
    from loader import Catalog, _load_dir_into_catalog

    catalog = Catalog()
    _load_dir_into_catalog(
        Path(__file__).resolve().parents[2] / "executors", catalog,
        verify=False, is_synthesized=False,
    )
    assert catalog.executors, "candidate catalog is empty"
    return tuple(catalog.executors.values())

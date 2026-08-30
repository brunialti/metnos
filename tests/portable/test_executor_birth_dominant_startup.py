"""G7-A: the private completion capability, its binding and its single use."""
from __future__ import annotations

import copy
import pickle

import pytest

import executor_birth_dominant_startup as dominant


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _bindings(**overrides: str) -> dominant.DominantStartupBindingsV1:
    fields = {
        "request_id": _digest("1"),
        "previous_head_digest": _digest("2"),
        "catalog_id": _digest("3"),
        "effective_topology_hash": _digest("4"),
        "enforcement_evidence_digest": _digest("5"),
    }
    fields.update(overrides)
    return dominant.DominantStartupBindingsV1(**fields)


class _Session:
    """A stand-in with the one property the product's sessions all have."""

    __slots__ = ()

    def __reduce__(self):
        raise TypeError("sessions cannot be serialized")


def _sessions() -> tuple[object, ...]:
    return (_Session(), _Session(), _Session())


def test_the_capability_is_consumed_exactly_once() -> None:
    """A second crossing on the same authority is refused, not repeated."""
    bindings = _bindings()
    capability = dominant.mint_for_test_v1(bindings, _sessions())

    assert dominant.consume_v1(capability, bindings) == (
        dominant.bindings_digest_v1(bindings)
    )
    with pytest.raises(dominant.DominantStartupError) as spent:
        dominant.consume_v1(capability, bindings)
    assert spent.value.code == "dominant_startup_capability_spent"


def test_a_drift_between_mint_and_use_refuses_the_crossing() -> None:
    """The caller re-reads; a single changed field stops the crossing."""
    capability = dominant.mint_for_test_v1(_bindings(), _sessions())
    with pytest.raises(dominant.DominantStartupError) as drifted:
        dominant.consume_v1(capability, _bindings(catalog_id=_digest("9")))
    assert drifted.value.code == "dominant_startup_binding_drift"


def test_the_framing_separates_neighbouring_fields() -> None:
    """Moving a value from one field to the next must change the digest."""
    first = dominant.bindings_digest_v1(_bindings())
    swapped = dominant.bindings_digest_v1(
        _bindings(request_id=_digest("2"), previous_head_digest=_digest("1")),
    )
    assert first != swapped


@pytest.mark.parametrize(("case", "code"), [
    ("aliased", "dominant_startup_sessions_invalid"),
    ("too_few", "dominant_startup_sessions_invalid"),
    ("absent", "dominant_startup_sessions_invalid"),
    ("transferable", "dominant_startup_sessions_invalid"),
])
def test_the_three_locks_must_be_three_live_sessions(
    case: str, code: str,
) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    if case == "aliased":
        shared = _Session()
        sessions = (shared, shared, _Session())
    elif case == "too_few":
        sessions = (_Session(), _Session())
    elif case == "absent":
        sessions = (_Session(), None, _Session())
    else:
        sessions = (_Session(), _Session(), object())

    with pytest.raises(dominant.DominantStartupError) as denied:
        dominant.mint_for_test_v1(_bindings(), sessions)
    assert denied.value.code == code


def test_a_look_alike_capability_does_not_open_the_door() -> None:
    """The type is the credential, and the seal is not reachable."""
    class LookAlike:
        _digest = dominant.bindings_digest_v1(_bindings())
        _sessions = _sessions()

    with pytest.raises(dominant.DominantStartupError) as denied:
        dominant.consume_v1(LookAlike(), _bindings())
    assert denied.value.code == "dominant_startup_capability_invalid"

    with pytest.raises(dominant.DominantStartupError) as sealed:
        dominant._DominantStartupInstalledV1(_bindings(), _sessions(), object())
    assert sealed.value.code == "dominant_startup_capability_invalid"


def test_the_capability_survives_neither_a_copy_nor_a_pickle() -> None:
    """A capability that crossed a process boundary would outlive its locks."""
    capability = dominant.mint_for_test_v1(_bindings(), _sessions())
    for attempt in (
        lambda: copy.copy(capability),
        lambda: copy.deepcopy(capability),
        lambda: pickle.dumps(capability),
    ):
        with pytest.raises(TypeError):
            attempt()


@pytest.mark.parametrize("field", [
    "request_id", "previous_head_digest", "catalog_id",
    "effective_topology_hash", "enforcement_evidence_digest",
])
def test_every_binding_must_be_a_digest(field: str) -> None:
    """A name, a path or a truncated hash is not an identity."""
    with pytest.raises(dominant.DominantStartupError) as denied:
        _bindings(**{field: "metnos-http.service"})
    assert denied.value.code == "dominant_startup_binding_invalid"


def test_no_productive_minter_is_exported() -> None:
    """G7's wrapper will mint it; importing a name must not.

    Everything reachable from `__all__` frames or describes; nothing mints and
    nothing consumes. The same rule the publication core follows.
    """
    assert "mint_for_test_v1" not in dominant.__all__
    assert not any(
        name.startswith("mint") or name.startswith("consume")
        for name in dominant.__all__
    )

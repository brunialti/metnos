"""G7-A: the private completion capability, its binding and its single use."""
from __future__ import annotations

import copy
import hashlib
import pickle

import pytest

import executor_birth_dominant_startup as dominant


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _bindings(**overrides: str) -> dominant.DominantStartupBindingsV1:
    fields = {
        "request_id": _digest("1"),
        "previous_head_digest": _digest("2"),
        "context_transition_id": _digest("7"),
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


def test_the_complete_receipt_uses_the_required_domain_and_order() -> None:
    values = (_digest("1"), _digest("2"), _digest("3"))
    independent = hashlib.sha256(
        dominant.DOMINANT_STARTUP_RECEIPT_DOMAIN_V1,
    )
    for value in values:
        encoded = value.encode("ascii")
        independent.update(len(encoded).to_bytes(8, "big"))
        independent.update(encoded)

    observed = dominant.dominant_startup_receipt_v1(*values)

    assert observed == "sha256:" + independent.hexdigest()
    assert observed != values[0]
    assert observed != dominant.dominant_startup_receipt_v1(
        values[0], values[2], values[1],
    )


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
    "context_transition_id", "effective_topology_hash",
    "enforcement_evidence_digest",
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


class _Observers:
    """Observers that can be made to drift on their SECOND reading."""

    def __init__(self, drift: str | None = None) -> None:
        self.drift = drift
        self.reads: dict[str, int] = {}
        self.order: list[str] = []
        self.crossed: list[str] = []

    def _value(self, name: str, first: str, second: str) -> str:
        self.order.append(name)
        count = self.reads.get(name, 0) + 1
        self.reads[name] = count
        if count == 1 or self.drift != name:
            return first
        return second

    def identity(self):
        value = self._value("identity", _digest("1"), _digest("e"))
        return (value, _digest("2"), _digest("7"))

    def topology(self) -> str:
        return self._value("topology", _digest("4"), _digest("a"))

    def catalog(self) -> str:
        return self._value("catalog", _digest("3"), _digest("b"))

    def retirement(self) -> str:
        return self._value("retirement", _digest("6"), _digest("c"))

    def enforcement(self) -> str:
        return self._value("enforcement", _digest("5"), _digest("d"))

    def cross(self, receipt: dominant.DominantStartupReceiptV1) -> None:
        self.crossed.append(receipt)


def _complete(observers: _Observers, **extra):
    return dominant._complete_dominant_startup_for_test_v1(
        sessions=_sessions(),
        observe_identity=observers.identity,
        observe_topology=observers.topology,
        observe_catalog=observers.catalog,
        plan_retirement=observers.retirement,
        observe_enforcement=observers.enforcement,
        cross=observers.cross,
        **extra,
    )


def test_the_crossing_reads_everything_twice_before_it_runs() -> None:
    """One reading says it was true when we looked; two say it is true now."""
    observers = _Observers()
    receipt = _complete(observers)

    assert observers.crossed == [receipt]
    assert dominant.is_dominant_startup_receipt_v1(receipt)
    assert receipt.retirement_plan_digest == _digest("6")
    assert receipt.enforcement_evidence_digest == _digest("5")
    # Every observer was consulted exactly twice: once to bind, once to agree.
    assert set(observers.reads.values()) == {2}
    assert observers.order == [
        "identity", "catalog", "enforcement", "retirement", "topology",
        "identity", "catalog", "enforcement", "retirement", "topology",
    ]


def test_the_product_crossing_rejects_portable_session_stand_ins() -> None:
    observers = _Observers()
    with pytest.raises(dominant.DominantStartupError) as denied:
        dominant.complete_dominant_startup_v1(
            sessions=_sessions(),
            observe_identity=observers.identity,
            observe_topology=observers.topology,
            observe_catalog=observers.catalog,
            plan_retirement=observers.retirement,
            observe_enforcement=observers.enforcement,
            cross=observers.cross,
        )
    assert denied.value.code == "dominant_startup_sessions_invalid"
    assert observers.crossed == []


@pytest.mark.parametrize(
    "drift", ["identity", "topology", "catalog", "retirement", "enforcement"],
)
def test_any_field_that_moves_between_the_two_readings_stops_it(
    drift: str,
) -> None:
    """The ground moving under the crossing is the failure this prevents."""
    observers = _Observers(drift=drift)
    with pytest.raises(dominant.DominantStartupError) as drifted:
        _complete(observers)
    assert drifted.value.code == "dominant_startup_binding_drift"
    assert observers.crossed == []


def test_an_interruption_before_the_consumption_crosses_nothing() -> None:
    """A capability minted and not consumed authorises no crossing."""
    observers = _Observers()

    class _Interrupted(Exception):
        pass

    def seam(stage: str) -> None:
        if stage == "capability_minted":
            raise _Interrupted

    with pytest.raises(_Interrupted):
        _complete(observers, _crash_seam=seam)
    assert observers.crossed == []


def test_an_observer_that_is_a_value_is_refused() -> None:
    """The caller supplies ways to observe, never observations.

    A digest passed in place of an observer would let the decision be made
    outside the locks and merely reported here.
    """
    observers = _Observers()
    with pytest.raises(dominant.DominantStartupError) as denied:
        dominant._complete_dominant_startup_for_test_v1(
            sessions=_sessions(),
            observe_identity=observers.identity,
            observe_topology=_digest("4"),
            observe_catalog=observers.catalog,
            plan_retirement=observers.retirement,
            observe_enforcement=observers.enforcement,
            cross=observers.cross,
        )
    assert denied.value.code == "dominant_startup_observer_invalid"


def test_a_caller_cannot_construct_or_relabel_a_crossing_receipt() -> None:
    values = tuple(_digest(character) for character in "123")
    expected = dominant.dominant_startup_receipt_v1(*values)
    bindings = dominant.DominantStartupBindingsV1(
        request_id=_digest("4"),
        previous_head_digest=_digest("5"),
        context_transition_id=_digest("6"),
        catalog_id=_digest("7"),
        effective_topology_hash=_digest("8"),
        enforcement_evidence_digest=values[2],
    )

    with pytest.raises(dominant.DominantStartupError) as unsealed:
        dominant.DominantStartupReceiptV1(
            bindings, *values, expected, object(),
        )
    assert unsealed.value.code == "dominant_startup_receipt_invalid"

    receipt = _complete(_Observers())
    assert not dominant.is_dominant_startup_receipt_v1(
        object.__new__(dominant.DominantStartupReceiptV1)
    )

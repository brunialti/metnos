"""CallbackRegistry behavior."""
from __future__ import annotations

import pytest

from scheduler_v2.callbacks import CallbackRegistry


def test_register_and_get():
    r = CallbackRegistry()
    r.register("foo", lambda p: 42, "demo")
    info = r.get("foo")
    assert info is not None
    assert info.key == "foo"
    assert info.description == "demo"
    assert info.is_async is False
    assert info.fn(None) == 42


def test_register_async_detected():
    r = CallbackRegistry()
    async def f(p):
        return 1
    r.register("a", f)
    info = r.get("a")
    assert info.is_async is True


def test_duplicate_key_raises():
    r = CallbackRegistry()
    r.register("k", lambda p: None)
    with pytest.raises(KeyError):
        r.register("k", lambda p: None)


def test_replace_true_overwrites():
    r = CallbackRegistry()
    r.register("k", lambda p: 1)
    r.register("k", lambda p: 2, replace=True)
    assert r.get("k").fn(None) == 2


def test_unregister():
    r = CallbackRegistry()
    r.register("k", lambda p: None)
    assert r.unregister("k") is True
    assert r.get("k") is None
    assert r.unregister("k") is False


def test_list_callbacks():
    r = CallbackRegistry()
    r.register("a", lambda p: None, "alpha")
    r.register("b", lambda p: None, "beta")
    keys = sorted(c.key for c in r.list())
    assert keys == ["a", "b"]


def test_register_rejects_non_callable():
    r = CallbackRegistry()
    with pytest.raises(TypeError):
        r.register("bad", "not callable")  # type: ignore[arg-type]


def test_register_rejects_empty_key():
    r = CallbackRegistry()
    with pytest.raises(ValueError):
        r.register("", lambda p: None)


def test_contains():
    r = CallbackRegistry()
    r.register("k", lambda p: None)
    assert "k" in r
    assert "nope" not in r

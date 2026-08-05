"""The in-process progress channel is scoped and failure-isolated."""
from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class _Progress:
    def __init__(self):
        self.labels = []

    def update_free(self, label):
        self.labels.append(label)


def test_progress_binding_delivers_and_resets():
    from executor_progress import bind, update

    progress = _Progress()
    assert update("outside") is False
    with bind(progress):
        assert update("batch 1/3") is True
    assert update("outside again") is False
    assert progress.labels == ["batch 1/3"]


def test_nested_binding_restores_outer_context():
    from executor_progress import bind, update

    outer, inner = _Progress(), _Progress()
    with bind(outer):
        update("outer before")
        with bind(inner):
            update("inner")
        update("outer after")
    assert outer.labels == ["outer before", "outer after"]
    assert inner.labels == ["inner"]


def test_progress_callback_failure_cannot_fail_executor():
    from executor_progress import bind, update

    class Broken:
        def update_free(self, _label):
            raise RuntimeError("transport closed")

    with bind(Broken()):
        assert update("safe") is False

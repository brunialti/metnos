"""Adversarial regressions for LRE file and persisted-argument boundaries."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest


def _read_fifo(kind, path, result):
    try:
        if kind == "inventory":
            from durable_workloads.inventory import _stable_file_digest

            _stable_file_digest(
                Path(path), chunk_bytes=4096, max_bytes=4096,
                before_final_stat=None,
            )
        else:
            from durable_workloads.artifacts import ArtifactStore

            directory = os.open(str(Path(path).parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                descriptor = ArtifactStore._open_regular(directory, Path(path).name)
                os.close(descriptor)
            finally:
                os.close(directory)
    except Exception as exc:
        result.send(type(exc).__name__)
    else:
        result.send("unexpected_success")
    finally:
        result.close()


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or "fork" not in multiprocessing.get_all_start_methods(),
    reason="POSIX FIFO boundary",
)
@pytest.mark.parametrize("kind,error", [
    ("inventory", "InventorySealError"),
    ("artifact", "ArtifactSecurityError"),
])
def test_nonregular_file_is_rejected_without_waiting_for_a_fifo_writer(
    tmp_path, kind, error,
):
    path = tmp_path / "substituted-file"
    os.mkfifo(path, 0o600)
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_read_fifo, args=(kind, str(path), sender))
    process.start()
    sender.close()
    try:
        process.join(timeout=3)
        assert not process.is_alive(), "file validation blocked before checking its type"
        assert process.exitcode == 0
        assert receiver.poll(timeout=1)
        assert receiver.recv() == error
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        receiver.close()

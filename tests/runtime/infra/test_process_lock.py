from __future__ import annotations

import pytest

from process_lock import ProcessLock


def test_process_lock_is_exclusive_and_reusable(tmp_path):
    path = tmp_path / "daemon.lock"
    first = ProcessLock(path, "first")
    second = ProcessLock(path, "second")
    first.acquire()
    with pytest.raises(RuntimeError, match="second gia' in esecuzione"):
        second.acquire()
    first.release()
    second.acquire()
    second.release()

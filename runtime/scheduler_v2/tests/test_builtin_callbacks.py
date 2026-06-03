"""install_default_callbacks: every key registered, payload-shape compatible."""
from __future__ import annotations

import inspect

from scheduler_v2.builtin_callbacks import (
    _BUILTIN_JOBS,
    install_default_callbacks,
)
from scheduler_v2.daemon import SchedulerDaemon


# Derivato dalla FONTE DI VERITA' (§7.3 universale): ogni callback_key
# referenziata da _BUILTIN_JOBS DEVE essere registrata, piu' `run_user_query`
# (callback dei task utente, non un builtin job). Cosi' il test non va stale
# quando i builtin vengono consolidati/aggiunti (es. ADR 0167: nightly_aging,
# state_reaper, ...).
_EXPECTED_KEYS = {j["callback_key"] for j in _BUILTIN_JOBS} | {"run_user_query"}


def test_install_registers_all_keys(db_path):
    d = SchedulerDaemon(db_path)
    install_default_callbacks(d)
    keys = {info.key for info in d.callbacks.list()}
    assert _EXPECTED_KEYS.issubset(keys), (
        f"missing: {_EXPECTED_KEYS - keys}"
    )


def test_install_idempotent(db_path):
    d = SchedulerDaemon(db_path)
    install_default_callbacks(d)
    # Second call must not raise (replace=True path).
    install_default_callbacks(d)
    keys = {info.key for info in d.callbacks.list()}
    assert _EXPECTED_KEYS.issubset(keys)


def test_callbacks_accept_payload_dict(db_path):
    """Every wrapped callback must be invocable with a dict payload arg."""
    d = SchedulerDaemon(db_path)
    install_default_callbacks(d)
    for key in _EXPECTED_KEYS:
        info = d.callbacks.get(key)
        assert info is not None, key
        # Inspect arity; must accept exactly one positional or take **kwargs.
        sig = inspect.signature(info.fn)
        params = list(sig.parameters.values())
        # All wrappers we install accept (payload) — at least one parameter.
        assert len(params) >= 1, f"{key}: wrapper takes no args"


def test_builtin_jobs_table_matches_callbacks():
    """Every builtin job's callback_key has a registered callback."""
    d = SchedulerDaemon(_in_memory_path())
    install_default_callbacks(d)
    for job in _BUILTIN_JOBS:
        assert d.callbacks.get(job["callback_key"]) is not None, job["name"]


def _in_memory_path(tmp_factory=None):
    # Cheap helper: SQLite needs a file, but tmp_path is per-test; use a
    # tmpfs path keyed on test name to avoid a fixture parameter here.
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp(prefix="schedv2_")) / "x.sqlite"

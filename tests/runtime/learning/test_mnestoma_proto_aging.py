"""Proto-mnest inactivity must make the purge threshold reachable."""
from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from mnestoma import Mnestoma


def test_ager_decays_and_purges_old_proto(tmp_path):
    store = Mnestoma(tmp_path / "mnest.sqlite")
    proto_id = store.record_passing(
        "read_source", "1", "missing_executor", dst_exists=False,
        decay_lambda=1.0,
    )
    store.conn.execute(
        "UPDATE mnests SET ts_first=?, ts_last=? WHERE id=?",
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", proto_id),
    )

    result = store.apply_ager(now_iso="2026-01-10T00:00:00Z")

    assert result["decayed_protos"] == 1
    assert result["purged_protos"] == 1
    assert store.get(proto_id) is None

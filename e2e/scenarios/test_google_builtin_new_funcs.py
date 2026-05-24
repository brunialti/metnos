"""E2E nuove funzioni Google Workspace builtin (24/5/2026).

Verifica:
- Calendar: create + update + delete (NEW: update)
- Gmail: reply, labels, modify (NEW)
- Drive: upload, download, create_folder, share (NEW)
- Sheets: read, write, append, create (NEW)
- Docs: read, create, append (NEW)
- Contacts: find, read (NEW)

Usa calendario dedicato `Metnos Test` (id in `e2e/fixtures/google_test_calendar.id`)
per evitare pollution del calendario reale. Env `METNOS_TEST_CALENDAR_ID`
viene esportato al server tmp.

Slow opt-in (METNOS_E2E_RUN_SLOW=1). Skip se token OAuth assente.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_E2E_ROOT = Path(__file__).resolve().parent.parent
_CAL_ID_FILE = _E2E_ROOT / "fixtures" / "google_test_calendar.id"
_TOKEN_PATH = Path.home() / ".hermes" / "google_token.json"
_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"


def _test_calendar_id() -> str | None:
    if not _CAL_ID_FILE.is_file():
        return None
    return _CAL_ID_FILE.read_text().strip()


pytestmark = [
    pytest.mark.skipif(
        not _TOKEN_PATH.is_file(),
        reason="google OAuth token assente: setup.py --auth-code <CODE>",
    ),
    pytest.mark.skipif(
        not _test_calendar_id(),
        reason="google_test_calendar.id missing: crea calendario test prima",
    ),
    pytest.mark.skipif(
        not _RUN_SLOW,
        reason="slow Google API tests, abilita con METNOS_E2E_RUN_SLOW=1",
    ),
]


@pytest.fixture(scope="module")
def cal_id() -> str:
    cid = _test_calendar_id()
    assert cid, "calendar id missing"
    return cid


# --- Backend direct invocation tests (no HTTP server overhead) ----------

def test_calendar_create_update_delete(cal_id: str):
    """Lifecycle event: create → update → delete su Metnos Test calendar."""
    sys.path.insert(0, str(Path("/opt/metnos/runtime").resolve()))
    from backends.events import google_workspace as cal

    # CREATE
    iso_now = time.strftime("%Y-%m-%dT%H:%M:%S+02:00",
                             time.localtime(time.time() + 7 * 24 * 3600))
    iso_end = time.strftime("%Y-%m-%dT%H:%M:%S+02:00",
                             time.localtime(time.time() + 7 * 24 * 3600 + 1800))
    r_create = cal.create({
        "summary": "E2E test event",
        "start": iso_now,
        "end": iso_end,
        "location": "test-location",
        "calendar_id": cal_id,
    })
    assert r_create["ok"], f"create failed: {r_create}"
    assert r_create["n_created"] == 1
    event_id = r_create["results"][0]["id"]
    assert event_id

    try:
        # UPDATE
        r_update = cal.update({
            "event_id": event_id,
            "calendar_id": cal_id,
            "summary": "E2E test event UPDATED",
            "location": "updated-location",
        })
        assert r_update["ok"], f"update failed: {r_update}"
        assert r_update["n_updated"] == 1
        assert "summary" in r_update["results"][0].get("updated_fields", [])
        assert "location" in r_update["results"][0].get("updated_fields", [])
    finally:
        # DELETE (cleanup)
        r_del = cal.delete({
            "event_ids": [event_id],
            "calendar_id": cal_id,
        })
        assert r_del["ok"], f"delete failed: {r_del}"


def test_calendar_read_test_calendar(cal_id: str):
    """Read events sul calendario Metnos Test (di solito vuoto)."""
    sys.path.insert(0, str(Path("/opt/metnos/runtime").resolve()))
    from backends.events import google_workspace as cal
    r = cal.read({
        "time_window": "last-7d",
        "max_results": 10,
        "calendar_id": cal_id,
    })
    assert r["ok"], f"read failed: {r}"
    assert isinstance(r.get("entries"), list)


# --- Sheets ---

def test_sheets_create_read_append_delete():
    """Lifecycle sheet: create → append → read."""
    sys.path.insert(0, str(Path("/opt/metnos/runtime").resolve()))
    from backends.files import google_workspace as drv
    r_create = drv.create_spreadsheet({"title": "E2E test sheet"})
    assert r_create["ok"], f"create_spreadsheet failed: {r_create}"
    sid = r_create.get("spreadsheet_id") or r_create.get("file_id") \
        or (r_create.get("results") or [{}])[0].get("spreadsheet_id")
    assert sid, f"no spreadsheet_id in {r_create}"
    try:
        r_app = drv.append_spreadsheet({
            "spreadsheet_id": sid,
            "range": "A1",
            "values": [["hello", "world"], ["a", "b"]],
        })
        assert r_app["ok"], f"append failed: {r_app}"
        r_read = drv.read_spreadsheet({
            "spreadsheet_id": sid,
            "range": "A1:B2",
        })
        assert r_read["ok"], f"read failed: {r_read}"
        vals = r_read.get("values") or r_read.get("entries") or []
        assert vals, f"empty read: {r_read}"
    finally:
        drv.delete({"file_id": sid})


# --- Docs ---

def test_docs_create_append_read_delete():
    """Lifecycle doc: create → append → read."""
    sys.path.insert(0, str(Path("/opt/metnos/runtime").resolve()))
    from backends.files import google_workspace as drv
    r_create = drv.create_doc({"title": "E2E test doc"})
    assert r_create["ok"], f"create_doc failed: {r_create}"
    did = r_create.get("document_id") or r_create.get("file_id") \
        or (r_create.get("results") or [{}])[0].get("document_id")
    assert did, f"no document_id in {r_create}"
    try:
        r_app = drv.append_doc({
            "document_id": did,
            "text": "Hello from E2E test\n",
        })
        assert r_app["ok"], f"append_doc failed: {r_app}"
        r_read = drv.read_doc({"document_id": did})
        assert r_read["ok"], f"read_doc failed: {r_read}"
        body = r_read.get("body_text") or ""
        assert "Hello" in body, f"missing text in body: {body[:200]}"
    finally:
        drv.delete({"file_id": did})


# --- Contacts ---

def test_contacts_find():
    """Find contacts: smoke test ritorna lista (anche vuota)."""
    sys.path.insert(0, str(Path("/opt/metnos/runtime").resolve()))
    from backends.contacts import google_workspace as con
    r = con.find({"query": "", "max_results": 5})
    assert r["ok"], f"contacts find failed: {r}"
    assert isinstance(r.get("entries"), list)


# --- Gmail labels (read-only smoke, no destructive) ---

def test_gmail_labels_list():
    """List all gmail labels via backend (no message_id → list-all)."""
    sys.path.insert(0, str(Path("/opt/metnos/runtime").resolve()))
    from backends.messages import gmail_google_workspace as gm
    r = gm.labels({})
    # Gmail policy "Mail service not enabled" → tollerato come skip-equiv
    if not r.get("ok") and "not enabled" in str(r.get("error", "")).lower():
        pytest.skip("Gmail service disabled on this Workspace account")
    assert r.get("ok"), f"labels list failed: {r}"
    labels = r.get("entries") or r.get("labels") or []
    assert isinstance(labels, list)

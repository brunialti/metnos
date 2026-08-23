from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
GOOGLE_API = (
    ROOT / "executors" / "skills" / "google-workspace" / "scripts"
    / "google_api.py"
)


def _load_google_api():
    spec = importlib.util.spec_from_file_location(
        "metnos_test_google_api_permissions", GOOGLE_API)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_drive_share_requests_permission_receipt_with_gws(
        monkeypatch, capsys) -> None:
    google_api = _load_google_api()
    calls = []
    monkeypatch.setattr(google_api, "_gws_binary", lambda: "gws")

    def fake_gws(parts, *, params=None, body=None):
        calls.append((parts, params, body))
        return {"id": "permission-001"}

    monkeypatch.setattr(google_api, "_run_gws", fake_gws)
    google_api.drive_share(SimpleNamespace(
        file_id="drive-file-000001", type="user", role="reader",
        email="guest@example.com", domain="", notify=False,
    ))

    output = json.loads(capsys.readouterr().out)
    assert output["permissionId"] == "permission-001"
    assert calls == [(
        ["drive", "permissions", "create"],
        {
            "fileId": "drive-file-000001",
            "sendNotificationEmail": False,
            "fields": "id",
        },
        {"type": "user", "role": "reader",
         "emailAddress": "guest@example.com"},
    )]


def test_drive_unshare_deletes_exact_permission_with_gws(
        monkeypatch, capsys) -> None:
    google_api = _load_google_api()
    calls = []
    monkeypatch.setattr(google_api, "_gws_binary", lambda: "gws")

    def fake_gws(parts, *, params=None, body=None):
        calls.append((parts, params, body))
        return {}

    monkeypatch.setattr(google_api, "_run_gws", fake_gws)
    google_api.drive_unshare(SimpleNamespace(
        file_id="drive-file-000001", permission_id="permission-001"))

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "status": "revoked", "fileId": "drive-file-000001",
        "permissionId": "permission-001",
    }
    assert calls == [(
        ["drive", "permissions", "delete"],
        {
            "fileId": "drive-file-000001",
            "permissionId": "permission-001",
        },
        None,
    )]


def test_drive_unshare_is_registered_in_cli(monkeypatch) -> None:
    google_api = _load_google_api()
    called = []
    monkeypatch.setattr(
        google_api, "drive_unshare",
        lambda args: called.append((args.file_id, args.permission_id)))
    monkeypatch.setattr(
        "sys.argv",
        ["google_api.py", "drive", "unshare", "drive-file-000001",
         "permission-001"],
    )

    google_api.main()

    assert called == [("drive-file-000001", "permission-001")]

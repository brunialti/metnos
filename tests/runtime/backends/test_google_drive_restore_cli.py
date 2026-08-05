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
        "metnos_test_google_api_restore", GOOGLE_API)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_drive_restore_sets_trashed_false_with_gws(monkeypatch, capsys) -> None:
    google_api = _load_google_api()
    calls = []
    monkeypatch.setattr(google_api, "_gws_binary", lambda: "gws")

    def fake_gws(parts, *, params=None, body=None):
        calls.append((parts, params, body))
        return {"id": params["fileId"], "trashed": False}

    monkeypatch.setattr(google_api, "_run_gws", fake_gws)

    google_api.drive_restore(SimpleNamespace(file_id="drive-id-000001"))

    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "restored", "fileId": "drive-id-000001"}
    assert calls == [(
        ["drive", "files", "update"],
        {"fileId": "drive-id-000001"},
        {"trashed": False},
    )]


def test_drive_restore_is_registered_in_cli(monkeypatch) -> None:
    google_api = _load_google_api()
    called = []
    monkeypatch.setattr(google_api, "drive_restore",
                        lambda args: called.append(args.file_id))
    monkeypatch.setattr(
        "sys.argv", ["google_api.py", "drive", "restore", "drive-id-000001"])

    google_api.main()

    assert called == ["drive-id-000001"]

from __future__ import annotations

import tomllib
from pathlib import Path

from presentation_contract import normalize_presentation


def test_missing_contract_is_legacy_compatible():
    assert normalize_presentation({}) == {}


def test_contract_is_bounded_and_normalized():
    out = normalize_presentation({
        "presentation": {
            "default_view": "list",
            "list": {
                "mode": "table",
                "columns": [
                    {"key": "date", "source": "date", "cell_max": 24},
                ],
                "max_rows": 200,
                "max_chars": 16000,
                "overflow": "notice",
            }
        }
    })
    assert out["list"]["columns"][0]["key"] == "date"
    assert out["list"]["max_rows"] == 200


def test_contract_accepts_entry_value_as_last_resort_source():
    out = normalize_presentation({
        "presentation": {
            "default_view": "list",
            "list": {"columns": [{"key": "item", "source": ["name", "$entry"]}]},
        }
    })
    assert out["list"]["columns"][0]["source"] == ["name", "$entry"]


def test_contract_normalizes_boolean_nowrap_only():
    manifest = {
        "presentation": {
            "default_view": "list",
            "list": {"columns": [{"key": "account", "source": "account", "nowrap": True}]},
        }
    }
    assert normalize_presentation(manifest)["list"]["columns"][0]["nowrap"] is True
    manifest["presentation"]["list"]["columns"][0]["nowrap"] = "yes"
    assert normalize_presentation(manifest) == {}


def test_malformed_contract_falls_back_without_rejecting_executor():
    assert normalize_presentation({
        "presentation": {"list": {"mode": "unknown", "columns": []}}
    }) == {}


def test_contract_rejects_unknown_column_type():
    assert normalize_presentation({
        "presentation": {
            "default_view": "list",
            "list": {"columns": [{"key": "when", "source": "when", "type": "calendarish"}]},
        }
    }) == {}


def test_every_shipped_entries_producer_declares_a_valid_list_contract():
    root = Path(__file__).resolve().parents[3] / "executors"
    missing: list[str] = []
    invalid: list[str] = []
    for manifest_path in sorted(root.glob("*/manifest.toml")):
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        schema = (manifest.get("output") or {}).get("schema_inline", "")
        if not isinstance(schema, str) or "entries:" not in schema:
            continue
        if not manifest.get("presentation"):
            missing.append(manifest_path.parent.name)
        elif not normalize_presentation(manifest):
            invalid.append(manifest_path.parent.name)
    assert missing == []
    assert invalid == []

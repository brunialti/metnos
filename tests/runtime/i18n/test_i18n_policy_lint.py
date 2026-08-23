from __future__ import annotations

from pathlib import Path

from i18n_policy_lint import scan, scan_runtime


def test_detects_user_language_preference_and_request_context(tmp_path: Path):
    source = tmp_path / "bad.py"
    source.write_text(
        "users.get_pref(owner, 'lang', None)\n"
        "with i18n.language_context(payload.get('lang')):\n"
        "    pass\n",
        encoding="utf-8",
    )
    assert {issue.code for issue in scan([source])} == {
        "I18N_USER_PREFERENCE", "I18N_PER_REQUEST_CONTEXT",
    }


def test_instance_context_is_allowed(tmp_path: Path):
    source = tmp_path / "good.py"
    source.write_text(
        "with i18n.instance_language_context():\n    pass\n",
        encoding="utf-8",
    )
    assert scan([source]) == []


def test_operational_runtime_has_no_language_override_path():
    root = Path(__file__).resolve().parents[3] / "runtime"
    assert scan_runtime(root) == []

"""Task-name routing follows the scheduler builtin source of truth."""
from __future__ import annotations

from recurring_tasks import _normalize_task_name
from scheduler_v2.builtin_callbacks import builtin_job_names


def test_every_builtin_keeps_its_canonical_name() -> None:
    names = builtin_job_names()
    assert names
    for name in names:
        assert _normalize_task_name(name) == name


def test_user_task_receives_prefix_once() -> None:
    assert _normalize_task_name("weekly_report") == "weekly_report"
    assert _normalize_task_name("user_weekly_report") == "user_weekly_report"


def test_retired_builtin_is_not_special_cased() -> None:
    assert _normalize_task_name("apply_ager") == "apply_ager"

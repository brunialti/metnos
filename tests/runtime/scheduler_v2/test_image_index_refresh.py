"""Scheduled photo maintenance reuses ordinary LRE admission, never inline work."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def refresh(tmp_path, monkeypatch):
    import config
    import loader
    import lre_submission
    import users
    import vaglio
    from scheduler_v2.builtin_callbacks import task_images_index_refresh

    data = tmp_path / "configured-data"
    data.mkdir()
    photos = data / "Immagini"
    photos.mkdir()
    monkeypatch.setattr(config, "PATH_USER_DATA", data)
    executor = SimpleNamespace(name="create_images_indices", lre_plan="images.index.v1")
    state = {"owners": [{"id": "fixture-owner"}], "catalog": [executor],
             "guard": True, "result": {"ok": True, "workload_id": "fixture-work"}}
    calls = []
    monkeypatch.setattr(users, "list_users", lambda *, role: state["owners"] if role == "host" else [])

    def catalog(*, verify):
        assert verify is True
        calls.append("verified_catalog")
        return state["catalog"]

    def guard(name, args, *, executor):
        assert executor is state["catalog"][0]
        calls.append("guard")
        return state["guard"], None

    def submit(framework, **kwargs):
        calls.append((framework, kwargs))
        return state["result"]

    monkeypatch.setattr(loader, "load_catalog", catalog)
    monkeypatch.setattr(vaglio, "guard_check", guard)
    monkeypatch.setattr(lre_submission, "submit_automatic_lre", submit)
    return task_images_index_refresh, state, calls, photos


def test_refresh_submits_configured_archive_under_exact_owner(refresh):
    callback, state, calls, photos = refresh
    assert callback() is state["result"]
    assert calls[:2] == ["verified_catalog", "guard"]
    framework, kwargs = calls[2]
    assert len(framework.steps) == 1
    assert framework.steps[0].tool == "create_images_indices"
    assert framework.steps[0].args == {"base_path": str(photos), "force": False, "recursive": True}
    assert kwargs["catalog"] is state["catalog"]
    assert kwargs["owner_user_id"] == "fixture-owner"
    assert len(kwargs["turn_id"]) == 32


def test_missing_archive_does_not_submit(refresh):
    callback, _, calls, photos = refresh
    photos.rmdir()
    assert callback()["skipped"] is True
    assert not calls


@pytest.mark.parametrize("owners", [[], [{"id": ""}], [{"id": "a"}, {"id": "b"}]])
def test_missing_or_ambiguous_owner_never_falls_back_to_sentinel(refresh, owners):
    callback, state, calls, _ = refresh
    state["owners"] = owners
    assert callback() == {"ok": False, "error_class": "owner_unavailable"}
    assert not calls


@pytest.mark.parametrize("catalog", [[], [SimpleNamespace(name="create_images_indices", lre_plan="")]])
def test_no_legacy_inline_fallback(refresh, catalog):
    callback, state, calls, _ = refresh
    state["catalog"] = catalog
    assert callback()["error_class"] == "contract_unavailable"
    assert calls == ["verified_catalog"]


def test_guard_denial_prevents_submission(refresh):
    callback, state, calls, _ = refresh
    state["guard"] = False
    assert callback()["error_class"] == "guard_denied"
    assert calls == ["verified_catalog", "guard"]


@pytest.mark.parametrize("result", [None, {"ok": False, "error_class": "worker_unavailable"}])
def test_missing_receipt_or_worker_never_means_success(refresh, result):
    callback, state, _, _ = refresh
    state["result"] = result
    assert callback()["ok"] is False

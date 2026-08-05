from __future__ import annotations

import json
import stat
import sys
from types import SimpleNamespace

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


def _family(payload: dict, family: str) -> dict:
    return next(item for item in payload["families"] if item["key"] == family)


def _editable_form(family_view: dict) -> dict[str, str]:
    form: dict[str, str] = {}
    for role in family_view["roles"]:
        for field in role["fields"]:
            if field.get("editable"):
                form[f"field_{field['edit_key']}"] = str(field["edit_value"])
    return form


def _field(family_view: dict, role_name: str, field_name: str) -> dict:
    role = next(role for role in family_view["roles"]
                if role["name"] == role_name)
    return next(field for field in role["fields"]
                if field["name"] == field_name)


def test_llm_save_is_allowlisted_atomic_and_preserves_secrets(
        tmp_path, monkeypatch):
    path = tmp_path / "llm_tiers.toml"
    secret = "sk-secret-material-that-must-survive"
    original = (
        "[fast]\n"
        "provider = \"llamacpp\"\n"
        "model = \"local\"\n"
        "endpoint = \"http://127.0.0.1:8080\"\n"
        "think = false\n"
        "temperature = 0.0\n"
        "reasoning_budget = 0\n\n"
        "[wise]\n"
        "provider = \"anthropic\"\n"
        "model = \"example-wise\"\n"
        f"api_key = \"{secret}\"\n"
    )
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(path))

    from virt.configuration import snapshot
    from virt.config_editor import save

    view = _family(snapshot(edit_family="llm"), "llm")
    serialized = json.dumps(view, ensure_ascii=False)
    assert secret not in serialized
    secret_field = _field(view, "wise", "api_key")
    assert secret_field["editable"] is False
    assert "edit_value" not in secret_field

    form = _editable_form(view)
    budget = _field(view, "fast.fidelity", "reasoning_budget")
    form[f"field_{budget['edit_key']}"] = "192"
    before = view["config"]["revision"]
    result = save("llm", form, expected_revision=before)

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["fast"]["level"]["fidelity"]["reasoning_budget"] == 192
    assert parsed["wise"]["api_key"] == secret
    assert result.revision_before == before
    assert result.revision_after != before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    backup = path.__class__(result.backup_path)
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_save_rejects_stale_revision_without_overwriting(
        tmp_path, monkeypatch):
    path = tmp_path / "embedding_tiers.toml"
    path.write_text(
        "[text]\nprovider = \"bge\"\n\n"
        "[image]\nprovider = \"siglip\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_EMBEDDING_TIERS_CONFIG", str(path))

    from virt.configuration import snapshot
    from virt.config_editor import ConfigEditError, save

    view = _family(snapshot(edit_family="embedding"), "embedding")
    form = _editable_form(view)
    old_revision = view["config"]["revision"]
    external = path.read_text(encoding="utf-8") + "\n# external change\n"
    path.write_text(external, encoding="utf-8")

    with pytest.raises(ConfigEditError, match="revision_conflict"):
        save("embedding", form, expected_revision=old_revision)
    assert path.read_text(encoding="utf-8") == external


def test_missing_toml_writer_fails_closed_without_hiding_models_page(
        tmp_path, monkeypatch):
    """A damaged runtime cannot make the read-only models overview a 500."""
    path = tmp_path / "embedding_tiers.toml"
    original = "[text]\nprovider = \"bge\"\n"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("METNOS_EMBEDDING_TIERS_CONFIG", str(path))

    from virt.configuration import snapshot
    import virt.config_editor as editor

    view = _family(snapshot(edit_family="embedding"), "embedding")
    form = _editable_form(view)
    monkeypatch.setattr(editor, "tomli_w", None)

    with pytest.raises(editor.ConfigEditError, match="toml_unavailable") as raised:
        editor.save("embedding", form,
                    expected_revision=view["config"]["revision"])
    assert raised.value.code == "toml_unavailable"
    assert path.read_text(encoding="utf-8") == original


def test_vlm_edits_the_runtime_base_url_and_reset_restores_factory_values(
        tmp_path, monkeypatch):
    path = tmp_path / "vlm_tiers.toml"
    original = (
        "[default]\n"
        "provider = \"llamacpp\"\n"
        "model = \"vision-custom\"\n"
        "base_url = \"http://127.0.0.1:8181\"\n"
        "timeout_s = 12\n"
        "max_edge = 768\n"
        "max_tokens = 300\n"
    )
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("METNOS_VLM_TIERS_CONFIG", str(path))

    from virt import DEFAULT_VLM, get_vlm
    from virt.configuration import snapshot
    from virt.config_editor import reset, save

    view = _family(snapshot(edit_family="vlm"), "vlm")
    base_url = _field(view, "default", "base_url")
    form = _editable_form(view)
    form[f"field_{base_url['edit_key']}"] = "http://127.0.0.1:8282"
    saved = save(
        "vlm", form, expected_revision=view["config"]["revision"])
    assert get_vlm()["base_url"] == "http://127.0.0.1:8282"

    restored = reset("vlm", expected_revision=saved.revision_after)
    assert restored.reset is True
    assert tomllib.loads(path.read_text(encoding="utf-8")) == DEFAULT_VLM
    runtime_spec = get_vlm()
    assert runtime_spec["base_url"] == DEFAULT_VLM["default"]["base_url"]
    assert runtime_spec["endpoint"] == DEFAULT_VLM["default"]["base_url"]


def test_vlm_invalidation_refreshes_the_request_client(monkeypatch):
    import virt
    import virt.config_editor as editor

    calls: list[bool] = []
    monkeypatch.setitem(
        sys.modules, "vlm_client",
        SimpleNamespace(reload_configuration=lambda: calls.append(True)),
    )
    virt._vlm_started["default"] = True

    editor._invalidate_runtime("vlm")

    assert calls == [True]
    assert not virt._vlm_started


def test_invalid_endpoint_leaves_the_previous_file_untouched(
        tmp_path, monkeypatch):
    path = tmp_path / "vlm_tiers.toml"
    original = (
        "[default]\nprovider = \"llamacpp\"\n"
        "model = \"vision\"\n"
        "base_url = \"http://127.0.0.1:8081\"\n"
        "timeout_s = 60\nmax_edge = 1024\nmax_tokens = 512\n"
    )
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("METNOS_VLM_TIERS_CONFIG", str(path))

    from virt.configuration import snapshot
    from virt.config_editor import ConfigEditError, save

    view = _family(snapshot(edit_family="vlm"), "vlm")
    form = _editable_form(view)
    base_url = _field(view, "default", "base_url")
    form[f"field_{base_url['edit_key']}"] = "file:///tmp/not-an-endpoint"

    with pytest.raises(ConfigEditError) as raised:
        save("vlm", form, expected_revision=view["config"]["revision"])
    assert raised.value.code == "invalid_url"
    assert path.read_text(encoding="utf-8") == original


def test_vlm_synchronous_work_limits_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "vlm_tiers.toml"
    path.write_text(
        "[default]\nprovider = \"llamacpp\"\n"
        "base_url = \"http://127.0.0.1:8081\"\n"
        "max_images_per_request = 8\nrequest_budget_s = 45\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_VLM_TIERS_CONFIG", str(path))

    from virt.configuration import snapshot
    from virt.config_editor import ConfigEditError, save

    view = _family(snapshot(edit_family="vlm"), "vlm")
    form = _editable_form(view)
    limit = _field(view, "default", "max_images_per_request")
    form[f"field_{limit['edit_key']}"] = "1000"

    with pytest.raises(ConfigEditError) as raised:
        save("vlm", form, expected_revision=view["config"]["revision"])
    assert raised.value.code == "invalid_configuration"


@pytest.mark.asyncio
async def test_admin_mutation_routes_return_bounded_json_and_conflicts(
        tmp_path, monkeypatch):
    path = tmp_path / "embedding_tiers.toml"
    path.write_text(
        "[text]\nprovider = \"bge\"\n\n"
        "[image]\nprovider = \"siglip\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_EMBEDDING_TIERS_CONFIG", str(path))

    import http_routes_admin
    from virt.configuration import snapshot

    async def _inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(http_routes_admin.asyncio, "to_thread", _inline)
    view = _family(snapshot(edit_family="embedding"), "embedding")
    data = {
        "revision": view["config"]["revision"],
        **_editable_form(view),
    }

    class Request:
        match_info = {"family": "embedding"}
        headers = {"Accept": "application/json"}

        async def post(self):
            return data

    saved = await http_routes_admin.admin_virt_save(Request())
    assert saved.status == 200
    body = json.loads(saved.text)
    assert body["family"] == "embedding"
    assert set(body) == {
        "family", "revision_before", "revision_after", "backup_path",
        "reset",
    }

    stale = await http_routes_admin.admin_virt_save(Request())
    assert stale.status == 409
    assert json.loads(stale.text)["error"] == "revision_conflict"

    data = {"revision": body["revision_after"]}
    restored = await http_routes_admin.admin_virt_reset(Request())
    assert restored.status == 200
    assert json.loads(restored.text)["reset"] is True

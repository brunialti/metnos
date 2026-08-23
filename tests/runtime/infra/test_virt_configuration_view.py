from __future__ import annotations

import json
from pathlib import Path

import pytest


def _family(payload: dict, key: str) -> dict:
    return next(item for item in payload["families"] if item["key"] == key)


def _role(family: dict, name: str) -> dict:
    return next(item for item in family["roles"] if item["name"] == name)


def _values(role: dict) -> dict[str, dict]:
    return {field["name"]: field for field in role["fields"]}


def test_snapshot_resolves_all_families_and_redacts_secrets(tmp_path, monkeypatch):
    llm = tmp_path / "llm.toml"
    llm.write_text(
        """
[fast]
provider = "llamacpp"
endpoint = "http://127.0.0.1:8080"

[wise]
provider = "anthropic"
model = "example-wise"
api_key = "sk-this-must-never-appear"
""".strip(),
        encoding="utf-8",
    )
    embedding = tmp_path / "embedding.toml"
    embedding.write_text(
        """
[text]
provider = "http"
base_url = "https://alice:password@example.test/embed?future_secret=value"
api_key = "sk-another-secret-value"

[image]
provider = "siglip"
model_dir = "/models/siglip"
""".strip(),
        encoding="utf-8",
    )
    vlm = tmp_path / "vlm.toml"
    vlm.write_text(
        """
[default]
provider = "llamacpp"
model = "vision-example"
base_url = "http://127.0.0.1:8081"
max_tokens = 640
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(llm))
    monkeypatch.setenv("METNOS_EMBEDDING_TIERS_CONFIG", str(embedding))
    monkeypatch.setenv("METNOS_VLM_TIERS_CONFIG", str(vlm))

    from virt.configuration import snapshot

    payload = snapshot()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["read_only"] is True
    assert payload["secrets_redacted"] is True
    assert '"edit_value"' not in serialized
    assert '"pointer"' not in serialized
    assert "sk-this-must-never-appear" not in serialized
    assert "sk-another-secret-value" not in serialized
    assert "alice:password" not in serialized
    assert "future_secret=value" not in serialized

    llm_family = _family(payload, "llm")
    assert llm_family["config"]["status"] == "configured"
    assert llm_family["config"]["env_override"] is True
    assert [role["name"] for role in llm_family["roles"]] == [
        "fast.micro", "fast.procedural", "fast.fidelity",
        "middle", "wise", "creative", "frontier",
    ]
    assert _role(llm_family, "fast.micro")["origin"] == "configured"
    assert _role(llm_family, "fast.procedural")["origin"] == "configured"
    assert _role(llm_family, "fast.fidelity")["origin"] == "configured"
    assert _role(llm_family, "middle")["origin"] == "defaults"
    assert _role(llm_family, "creative")["origin"] == "alias"
    assert _role(llm_family, "frontier")["origin"] == "defaults"
    assert _values(_role(llm_family, "frontier"))["provider"]["value"] == "none"
    wise = _values(_role(llm_family, "wise"))
    assert wise["provider"]["value"] == "anthropic"
    assert wise["api_key"]["visibility"] == "redacted"

    text = _values(_role(_family(payload, "embedding"), "text"))
    assert text["base_url"]["value"] == "https://example.test/embed?…"
    assert text["base_url"]["visibility"] == "sanitized"
    assert text["api_key"]["visibility"] == "redacted"

    vision = _values(_role(_family(payload, "vlm"), "default"))
    assert vision["model"]["value"] == "vision-example"
    assert vision["max_tokens"]["value"] == "640"


def test_snapshot_reports_invalid_files_without_loading_models(
        tmp_path, monkeypatch):
    broken_llm = tmp_path / "broken-llm.toml"
    broken_llm.write_text("[fast\nprovider =", encoding="utf-8")
    broken_embedding = tmp_path / "broken-embedding.toml"
    broken_embedding.write_text("[text\nprovider =", encoding="utf-8")
    missing_vlm = tmp_path / "missing-vlm.toml"
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(broken_llm))
    monkeypatch.setenv("METNOS_EMBEDDING_TIERS_CONFIG", str(broken_embedding))
    monkeypatch.setenv("METNOS_VLM_TIERS_CONFIG", str(missing_vlm))

    from virt.configuration import snapshot

    payload = snapshot()
    llm = _family(payload, "llm")
    embedding = _family(payload, "embedding")
    vlm = _family(payload, "vlm")
    assert llm["config"]["status"] == "invalid"
    assert embedding["config"]["status"] == "invalid"
    assert llm["config"]["error"]
    assert embedding["config"]["error"]
    assert all(role["origin"] == "fallback" for role in llm["roles"])
    assert all(role["origin"] == "fallback" for role in embedding["roles"])
    assert vlm["config"]["status"] == "defaults"
    assert _role(vlm, "default")["origin"] == "defaults"


def test_semantic_tier_errors_cannot_echo_the_offending_mapping(
        tmp_path, monkeypatch):
    llm = tmp_path / "semantically-invalid.toml"
    llm.write_text(
        """
[wise]
provider = ""
api_key = "ordinary-looking-secret"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(llm))
    monkeypatch.setenv(
        "METNOS_EMBEDDING_TIERS_CONFIG", str(tmp_path / "missing-embedding"))
    monkeypatch.setenv("METNOS_VLM_TIERS_CONFIG", str(tmp_path / "missing-vlm"))

    from virt.configuration import snapshot

    payload = snapshot()
    serialized = json.dumps(payload, ensure_ascii=False)
    family = _family(payload, "llm")
    assert family["config"]["status"] == "invalid"
    assert family["config"]["error"] == ""
    assert family["config"]["error_key"] == "UI_VIRT_ERROR_LLM_PROVIDER"
    assert "ordinary-looking-secret" not in serialized


def test_admin_virt_surface_and_template_are_registered():
    from http_render import render_template
    from http_routes_admin import ROUTES, admin_virt
    from ui_surfaces import by_key
    from virt.configuration import snapshot

    assert ("GET", "/admin/virt", admin_virt) in ROUTES
    virt_mutation_routes = [
        path for method, path, _handler in ROUTES
        if method == "POST" and path.startswith("/admin/virt/")
    ]
    assert r"/admin/virt/{family:llm|vlm}/save" in virt_mutation_routes
    assert r"/admin/virt/{family:llm|vlm}/reset" in virt_mutation_routes
    assert not any("embedding" in path for path in virt_mutation_routes)
    surface = by_key("virt")
    assert surface.breadcrumb("it") == "Settings > Sistema > Modelli"
    assert surface.breadcrumb("en") == "Settings > System > Models"
    assert "embedding è solo consultabile" in surface.summary("it")
    assert "embedding is view-only" in surface.summary("en")
    assert any("embedding" in line and "consultabile" in line
               for line in surface.procedure("it"))
    assert any("parametri effettivi di generazione" in line
               for line in surface.visible("it"))
    assert any("override richiesto" in line and "non modifica" in line
               for line in surface.procedure("it"))
    assert any("embedding backend cannot be changed" in line
               for line in surface.stop_conditions("en"))
    families = {item["key"]: item for item in snapshot()["families"]}
    assert families["llm"]["tip_key"] == "UI_VIRT_LLM_TIP"
    assert families["embedding"]["tip_key"] == "UI_VIRT_EMBEDDING_READONLY_TIP"
    assert families["vlm"]["tip_key"] == "UI_VIRT_VLM_TIP"
    html = render_template("virt.html", snapshot=snapshot())
    assert "missing:UI_" not in html
    assert "/admin/virt" in html
    assert "Configurazione dei modelli" in html
    assert "/admin/virt?edit=embedding" not in html
    assert "/admin/virt/embedding/reset" not in html
    assert "/admin/virt?edit=llm" in html
    template = (
        (Path(__file__).resolve().parents[3] / "runtime/templates/virt.html")
        .read_text(encoding="utf-8"))
    assert "msg(family.tip_key)" in template
    assert "virt-info-tip" in template
    assert "virt-family-help" not in template
    assert "UI_VIRT_EFFECTIVE_TIER_VALUES" not in template


def test_embedding_is_view_only_even_when_the_query_requests_edit():
    from virt.configuration import snapshot

    payload = snapshot(edit_family="embedding")
    embedding = _family(payload, "embedding")
    assert payload["read_only"] is True
    assert embedding["ui_editable"] is False
    assert all("edit_value" not in field for role in embedding["roles"]
               for field in role["fields"])
    assert _family(payload, "llm")["ui_editable"] is True
    assert _family(payload, "vlm")["ui_editable"] is True


def test_settings_shell_and_virt_page_follow_instance_language_with_fallback(
        monkeypatch):
    from http_render import render_template
    import i18n
    from virt.configuration import snapshot

    payload = snapshot()
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "en")
    english = render_template("virt.html", snapshot=payload)
    assert '<html lang="en">' in english
    assert "Model configuration" in english
    assert ">System<" in english
    assert ">Models<" in english
    assert ">Sign out<" in english

    # A newly admitted language uses the catalog fallback without letting the
    # Italian prompt or the instance default leak into the page.
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")
    fallback = render_template("virt.html", snapshot=payload)
    assert '<html lang="fr">' in fallback
    assert "Model configuration" in fallback
    assert ">System<" in fallback


@pytest.mark.asyncio
async def test_admin_virt_endpoint_negotiates_redacted_json_and_html(
        monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    import http_routes_admin

    async def _inline(func, *args, **kwargs):
        # Some restricted test sandboxes cannot create worker threads.  The
        # route's asynchronous offload is orthogonal to this response
        # contract, so execute the pure snapshot inline here.
        return func(*args, **kwargs)

    monkeypatch.setattr(http_routes_admin.asyncio, "to_thread", _inline)
    admin_virt = http_routes_admin.admin_virt

    json_response = await admin_virt(make_mocked_request(
        "GET", "/admin/virt", headers={"Accept": "application/json"}))
    assert json_response.status == 200
    assert json_response.headers["Cache-Control"] == "no-store"
    payload = json.loads(json_response.text)
    assert payload["read_only"] is True
    assert payload["secrets_redacted"] is True

    html_response = await admin_virt(make_mocked_request(
        "GET", "/admin/virt", headers={"Accept": "text/html"}))
    assert html_response.status == 200
    assert html_response.headers["Cache-Control"] == "no-store"
    assert "Configurazione dei modelli" in html_response.text
    assert "missing:UI_" not in html_response.text

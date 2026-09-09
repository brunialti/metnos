"""Failed execution bootstrap leaves authenticated repair, not execution, open."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture(params=["birth", "prompts"])
def maintenance_app(request, monkeypatch):
    import executor_birth_bootstrap as birth
    import metnos_http_server as server
    import prompt_loader
    import users

    def refuse():
        raise RuntimeError("private failure detail must not reach HTTP")

    def forbidden(*args, **kwargs):
        pytest.fail("maintenance must not start producer-dependent jobs")

    monkeypatch.setattr(birth, "require_birth_runtime_before_workers",
                        refuse if request.param == "birth" else lambda: None)
    monkeypatch.setattr(prompt_loader, "validate_invariant",
                        refuse if request.param == "prompts" else lambda: None)
    monkeypatch.setattr(server.http_async_tasks, "register_async_tasks", forbidden)
    monkeypatch.setattr(server, "_build_catalog_provider", forbidden)
    monkeypatch.setattr(users, "list_users", lambda **kwargs: [{"id": "test-host"}])
    monkeypatch.setattr(server.http_routes_stack, "_probe_sidecar", lambda: {})
    app = server.make_app(admin_key="test-maintenance-key")
    assert len(app.on_startup) == 1  # aiohttp freezes its middleware signal only.
    return app


def test_maintenance_http_preserves_repair_and_authentication(maintenance_app):
    async def check():
        async with TestClient(TestServer(maintenance_app)) as client:
            health = await client.get("/agent/health")
            assert health.status == 200
            payload = await health.json()
            assert payload["ok"] is True
            assert payload["operational"] is False
            assert payload["maintenance_only"] is True

            headers = {"Authorization": "Bearer test-maintenance-key"}
            for path in ("/admin/login", "/admin/virt"):
                response = await client.get(path, headers=headers)
                assert response.status == 200
            readiness = await client.get("/agent/stack/health", headers=headers)
            assert readiness.status == 200
            assert (await readiness.json())["ready"] is False

            for token in ("", "invalid-test-key"):
                auth = {"Authorization": "Bearer " + token}
                response = await client.post("/admin/services/http/restart", headers=auth)
                assert response.status == 401
            # Even a valid credential cannot compensate for ambiguous identity.
            import users
            from unittest.mock import patch
            with patch.object(users, "list_users", return_value=[]):
                response = await client.get("/admin/virt", headers=headers)
                assert response.status == 503
                assert (await response.json())["error"] == "admin_identity_ambiguous"
    asyncio.run(check())


def test_maintenance_rejects_execution_and_publication(maintenance_app):
    async def check():
        async with TestClient(TestServer(maintenance_app)) as client:
            headers = {"Authorization": "Bearer test-maintenance-key"}
            for method, path in (
                ("POST", "/agent/turn"),
                ("POST", "/admin/changes/test/accept"),
                ("GET", "/admin/synth-proposals/test/evaluate"),
                ("POST", "/admin/devices/test/test-invoke"),
                ("POST", "/admin/jobs/test/fire"),
            ):
                response = await client.request(method, path, headers=headers)
                assert response.status == 503
                payload = await response.json()
                assert payload["error"] == "runtime_unavailable"
                assert "private" not in str(payload)
            assert (await client.get("/missing")).status == 404
            assert (await client.post("/agent/health")).status == 405
    asyncio.run(check())


@pytest.mark.parametrize("family", ["llm", "vlm"])
def test_maintenance_can_repair_and_edit_model_configuration(
        maintenance_app, tmp_path, monkeypatch, family):
    """Exercise the real routes/editor without touching a model or live config."""
    import config
    import llm_router
    from virt.configuration import snapshot

    path = tmp_path / f"{family}_tiers.toml"
    damaged = "[broken configuration\n"
    path.write_text(damaged, encoding="utf-8")
    monkeypatch.setenv(f"METNOS_{family.upper()}_TIERS_CONFIG", str(path))
    monkeypatch.setattr(config, "PATH_USER_STATE", tmp_path / "state")

    def forbidden(*args, **kwargs):
        pytest.fail("configuration repair must not load a model")

    monkeypatch.setattr(llm_router, "provider_from_tier_spec", forbidden)

    def view():
        return next(item for item in snapshot(edit_family=family)["families"]
                    if item["key"] == family)

    async def check():
        headers = {"Authorization": "Bearer test-maintenance-key",
                   "Accept": "application/json"}
        async with TestClient(TestServer(maintenance_app)) as client:
            page = await client.get("/admin/virt", headers=headers)
            assert page.status == 200
            assert next(item for item in (await page.json())["families"]
                        if item["key"] == family)["config"]["status"] == "invalid"
            revision = view()["config"]["revision"]
            url = f"/admin/virt/{family}"
            denied = await client.post(url + "/reset", data={"revision": revision})
            assert denied.status == 401
            assert path.read_text(encoding="utf-8") == damaged

            reset = await client.post(url + "/reset", headers=headers,
                                      data={"revision": revision})
            assert reset.status == 200
            result = await reset.json()
            assert result["reset"] is True
            assert Path(result["backup_path"]).read_text(encoding="utf-8") == damaged
            current = view()
            fields = [field for role in current["roles"] for field in role["fields"]
                      if field.get("editable")]
            form = {f"field_{field['edit_key']}": str(field["edit_value"])
                    for field in fields}
            form["revision"] = current["config"]["revision"]
            endpoint = next(field for field in fields
                            if field["name"] in ("endpoint", "base_url"))
            key = f"field_{endpoint['edit_key']}"
            form[key] = "file:///not-a-model-endpoint"
            before = path.read_bytes()
            invalid = await client.post(url + "/save", headers=headers, data=form)
            assert invalid.status == 400
            assert path.read_bytes() == before

            form[key] = "http://127.0.0.1:18282"
            saved = await client.post(url + "/save", headers=headers, data=form)
            assert saved.status == 200
            assert "http://127.0.0.1:18282" in path.read_text(encoding="utf-8")
            after = path.read_bytes()
            stale = await client.post(url + "/save", headers=headers, data=form)
            assert stale.status == 409
            assert path.read_bytes() == after
            health = await client.get("/agent/health")
            assert health.status == 200
            assert (await health.json())["maintenance_only"] is True
            execution = await client.post("/agent/turn", headers=headers)
            assert execution.status == 503  # An edit cannot bypass bootstrap.

    asyncio.run(check())

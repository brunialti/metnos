from __future__ import annotations

import i18n
import services_registry
import ui_surfaces


def test_runtime_catalogs_enumerate_stable_open_resource_keys():
    ui = dict(ui_surfaces.localization_inventory("en"))
    services = dict(services_registry.localization_inventory("en"))
    assert ui["UI_SECTION_SYSTEM_LABEL"] == "System"
    assert ui["UI_SURFACE_SERVICES_LABEL"] == "Services"
    assert services["UI_SERVICE_HTTP_LABEL"] == "HTTP server"
    assert len(ui) == len(set(ui))
    assert len(services) == len(set(services))


def test_third_language_resolution_uses_catalog_not_language_branch(monkeypatch):
    calls: list[tuple[str, str]] = []

    def translated(key: str, lang: str, baselines: dict[str, str]) -> str:
        calls.append((key, lang))
        assert "en" in baselines
        return f"{lang}:{key}"

    monkeypatch.setattr(i18n, "editorial_text", translated)
    services = services_registry.localized([
        {
            "key": "http", "label": "Server HTTP", "label_en": "HTTP server",
            "description": "API", "description_en": "API",
            "group": "Nucleo", "group_en": "Core",
        },
    ], "nl")
    assert services[0]["label"] == "nl:UI_SERVICE_HTTP_LABEL"
    assert ui_surfaces.by_key("services").label("nl") == (
        "nl:UI_SURFACE_SERVICES_LABEL"
    )
    assert ("UI_SERVICE_HTTP_DESCRIPTION", "nl") in calls

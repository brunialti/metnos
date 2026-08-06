"""P1 stealth (ADR 0191) — tecniche indipendenti, routing, ceiling e UI prefs.

Test di propagazione SENZA browser reale: un BrowserProvider mock cattura se la
selezione richiede il browser alternativo. Il lazy-launch resta coperto dallo
smoke del sidecar e dall'E2E opt-in.
"""
from __future__ import annotations

import asyncio

import importlib.util
import pytest
import sys
import time
from pathlib import Path


@pytest.fixture(autouse=True)
def _supply_test_owner_identity(monkeypatch):
    """The logical owner is a separate required boundary in the broker."""
    from playwright_sidecar import session_broker as broker
    original = broker.op_open

    async def scoped_open(*args, **kwargs):
        owner = str(kwargs.get("owner") or "test")
        kwargs.setdefault("owner_user_id", f"test-owner:{owner}")
        return await original(*args, **kwargs)

    monkeypatch.setattr(broker, "op_open", scoped_open)



# ── Registro tecniche ────────────────────────────────────────────────────────

def test_stealth_registry_launch_args_and_gating():
    from playwright_sidecar import stealth

    webdriver = ["--base"]
    stealth.apply_launch_args(
        webdriver, techniques=["webdriver_launch_arg"])
    assert "--disable-blink-features=AutomationControlled" in webdriver
    # idempotente
    stealth.apply_launch_args(
        webdriver, techniques=["webdriver_launch_arg"])
    assert webdriver.count("--disable-blink-features=AutomationControlled") == 1

    ua_only = ["--base"]
    stealth.apply_launch_args(ua_only, techniques=["ua_override"])
    assert ua_only == ["--base"]

    assert stealth.technique_enabled(
        "ua_override", techniques=["ua_override"]) is True
    assert stealth.technique_enabled(
        "ua_override", techniques=["human_delays"]) is False
    assert stealth.technique_enabled(
        "human_delays", techniques=["human_delays"]) is True
    assert stealth.launch_browser_required(["ua_override"]) is False
    assert stealth.launch_browser_required(["webdriver_launch_arg"]) is True
    assert stealth.unknown_techniques(["not_registered"]) == ("not_registered",)


# ── Context kwargs: default onesto, locale/timezone derivati (H1) ────────────

def test_context_kwargs_default_is_native(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.setenv("METNOS_LANG", "it")
    kw = sb._context_kwargs(stealth_techniques=())
    assert "user_agent" not in kw          # UA nativo nel default
    assert kw["locale"] == "it-IT"         # derivato da lang, non costante
    assert kw["service_workers"] == "block"


def test_context_kwargs_applies_only_selected_technique(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.delenv("METNOS_SITES_USER_AGENT", raising=False)
    kw = sb._context_kwargs(
        stealth_techniques=["ua_override"],
        browser_version="149.0.7827.55")
    assert "user_agent" in kw and kw["user_agent"]
    assert "Chrome/149.0.7827.55" in kw["user_agent"]
    webdriver_only = sb._context_kwargs(
        stealth_techniques=["webdriver_launch_arg"])
    assert "user_agent" not in webdriver_only


def test_context_coherence_matches_viewport_and_screen():
    from playwright_sidecar import session_broker as sb
    desktop = sb._context_kwargs(
        stealth_techniques=["context_coherence"])
    assert desktop["viewport"] == desktop["screen"] == {
        "width": 1280, "height": 800}
    assert desktop["device_scale_factor"] == 1

    mobile = sb._context_kwargs(
        stealth_techniques=["mobile_emulation", "context_coherence"])
    assert mobile["viewport"] == mobile["screen"] == {
        "width": 412, "height": 915}
    assert mobile["device_scale_factor"] == 2.625


def test_behavior_components_apply_focus_and_bounded_pause(monkeypatch):
    from playwright_sidecar import stealth
    events = []

    class Locator:
        async def scroll_into_view_if_needed(self, **_kw):
            events.append("scroll")
        async def hover(self, **_kw):
            events.append("hover")
        async def focus(self, **_kw):
            events.append("focus")

    class Page:
        async def bring_to_front(self):
            events.append("front")
        async def wait_for_timeout(self, value):
            events.append(("pause", value))

    monkeypatch.setenv("METNOS_SITES_HUMAN_DELAY_MS", "37")
    asyncio.run(stealth.prepare_interaction(
        Page(), Locator(), techniques=["focus_events"]))
    asyncio.run(stealth.pause_before_interaction(
        Page(), techniques=["human_delays"]))
    assert events == ["front", "scroll", "hover", "focus", ("pause", 37)]


def test_locale_derives_from_lang_no_hardcoded_it(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.delenv("METNOS_SITES_LOCALE", raising=False)
    assert sb._locale_for("it") == "it-IT"
    assert sb._locale_for("en") == "en-US"
    assert sb._locale_for("xx") is None            # lingua ignota → nessun override
    monkeypatch.setenv("METNOS_LANG", "en")
    assert sb._locale_for(None) == "en-US"         # fallback su METNOS_LANG


def test_ceiling_env_disables_stealth(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.delenv("METNOS_SITES_STEALTH_ALLOWED", raising=False)
    assert sb._stealth_allowed() is True           # default ON
    monkeypatch.setenv("METNOS_SITES_STEALTH_ALLOWED", "0")
    assert sb._stealth_allowed() is False


# ── op_open: routing dello stealth EFFETTIVO al provider + ceiling + audit ───

class _CapturingContext:
    # add_init_script e' DENTRO il try/except di op_open: sollevare qui fa
    # ritornare op_open in modo pulito subito dopo la scelta del browser, senza
    # dover simulare navigazione/host-check completi.
    async def add_init_script(self, *_a):
        raise RuntimeError("stop-after-capture")

    async def close(self):
        return None


class _CapturingBrowser:
    async def new_context(self, **_kw):
        return _CapturingContext()


def _run_open_capturing(sb, monkeypatch, *, requested_stealth,
                        techniques, ceiling):
    captured = {}

    async def _provider(browser_mode="headless", stealth=False):
        captured["browser_mode"] = browser_mode
        captured["stealth"] = stealth
        return _CapturingBrowser()

    monkeypatch.setattr(sb, "_browser_provider", _provider)
    monkeypatch.setattr(sb, "_stealth_allowed", lambda: ceiling)
    audited = []
    monkeypatch.setattr(sb.sites_audit, "record",
                        lambda event, **f: audited.append(event))
    sb._sessions.clear()
    sb._pending_opens.clear()
    res = asyncio.run(sb.op_open(
        owner="stealth-test", url="https://x.test", stealth=requested_stealth,
        stealth_techniques=techniques))
    return captured, audited, res


def test_op_open_routes_effective_stealth_true(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, res = _run_open_capturing(
        sb, monkeypatch, requested_stealth=True,
        techniques=["webdriver_launch_arg"], ceiling=True)
    assert captured.get("stealth") is True
    assert captured.get("browser_mode") == "headless"
    assert "stealth_denied_by_ceiling" not in audited
    assert res["ok"] is False  # interrotto dopo la scelta browser (mock)


def test_op_open_ceiling_downgrades_and_audits(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, _ = _run_open_capturing(
        sb, monkeypatch, requested_stealth=True,
        techniques=["webdriver_launch_arg"], ceiling=False)
    assert captured.get("stealth") is False          # forzato honest
    assert "stealth_denied_by_ceiling" in audited     # audit, non errore


def test_op_open_default_is_honest(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, _ = _run_open_capturing(
        sb, monkeypatch, requested_stealth=False,
        techniques=["webdriver_launch_arg"], ceiling=True)
    assert captured.get("stealth") is False
    assert "stealth_denied_by_ceiling" not in audited


def test_context_only_technique_keeps_honest_browser(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, _ = _run_open_capturing(
        sb, monkeypatch, requested_stealth=True,
        techniques=["ua_override"], ceiling=True)
    assert captured.get("stealth") is False
    assert "stealth_denied_by_ceiling" not in audited


def test_open_approval_binds_exact_technique_selection(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.setattr(sb, "_browser_provider", lambda *_a: None)
    sb._pending_opens.clear()
    first = asyncio.run(sb.op_open(
        owner="stealth-token", url="https://x.test",
        allowlist_arg=["x.test", "cdn.test"], stealth=True,
        stealth_techniques=["webdriver_launch_arg"]))
    assert first["error_class"] == "approval_required"
    mismatch = asyncio.run(sb.op_open(
        owner="stealth-token", url="https://x.test",
        allowlist_arg=["x.test", "cdn.test"], stealth=True,
        stealth_techniques=["ua_override"],
        approval_token=first["approval_token"]))
    assert mismatch["error_class"] == "approval_invalid"

    second = asyncio.run(sb.op_open(
        owner="stealth-token", url="https://x.test",
        allowlist_arg=["x.test", "cdn.test"], browser_mode="headless"))
    mode_mismatch = asyncio.run(sb.op_open(
        owner="stealth-token", url="https://x.test",
        allowlist_arg=["x.test", "cdn.test"], browser_mode="side",
        approval_token=second["approval_token"]))
    assert mode_mismatch["error_class"] == "approval_invalid"


def test_side_mode_is_orthogonal_to_stealth_selection(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured = {}

    async def _provider(browser_mode, launch_stealth):
        captured.update({"mode": browser_mode, "launch": launch_stealth})
        return _CapturingBrowser()

    monkeypatch.setattr(sb, "_browser_provider", _provider)
    monkeypatch.setattr(sb, "_stealth_allowed", lambda: True)
    sb._sessions.clear()
    out = asyncio.run(sb.op_open(
        owner="side-mode", url="https://x.test", browser_mode="side",
        stealth=True, stealth_techniques=["ua_override"]))
    assert out["ok"] is False
    assert captured == {"mode": "side", "launch": False}


def test_live_session_reuse_requires_exact_authenticated_boundary(monkeypatch):
    from playwright_sidecar import session_broker as sb

    class Page:
        url = "https://secure.x.test/account"
        def is_closed(self):
            return False
        async def title(self):
            return "Account"

    sid = "reuse-session"
    entry = {
        "page": Page(), "owner": "reuse-owner", "open_host": "www.x.test",
        "owner_user_id": "reuse-owner-id",
        "allowlist": {"www.x.test", "secure.x.test"}, "label": "",
        "credential_mode": "default", "browser_mode": "side",
        "stealth_techniques": ("reuse_live_session",),
        "task_mandate": None, "credential_mandate": {"root_host": "x.test"},
        "authenticated": True, "gate_pending": False, "factor_pending": False,
        "secret_pending": False, "last_used": time.time(),
        "domain": "x.test", "observed_reason": None, "lock": asyncio.Lock(),
    }
    monkeypatch.setattr(sb, "_sessions", {sid: entry})
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)
    out = asyncio.run(sb._reuse_compatible_session(
        owner="reuse-owner", owner_user_id="reuse-owner-id",
        open_host="www.x.test",
        allowlist={"www.x.test", "secure.x.test"}, session_label="",
        credential_mode="default", browser_mode="side",
        stealth_techniques=("reuse_live_session",), task_binding=None,
        credential_binding={"root_host": "x.test"}))
    assert out and out["ok"] and out["reused"] and out["session_id"] == sid

    entry["authenticated"] = False
    assert asyncio.run(sb._reuse_compatible_session(
        owner="reuse-owner", owner_user_id="reuse-owner-id",
        open_host="www.x.test",
        allowlist={"www.x.test", "secure.x.test"}, session_label="",
        credential_mode="default", browser_mode="side",
        stealth_techniques=("reuse_live_session",), task_binding=None,
        credential_binding={"root_host": "x.test"})) is None


def test_side_browser_launches_full_chromium_with_selected_launch_technique(
        monkeypatch):
    from playwright_sidecar import server

    class _Browser:
        def is_connected(self):
            return True

        def on(self, *_args):
            return None

    launched = {}

    class _Chromium:
        async def launch(self, **kwargs):
            launched.update(kwargs)
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(server, "_playwright", _Playwright())
    monkeypatch.setattr(server, "_browser_side_stealth", None)
    monkeypatch.setattr(server, "_stealth_launch_lock", asyncio.Lock())
    browser = asyncio.run(server._get_browser("side", True))
    assert browser.is_connected()
    assert launched["headless"] is False
    assert "--disable-blink-features=AutomationControlled" in launched["args"]


def test_side_browser_without_display_fails_without_headless_fallback(monkeypatch):
    from playwright_sidecar import server
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    try:
        asyncio.run(server._get_browser("side", False))
    except RuntimeError as exc:
        assert str(exc) == "side_browser_display_unavailable"
    else:
        raise AssertionError("side browser must not fall back to headless")


def test_broker_reports_side_browser_unavailable(monkeypatch):
    from playwright_sidecar import session_broker as sb

    async def _unavailable(_mode, _launch):
        raise RuntimeError("side_browser_display_unavailable")

    monkeypatch.setattr(sb, "_browser_provider", _unavailable)
    out = asyncio.run(sb.op_open(
        owner="side-unavailable", url="https://x.test", browser_mode="side"))
    assert out["ok"] is False
    assert out["error_class"] == "side_browser_unavailable"


# ── Pref sites_stealth (M1) ──────────────────────────────────────────────────

def test_pref_sites_stealth_in_closed_vocab():
    from playwright_sidecar import stealth
    import users
    assert "sites_stealth" in users.PREF_KEYS
    assert users.PREF_ALLOWED["sites_browser_mode"] == ("headless", "side")
    assert users.PREF_ALLOWED["sites_stealth"] == ("on", "off")
    specs = users.sites_stealth_preference_specs()
    assert tuple(specs) == stealth.preference_specs()
    for spec in specs:
        key = spec["preference_key"]
        assert key in users.PREF_KEYS
        assert users.PREF_ALLOWED[key] == ("on", "off")


def test_settings_ui_renders_independent_stealth_checkboxes():
    import users
    from http_render import render_template

    prefs = {
        "sites_browser_mode": "side",
        "sites_stealth": "on",
        "sites_stealth_webdriver": "on",
    }
    user = {
        "id": "u1", "name": "host", "role": "host", "created_at": "",
        "display_name": "", "email": "", "autonomy_level": "full",
        "notes": "", "channels": [],
    }
    html = render_template(
        "user_detail.html", user=user, channels=[], devices=[], prefs=prefs,
        pref_allowed={"lang": users.PREF_ALLOWED["lang"]},
        stealth_options=users.sites_stealth_preference_specs(), flash="")
    assert '<section class="web-panel"' in html
    assert 'name="sites_browser_mode" value="headless"' in html
    assert 'name="sites_stealth" value="on"' in html
    assert 'name="sites_browser_mode" value="side"' in html
    for spec in users.sites_stealth_preference_specs():
        assert f'name="{spec["preference_key"]}" value="on"' in html
    webdriver = html.split('id="pref_sites_stealth_webdriver"', 1)[1]
    assert "checked" in webdriver.split(">", 1)[0]
    user_agent = html.split('id="pref_sites_stealth_user_agent"', 1)[1]
    assert "checked" not in user_agent.split(">", 1)[0]


def test_settings_post_persists_exact_side_webdriver_only_group(monkeypatch):
    """The supported admin route stores one coherent surface profile."""
    from aiohttp import web
    import http_routes_admin

    writes = []
    monkeypatch.setattr(
        http_routes_admin.users, "set_pref",
        lambda user_id, key, value: (
            writes.append((user_id, key, value)) or {"ok": True}))

    class Request:
        match_info = {"id": "u1"}

        async def post(self):
            return {
                "_sites_web_browsing_group": "1",
                "sites_browser_mode": "side",
                "sites_stealth": "on",
                "sites_stealth_webdriver": "on",
            }

    with pytest.raises(web.HTTPFound):
        asyncio.run(http_routes_admin.admin_user_prefs(Request()))

    assert writes[:2] == [
        ("u1", "sites_browser_mode", "side"),
        ("u1", "sites_stealth", "on"),
    ]
    assert ("u1", "sites_stealth_webdriver", "on") in writes
    # Lo sblocco automatico delle risorse fa parte dello stesso profilo di
    # superficie: casella assente = off, come per ogni tecnica (6/8/2026).
    assert ("u1", "sites_auto_allow_resources", "off") in writes
    for spec in http_routes_admin.users.sites_stealth_preference_specs():
        expected = ("on" if spec["preference_key"] ==
                    "sites_stealth_webdriver" else "off")
        assert ("u1", spec["preference_key"], expected) in writes


# ── open_sites: validazione + passaggio stealth a session_open (M5) ──────────

def _load_open_sites():
    path = (Path(__file__).resolve().parents[3]
            / "executors/open_sites/open_sites.py")
    spec = importlib.util.spec_from_file_location("_open_sites_stealth_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_open_sites_passes_master_and_selected_techniques(monkeypatch):
    module = _load_open_sites()
    seen = {}

    def _fake_open(**kw):
        seen.update(kw)
        return {"ok": True, "session_id": "s1", "url": kw["url"], "title": ""}

    monkeypatch.setattr(module.session_client, "session_open", _fake_open)
    out = module.invoke({
        "urls": ["https://x.test"], "_stealth": "on",
        "_stealth_techniques": ["webdriver_launch_arg"],
    })
    assert out["ok"] and seen.get("stealth") is True
    assert seen.get("stealth_techniques") == ["webdriver_launch_arg"]
    assert seen.get("browser_mode") == "headless"

    seen.clear()
    out = module.invoke({"urls": ["https://x.test"], "_stealth": "off"})
    assert out["ok"] and seen.get("stealth") is False
    assert seen.get("stealth_techniques") == []

    seen.clear()
    out = module.invoke({
        "urls": ["https://x.test"], "_browser_mode": "side",
    })
    assert out["ok"] and seen.get("browser_mode") == "side"

    # default: nessun _stealth → off
    seen.clear()
    out = module.invoke({"urls": ["https://x.test"]})
    assert out["ok"] and seen.get("stealth") is False
    assert seen.get("stealth_techniques") == []


def test_open_sites_rejects_invalid_stealth(monkeypatch):
    module = _load_open_sites()
    monkeypatch.setattr(module.session_client, "session_open",
                        lambda **_kw: (_ for _ in ()).throw(
                            AssertionError("non deve aprire")))
    out = module.invoke({"urls": ["https://x.test"], "_stealth": "maybe"})
    assert out["ok"] is False and out["error_class"] == "invalid_args"

    out = module.invoke({
        "urls": ["https://x.test"], "_stealth": "on",
        "_stealth_techniques": ["unknown"],
    })
    assert out["ok"] is False and out["error_class"] == "invalid_args"

    out = module.invoke({
        "urls": ["https://x.test"], "_browser_mode": "automatic",
    })
    assert out["ok"] is False and out["error_class"] == "invalid_args"


def test_open_sites_reports_unavailable_side_browser(monkeypatch):
    module = _load_open_sites()
    monkeypatch.setattr(module.session_client, "session_open", lambda **_kw: {
        "ok": False, "error_class": "side_browser_unavailable"})
    out = module.invoke({
        "urls": ["https://x.test"], "_browser_mode": "side"})
    assert out["ok"] is False
    assert out["error_class"] == "side_browser_unavailable"
    assert out["error"]

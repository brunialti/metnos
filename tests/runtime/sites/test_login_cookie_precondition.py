"""Semantic cookie preconditions; real Chromium, synthetic local routes only."""
from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from playwright_sidecar import credential_injection as ci
from playwright_sidecar import cookie_privacy as cp
from playwright_sidecar import session_broker as sb


def _reject_decision(panels):
    return {"kind": "cookie", "panel_id": panels[0]["id"],
            "control_id": panels[0]["controls"][-1]["id"],
            "effect": "reject_optional"}


_EVENT = "sessionStorage.events = JSON.stringify([...(JSON.parse(sessionStorage.events || '[]')), EVENT]);"


def _document(stage, label, with_overlay=True):
    field = ('<input name="username" autocomplete="username">'
             if stage == "username" else '<input type="password" name="password">')
    action = "/password" if stage == "username" else "/done"
    panel = f"""<aside role="dialog" aria-modal="true" id="privacy"
      style="position:fixed;inset:0;background:white;z-index:99">
      <h2>Cookie privacy</h2>
      <span>fixture-user fixture-password</span>
      <button type="button" onclick="{_EVENT.replace('EVENT', "'accept'")}
        this.parentElement.remove()">Accept all / Accetta tutto</button>
      <button type="button" onclick="{_EVENT.replace('EVENT', "'necessary'")}
        this.parentElement.remove()">{label}</button></aside>"""
    return f"""<!doctype html><html><meta charset="utf-8"><body><form method="post" action="{action}">
      {field}<button type="submit">Login</button></form>
      {panel if with_overlay else ''}
      <script>document.querySelector('input').addEventListener('input', () => {{
        {_EVENT.replace('EVENT', "'fill'")}
      }});</script></body></html>"""


@pytest.mark.skipif(os.environ.get("METNOS_SITES_SIM") != "1",
                    reason="opt-in real Chromium test; no external site or credentials")
@pytest.mark.parametrize("label", ["Solo necessari", "Necessary only", "拒绝全部", "رفض الكل"])
@pytest.mark.parametrize("stage", ["password", "username", "direct_url"])
def test_real_login_rejects_cookies_before_each_credential_form(
        monkeypatch, label, stage):
    from playwright.async_api import async_playwright

    origin = "https://cookie.example.test"
    payload = {"username": "fixture-user", "password": "fixture-password",
               "credential_origins": [origin], "session_cookie_names": ["fixture-session"]}
    if stage == "direct_url":
        payload["login_url"] = origin + "/password"
    monkeypatch.setattr(ci, "_load_site_credentials", lambda _d: (payload, "cookie.example.test"))
    monkeypatch.setattr(ci.credentials, "fingerprint", lambda _d: "fixture")
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)
    calls = []

    def classify(panels, timeout_s):
        observed = json.dumps(panels, ensure_ascii=False)
        assert label in observed  # Unicode survives the whole model boundary.
        assert payload["username"] not in observed
        assert payload["password"] not in observed
        calls.append(panels)
        return _reject_decision(panels)

    monkeypatch.setattr(cp, "_classify", classify)

    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context()

            async def serve(route):
                path = route.request.url.removeprefix(origin)
                if path == "/done":
                    await route.fulfill(body="<h1>Account</h1>", headers={
                        "Content-Type": "text/html",
                        "Set-Cookie": "fixture-session=verified; Path=/; Secure"})
                elif path in {"/", "/password"}:
                    body = ("<h1>Landing</h1>" if stage == "direct_url" and path == "/"
                            else _document("password" if path == "/password" else stage, label))
                    await route.fulfill(body=body, content_type="text/html")
                else:
                    await route.abort()

            await context.route("**/*", serve)
            page = await context.new_page()
            await page.goto(origin + "/")
            sid = "cookie-precondition-fixture"
            entry = {"owner": "fixture", "domain": "cookie.example.test",
                     "page": page, "context": context, "lock": asyncio.Lock(),
                     "last_used": time.time(), "gate_pending": False,
                     "authenticated": False, "allowlist": {"cookie.example.test"},
                     "pending_actions": {}, "secret_pending": False}
            monkeypatch.setitem(sb._sessions, sid, entry)
            try:
                result = await sb.op_login(session_id=sid, owner="fixture")
                events = json.loads(await page.evaluate("sessionStorage.events || '[]'"))
                assert result.get("logged_in"), result
                expected = ["necessary", "fill"] * (2 if stage == "username" else 1)
                assert events == expected
                assert len(calls) == 1  # Identical panel decisions are reused.
                assert "_cookie_redact" not in entry
            finally:
                sb._sessions.pop(sid, None)
                await browser.close()

    asyncio.run(exercise())


def test_semantic_cookie_classifier_uses_local_router_and_strict_json(monkeypatch):
    import llm_router
    from llm_workloads import tier_for

    calls = []
    decision = {"kind": "other", "panel_id": "", "control_id": "", "effect": "none"}

    class Provider:
        mode = "local"

        def chat(self, system, user, **kwargs):
            calls.append((system, user, kwargs))
            return SimpleNamespace(text=json.dumps(decision))

    provider = Provider()
    tiers = []

    class Router:
        def provider(self, tier):
            tiers.append(tier)
            return provider

    monkeypatch.setattr(llm_router, "LLMRouter", Router)
    assert cp._classify([{"text": "拒绝全部"}], .5) == decision
    assert tiers == [tier_for("sites.cookie_resolution")]
    assert "拒绝全部" in calls[0][1]
    assert calls[0][2] == {"max_tokens": 160, "request_timeout_s": .5}
    provider.mode = "frontier"
    with pytest.raises(RuntimeError, match="local_model_required"):
        cp._classify([], .5)
    assert len(calls) == 1
    provider.mode = "local"
    decision["effect"] = "accept_all"
    with pytest.raises(ValueError):
        cp._classify([], .5)


def test_cookie_audit_does_not_call_an_unresolved_click_success(monkeypatch):
    audit = []

    async def unresolved(_page, state, **_kwargs):
        state["clicks"] = 1
        return cp.CookieOutcome("blocked", "cookie", "obstruction_remains")

    monkeypatch.setattr(cp, "reject_cookies", unresolved)
    monkeypatch.setattr(sb.sites_audit, "record", lambda *a, **kw: audit.append((a, kw)))
    result = asyncio.run(sb._dismiss_privacy_obstruction({"page": object()}))
    assert result.status == "blocked"
    assert audit[0][1]["outcome"] is False
    assert audit[0][1]["reason"] == "obstruction_remains"
    assert audit[0][1]["method"] == "semantic"


def _browser_scenario(body, check):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(body)
            try:
                await check(page)
            finally:
                await browser.close()

    asyncio.run(run())


_BROWSER = pytest.mark.skipif(
    os.environ.get("METNOS_SITES_SIM") != "1", reason="opt-in real Chromium")


@_BROWSER
@pytest.mark.parametrize("surface", ["none", "login", "other"])
def test_cookie_precondition_leaves_normal_surfaces_usable(monkeypatch, surface):
    bodies = {"none": "<h1>Account</h1><button>Continue</button>",
              "login": '<dialog open><form><input type="password"><button>Login</button></form></dialog>',
              "other": '<dialog open><h1>Delivery address</h1><button>Continue</button></dialog>'}
    calls = []

    def classify(panels, _timeout):
        assert surface == "other"
        calls.append(panels)
        return {"kind": "other", "panel_id": "", "control_id": "", "effect": "none"}

    monkeypatch.setattr(cp, "_classify", classify)

    async def check(page):
        state = {}
        for _ in range(2):
            assert (await cp.reject_cookies(page, state)).status == "clear"
        assert len(calls) == (1 if surface == "other" else 0)

    _browser_scenario(bodies[surface], check)


@_BROWSER
@pytest.mark.parametrize("control", [
    '<button type="submit" form="external">Reject all</button>',
    '<a href="/elsewhere">Reject all</a>',
    '<button formaction="/elsewhere">Reject all</button>',
    '<button>×</button>',
    '<button>X</button>',
    '<button>x</button>',
    '<button disabled>Reject all</button>',
    '<button aria-disabled="true">Reject all</button>',
])
def test_cookie_model_cannot_authorize_unsafe_controls(monkeypatch, control):
    body = '<form id="external"></form>' + _document("password", "Reject all")
    body = body.replace('<button type="button" onclick="' +
                        _EVENT.replace('EVENT', "'necessary'") + '\n        this.parentElement.remove()">Reject all</button>', control)
    monkeypatch.setattr(cp, "_classify", lambda panels, _timeout: _reject_decision(panels))

    async def check(page):
        await page.evaluate("window.clicked = 0; document.addEventListener('click', () => window.clicked++)")
        outcome = await cp.reject_cookies(page, {})
        assert outcome.status == "blocked", outcome
        assert outcome.reason == "unsafe_control"
        assert await page.evaluate("window.clicked") == 0

    _browser_scenario(body, check)


@_BROWSER
@pytest.mark.parametrize("mutation", ["replace", "label", "href", "hide", "handler", "other_stale"])
def test_cookie_decision_does_not_survive_dom_change(monkeypatch, mutation):
    async def check(page):
        await page.evaluate("window.clicked = 0; document.addEventListener('click', () => window.clicked++)")
        loop = asyncio.get_running_loop()

        def classify(panels, _timeout):
            script = {
                "replace": "el.replaceWith(el.cloneNode(true))",
                "label": "el.textContent = 'Accept all'",
                "href": "el.setAttribute('formaction', '/elsewhere')",
                "hide": "el.style.display = 'none'",
                "handler": "el.onclick = () => location.assign('/elsewhere')",
                "other_stale": "el.textContent = 'Accept all'",
            }[mutation]
            changed = asyncio.run_coroutine_threadsafe(
                page.evaluate("() => { const el = document.querySelector('#privacy button:last-child'); " + script + "}"), loop)
            changed.result(timeout=2)
            if mutation == "other_stale":
                return {"kind": "other", "panel_id": "", "control_id": "", "effect": "none"}
            return _reject_decision(panels)

        monkeypatch.setattr(cp, "_classify", classify)
        outcome = await cp.reject_cookies(page, {})
        assert outcome.reason == "stale_dom", outcome
        assert await page.evaluate("window.clicked") == 0

    _browser_scenario(_document("password", "Reject all"), check)


@_BROWSER
@pytest.mark.parametrize("failure", ["unknown", "no_choice", "invalid_id", "timeout", "unavailable"])
def test_unresolved_cookie_precondition_never_fills_credentials(monkeypatch, failure):
    def classify(panels, _timeout):
        if failure == "timeout":
            raise TimeoutError()
        if failure == "unavailable":
            raise ConnectionError()
        decision = _reject_decision(panels)
        if failure == "unknown":
            decision.update(kind="unknown", effect="none", control_id="")
        elif failure == "no_choice":
            decision.update(effect="none", control_id="")
        else:
            decision["control_id"] = "not-observed"
        return decision

    monkeypatch.setattr(cp, "_classify", classify)
    payload = {"username": "fixture-user", "password": "fixture-password"}
    monkeypatch.setattr(ci, "_load_site_credentials", lambda _d: (payload, "cookie.example.test"))
    monkeypatch.setattr(ci, "cooldown_block", lambda *_a, **_kw: None)

    async def check(page):
        async def prepare(**kwargs):
            return await cp.reject_cookies(page, {}, **kwargs)

        result = await ci.perform_login(
            page=page, context=page.context, domain="cookie.example.test", form_hint=None,
            owner="fixture", session_id="fixture", op_timeout_s=2, prepare_page=prepare)
        assert result["error_class"] == "cookie_precondition_unresolved"
        if failure == "timeout":
            assert result["obstruction_reason"] == "model_timeout"
        if failure == "unavailable":
            assert result["obstruction_reason"] == "model_failed"
        assert await page.locator('input[type=password]').input_value() == ""

    _browser_scenario(_document("password", "Reject all"), check)


@_BROWSER
def test_cookie_limits_and_unchanged_panel_postcondition(monkeypatch):
    calls = []

    def classify(panels, _timeout):
        calls.append(panels)
        return _reject_decision(panels)

    monkeypatch.setattr(cp, "_classify", classify)

    async def check(page):
        await page.evaluate("window.clicked = 0; document.querySelectorAll('button').forEach(el => el.onclick = () => window.clicked++)")
        state = {}
        result = await cp.reject_cookies(page, state)
        assert result.reason == "obstruction_remains"
        assert await page.evaluate("window.clicked") == 1
        assert len(calls) == 1
        state["clicks"] = 2
        assert (await cp.reject_cookies(page, state)).reason == "dismissal_limit"
        assert await page.evaluate("window.clicked") == 1
        assert (await cp.reject_cookies(page, {"calls": 4})).reason == "decision_limit"
        assert (await cp.reject_cookies(page, {}, enabled=False)).reason == "model_disabled"
        assert len(calls) == 1

    _browser_scenario(_document("password", "Reject all"), check)


@_BROWSER
def test_cookie_redaction_preserves_ids_and_json_structure(monkeypatch):
    def classify(panels, _timeout):
        assert panels[0]["id"] == "p1"
        assert panels[0]["controls"][-1]["safe"] is True
        assert "p1" not in panels[0]["text"]
        assert "true" not in panels[0]["text"]
        return _reject_decision(panels)

    monkeypatch.setattr(cp, "_classify", classify)

    async def check(page):
        await page.evaluate("() => { document.querySelector('#privacy button:last-child').onclick = () => document.querySelector('#privacy').remove(); }")
        result = await cp.reject_cookies(
            page, {}, redact=lambda text: text.replace("p1", "[redacted]").replace("true", "[redacted]"))
        assert result.status == "resolved", result

    _browser_scenario(_document("password", "Reject all").replace(
        "fixture-user fixture-password", "p1 true"), check)


class _Handle:
    """Minimal stand-in for one context's JSHandle."""

    def __init__(self, panels, commit="clicked"):
        self.panels = panels
        self.commit = commit
        self.commits = []
        self.disposed = False

    async def evaluate(self, script, argument=None):
        # Exact scripts: the commit script embeds the observation one, so a
        # substring match would answer the wrong question.
        if script == "(saved) => saved.public":
            return self.panels
        if script == "(saved) => saved.url !== location.href":
            return False
        self.commits.append(argument)
        if isinstance(argument, dict) and argument.get("check_only"):
            return "unchanged"
        if self.commit == "clicked":
            self.panels = []  # A real dismissal removes the panel.
        return self.commit

    async def dispose(self):
        self.disposed = True


class _Frame:
    def __init__(self, panels, commit="clicked", detached=False):
        self.detached = detached
        self.handle = _Handle(panels, commit)

    async def evaluate_handle(self, _script):
        if self.detached:
            raise RuntimeError("frame detached")
        return self.handle


class _Page:
    def __init__(self, frames):
        self.frames = frames

    async def wait_for_timeout(self, _milliseconds):
        return None


def _panel(identifier, name="Solo necessari"):
    return {"id": identifier, "text": "Che biscotti vuoi?",
            "controls": [{"id": identifier + "c1", "name": name, "safe": True}]}


def test_cookie_panel_in_a_nested_context_is_observed_and_rejected(monkeypatch):
    """The panel a main-document observation cannot see must still be resolved."""
    page = _Page([_Frame([]), _Frame([_panel("p1")])])
    seen = []

    def classify(panels, _timeout):
        seen.append(panels)
        return {"kind": "cookie", "panel_id": panels[0]["id"],
                "control_id": panels[0]["controls"][0]["id"],
                "effect": "reject_optional"}

    monkeypatch.setattr(cp, "_classify", classify)
    outcome = asyncio.run(cp.reject_cookies(page, {}))

    assert outcome.status == "resolved" and outcome.kind == "cookie"
    assert seen[0][0]["id"] == "f1p1"
    assert seen[0][0]["controls"][0]["id"] == "f1p1c1"
    # The click is committed in the owning context, with that context's own ids.
    assert page.frames[1].handle.commits[-1]["panel_id"] == "p1"
    assert page.frames[1].handle.commits[-1]["control_id"] == "p1c1"
    assert page.frames[0].handle.commits == []
    assert all(frame.handle.disposed for frame in page.frames[1:])


def test_cookie_panel_budget_stays_global_across_contexts(monkeypatch):
    """Reading more contexts must not widen what the model is shown."""
    frames = [_Frame([_panel("p1"), _panel("p2")]) for _ in range(3)]
    seen = []

    def classify(panels, _timeout):
        seen.append(panels)
        return {"kind": "other", "panel_id": "", "control_id": "", "effect": "none"}

    monkeypatch.setattr(cp, "_classify", classify)
    outcome = asyncio.run(cp.reject_cookies(_Page(frames), {}))

    assert len(seen[0]) == cp.MAX_OBSERVED_PANELS
    assert [panel["id"] for panel in seen[0]] == ["f0p1", "f0p2", "f1p1"]
    assert outcome.status == "clear" and outcome.panels == cp.MAX_OBSERVED_PANELS


def test_cookie_outcome_reports_what_was_observed(monkeypatch):
    """A covered page and a page with no panel must not be equally silent."""
    monkeypatch.setattr(cp, "_classify", lambda *_a: {
        "kind": "cookie", "panel_id": "nope", "control_id": "nope",
        "effect": "reject_optional"})
    covered = asyncio.run(cp.reject_cookies(
        _Page([_Frame([], detached=True), _Frame([_panel("p1")])]), {}))
    assert covered.status == "blocked" and covered.reason == "unknown_panel"
    assert (covered.frames, covered.panels) == (1, 1)

    empty = asyncio.run(cp.reject_cookies(_Page([_Frame([])]), {}))
    assert empty.status == "clear" and (empty.frames, empty.panels) == (0, 0)

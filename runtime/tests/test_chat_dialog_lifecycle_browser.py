"""Browser E2E for restored inline-dialog lifecycle."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

_RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RUNTIME))

from http_render import render_template  # noqa: E402


def _chat_html() -> str:
    return render_template("chat.html", role="admin")


def _form_html(dialog_id: str, turn_id: str) -> str:
    return render_template(
        "dialog_form.html",
        dialog_id=dialog_id,
        origin_turn_id=turn_id,
        title="Permessi credenziali test",
        description="",
        expired_message="Dialogo scaduto.",
        dialog=[{
            "var": "decision",
            "prompt": "Confermi?",
            "schema": {"kind": "choice", "choices": [
                {"label": "Approva", "value": "approve"},
                {"label": "Rifiuta", "value": "reject"},
            ]},
        }],
        role="admin",
    )


def _completion_html(turn_id: str) -> str:
    meta = base64.b64encode(json.dumps({
        "turn_id": "", "total_ms": 0, "path": [],
    }).encode()).decode()
    return (
        '<div data-completion-text="Dialogo completato." '
        f'data-turn-id="{turn_id}" data-attachments-b64="" '
        f'data-turn-meta-b64="{meta}"></div>'
    )


def _route_session_and_history(route, chat_html: str) -> bool:
    path = urlparse(route.request.url).path
    if path == "/":
        route.fulfill(status=200, content_type="text/html", body=chat_html)
    elif path == "/agent/session/register":
        route.fulfill(status=200, content_type="application/json",
                      body='{"device_token":"browser-test-token"}')
    elif path == "/agent/session/ping":
        route.fulfill(status=200, content_type="application/json",
                      body='{"ok":true}')
    elif path == "/agent/session/events":
        route.fulfill(status=200, content_type="text/event-stream", body="")
    elif path == "/agent/turns/recent":
        route.fulfill(status=200, content_type="application/json",
                      body='{"turns":[]}')
    else:
        return False
    return True


def _history_record(dialog_id: str, turn_id: str) -> dict:
    return {
        "cls": "bot",
        "text": "Permessi credenziali test\n\n"
                f"INLINE_FORM:/agent/dialog/{dialog_id}/form",
        "meta": {"turn_id": turn_id, "left": "turn:" + turn_id[:8],
                 "path": [{"tool": "set_credentials", "ok": True}]},
        "ts": 1,
        "htmlMode": False,
    }


def _seed_history(page, records: list[dict]) -> None:
    page.evaluate(
        "items => localStorage.setItem('metnos_chat_history', "
        "JSON.stringify(items))", records)


def test_arrow_history_migrates_existing_user_messages():
    chat_html = _chat_html()

    def route_request(route):
        if not _route_session_and_history(route, chat_html):
            route.fulfill(status=404, body="not found")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        _seed_history(page, [
            {"cls": "me", "text": "primo comando", "ts": 1},
            {"cls": "bot", "text": "risposta", "ts": 2},
            {"cls": "me", "text": "secondo comando", "ts": 3},
        ])
        page.evaluate("localStorage.removeItem('metnos_cmd_buffer')")
        composer = page.locator("#q")
        composer.focus()
        composer.press("ArrowUp")
        assert composer.input_value() == "secondo comando"
        composer.press("ArrowUp")
        assert composer.input_value() == "primo comando"
        composer.press("ArrowDown")
        assert composer.input_value() == "secondo comando"
        browser.close()


def test_restored_dialog_is_active_once_and_does_not_reappear():
    active_id = "activebrowser001"
    active_turn = "turnbrowser000001"
    chat_html = _chat_html()
    form_html = _form_html(active_id, active_turn)

    def route_request(route):
        request = route.request
        path = urlparse(request.url).path
        if path == f"/agent/dialog/{active_id}/form":
            route.fulfill(status=200, content_type="text/html", body=form_html,
                          headers={"Cache-Control": "no-store"})
        elif path == f"/agent/dialog/{active_id}/submit":
            route.fulfill(status=200, content_type="text/html",
                          body=_completion_html(active_turn))
        elif path == "/agent/dialog/missingbrowser01/form":
            route.fulfill(status=404, content_type="application/json",
                          body='{"error":"dialog_not_found"}')
        elif not _route_session_and_history(route, chat_html):
            route.fulfill(status=404, body="not found")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        history = [_history_record("missingbrowser01", "turnbrowserold001"),
                   _history_record(active_id, active_turn)]
        history[1]["ts"] = 2
        _seed_history(page, history)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "document.querySelectorAll('#log iframe').length === 1")

        iframe = page.locator("#log iframe")
        assert iframe.count() == 1
        assert iframe.evaluate("""el => {
          const children = Array.from(el.closest('.msg').children);
          const path = children.findIndex(x => x.classList.contains('path-badges'));
          const meta = children.findIndex(x => x.classList.contains('meta'));
          const frame = children.findIndex(x => x.contains(el));
          return path >= 0 && meta > path && frame > meta;
        }""")
        transcript = page.evaluate("buildChatTranscript()")
        assert active_turn in transcript
        assert "path: set_credentials" in transcript
        resumed = page.evaluate("""() => mergeDialogCompletionMeta(
          {turn_id:'oldturn', path:[{tool:'set_credentials',ok:true}]},
          'newturn',
          {turn_id:'newturn', total_ms:1200,
           path:[{tool:'act_sites',ok:true}], target_device:''})""")
        assert resumed["turn_id"] == "newturn"
        assert resumed["path"] == [{"tool": "act_sites", "ok": True}]

        frame = iframe.content_frame
        frame.locator("input[value=reject]").check()
        frame.locator("button[type=submit]").click()
        page.wait_for_function(
            "document.querySelectorAll('#log iframe').length === 0")
        completed_transcript = page.evaluate("buildChatTranscript()")
        assert active_turn in completed_transcript
        assert "path: set_credentials" in completed_transcript
        stored = page.evaluate(
            "JSON.parse(localStorage.getItem('metnos_chat_history') || '[]')")
        assert not any(
            f"/agent/dialog/{active_id}/form" in str(item.get("text", ""))
            for item in stored)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        assert page.locator("#log iframe").count() == 0
        browser.close()


@pytest.mark.parametrize("state,status", [
    ("completed", 410),
    ("cancelled", 410),
    ("expired", 410),
    ("missing", 404),
])
def test_restored_terminal_state_matrix(state, status):
    dialog_id = "terminal" + state
    turn_id = "turn" + state
    chat_html = _chat_html()

    def route_request(route):
        path = urlparse(route.request.url).path
        if path == f"/agent/dialog/{dialog_id}/form":
            headers = ({"X-Metnos-Dialog-State": state}
                       if status == 410 else {})
            route.fulfill(status=status, content_type="text/html",
                          headers=headers, body=state)
        elif not _route_session_and_history(route, chat_html):
            route.fulfill(status=404, body="not found")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        _seed_history(page, [_history_record(dialog_id, turn_id)])
        page.reload(wait_until="domcontentloaded")
        marker = f"/agent/dialog/{dialog_id}/form"
        page.wait_for_function("""marker => {
          const items = JSON.parse(localStorage.getItem('metnos_chat_history') || '[]');
          return !items.some(x => String(x.text || '').includes(marker));
        }""", arg=marker)
        assert page.locator("#log iframe").count() == 0
        transcript = page.evaluate("buildChatTranscript()")
        assert turn_id in transcript
        assert "path: set_credentials" in transcript
        browser.close()


def test_network_failure_is_retryable_not_terminal():
    dialog_id = "networkbrowser01"
    turn_id = "turnnetwork00001"
    chat_html = _chat_html()
    form_html = _form_html(dialog_id, turn_id)
    attempts = {"form": 0}

    def route_request(route):
        path = urlparse(route.request.url).path
        if path == f"/agent/dialog/{dialog_id}/form":
            attempts["form"] += 1
            if attempts["form"] == 1:
                route.abort("failed")
            else:
                route.fulfill(status=200, content_type="text/html", body=form_html)
        elif not _route_session_and_history(route, chat_html):
            route.fulfill(status=404, body="not found")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        _seed_history(page, [_history_record(dialog_id, turn_id)])
        page.reload(wait_until="domcontentloaded")
        retry = page.locator("#log .dialog-stale button")
        retry.wait_for()
        stored = page.evaluate("localStorage.getItem('metnos_chat_history')")
        assert f"/agent/dialog/{dialog_id}/form" in stored
        retry.click()
        page.wait_for_function(
            "document.querySelectorAll('#log iframe').length === 1")
        assert attempts["form"] == 2
        browser.close()

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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from http_render import render_template  # noqa: E402


TEST_USER_SCOPE = "browser-test-user"


def _chat_html(*, user_scope: str = TEST_USER_SCOPE,
               migrate_legacy_storage: bool = True) -> str:
    return render_template(
        "chat.html",
        role="admin",
        chat_user_scope=user_scope,
        chat_migrate_legacy_storage=migrate_legacy_storage,
    )


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
        body = json.loads(route.request.post_data or "{}")
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({
                          "device_token": "browser-test-token",
                          "conversation_id": body.get("conversation_id", ""),
                      }))
    elif path == "/agent/session/ping":
        body = json.loads(route.request.post_data or "{}")
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({
                          "ok": True,
                          "conversation_id": body.get("conversation_id", ""),
                      }))
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
        "items => localStorage.setItem(historyKey(), JSON.stringify(items))",
        records,
    )


def _stored_history(page) -> list[dict]:
    return page.evaluate(
        "() => JSON.parse(localStorage.getItem(historyKey()) || '[]')"
    )


def test_recovered_in_flight_turn_keeps_badges_and_metadata():
    turn_id = "recovermeta00001"
    chat_html = render_template(
        "chat.html",
        role="admin",
        chat_user_scope=TEST_USER_SCOPE,
        chat_migrate_legacy_storage=True,
        executor_intelligence={
            "read_files": "deterministic",
            "extract_entries": "agentic",
            "classify_entries": "llm",
        },
    )
    final_payload = {
        "turn_id": turn_id,
        "final_message": "Risposta recuperata.",
        "final_message_html": "<p>Risposta recuperata.</p>",
        "total_ms": 38623,
        "ts_end": 1785854002.7782238,
        "target_device": None,
        "path": [
            {"tool": "read_files", "ok": True},
            {"tool": "extract_entries", "ok": True},
            {"tool": "classify_entries", "ok": True},
        ],
        "attachments": [],
    }

    def route_request(route):
        path = urlparse(route.request.url).path
        if path == "/agent/turns/recent":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"turns": [{
                    "turn_id": turn_id,
                    "query": "Esegui i tre tipi di executor",
                    "ts_start": 1785853964.1546092,
                    "ts_end": None,
                    "in_flight": True,
                    "final_message": "",
                    "steps_summary": [],
                    "attachments": [],
                }]}),
            )
        elif path == f"/agent/turns/{turn_id}/stream":
            body = (
                "id: 1\n"
                "event: final\n"
                f"data: {json.dumps(final_payload)}\n\n"
            )
            route.fulfill(status=200, content_type="text/event-stream", body=body)
        elif not _route_session_and_history(route, chat_html):
            route.fulfill(status=404, body="not found")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        bubble = page.locator(
            f'#log .msg.bot[data-turn-id="{turn_id}"]:not([data-in-flight])'
        )
        bubble.wait_for()

        assert bubble.locator(".path-badges .badge").all_text_contents() == [
            "read_files", "extract_entries", "classify_entries",
        ]
        assert bubble.locator(".path-badges .badge").evaluate_all(
            "els => els.map(el => el.dataset.intelligence)"
        ) == ["deterministic", "agentic", "llm"]
        assert bubble.locator("svg.executor-brain").count() == 3
        assert "server" in bubble.locator(".meta .where").inner_text().lower()
        # Il prefisso e' localizzato (per esempio ``turno:`` in italiano):
        # questo test verifica l'identificativo, non una superficie linguistica.
        assert "recoverm" in bubble.locator(".meta-left").inner_text()
        assert "38.6" in bubble.locator(".meta-left").inner_text()
        assert bubble.locator(".meta-right").inner_text().strip()
        assert bubble.locator(".msg-fb").count() == 1

        stored = _stored_history(page)
        recovered = next(
            item for item in reversed(stored)
            if isinstance(item.get("meta"), dict)
            and item["meta"].get("turn_id") == turn_id
        )
        assert [step["tool"] for step in recovered["meta"]["path"]] == [
            "read_files", "extract_entries", "classify_entries",
        ]
        browser.close()


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
        page.evaluate("localStorage.removeItem(CMDBUF_KEY)")
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
        assert "set_credentials" in transcript
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
        assert "set_credentials" in completed_transcript
        stored = _stored_history(page)
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
          const items = JSON.parse(localStorage.getItem(historyKey()) || '[]');
          return !items.some(x => String(x.text || '').includes(marker));
        }""", arg=marker)
        assert page.locator("#log iframe").count() == 0
        transcript = page.evaluate("buildChatTranscript()")
        assert turn_id in transcript
        assert "set_credentials" in transcript
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
        stored = _stored_history(page)
        assert any(
            f"/agent/dialog/{dialog_id}/form" in str(item.get("text", ""))
            for item in stored
        )
        retry.click()
        page.wait_for_function(
            "document.querySelectorAll('#log iframe').length === 1")
        assert attempts["form"] == 2
        browser.close()


@pytest.mark.parametrize(("button_id", "mode", "expected_conv", "visible_text"), [
    ("sessionContinueBtn", "continue_existing", "c_phone_existing", "risposta dal telefono"),
    ("sessionCurrentBtn", "activate_current", "c_windows_candidate", "messaggio locale Windows"),
    ("sessionDeclineBtn", None, "c_windows_candidate", "messaggio locale Windows"),
])
def test_session_conflict_has_three_semantic_choices(
        button_id, mode, expected_conv, visible_text):
    """La terza scelta adotta davvero la conversazione del vecchio device.

    La prova copre anche le due scelte preesistenti: attivare la conversazione
    locale e annullare senza mutare il server.
    """
    chat_html = _chat_html()
    calls: list[dict] = []

    def route_request(route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=chat_html)
        elif path == "/agent/session/register":
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({
                    "conflict": True,
                    "takeover_token": "takeover-browser-test",
                    "existing": {
                        "device_label": "Chrome Android",
                        "started_at": "2026-07-27T08:00:00Z",
                        "can_continue": True,
                    },
                }),
            )
        elif path == "/agent/session/takeover":
            payload = json.loads(request.post_data or "{}")
            calls.append(payload)
            selected = ("c_phone_existing"
                        if payload.get("mode") == "continue_existing"
                        else "c_windows_candidate")
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "device_token": "new-browser-token",
                    "conversation_id": selected,
                    "mode": payload.get("mode"),
                }),
            )
        elif path == "/agent/session/events":
            route.fulfill(status=200, content_type="text/event-stream", body="")
        elif path == "/agent/turns/recent":
            if "conversation_id=c_phone_existing" in parsed.query:
                body = {"turns": [{
                    "turn_id": "phone-turn-1",
                    "query": "domanda dal telefono",
                    "final_message": "risposta dal telefono",
                    "final_message_html": "",
                    "ts_start": 1,
                    "ts_end": 2,
                    "total_ms": 1000,
                    "in_flight": False,
                    "steps_summary": [],
                    "attachments": [],
                }]}
            else:
                body = {"turns": []}
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(body))
        else:
            route.fulfill(status=404, body="not found")

    local_record = [{
        "cls": "me",
        "text": "messaggio locale Windows",
        "meta": "",
        "ts": 1,
    }]
    init = """(() => {
      localStorage.setItem('metnos_conv_id:v2:%s', 'c_windows_candidate');
      localStorage.setItem(
        'metnos_chat_history:v3:%s:c_windows_candidate', JSON.stringify(%s));
      localStorage.removeItem('metnos_device_token:v2:%s');
    })();""" % (
        TEST_USER_SCOPE,
        TEST_USER_SCOPE,
        json.dumps(local_record),
        TEST_USER_SCOPE,
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        context.add_init_script(script=init)
        page = context.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        page.wait_for_function(
            "document.getElementById('sessionModal').classList.contains('show')")
        assert page.locator("#sessionModal .actions button").count() == 3
        page.locator("#" + button_id).click()
        page.wait_for_function(
            "!document.getElementById('sessionModal').classList.contains('show')")
        page.wait_for_function(
            "expected => document.getElementById('log').innerText.includes(expected)",
            arg=visible_text,
        )
        assert page.evaluate("localStorage.getItem(CONV_KEY)") == expected_conv
        if mode is None:
            assert calls == []
            assert page.locator("body").evaluate("el => el.classList.contains('readonly')")
        else:
            assert calls[-1]["mode"] == mode
            assert page.evaluate(
                "localStorage.getItem(DEVICE_TOKEN_KEY)"
            ) == "new-browser-token"
        browser.close()


def test_browser_storage_is_independent_for_each_user():
    """Uno stesso profilo browser non condivide stato chat tra due utenti."""
    chat_html = _chat_html(
        user_scope="user-b",
        migrate_legacy_storage=False,
    )

    def route_request(route):
        if not _route_session_and_history(route, chat_html):
            route.fulfill(status=404, body="not found")

    init = """(() => {
      localStorage.setItem('metnos_conv_id:v2:user-a', 'c_user_a_history');
      localStorage.setItem('metnos_device_token:v2:user-a', 'token-a');
      localStorage.setItem(
        'metnos_cmd_buffer:v2:user-a', JSON.stringify(['secret-command-a']));
      localStorage.setItem(
        'metnos_chat_history:v3:user-a:c_user_a_history',
        JSON.stringify([{cls:'me', text:'secret-history-a', ts:1}]));
      localStorage.setItem('metnos_conv_id', 'c_legacy_host_history');
      localStorage.setItem(
        'metnos_chat_history:v2:c_legacy_host_history',
        JSON.stringify([{cls:'me', text:'secret-legacy-host', ts:1}]));
      localStorage.setItem(
        'metnos_cmd_buffer', JSON.stringify(['secret-legacy-command']));
      localStorage.setItem('metnos_device_token', 'legacy-host-token');
    })();"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        context.add_init_script(script=init)
        page = context.new_page()
        page.route("**/*", route_request)
        page.goto("http://metnos.test/", wait_until="domcontentloaded")
        page.wait_for_function(
            "localStorage.getItem(DEVICE_TOKEN_KEY) === 'browser-test-token'"
        )

        assert page.evaluate("CHAT_USER_SCOPE") == "user-b"
        assert page.evaluate("conversationId") != "c_user_a_history"
        assert page.evaluate("deviceToken") == "browser-test-token"
        assert page.evaluate("loadCmdBuf()") == []
        assert page.evaluate("loadHistory()") == []
        assert "secret-history-a" not in page.locator("#log").inner_text()
        assert "secret-legacy-host" not in page.locator("#log").inner_text()

        # Lo stato dell'altro utente resta intatto e non viene adottato.
        assert page.evaluate(
            "localStorage.getItem('metnos_conv_id:v2:user-a')"
        ) == "c_user_a_history"
        assert page.evaluate(
            "localStorage.getItem('metnos_device_token:v2:user-a')"
        ) == "token-a"
        browser.close()

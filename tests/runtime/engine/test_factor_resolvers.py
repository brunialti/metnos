from __future__ import annotations

import asyncio
import time


class _FakeImap:
    def __init__(self, folders, messages=None):
        self.folders = {name: list(uids) for name, uids in folders.items()}
        self.messages = dict(messages or {})
        self.selected = "INBOX"

    def list(self):
        rows = [b'(\\HasNoChildren) "/" "INBOX"']
        if "INBOX.Spam" in self.folders:
            rows.append(b'(\\HasNoChildren \\Junk) "/" "INBOX.Spam"')
        return "OK", rows

    def select(self, folder, readonly=True):
        self.selected = str(folder).strip('"')
        return ("OK", [b"1"]) if self.selected in self.folders else ("NO", [])

    def uid(self, command, *args):
        if command == "SEARCH":
            joined = b" ".join(
                str(uid).encode() for uid in self.folders[self.selected])
            return "OK", [joined]
        if command == "FETCH":
            uid = int(args[0])
            payload = self.messages[(self.selected, uid)]
            meta = (
                f'{uid} (INTERNALDATE "13-Jul-2026 15:15:00 +0000" '
                f'BODY[] {{{len(payload)}}}'
            ).encode()
            return "OK", [(meta, payload), b")"]
        raise AssertionError(command)

    def close(self):
        return "OK", []

    def logout(self):
        return "BYE", []


def _mail(subject: str, sender: str, body: str) -> bytes:
    return (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        "Date: Mon, 13 Jul 2026 15:15:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode()


def test_factor_mailbox_binding_is_exact(monkeypatch):
    import mail_client

    monkeypatch.setattr(mail_client, "list_known_accounts", lambda: ["only"])
    monkeypatch.setattr(mail_client, "_account_creds", lambda _account: {
        "user": "alice@example.test",
    })

    assert mail_client.exact_account_for_address("ALICE@example.test") == "only"
    assert mail_client.exact_account_for_address("other@example.test") is None
    # Ordinary mail UX may retain its conservative single-host alias fallback.
    assert mail_client.account_for_address("other@example.test") == "only"


def test_email_factor_cursor_covers_inbox_and_server_marked_junk(monkeypatch):
    import mail_client
    from playwright_sidecar import factor_resolvers as fr

    fake = _FakeImap({"INBOX": [10, 11], "INBOX.Spam": [20]})
    monkeypatch.setattr(mail_client, "exact_account_for_address",
                        lambda _address: "mailbox")
    monkeypatch.setattr(mail_client, "open_imap", lambda *_a, **_kw: fake)

    cursor = fr._prepare_email_cursor_sync("alice@example.test")

    assert cursor["account"] == "mailbox"
    assert cursor["folders"] == {"INBOX": 11, "INBOX.Spam": 20}


def test_email_factor_uses_only_new_issuer_relevant_message(monkeypatch):
    import mail_client
    from playwright_sidecar import factor_resolvers as fr

    messages = {
        ("INBOX", 12): _mail(
            "Unrelated login code", "noreply@unrelated.test",
            "Your verification code is WRONG1"),
        ("INBOX.Spam", 21): _mail(
            "Example.com - AFREQG is your verification code",
            "noreply@auth.example.com",
            "AFREQG is your verification code"),
    }
    fake = _FakeImap(
        {"INBOX": [10, 11, 12], "INBOX.Spam": [20, 21]}, messages)
    monkeypatch.setattr(mail_client, "exact_account_for_address",
                        lambda _address: "mailbox")
    monkeypatch.setattr(mail_client, "open_imap", lambda *_a, **_kw: fake)

    result = fr._poll_email_factor_sync(
        "alice@example.test", "example.com", time.time(), {
            "account": "mailbox",
            "folders": {"INBOX": 11, "INBOX.Spam": 20},
        })

    assert result == fr.FactorResolution("found", "AFREQG")


def test_email_factor_ranks_explicit_subject_over_prose_after_code(monkeypatch):
    import mail_client
    from playwright_sidecar import factor_resolvers as fr

    message = _mail(
        "ZX9Q2A is your verification code", "noreply@auth.example.com",
        "Use your verification code with the sign-in screen.")
    fake = _FakeImap({"INBOX": [12]}, {("INBOX", 12): message})
    monkeypatch.setattr(mail_client, "exact_account_for_address",
                        lambda _address: "mailbox")
    monkeypatch.setattr(mail_client, "open_imap", lambda *_a, **_kw: fake)

    result = fr._poll_email_factor_sync(
        "alice@example.test", "example.com", time.time(), {
            "account": "mailbox", "folders": {"INBOX": 11},
        }, expected_length=6)

    assert result == fr.FactorResolution("found", "ZX9Q2A")
    assert result.diagnostics["candidate_count"] == 1
    assert fr._extract_codes(
        "Use your verification code with the sign-in screen.") == []


def test_email_factor_keeps_equal_strength_codes_ambiguous(monkeypatch):
    import mail_client
    from playwright_sidecar import factor_resolvers as fr

    message = _mail(
        "ZX9Q2A is your verification code; Q7R8S9 is your verification code",
        "noreply@auth.example.com", "Sign-in verification requested.")
    fake = _FakeImap({"INBOX": [12]}, {("INBOX", 12): message})
    monkeypatch.setattr(mail_client, "exact_account_for_address",
                        lambda _address: "mailbox")
    monkeypatch.setattr(mail_client, "open_imap", lambda *_a, **_kw: fake)

    result = fr._poll_email_factor_sync(
        "alice@example.test", "example.com", time.time(), {
            "account": "mailbox", "folders": {"INBOX": 11},
        }, expected_length=6)

    assert result.status == "ambiguous"
    assert result.code is None
    assert result.diagnostics["top_tie_count"] == 2


def test_email_factor_applies_form_length_before_disambiguation(monkeypatch):
    import mail_client
    from playwright_sidecar import factor_resolvers as fr

    message = _mail(
        "ZX9Q2A is your verification code; 1234 is your verification code",
        "noreply@auth.example.com", "Sign-in verification requested.")
    fake = _FakeImap({"INBOX": [12]}, {("INBOX", 12): message})
    monkeypatch.setattr(mail_client, "exact_account_for_address",
                        lambda _address: "mailbox")
    monkeypatch.setattr(mail_client, "open_imap", lambda *_a, **_kw: fake)

    result = fr._poll_email_factor_sync(
        "alice@example.test", "example.com", time.time(), {
            "account": "mailbox", "folders": {"INBOX": 11},
        }, expected_length=6)

    assert result == fr.FactorResolution("found", "ZX9Q2A")
    assert result.diagnostics["expected_length"] == 6


def test_email_factor_rejects_preexisting_or_wrong_issuer(monkeypatch):
    import mail_client
    from playwright_sidecar import factor_resolvers as fr

    old = _mail(
        "Example.com - OLD999 is your verification code",
        "noreply@example.com", "OLD999 is your verification code")
    wrong = _mail(
        "Other.test - NEW999 is your verification code",
        "noreply@other.test", "NEW999 is your verification code")
    fake = _FakeImap(
        {"INBOX": [11, 12]}, {("INBOX", 11): old, ("INBOX", 12): wrong})
    monkeypatch.setattr(mail_client, "exact_account_for_address",
                        lambda _address: "mailbox")
    monkeypatch.setattr(mail_client, "open_imap", lambda *_a, **_kw: fake)

    result = fr._poll_email_factor_sync(
        "alice@example.test", "example.com", time.time(), {
            "account": "mailbox", "folders": {"INBOX": 11},
        })

    assert result.status == "missing"


def test_passwordless_email_factor_is_bounded_segmented_and_resumable(
        monkeypatch):
    from playwright_sidecar import credential_injection as ci
    from playwright_sidecar import factor_resolvers as fr

    email = "alice@example.test"
    code = "AFREQG"
    monkeypatch.setattr(ci.credentials, "load", lambda domain: {
        "email": email,
        "scopes": [fr.EMAIL_FACTOR_SCOPE],
        "session_cookie_names": ["SESSION_ID"],
    } if domain == "login.example.test" else None)
    monkeypatch.setattr(ci.credentials, "fingerprint", lambda _domain: "fp")
    monkeypatch.setattr(ci.sites_audit, "record", lambda *_a, **_kw: None)

    prepared = []
    resolved = []

    async def prepare(address, *, allowed):
        prepared.append((address, allowed))
        return {"account": "mailbox", "folders": {"INBOX": 40}}

    async def resolve(**kwargs):
        resolved.append(kwargs)
        return fr.FactorResolution("found", code)

    monkeypatch.setattr(fr, "prepare_email_factor", prepare)
    monkeypatch.setattr(fr, "resolve_email_factor", resolve)

    class Context:
        authenticated = False

        async def cookies(self):
            if self.authenticated:
                return [{"name": "SESSION_ID", "domain": "login.example.test",
                         "path": "/", "value": "new-session"}]
            return []

    context = Context()

    class Fields:
        def __init__(self, page):
            self.page = page
            self.index = 0

        def nth(self, index):
            self.index = index
            return self

        async def fill(self, value, **_kw):
            self.page.otp[self.index] = value

    class Page:
        def __init__(self):
            self.state = "username"
            self.url = "https://login.example.test/auth"
            self.otp = [""] * 6
            self.clicks = []

        async def evaluate(self, script):
            if script == ci._HAS_PASSWORD_JS:
                return False
            if script == ci._LOCATE_USERNAME_STAGE_JS:
                return ({"found": True, "actionResolved": self.url,
                         "hasSubmit": True}
                        if self.state == "username" else {"found": False})
            if script == ci._CURRENT_USERNAME_ACTION_JS:
                return self.url
            if script == ci._DETECT_OTP_JS:
                return self.state == "otp"
            if script in (ci._DETECT_CAPTCHA_JS,
                          ci._PASSWORD_REJECTED_JS):
                return False
            if script == ci._LOCATE_OTP_FORM_JS:
                return {"found": self.state == "otp",
                        "actionResolved": self.url, "hasSubmit": True,
                        "segmented": True, "fieldCount": 6,
                        "expectedLength": 6, "numericOnly": False}
            if script == ci._CURRENT_OTP_ACTION_JS:
                return self.url
            if isinstance(script, str) and "document.body" in script:
                return "We sent a verification code to your email address"
            return None

        async def fill(self, _selector, _value, **_kw):
            return None

        async def click(self, selector, **kwargs):
            self.clicks.append((selector, kwargs))
            if selector == '[data-metnos-user-submit="1"]':
                self.state = "otp"
            elif selector == '[data-metnos-otp-submit="1"]':
                assert "".join(self.otp) == code
                self.state = "done"
                self.url = "https://login.example.test/home"
                context.authenticated = True

        async def press(self, *_a, **_kw):
            raise AssertionError("a submit button is available")

        async def wait_for_load_state(self, *_a, **_kw):
            return None

        async def wait_for_timeout(self, _ms):
            return None

        def locator(self, selector):
            if selector == '[data-metnos-otp="1"]':
                return Fields(self)
            raise AttributeError(selector)

    phases = []
    page = Page()
    out = asyncio.run(ci.perform_login(
        page=page, context=context, domain="login.example.test",
        form_hint=None, owner="alice", session_id="sid-email-factor",
        op_timeout_s=1, total_timeout_s=10, factor_state={},
        checkpoint=lambda stage: phases.append(stage)))

    assert out == {"ok": True, "logged_in": True, "reason_code": None}
    assert prepared == [(email, True)]
    assert resolved and resolved[0]["issuer_domain"] == "login.example.test"
    assert resolved[0]["cursor"]["folders"] == {"INBOX": 40}
    assert resolved[0]["expected_length"] == 6
    assert resolved[0]["numeric_only"] is False
    assert all(kwargs.get("no_wait_after") is True
               for _selector, kwargs in page.clicks)
    assert "factor_pending" in phases
    assert phases[-1] == "complete"


def test_factor_pending_session_pauses_idle_ttl(monkeypatch):
    from playwright_sidecar import session_broker as sb

    entry = {
        "last_used": time.time() - sb._TTL_IDLE_S - 10,
        "factor_pending": True,
        "gate_pending": False,
    }
    monkeypatch.setitem(sb._sessions, "sid-factor-pending", entry)
    try:
        assert sb._validate("sid-factor-pending") is entry
    finally:
        sb._sessions.pop("sid-factor-pending", None)


def test_broker_timeout_preserves_observed_factor_state(monkeypatch):
    from playwright_sidecar import session_broker as sb

    class Page:
        url = "https://login.example.test/verify"

    async def timed_out_login(**kwargs):
        await kwargs["checkpoint"]("factor_pending")
        raise asyncio.TimeoutError

    async def classify(_page):
        return "two_factor_required"

    async def no_screenshot(_entry):
        return None

    monkeypatch.setattr(sb.credential_injection, "perform_login",
                        timed_out_login)
    monkeypatch.setattr(sb.credential_injection, "classify_login_surface",
                        classify)
    monkeypatch.setattr(sb, "_capture_screenshot", no_screenshot)
    entry = {
        "page": Page(), "context": object(), "owner": "alice",
        "domain": "example.test", "last_used": time.time(),
        "gate_pending": False, "factor_pending": False,
        "credential_mode": "default", "authenticated": False,
        "pending_actions": {}, "approved_actions": set(),
        "action_replans": {}, "lock": asyncio.Lock(),
    }
    monkeypatch.setitem(sb._sessions, "sid-factor-timeout", entry)
    try:
        out = asyncio.run(sb.op_login(
            session_id="sid-factor-timeout", owner="alice"))
        assert out["reason_code"] == "two_factor_required"
        assert out["error_class"] == "timeout"
        assert entry["factor_pending"] is True
        assert entry["login_flow"]["phase"] == "factor_pending"
    finally:
        sb._sessions.pop("sid-factor-timeout", None)


def test_factor_code_resume_bypasses_pending_state_and_clears_it(monkeypatch):
    from playwright_sidecar import session_broker as sb

    class Page:
        url = "https://login.example.test/verify"

    async def completed_login(**kwargs):
        assert kwargs["one_time_code"] == "123456"
        await kwargs["checkpoint"]("complete")
        return {"ok": True, "logged_in": True, "reason_code": None}

    monkeypatch.setattr(sb.credential_injection, "perform_login",
                        completed_login)
    entry = {
        "page": Page(), "context": object(), "owner": "alice",
        "domain": "example.test", "last_used": time.time(),
        "gate_pending": False, "factor_pending": True,
        "factor_started": time.time(), "credential_mode": "default",
        "authenticated": False, "pending_actions": {},
        "approved_actions": set(), "action_replans": {},
        "login_flow": {"domain": "example.test", "started": time.time(),
                       "steps": 1, "factor_state": {}},
        "lock": asyncio.Lock(),
    }
    monkeypatch.setitem(sb._sessions, "sid-factor-resume", entry)
    try:
        out = asyncio.run(sb.op_login(
            session_id="sid-factor-resume", owner="alice",
            one_time_code="123456"))
        assert out["logged_in"] is True
        assert entry["authenticated"] is True
        assert entry["factor_pending"] is False
    finally:
        sb._sessions.pop("sid-factor-resume", None)

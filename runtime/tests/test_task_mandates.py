from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))


def _isolated_credentials(tmp_path, monkeypatch):
    import credentials
    monkeypatch.setattr(credentials, "ADMIN_KEY_PATH", tmp_path / "admin.key")
    monkeypatch.setattr(credentials, "CRED_DIR", tmp_path / "credentials")
    credentials.ADMIN_KEY_PATH.write_text(secrets.token_hex(32))
    return credentials


def test_sites_mandate_uses_only_verified_exact_hosts(tmp_path, monkeypatch):
    import task_mandates

    credentials = _isolated_credentials(tmp_path, monkeypatch)
    credentials.store("telepass.com", {
        "username": "u", "password": "p", "scopes": ["sites.read"],
    })
    audit = tmp_path / "sites.jsonl"
    events = [
        {"event": "session_open", "owner": "host", "session_id": "s1",
         "domain": "telepass.com",
         "allowlist": ["telepass.com", "www.telepass.com"]},
        {"event": "allowlist_change", "owner": "host", "session_id": "s1",
         "domain": "telepass.com", "added_host": "login.telepass.com",
         "source": "approved_blocked_resource"},
        {"event": "credential_origin_approval", "owner": "host",
         "session_id": "s1", "domain": "telepass.com",
         "origin": "login.telepass.com", "outcome": True},
        {"event": "allowlist_change", "owner": "host", "session_id": "s1",
         "domain": "telepass.com", "added_host": "tracker.example",
         "source": "observed_only"},
    ]
    audit.write_text("\n".join(json.dumps(item) for item in events) + "\n")

    mandate = task_mandates.build_for_task(
        "ogni giorno controlla le fatture su telepass.com", "host",
        audit_path=audit)
    binding = mandate["capabilities"]["sites"]["bindings"][0]

    assert binding["allowed_hosts"] == [
        "login.telepass.com", "telepass.com", "www.telepass.com"]
    assert binding["credential_origins"] == ["login.telepass.com"]
    assert "login" in binding["operations"]
    assert "tracker.example" not in binding["allowed_hosts"]


def test_explicit_hosts_never_treats_url_paths_as_domains():
    import task_mandates

    assert task_mandates.explicit_hosts(
        "leggi https://rocm.docs.amd.com/en/latest/versions.html"
    ) == ["rocm.docs.amd.com"]
    assert task_mandates.explicit_hosts(
        "leggi docs.example.test/releases/archive.json?page=next.example"
    ) == ["docs.example.test"]
    assert task_mandates.explicit_hosts(
        "confronta example.com e status.example.org"
    ) == ["example.com", "status.example.org"]


def test_credential_scope_is_rechecked_and_revocable(tmp_path, monkeypatch):
    import credential_mandates

    credentials = _isolated_credentials(tmp_path, monkeypatch)
    credentials.store("x.test", {
        "username": "u", "password": "p", "scopes": ["sites.read"],
    })
    assert credential_mandates.has_scope("x.test", "sites.read")
    payload = credentials.load("x.test")
    payload["scopes"] = []
    credentials.store("x.test", payload)
    assert not credential_mandates.has_scope("x.test", "sites.read")


def test_credential_mandate_is_the_interactive_default(tmp_path, monkeypatch):
    import credential_mandates

    credentials = _isolated_credentials(tmp_path, monkeypatch)
    credentials.store("x.test", {
        "username": "u", "password": "p", "scopes": ["sites.read"],
    })
    audit = tmp_path / "sites.jsonl"
    audit.write_text(json.dumps({
        "event": "session_open", "owner": "host", "session_id": "s1",
        "domain": "x.test", "allowlist": ["x.test", "www.x.test"],
    }) + "\n")

    binding = credential_mandates.resolve_sites_binding(
        "host", "www.x.test", audit_path=audit)

    assert binding["credential_default"] is True
    assert binding["operations"] == ["login", "navigate", "open", "read"]
    assert binding["allowed_hosts"] == ["www.x.test", "x.test"]

    payload = credentials.load("x.test")
    payload["scopes"] = []
    credentials.store("x.test", payload)
    assert credential_mandates.resolve_sites_binding(
        "host", "www.x.test", audit_path=audit) is None


def test_unscoped_exact_topology_does_not_shadow_scoped_binding(
        tmp_path, monkeypatch):
    import credential_mandates

    credentials = _isolated_credentials(tmp_path, monkeypatch)
    credentials.store("x.test", {
        "username": "u", "password": "p", "scopes": ["sites.read"],
    })
    audit = tmp_path / "sites.jsonl"
    events = [
        {"event": "session_open", "owner": "host", "session_id": "root",
         "domain": "x.test", "allowlist": ["x.test", "www.x.test"]},
        {"event": "session_open", "owner": "host", "session_id": "exact",
         "domain": "www.x.test", "allowlist": ["www.x.test"]},
    ]
    audit.write_text("\n".join(json.dumps(item) for item in events) + "\n")

    binding = credential_mandates.resolve_sites_binding(
        "host", "www.x.test", audit_path=audit)

    assert binding is not None
    assert binding["root_host"] == "x.test"
    assert binding["credential_default"] is True


def test_query_can_narrow_credential_mandate_without_a_command_grammar():
    import credential_mandates

    assert credential_mandates.site_mode_for_query(
        "apri example.com senza usare le credenziali"
    ) == credential_mandates.SITE_MODE_NONE
    assert credential_mandates.site_mode_for_query(
        "open example.com without credentials"
    ) == credential_mandates.SITE_MODE_NONE
    assert credential_mandates.site_mode_for_query(
        "accedi a example.com e leggi le fatture"
    ) == credential_mandates.SITE_MODE_DEFAULT


def test_new_site_credentials_choose_mandate_before_storage(tmp_path,
                                                            monkeypatch):
    import importlib.util

    path = RUNTIME.parent / "executors" / "set_credentials" / "set_credentials.py"
    spec = importlib.util.spec_from_file_location("_set_credentials_mandate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    monkeypatch.setattr(module._cred, "ADMIN_KEY_PATH", tmp_path / "admin.key")
    monkeypatch.setattr(module._cred, "CRED_DIR", tmp_path / "credentials")
    module._cred.ADMIN_KEY_PATH.write_text(secrets.token_hex(32))

    first = module.invoke({
        "binding": "x.test",
        "fields": {"username": "alice", "password": "secret"},
    })
    assert first["decision"] == "needs_inputs"
    step = first["needs_inputs"]["dialog"][-1]
    assert step["var"] == "credential_mandate"
    assert step["schema"]["kind"] == "choice"
    pending_id = first["needs_inputs"]["on_complete"]["args_base"]["pending_id"]
    assert module._cred.load("x.test") is None

    second = module.invoke({
        "binding": "x.test", "pending_id": pending_id,
        "credential_mandate": "sites.read", "_mandate_form": True,
    })
    assert second["ok"] and second["results"][0]["scopes"] == ["sites.read"]
    stored = module._cred.load("x.test")
    assert stored["username"] == "alice" and stored["password"] == "secret"
    assert stored["scopes"] == ["sites.read"]

    update = module.invoke({"binding": "x.test"})
    assert update["decision"] == "needs_inputs"
    revoked = module.invoke({
        "binding": "x.test", "credential_mandate": "interactive",
        "_mandate_form": True})
    assert revoked["ok"] and module._cred.load("x.test")["scopes"] == []
    assert module._cred.load("x.test")["password"] == "secret"

    # A planner-provided profile cannot bypass the form.
    bypass = module.invoke({
        "binding": "x.test", "credential_mandate": "sites.read"})
    assert bypass["decision"] == "needs_inputs"
    assert module._cred.load("x.test")["scopes"] == []


def test_set_credentials_manifest_keeps_mandate_choice_form_only():
    import tomllib

    manifest = tomllib.loads((
        RUNTIME.parent / "executors" / "set_credentials" / "manifest.toml"
    ).read_text(encoding="utf-8"))
    properties = manifest["args"]["properties"]
    assert "credential_mandate" not in properties


def test_recurring_task_persists_integrity_checked_mandate(tmp_path,
                                                            monkeypatch):
    import recurring_tasks
    import task_mandates

    db = tmp_path / "recurring.sqlite"
    monkeypatch.setattr(recurring_tasks, "DB_PATH", db)
    mandate = {
        "version": task_mandates._VERSION,
        "query_hash": task_mandates._query_hash("leggi x.test"),
        "capabilities": {"sites": {"bindings": []}},
    }
    row = recurring_tasks.register_user_task(
        label="x", when="every_30m", query="leggi x.test",
        actor="host", channel="telegram", mandates=mandate)
    assert json.loads(row["mandates"]) == mandate
    assert task_mandates.load_for_task(
        row["name"], "host", db_path=db)["query_hash"] == mandate["query_hash"]

    conn = sqlite3.connect(db)
    conn.execute("UPDATE recurring_tasks SET query='different' WHERE name=?",
                 (row["name"],))
    conn.commit()
    conn.close()
    assert task_mandates.load_for_task(
        row["name"], "host", db_path=db) is None


def test_recurring_task_init_migrates_and_backfills_legacy_db(tmp_path,
                                                               monkeypatch):
    import recurring_tasks

    db = tmp_path / "legacy-recurring.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE recurring_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            schedule TEXT NOT NULL,
            query TEXT NOT NULL,
            actor TEXT NOT NULL,
            channel TEXT NOT NULL,
            chat_id TEXT,
            label TEXT,
            created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',
            enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO recurring_tasks
            (name, schedule, query, actor, channel, label)
        VALUES
            ('daily_example', 'daily@08:00', 'leggi example.com',
             'host', 'telegram', 'daily example');
    """)
    conn.close()

    monkeypatch.setattr(recurring_tasks, "DB_PATH", db)
    recurring_tasks.init_db()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(recurring_tasks)")}
    row = conn.execute(
        "SELECT mandates FROM recurring_tasks WHERE name='daily_example'"
    ).fetchone()
    conn.close()

    assert "mandates" in columns
    mandate = json.loads(row["mandates"])
    assert mandate["version"] == 2
    assert mandate["capabilities"]["sites"]["bindings"][0][
        "root_host"] == "example.com"


def test_scheduled_redirect_uses_task_hosts_without_dialog(monkeypatch):
    from playwright_sidecar import session_broker as sb

    class Page:
        url = "about:blank"
        async def goto(self, _url, **_kwargs):
            self.url = "https://www.x.test/home"
        async def title(self):
            return "X"

    class Context:
        def __init__(self):
            self.page = Page()
        async def add_init_script(self, _script):
            return None
        async def route(self, *_args):
            return None
        async def new_page(self):
            return self.page
        async def close(self):
            return None

    class Browser:
        async def new_context(self, **_kwargs):
            return Context()

    binding = {
        "task_name": "read_x", "root_host": "x.test",
        "entry_hosts": ["x.test"],
        "allowed_hosts": ["x.test", "www.x.test"],
        "credential_origins": [],
        "operations": ["open", "navigate", "read"],
        "query": "leggi x.test",
    }
    monkeypatch.setattr(sb, "_browser", Browser())
    monkeypatch.setattr(
        sb.task_mandates, "sites_binding",
        lambda task, owner, host: binding
        if (task, owner, host) == ("read_x", "alice", "x.test") else None)
    sb._sessions.clear()
    sb._pending_opens.clear()

    out = asyncio.run(sb.op_open(
        owner="alice", url="https://x.test", task_name="read_x"))

    assert out["ok"] and not out.get("approval_required")
    assert sb._sessions[out["session_id"]]["task_mandate"] == binding
    asyncio.run(sb.op_close(owner="alice", session_id=out["session_id"]))


def test_interactive_query_restriction_is_sticky_on_browser_session(
        monkeypatch):
    from playwright_sidecar import session_broker as sb

    class Page:
        url = "about:blank"
        async def goto(self, _url, **_kwargs):
            self.url = "https://x.test/home"
        async def title(self):
            return "X"

    class Context:
        def __init__(self):
            self.page = Page()
        async def add_init_script(self, _script):
            return None
        async def route(self, *_args):
            return None
        async def new_page(self):
            return self.page
        async def close(self):
            return None

    class Browser:
        async def new_context(self, **_kwargs):
            return Context()

    binding = {
        "root_host": "x.test", "allowed_hosts": ["x.test"],
        "credential_origins": [],
        "operations": ["login", "navigate", "open", "read"],
        "credential_default": True,
    }
    monkeypatch.setattr(sb, "_browser", Browser())
    monkeypatch.setattr(
        sb.credential_mandates, "resolve_sites_binding",
        lambda owner, host: binding)
    async def fail_if_called(**_kwargs):
        raise AssertionError("credential injection must remain disabled")
    monkeypatch.setattr(
        sb.credential_injection, "perform_login", fail_if_called)
    sb._sessions.clear()
    sb._pending_opens.clear()

    opened = asyncio.run(sb.op_open(
        owner="alice", url="https://x.test", credential_mode="none"))
    entry = sb._sessions[opened["session_id"]]
    assert entry["credential_mandate"] is None
    assert entry["credential_mode"] == "none"

    login = asyncio.run(sb.op_login(
        owner="alice", session_id=opened["session_id"],
        credential_mode="default"))
    assert login["reason_code"] == "credential_use_disabled"
    assert login["error_class"] == "mandate_scope_exceeded"
    asyncio.run(sb.op_close(
        owner="alice", session_id=opened["session_id"]))


def test_task_read_scope_allows_goal_but_never_post(monkeypatch):
    from playwright_sidecar import session_broker as sb

    monkeypatch.setattr(
        sb.credential_mandates, "has_scope",
        lambda binding, scope: (binding, scope) == ("x.test", "sites.read"))
    entry = {
        "domain": "x.test", "authenticated": True,
        "task_mandate": {
            "root_host": "x.test", "allowed_hosts": ["x.test"],
            "credential_origins": ["login.x.test"],
            "operations": ["login", "navigate", "open", "read"],
            "query": "cerca fatture 2026 su x.test",
        },
    }
    safe = {
        "kind": "goal_navigation", "primitive": "click",
        "goal_flow_key": "flow", "original_action": "cerca fatture 2026",
        "destination_host": "x.test",
        "candidate": {"download": False, "secret_input": False,
                      "form_method": "GET"},
        "sensitivity_reasons": ["navigation", "tainted_turn"],
    }
    assert sb._mandate_allows_plan(entry, safe)
    unsafe = {**safe,
              "candidate": {**safe["candidate"], "form_method": "POST"},
              "sensitivity_reasons": ["post", "tainted_turn"]}
    assert not sb._mandate_allows_plan(entry, unsafe)


def test_www_login_uses_verified_root_credential_scope(monkeypatch):
    from playwright_sidecar import session_broker as sb

    monkeypatch.setattr(
        sb.credential_mandates, "has_scope",
        lambda binding, scope: (binding, scope) ==
        ("example.test", "sites.read"))
    entry = {
        "domain": "example.test", "authenticated": False,
        "login_flow": {"domain": "www.example.test"},
        "credential_mandate": {
            "root_host": "example.test",
            "allowed_hosts": ["example.test", "www.example.test"],
            "credential_origins": [],
            "operations": ["login", "navigate", "open", "read"],
            "credential_default": True,
        },
    }
    plan = {
        "login_flow": True, "login_procedure": "login",
        "primitive": "click", "destination_host": "www.example.test",
        "candidate": {"download": False, "secret_input": False,
                      "form_method": "GET"},
        "sensitivity_reasons": ["navigation", "low_confidence"],
        "model_selected": True,
    }

    assert sb._mandate_allows_plan(entry, plan)


def test_scheduled_scope_carries_task_identity():
    from treated_issues_guard import scheduled_task_name, scheduled_turn_scope

    assert scheduled_task_name() == ""
    with scheduled_turn_scope(task_name="daily_invoices"):
        assert scheduled_task_name() == "daily_invoices"
    assert scheduled_task_name() == ""

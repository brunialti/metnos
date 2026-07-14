"""P1 stealth (ADR 0191) — registro tecniche, due-browser routing, ceiling,
pref, locale/timezone derivati. Test di plumbing SENZA browser reale: il routing
per-sessione e' verificato tramite un BrowserProvider mock che cattura lo stealth
effettivo; il lazy-launch/health lato server.py sono coperti dall'avvio del
sidecar (smoke) e restano E2E opt-in.
"""
from __future__ import annotations

import asyncio

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Registro tecniche ────────────────────────────────────────────────────────

def test_stealth_registry_launch_args_and_gating():
    from playwright_sidecar import stealth

    on = ["--base"]
    stealth.apply_launch_args(on, stealth=True)
    assert "--disable-blink-features=AutomationControlled" in on
    # idempotente
    stealth.apply_launch_args(on, stealth=True)
    assert on.count("--disable-blink-features=AutomationControlled") == 1

    off = ["--base"]
    stealth.apply_launch_args(off, stealth=False)
    assert off == ["--base"]  # nessun flag anti-rilevamento nel default

    assert stealth.technique_enabled("ua_override", stealth=True) is True
    assert stealth.technique_enabled("ua_override", stealth=False) is False
    assert stealth.technique_enabled("human_delays", stealth=True) is True
    # mobile_emulation e' on_opt: OFF nel profilo binario
    assert stealth.technique_enabled("mobile_emulation", stealth=True) is False


# ── Context kwargs: default onesto, locale/timezone derivati (H1) ────────────

def test_context_kwargs_default_is_native(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.setenv("METNOS_LANG", "it")
    kw = sb._context_kwargs(stealth=False)
    assert "user_agent" not in kw          # UA nativo nel default
    assert kw["locale"] == "it-IT"         # derivato da lang, non costante
    assert kw["service_workers"] == "block"


def test_context_kwargs_stealth_adds_ua(monkeypatch):
    from playwright_sidecar import session_broker as sb
    monkeypatch.delenv("METNOS_SITES_USER_AGENT", raising=False)
    kw = sb._context_kwargs(stealth=True)
    assert "user_agent" in kw and kw["user_agent"]


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


def _run_open_capturing(sb, monkeypatch, *, requested_stealth, ceiling):
    captured = {}

    async def _provider(stealth=False):
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
        owner="stealth-test", url="https://x.test", stealth=requested_stealth))
    return captured, audited, res


def test_op_open_routes_effective_stealth_true(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, res = _run_open_capturing(
        sb, monkeypatch, requested_stealth=True, ceiling=True)
    assert captured.get("stealth") is True
    assert "stealth_denied_by_ceiling" not in audited
    assert res["ok"] is False  # interrotto dopo la scelta browser (mock)


def test_op_open_ceiling_downgrades_and_audits(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, _ = _run_open_capturing(
        sb, monkeypatch, requested_stealth=True, ceiling=False)
    assert captured.get("stealth") is False          # forzato honest
    assert "stealth_denied_by_ceiling" in audited     # audit, non errore


def test_op_open_default_is_honest(monkeypatch):
    from playwright_sidecar import session_broker as sb
    captured, audited, _ = _run_open_capturing(
        sb, monkeypatch, requested_stealth=False, ceiling=True)
    assert captured.get("stealth") is False
    assert "stealth_denied_by_ceiling" not in audited


# ── Pref sites_stealth (M1) ──────────────────────────────────────────────────

def test_pref_sites_stealth_in_closed_vocab():
    import users
    assert "sites_stealth" in users.PREF_KEYS
    assert users.PREF_ALLOWED["sites_stealth"] == ("on", "off")


# ── open_sites: validazione + passaggio stealth a session_open (M5) ──────────

def _load_open_sites():
    path = (Path(__file__).resolve().parents[2]
            / "executors/open_sites/open_sites.py")
    spec = importlib.util.spec_from_file_location("_open_sites_stealth_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_open_sites_passes_stealth_bool(monkeypatch):
    module = _load_open_sites()
    seen = {}

    def _fake_open(**kw):
        seen.update(kw)
        return {"ok": True, "session_id": "s1", "url": kw["url"], "title": ""}

    monkeypatch.setattr(module.session_client, "session_open", _fake_open)
    out = module.invoke({"urls": ["https://x.test"], "_stealth": "on"})
    assert out["ok"] and seen.get("stealth") is True

    seen.clear()
    out = module.invoke({"urls": ["https://x.test"], "_stealth": "off"})
    assert out["ok"] and seen.get("stealth") is False

    # default: nessun _stealth → off
    seen.clear()
    out = module.invoke({"urls": ["https://x.test"]})
    assert out["ok"] and seen.get("stealth") is False


def test_open_sites_rejects_invalid_stealth(monkeypatch):
    module = _load_open_sites()
    monkeypatch.setattr(module.session_client, "session_open",
                        lambda **_kw: (_ for _ in ()).throw(
                            AssertionError("non deve aprire")))
    out = module.invoke({"urls": ["https://x.test"], "_stealth": "maybe"})
    assert out["ok"] is False and out["error_class"] == "invalid_args"

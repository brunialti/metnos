"""P2 origine credenziale (ADR 0191 §4) — normalizzazione, regola http-privato,
derivazione di migrazione, autorizzazione a match esatto della tupla."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib.util

import sites_origin as so


def _load_set_credentials():
    path = (Path(__file__).resolve().parents[2]
            / "executors/set_credentials/set_credentials.py")
    spec = importlib.util.spec_from_file_location("_set_credentials_p2_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# ── Normalizzazione + scheme/porta ──────────────────────────────────────────

def test_normalize_explicit_port_and_scheme():
    assert so.normalize_origin("https", "example.com") == "https://example.com:443"
    assert so.normalize_origin("https", "example.com", 8443) == "https://example.com:8443"
    # http su host PUBBLICO = rifiutato
    assert so.normalize_origin("http", "example.com") is None
    # scheme sconosciuto
    assert so.normalize_origin("ftp", "example.com") is None


def test_http_allowed_only_on_local_private():
    assert so.normalize_origin("http", "192.168.1.10") == "http://192.168.1.10:80"
    assert so.normalize_origin("http", "127.0.0.1") == "http://127.0.0.1:80"
    assert so.normalize_origin("http", "localhost") == "http://localhost:80"
    assert so.normalize_origin("http", "10.0.0.5") == "http://10.0.0.5:80"
    assert so.normalize_origin("http", "169.254.1.1") == "http://169.254.1.1:80"
    assert so.normalize_origin("http", "nas.local") == "http://nas.local:80"
    # host pubblico http -> None
    assert so.normalize_origin("http", "8.8.8.8") is None


def test_host_normalization_trailing_dot_case_ipv6():
    assert so.origin_of_url("https://Example.COM./login") == "https://example.com:443"
    assert so.origin_of_url("https://[2001:DB8::1]/x") == "https://[2001:db8::1]:443"
    assert so.origin_of_url("http://[::1]:8080/") == "http://[::1]:8080"


def test_idna_punycode():
    # host non-ASCII -> punycode
    o = so.origin_of_url("https://bücher.example/")
    assert o == "https://xn--bcher-kva.example:443"


def test_distinct_scheme_and_port():
    assert so.origin_of_url("http://h.local") != so.origin_of_url("https://h.local")
    assert so.origin_of_url("https://x.com:8443") != so.origin_of_url("https://x.com")


def test_origin_of_bad_url():
    assert so.origin_of_url("file:///etc/passwd") is None
    assert so.origin_of_url("not a url") is None
    assert so.origin_of_url("") is None


# ── Derivazione di migrazione (apex ↔ www) ──────────────────────────────────

def test_migration_public_adds_www_counterpart():
    out = so.derive_default_origins("amazon.it")
    assert set(out) == {"https://amazon.it:443", "https://www.amazon.it:443"}
    # da www.D deriva anche l'apex
    out = so.derive_default_origins("www.amazon.it")
    assert set(out) == {"https://www.amazon.it:443", "https://amazon.it:443"}


def test_migration_no_www_for_ip_or_single_label():
    assert so.derive_default_origins("192.168.1.10") == ["http://192.168.1.10:80"]
    # single label (no dot) -> nessuna controparte www
    assert so.derive_default_origins("intranet") == ["https://intranet:443"]


def test_migration_local_is_http():
    assert so.derive_default_origins("127.0.0.1") == ["http://127.0.0.1:80"]
    assert so.derive_default_origins("router.local") == ["http://router.local:80"]


# ── authorized_origins: payload vs migrazione ───────────────────────────────

def test_authorized_prefers_payload():
    payload = {"credential_origins": ["https://accounts.example.com:443"]}
    assert so.authorized_origins(payload, "example.com") == [
        "https://accounts.example.com:443"]


def test_authorized_falls_back_to_migration():
    # chiave ASSENTE (record legacy) → migrazione apex+www
    assert so.authorized_origins({}, "amazon.it") == [
        "https://amazon.it:443", "https://www.amazon.it:443"]
    assert so.authorized_origins(None, "amazon.it") == [
        "https://amazon.it:443", "https://www.amazon.it:443"]
    assert so.authorized_origins({"other": 1}, "amazon.it") == [
        "https://amazon.it:443", "https://www.amazon.it:443"]


def test_explicit_empty_or_invalid_is_deny_all_not_migration():
    # fix adversarial #3: chiave PRESENTE ma vuota/invalida = deny-all fail-closed,
    # MAI allargamento alla migrazione.
    assert so.authorized_origins({"credential_origins": []}, "amazon.it") == []
    assert so.authorized_origins(
        {"credential_origins": ["http://amazon.it"]}, "amazon.it") == []
    # una valida + una invalida: resta solo la valida (l'invalida non allarga)
    assert so.authorized_origins(
        {"credential_origins": ["https://accounts.amazon.it", "http://amazon.it"]},
        "amazon.it") == ["https://accounts.amazon.it:443"]


def test_set_credentials_rejects_empty_origins_list():
    module = _load_set_credentials()
    _norm, err = module._normalize_origins([])
    assert err and "at least one" in err
    _norm, err = module._normalize_origins(["http://public.example"])
    assert err  # all-invalid → errore (non lista vuota silenziosa)


# ── authorize: match esatto, email-first su www, alias non registrato ───────

def test_authorize_exact_match_and_www_email_first():
    origins = so.derive_default_origins("amazon.it")   # apex + www
    # email-first servito su www: autorizzato (www e' nella migrazione)
    assert so.authorize("https://www.amazon.it/ap/signin", origins) is True
    assert so.authorize("https://amazon.it/login", origins) is True


def test_authorize_rejects_unregistered_alias_and_scheme():
    origins = ["https://amazon.it:443"]     # SENZA www esplicito
    assert so.authorize("https://www.amazon.it/x", origins) is False   # alias non registrato
    assert so.authorize("http://amazon.it/x", origins) is False        # http (public) rifiutato
    assert so.authorize("https://amazon.it:8443/x", origins) is False  # porta diversa
    assert so.authorize("https://evil.amazon.it/x", origins) is False  # sottodominio


def test_authorize_oneshot_extra_not_persisted():
    origins = ["https://shop.example:443"]
    # senza extra: l'IdP delegato non e' autorizzato
    assert so.authorize("https://idp.federated.example/auth", origins) is False
    # con extra one-shot: autorizzato solo per quel flusso
    assert so.authorize("https://idp.federated.example/auth", origins,
                        extra="https://idp.federated.example") is True


# ── set_credentials: validazione + default di migrazione (write side) ───────

def test_set_credentials_normalize_origins_helper():
    module = _load_set_credentials()
    ok, err = module._normalize_origins(
        ["https://accounts.example.com", "http://192.168.1.10"])
    assert err is None
    assert ok == ["https://accounts.example.com:443", "http://192.168.1.10:80"]
    # http su host pubblico = rifiutato
    bad, err = module._normalize_origins(["http://example.com"])
    assert bad is None and err
    # non fornito = None (userera' la migrazione)
    assert module._normalize_origins(None) == (None, None)


def test_set_credentials_rejects_invalid_origins_before_dialog(monkeypatch):
    module = _load_set_credentials()
    out = module.invoke({
        "binding": "example.com",
        "fields": {"username": "u", "password": "p"},
        "credential_origins": ["http://example.com"],  # http pubblico invalido
    })
    assert out["ok"] is False and out["error_class"] == "invalid_args"

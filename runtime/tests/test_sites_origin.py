"""P2 origine credenziale (ADR 0191 §4) — normalizzazione, regola http-privato,
autorita' del fill: esplicita (match esatto fail-closed) vs default stesso-sito
(sottodomini first-party, contratto storico dei binding per nome-sito)."""
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


# ── Default stesso-sito (chiave ASSENTE = contratto storico) ────────────────

def _auth(url: str, payload, domain: str, *, extra=None) -> bool:
    return so.origin_authorized(so.origin_of_url(url), payload, domain,
                                extra=extra)


def test_same_site_first_party_subdomain_no_gate():
    # Regressione turn 025c53fa: il login first-party (account.booking.com per
    # booking.com) NON deve chiedere consenso.
    assert _auth("https://account.booking.com/sign-in", None, "booking.com")
    assert _auth("https://www.amazon.it/ap/signin", {}, "amazon.it")
    assert _auth("https://amazon.it/login", {"other": 1}, "amazon.it")
    assert _auth("https://secure.pieces.account.booking.com/x", None,
                 "booking.com")


def test_same_site_anchors_www_handle_to_root():
    # handle `www.<root>` = contratto storico sul root
    assert _auth("https://account.booking.com/x", None, "www.booking.com")
    assert _auth("https://booking.com/x", None, "www.booking.com")


def test_same_site_rejects_other_registrable_site():
    assert not _auth("https://evil-booking.com/login", None, "booking.com")
    assert not _auth("https://booking.com.evil.com/login", None, "booking.com")
    assert not _auth("https://bookingcom.it/login", None, "booking.com")
    assert not _auth("http://booking.com/login", None, "booking.com")  # http pubblico


def test_same_site_local_and_ip_are_exact_host():
    assert _auth("http://192.168.1.10/admin", None, "192.168.1.10")
    assert _auth("http://192.168.1.10:8080/admin", None, "192.168.1.10")
    assert not _auth("http://192.168.1.11/admin", None, "192.168.1.10")
    assert _auth("http://router.local/login", None, "router.local")
    # label singola non-sito (handle tipo `github`): mai fill implicito su web
    assert not _auth("https://github.com/login", None, "github")


# ── Autorita' ESPLICITA (chiave presente = match esatto fail-closed) ─────────

def test_explicit_origins_exact_match_only():
    payload = {"credential_origins": ["https://accounts.example.com:443"]}
    assert so.explicit_origins(payload) == ["https://accounts.example.com:443"]
    assert _auth("https://accounts.example.com/login", payload, "example.com")
    # esplicita = NIENTE stesso-sito implicito ne' alias
    assert not _auth("https://example.com/login", payload, "example.com")
    assert not _auth("https://www.accounts.example.com/x", payload, "example.com")
    assert not _auth("https://accounts.example.com:8443/x", payload, "example.com")


def test_explicit_empty_or_invalid_is_deny_all_not_default():
    # fix adversarial #3: chiave PRESENTE ma vuota/invalida = deny-all
    # fail-closed, MAI allargamento al default stesso-sito.
    assert so.explicit_origins({"credential_origins": []}) == []
    assert not _auth("https://amazon.it/login",
                     {"credential_origins": []}, "amazon.it")
    assert not _auth("https://amazon.it/login",
                     {"credential_origins": ["http://amazon.it"]}, "amazon.it")
    # una valida + una invalida: resta solo la valida (l'invalida non allarga)
    payload = {"credential_origins": ["https://accounts.amazon.it",
                                      "http://amazon.it"]}
    assert _auth("https://accounts.amazon.it/x", payload, "amazon.it")
    assert not _auth("https://amazon.it/x", payload, "amazon.it")
    # chiave ASSENTE = regime stesso-sito (non deny-all)
    assert so.explicit_origins({"other": 1}) is None
    assert so.explicit_origins(None) is None


def test_set_credentials_rejects_empty_origins_list():
    module = _load_set_credentials()
    _norm, err = module._normalize_origins([])
    assert err and "at least one" in err
    _norm, err = module._normalize_origins(["http://public.example"])
    assert err  # all-invalid → errore (non lista vuota silenziosa)


# ── One-shot delegato (extra): match esatto, mai persistito ──────────────────

def test_oneshot_extra_exact_match():
    # senza extra: l'IdP delegato (altro sito) non e' autorizzato
    assert not _auth("https://idp.federated.example/auth", None, "shop.example")
    # con extra one-shot: autorizzato solo per quel flusso, tupla esatta
    assert _auth("https://idp.federated.example/auth", None, "shop.example",
                 extra="https://idp.federated.example")
    assert not _auth("https://idp.federated.example:8443/auth", None,
                     "shop.example", extra="https://idp.federated.example")
    assert not _auth("http://idp.federated.example/auth", None,
                     "shop.example", extra="https://idp.federated.example")


# ── set_credentials: validazione origini (write side) ───────────────────────

def test_set_credentials_normalize_origins_helper():
    module = _load_set_credentials()
    ok, err = module._normalize_origins(
        ["https://accounts.example.com", "http://192.168.1.10"])
    assert err is None
    assert ok == ["https://accounts.example.com:443", "http://192.168.1.10:80"]
    # http su host pubblico = rifiutato
    bad, err = module._normalize_origins(["http://example.com"])
    assert bad is None and err
    # non fornito = None (chiave assente → regime stesso-sito a runtime)
    assert module._normalize_origins(None) == (None, None)


def test_set_credentials_rejects_invalid_origins_before_dialog(monkeypatch):
    module = _load_set_credentials()
    out = module.invoke({
        "binding": "example.com",
        "fields": {"username": "u", "password": "p"},
        "credential_origins": ["http://example.com"],  # http pubblico invalido
    })
    assert out["ok"] is False and out["error_class"] == "invalid_args"

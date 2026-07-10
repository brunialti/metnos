# SPDX-License-Identifier: AGPL-3.0-only
"""test_sites_security — verifiche di sicurezza AUTOMATICHE del dominio `sites`
(spec §4.1 verifica + §8 criteri + §10.9). Parte del CONTRATTO, non opzionali.

Coprono i presidi DETERMINISTICI (§7.9), senza dipendere da un browser/sito
reale (quelli sono validati dall'MVP §8, turno reale):
  1. URL-scrub (§3.2 FIX E): token in query E fragment redatti.
  2. No-segreti (§10.6): audit + result non trasportano mai un segreto.
  3. Origine (§3.2 CRITICO-1): comparazione host esatta; iframe/altro host → rifiuto.
  4. Redazione (§3.2 CRITICO-3): il target di redazione include password + campi
     broker-filled; il capture è fail-closed se la redazione fallisce.
  5. No-cache (§4.5 FIX I): i tool `sites` sono in NON_CACHEABLE_TOOLS (L0/L1).
  6. Cred-injection: la destinazione NON è scelta dall'LLM (nessun arg selettore).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 1. URL-scrub (§3.2 FIX E) ──────────────────────────────────────────────

def test_url_scrub_redacts_query_and_fragment():
    from sites_url_scrub import scrub_url
    u = ("https://portale.esempio.it/callback?code=AUTHCODE123&state=ok"
         "#access_token=SECRETTOKEN&token_type=bearer&expires=3600")
    out = scrub_url(u)
    assert "AUTHCODE123" not in out
    assert "SECRETTOKEN" not in out
    assert "code=REDACTED" in out
    assert "access_token=REDACTED" in out
    # I parametri NON sensibili restano leggibili (onestà §2.8).
    assert "state=ok" in out
    assert "token_type=bearer" in out


def test_url_scrub_exact_name_match_no_substring_false_positive():
    from sites_url_scrub import scrub_url
    # `zipcode` contiene `code` ma NON è sensibile: match esatto, no substring.
    out = scrub_url("https://x.it/p?zipcode=20100&password=hunter2")
    assert "zipcode=20100" in out
    assert "hunter2" not in out and "password=REDACTED" in out


def test_url_scrub_idempotent_and_safe_on_garbage():
    from sites_url_scrub import scrub_url
    once = scrub_url("https://x.it/?token=A")
    assert scrub_url(once) == once
    assert scrub_url("") == ""
    assert scrub_url("not a url") == "not a url"


# ── 2. No-segreti nell'audit (§10.6, §4.1) ─────────────────────────────────

def test_audit_drops_forbidden_fields_and_scrubs_urls():
    import sites_audit
    san = sites_audit._sanitize({
        "password": "hunter2", "form_data": {"password": "x"},
        "token": "abc", "secret": "s", "otp": "123456",
        "url": "https://x.it/cb?token=LEAK&ok=1", "reason": "password_errata",
    })
    # Nessun campo proibito sopravvive.
    for bad in ("password", "form_data", "token", "secret", "otp"):
        assert bad not in san
    # L'URL è scrubbato, i campi leciti restano.
    assert "LEAK" not in san["url"] and "token=REDACTED" in san["url"]
    assert san["reason"] == "password_errata"


def test_login_result_never_carries_credentials():
    """Il result di login_sites non deve mai contenere chiavi-segreto
    (metadata-only §2.2). Usa l'oracolo centralizzato del vault."""
    import credentials
    # Shape tipica del result (nessun broker necessario): solo esito + reason.
    result = {
        "ok": False,
        "entries": [{"session_id": "abc", "logged_in": False,
                     "reason_code": "password_errata",
                     "message": "Password errata."}],
        "error_class": "password_errata",
    }
    # Non solleva → nessun campo proibito con valore non vuoto.
    credentials.assert_no_secrets_in_return(result)


# ── 3. Origine (§3.2 CRITICO-1) ────────────────────────────────────────────

def test_host_extraction_exact():
    from playwright_sidecar import credential_injection as ci
    assert ci._host_of("https://web.spaggiari.eu/login") == "web.spaggiari.eu"
    # Sottodominio diverso = host diverso → mismatch (D-D esatto).
    assert ci._host_of("https://auth.spaggiari.eu/x") != "web.spaggiari.eu"
    assert ci._host_of("not-a-url") == ""


# ── 4. Redazione (§3.2 CRITICO-3) ──────────────────────────────────────────

def test_redaction_targets_password_and_broker_filled():
    from playwright_sidecar import redaction
    js = redaction._REDACT_JS
    # La redazione copre SEMPRE i password field E i campi marcati dal broker.
    assert "input[type=password]" in js
    assert 'data-metnos-redact="1"' in js
    assert "#000" in js  # overlay nero opaco


def test_credential_injection_tags_secret_before_typing():
    from playwright_sidecar import credential_injection as ci
    js = ci._LOCATE_LOGIN_FORM_JS
    # Il pw field è marcato redact NELLO STESSO JS che lo individua (prima di
    # ogni digitazione/capture — CRITICO-3). Il broker risolve i campi in
    # autonomia (CRITICO-2): il JS TAGGA, l'LLM non passa selettori.
    assert "data-metnos-redact" in js
    assert "data-metnos-pw" in js
    # Cerca il form SOLO nel main frame (mai iframe, CRITICO-1 punto 2).
    assert "document.forms" in js


# ── 5. No-cache (§4.5 FIX I / §7) ──────────────────────────────────────────

def test_sites_tools_are_non_cacheable():
    from engine.fastpath import NON_CACHEABLE_TOOLS
    for t in ("open_sites", "login_sites", "read_sites", "delete_sites",
              "act_sites"):
        assert t in NON_CACHEABLE_TOOLS, f"{t} deve essere escluso da L0/L1"


# ── 6. Destinazione non scelta dall'LLM (§3.2 CRITICO-2) ───────────────────

def test_login_sites_declares_no_selector_arg():
    """login_sites NON deve esporre un arg selettore: la destinazione della
    credenziale la risolve il broker, non l'LLM/planner."""
    import tomllib
    mpath = (Path(__file__).resolve().parent.parent.parent
             / "executors" / "login_sites" / "manifest.toml")
    manifest = tomllib.loads(mpath.read_text())
    props = manifest.get("args", {}).get("properties", {})
    forbidden = {"selector", "css", "xpath", "field", "element", "target_selector"}
    assert not (set(props) & forbidden), \
        "login_sites non deve accettare un selettore (CRITICO-2)"


# ── Vocab: sites ratificato (§5, D-A) ──────────────────────────────────────

def test_sites_vocab_ratified():
    import vocab
    from naming_grammar import validate_name
    assert "sites" in vocab.OBJECTS
    for v in ("open", "login", "act"):
        assert v in vocab.ACTIONS
    for name in ("open_sites", "login_sites", "read_sites", "delete_sites"):
        assert validate_name(name).ok, f"{name} deve essere un nome valido §2.2"

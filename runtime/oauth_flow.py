"""oauth_flow — pattern OAuth 2.0 Authorization Code (RFC 6749) generico.

Indipendente dal provider: il chiamante fornisce il path al
`client_secret.json` (formato Google `installed`/`web` block, riusato da
molti provider) e la lista di scope. Per provider non-Google con schema
diverso, il caller costruisce il dict equivalente prima di invocare.

Niente hardcoding di provider, scope, o path filesystem. La
specializzazione (Google Workspace, GitHub, Atlassian, ...) sta
nel caller via:

- `scopes`: lista di scope URL (caller risolve da skill manifest /
  utente / preset).
- `redirect_uri`: URL completo dove il provider reindirizzera'. Deve
  essere autorizzato nella console del provider (per app desktop,
  Google accetta qualunque `http://localhost:PORT/path`).
- `mirror_paths`: lista di path filesystem opzionali dove duplicare
  il token in formato JSON plain (per backward-compat con script legacy
  della skill che leggono il token da posizione specifica).

Salvataggio canonico: il token cifrato (Fernet+HKDF, ADR 0082) vive
in `~/.local/share/metnos/credentials/<binding>.json` via `credentials.store`.

Determinismo §7.9: niente LLM. Niente subprocess. Solo google-auth-oauthlib
(libreria generica, non hardcoded a Google Workspace).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional


def start_flow(*,
               client_secret_path: str,
               scopes: list,
               redirect_uri: str,
               state: str,
               client_secret_install_path: Optional[Path] = None) -> tuple[str, dict]:
    """Stage 1 OAuth: salva client_secret se richiesto + costruisce
    authorization URL.

    Args:
      client_secret_path: path locale al file scaricato dalla console.
      scopes: lista di scope URL (es. ['https://www.googleapis.com/.../calendar']).
      redirect_uri: URL completo per il callback (es. 'http://localhost:8770/oauth/callback').
      state: opaco anti-CSRF.
      client_secret_install_path: se non-None, copia client_secret_path qui
                                  (chmod 0600). Util per script legacy che
                                  cercano il file a path fisso (es. Hermes:
                                  `~/.hermes/google_client_secret.json`).
                                  Se None, lavora direttamente sul path sorgente.

    Returns:
      (authorization_url, flow_state_dict).
      `flow_state_dict` e' JSON-serializable per persistenza.
    """
    from google_auth_oauthlib.flow import Flow

    src = Path(client_secret_path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"client_secret non trovato: {src}")

    if client_secret_install_path is not None:
        dest = Path(client_secret_install_path).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Skip copy quando src e dest puntano allo stesso file
        # (caso "renew after revoke" 18/5/2026: il default UI e' gia'
        # il path canonico installato).
        try:
            same = src.resolve() == dest.resolve()
        except OSError:
            same = False
        if not same:
            shutil.copy2(src, dest)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        secret_file = str(dest)
    else:
        secret_file = str(src)

    flow = Flow.from_client_secrets_file(
        secret_file, scopes=scopes, redirect_uri=redirect_uri,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    flow_state = {
        "scopes": list(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "client_secret_file": secret_file,
        # PKCE: `google-auth-oauthlib` puo' aggiungere `code_challenge` nel
        # URL. Il `code_verifier` corrispondente vive sul Flow object e va
        # persistito per `finish_flow` (caso default per Web app OAuth).
        "code_verifier": getattr(flow, "code_verifier", None),
    }
    return auth_url, flow_state


def finish_flow(*,
                flow_state: dict,
                code: str,
                binding: str,
                mirror_paths: list | None = None) -> tuple[bool, Optional[str]]:
    """Stage 2 OAuth: scambia code per token, salva (cifrato + opzionali
    plain mirror).

    Args:
      flow_state: dict restituito da `start_flow`.
      code: il `code` ricevuto dal redirect URI.
      binding: chiave di archivio in Metnos credentials (es. 'google-workspace').
      mirror_paths: lista di path filesystem per duplicare il token in plain
                    JSON (chmod 0600). Per skill legacy che si aspettano il
                    file a path specifico. Vuota = solo cifrato in Metnos.

    Returns: (ok, error_msg_or_None).
    """
    from google_auth_oauthlib.flow import Flow

    secret_file = flow_state.get("client_secret_file")
    if not secret_file or not Path(secret_file).is_file():
        return False, "client_secret_file mancante dal flow_state"

    scopes = flow_state.get("scopes") or []
    redirect_uri = flow_state.get("redirect_uri")
    if not redirect_uri:
        return False, "flow_state senza redirect_uri"

    try:
        flow = Flow.from_client_secrets_file(
            secret_file, scopes=scopes, redirect_uri=redirect_uri,
        )
        # PKCE: re-apply il `code_verifier` salvato in start_flow, altrimenti
        # Google rifiuta lo scambio con "Missing code verifier".
        code_verifier = flow_state.get("code_verifier")
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
    except Exception as e:
        return False, f"token exchange failed: {e}"

    creds = flow.credentials
    token_payload = {
        "token":          creds.token,
        "refresh_token":  creds.refresh_token,
        "token_uri":      creds.token_uri,
        "client_id":      creds.client_id,
        "client_secret":  creds.client_secret,
        "scopes":         creds.scopes,
    }

    # Canonical: cifrato in Metnos credentials store (ADR 0082).
    try:
        from credentials import store as _cred_store  # type: ignore
        _cred_store(binding, token_payload)
    except Exception:
        # Test env: store non disponibile -> mirror plain e' l'unica copia.
        pass

    # Mirror plain (opzionale).
    for mp in (mirror_paths or []):
        target = Path(mp).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(token_payload, indent=2), encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    return True, None


def has_valid_token(binding: str, mirror_paths: list | None = None) -> bool:
    """Best-effort: token presente con refresh_token.

    Lookup ordine: Metnos credentials store (cifrato) -> mirror_paths (plain).
    """
    try:
        from credentials import fetch as _cred_fetch  # type: ignore
        data = _cred_fetch(binding)
        if isinstance(data, dict) and data.get("refresh_token"):
            return True
    except Exception:
        pass
    for mp in (mirror_paths or []):
        target = Path(mp).expanduser()
        if not target.is_file():
            continue
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if data.get("refresh_token"):
                return True
        except Exception:
            continue
    return False

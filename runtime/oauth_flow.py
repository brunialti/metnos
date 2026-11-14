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

import ipaddress
import json
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional


def _this_computer(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def redirect_problem(client_secret_path: object, redirect_uri: str) -> Optional[str]:
    """Why the provider would refuse this return address, as far as this side
    can tell before sending the person there. A closed code or None.

    ``insecure``: plain http towards a host that is not this computer. A
    provider takes the return of a consent over https or on the loopback
    interface only (RFC 8252).
    ``unregistered``: a web client, whose file lists the return addresses
    registered for it, and this one is not among them.

    The file is a copy made when it was downloaded and the provider may know
    more: a problem is a warning for the person, never a refusal. A file that
    cannot be read says nothing here (`start_flow` reports it).
    """
    parts = urllib.parse.urlsplit(str(redirect_uri or ""))
    if parts.scheme != "https" and not _this_computer(parts.hostname or ""):
        return "insecure"
    try:
        data = json.loads(Path(str(client_secret_path or "")).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    web = data.get("web") if isinstance(data, dict) else None
    if isinstance(web, dict):
        listed = web.get("redirect_uris")
        if redirect_uri not in (listed if isinstance(listed, list) else []):
            return "unregistered"
    return None


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


def _accepted_returns() -> Path:
    import config as C
    return Path(C.PATH_USER_STATE) / "oauth" / "accepted_returns.json"


def return_accepted(binding: object, redirect_uri: str) -> bool:
    """Whether a consent for this integration already came back through this
    address: the provider knows it, whatever a copy of the client file says."""
    try:
        known = json.loads(_accepted_returns().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    listed = known.get(str(binding)) if isinstance(known, dict) else None
    return isinstance(listed, list) and redirect_uri in listed


def _remember_return(binding: str, redirect_uri: str) -> None:
    path = _accepted_returns()
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    if not isinstance(known, dict):
        known = {}
    listed = known.get(binding) if isinstance(known.get(binding), list) else []
    if redirect_uri in listed:
        return
    known[binding] = sorted({*listed, redirect_uri})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(known, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def finish_flow(*,
                flow_state: dict,
                code: str,
                binding: str,
                mirror_paths: list | None = None) -> tuple[bool, Optional[str], list]:
    """Stage 2 OAuth: scambia code per token, salva (cifrato + opzionali
    plain mirror).

    Args:
      flow_state: dict restituito da `start_flow`.
      code: il `code` ricevuto dal redirect URI.
      binding: chiave di archivio in Metnos credentials (es. 'google-workspace').
      mirror_paths: lista di path filesystem per duplicare il token in plain
                    JSON (chmod 0600). Per skill legacy che si aspettano il
                    file a path specifico. Vuota = solo cifrato in Metnos.

    Returns: (ok, error_msg_or_None, ungranted): the permissions that were
    asked and the person did not grant. A partial grant is a success, said in
    words by the caller; it is never an error that throws the consent away.
    """
    from google_auth_oauthlib.flow import Flow

    secret_file = flow_state.get("client_secret_file")
    if not secret_file or not Path(secret_file).is_file():
        return False, "client_secret_file mancante dal flow_state", []

    scopes = flow_state.get("scopes") or []
    redirect_uri = flow_state.get("redirect_uri")
    if not redirect_uri:
        return False, "flow_state senza redirect_uri", []

    # The provider lets the person tick the permissions one by one. The OAuth
    # library refuses an answer whose permissions differ from the ones asked
    # unless told otherwise: the consent would be lost on a message about
    # scopes, and the one-time code with it.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
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
        return False, f"token exchange failed: {e}", []

    creds = flow.credentials
    # What was granted, not what was asked: a later renewal that names a
    # permission the person refused is itself refused by the provider.
    answered = (getattr(getattr(flow, "oauth2session", None), "token", None) or {}).get("scope")
    if isinstance(answered, str):
        answered = answered.split()
    granted = ([str(scope) for scope in answered]
               if isinstance(answered, (list, tuple)) and answered
               else list(creds.scopes or scopes))
    ungranted = [scope for scope in scopes if scope not in granted]
    token_payload = {
        "token":          creds.token,
        "refresh_token":  creds.refresh_token,
        "token_uri":      creds.token_uri,
        "client_id":      creds.client_id,
        "client_secret":  creds.client_secret,
        "scopes":         granted,
    }

    # Canonical: cifrato in Metnos credentials store (ADR 0082).
    try:
        from credentials import store as _cred_store  # type: ignore
        _cred_store(binding, token_payload)
    except Exception as ex:  # noqa: BLE001 - the plain mirror below is then the only copy
        import logging
        logging.getLogger(__name__).warning(
            "oauth token not stored in the credentials store: %s", type(ex).__name__)

    # Mirror plain (opzionale): never readable by others, not even while written.
    for mp in (mirror_paths or []):
        target = Path(mp).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as out:
            out.write(json.dumps(token_payload, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)

    try:
        _remember_return(binding, redirect_uri)
    except OSError:
        pass          # only costs a notice the person does not need

    return True, None, ungranted


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

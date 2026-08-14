"""cifs_helper — gestione credentials file per `mount.cifs` (ADR 0087).

Pattern Linux standard: `mount.cifs` accetta `-o credentials=PATH`, dove
PATH e' un file con righe nella forma:

    username=...
    password=...
    domain=...

Il file deve essere mode 0600 (niente world/group readable, mount.cifs
rifiuta altrimenti). La sua vita e' brevissima: si crea, si lancia
mount, si distrugge subito dopo. Niente residui sul disco oltre la
durata del comando.

Riusa `runtime/credentials.py` (ADR 0082) per leggere le credenziali
cifrate sotto `~/.config/metnos/credentials/<domain>.json.age`. La
convenzione di dominio per uno share CIFS e' `cifs_<server-host>`
(es. `cifs_192.0.2.20`); questo permette di riusare la stessa
casella per piu' share dello stesso server, ed evita collisione con
domini web (chiave `web.spaggiari.eu` e simili).
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Tuple

import credentials  # runtime/credentials.py (ADR 0082)


# ── lettura: temp credentials file ────────────────────────────────────

@contextmanager
def temp_credentials_file(domain: str) -> Iterator[Tuple[Optional[str], Optional[str]]]:
    """Yield (path, error) — path al temp file pronto per `mount.cifs`.

    - Se il dominio non esiste in store: yield (None, "credenziali non trovate ...").
    - Se i campi obbligatori (username, password) mancano: yield (None, motivo).
    - Altrimenti yield (path, None) con `path` mode 0600 contenente
      `username=...\\npassword=...\\n[domain=...]\\n`.

    Cleanup garantito al termine del with-block (via finally), anche su
    eccezione del chiamante. Il file e' creato in `/tmp/` con prefix
    riconoscibile (`.cifs_*.creds`) e nome random per evitare collisioni
    fra mount paralleli.

    Uso tipico:

        from cifs_helper import temp_credentials_file
        with temp_credentials_file("cifs_192.0.2.20") as (cred_path, err):
            if err:
                return {"ok": False, "error": err}
            argv = ["sudo", "mount", "-t", "cifs",
                    "//192.0.2.20/Public/Images", "/home/user/nas-images",
                    "-o", f"credentials={cred_path},uid={os.getuid()}"]
            subprocess.run(argv, check=False)
    """
    payload = credentials.load(domain)
    if payload is None:
        yield (None, f"credenziali CIFS non trovate per dominio {domain!r}")
        return

    user, pwd, dom = _extract_fields(payload)
    if not user or not pwd:
        yield (
            None,
            f"credenziali CIFS incomplete per {domain!r}: "
            "username e password sono obbligatori",
        )
        return

    fd, path = tempfile.mkstemp(prefix=".cifs_", suffix=".creds", dir="/tmp")
    try:
        # mkstemp gia' apre con 0600 sulla maggior parte dei kernel; chmod
        # esplicito difende da umask permissive ed e' idempotente.
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"username={user}\n")
            fh.write(f"password={pwd}\n")
            if dom:
                fh.write(f"domain={dom}\n")
        yield (path, None)
    finally:
        # Cleanup best-effort: ENOENT (gia' rimosso) e' tollerato. Non
        # propaghiamo l'errore di rimozione perche' altrimenti coprirebbe
        # un'eccezione del chiamante.
        try:
            os.remove(path)
        except OSError:
            pass


def _extract_fields(payload: dict) -> Tuple[str, str, str]:
    """Estrae (username, password, domain) da un payload credentials.load.

    Tollera due layout:
      - flat:  {"username": "...", "password": "...", "domain": "..."}
      - form:  {"form_data": {"username": "...", "password": "..."}, ...}

    Il dominio CIFS (workgroup) e' opzionale. Se assente, mount.cifs usa
    "WORKGROUP" come default.
    """
    flat_user = payload.get("username") or ""
    flat_pwd = payload.get("password") or ""
    flat_dom = payload.get("domain") or payload.get("workgroup") or ""

    form = payload.get("form_data") or {}
    form_user = form.get("username") or form.get("user") or ""
    form_pwd = form.get("password") or form.get("pwd") or form.get("passwd") or ""

    return (
        str(flat_user or form_user),
        str(flat_pwd or form_pwd),
        str(flat_dom),
    )


# ── scrittura: store delle credenziali CIFS ───────────────────────────

def store_cifs_credentials(
    domain: str,
    *,
    username: str,
    password: str,
    workgroup: str = "WORKGROUP",
    server: str = "",
    share: str = "",
) -> Path:
    """Salva le credenziali cifrate per uno share CIFS, riusando
    `credentials.store` (ADR 0082).

    Convenzione: `domain` e' la chiave di store, tipicamente
    `cifs_<server>` (es. `cifs_192.0.2.20`). I campi `server` e `share`
    sono metadata informativi: utili per audit ma non per il mount stesso
    (il pianificatore passa il path `//server/share` esplicito nel argv).

    Ritorna il `Path` al file cifrato sul disco
    (`~/.config/metnos/credentials/<domain>.json.age`, mode 0600).
    """
    if not username or not password:
        raise ValueError("username e password sono obbligatori")
    payload = {
        "username": username,
        "password": password,
        "domain": workgroup,
        "server": server,
        "share": share,
    }
    return credentials.store(domain, payload)


# ── helper: dominio canonico da host/server ──────────────────────────

def domain_for_server(server: str) -> str:
    """Costruisce la chiave di store canonica per uno share CIFS dato il
    nome host del server (IP o FQDN). Sempre lowercase, prefissato con
    `cifs_` per disambiguare dai domini web di ADR 0082.

    Esempi:
        domain_for_server("192.0.2.20") -> "cifs_192.0.2.20"
        domain_for_server("NAS.lan")      -> "cifs_nas.lan"
    """
    s = (server or "").strip().lower()
    if not s:
        raise ValueError("server name vuoto")
    # Non sanifichiamo aggressivamente: credentials.store rifiuta gia'
    # path-traversal e separatori. Qui solo lowercase + strip.
    return f"cifs_{s}"

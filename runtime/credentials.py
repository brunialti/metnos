"""credentials — storage cifrato delle credenziali utente per login web (ADR 0082).

Cifratura simmetrica derivata da `~/.config/metnos/admin.key` via HKDF-SHA256
con salt random per file. Cipher: `cryptography.fernet.Fernet`. Niente
passphrase utente, l'admin key locale fa da master.

Storage: `~/.config/metnos/credentials/<domain>.json.age` (mode 0600).
Format JSON cifrato:
    {
      "login_url": "...",
      "method": "POST",
      "form_data": {"username": "...", "password": "..."},
      "session_cookie_names": ["JSESSIONID", "PHPSESSID"]
    }

Convenzione: la chiave del file e' il nome di dominio puro (es.
"web.spaggiari.eu"). Nessuna sanificazione: si usa esattamente la stringa
come chiave, ma path-traversal e' bloccato (segmenti `..`, `/`, NUL).

Contratto:
    store(domain, payload) -> Path     scrive cifrato, mode 0600
    load(domain) -> dict | None        decifra al volo
    list_domains() -> list[str]        nomi (senza estensione) ordinati
    remove(domain) -> bool             True se esisteva ed e' stato rimosso
    fingerprint(domain) -> str | None  sha256[:16] della pwd, per audit
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import config as _C  # §7.11


# Path canonici. Esposti come modulo-level per facilitare test (monkeypatch).
ADMIN_KEY_PATH = _C.PATH_USER_CONFIG / "admin.key"
CRED_DIR = _C.PATH_USER_CONFIG / "credentials"


# Caratteri proibiti nel nome di dominio: bloccano path-traversal e
# salvataggi accidentali in altre cartelle. Il dominio non e' sanitizzato
# automaticamente (non vogliamo perdere informazione), ma rifiutato.
_FORBIDDEN_CHARS = ("/", "\\", "\x00", "..")


def _validate_domain(domain: str) -> None:
    if not domain or not isinstance(domain, str):
        raise ValueError("domain must be a non-empty string")
    for bad in _FORBIDDEN_CHARS:
        if bad in domain:
            raise ValueError(f"domain contains forbidden chars: {domain!r}")
    if domain.startswith("."):
        raise ValueError(f"domain cannot start with '.': {domain!r}")


def _ensure_admin_key() -> bytes:
    """Carica la admin key (32 bytes equivalenti, hex 64 caratteri).
    Se assente, fallisce esplicitamente: la generazione e' responsabilita'
    di `runtime.http_auth.get_or_create_admin_key()` (boot del server).
    """
    if not ADMIN_KEY_PATH.exists():
        raise FileNotFoundError(
            f"admin key non trovata in {ADMIN_KEY_PATH}; avvia almeno una "
            "volta `metnos_http_server` per generarla."
        )
    raw = ADMIN_KEY_PATH.read_text().strip()
    # Tolerante: accetta hex (256-bit) o stringa qualunque.
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _derive_fernet_key(salt: bytes) -> bytes:
    """HKDF-SHA256(admin_key, salt) -> 32 byte -> base64 url-safe per Fernet."""
    master = _ensure_admin_key()
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"metnos-credentials-v1",
    ).derive(master)
    return base64.urlsafe_b64encode(derived)


def _file_for(domain: str) -> Path:
    _validate_domain(domain)
    return CRED_DIR / f"{domain}.json.age"


def store(domain: str, payload: dict) -> Path:
    """Scrive payload cifrato in <CRED_DIR>/<domain>.json.age. Mode 0600.

    payload: dict con chiavi tipiche login_url, method, form_data, session_cookie_names.
    Lo schema non e' validato qui: il chiamante (`login_urls`) sa cosa serve.
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CRED_DIR, 0o700)
    salt = secrets.token_bytes(16)
    key = _derive_fernet_key(salt)
    f = Fernet(key)
    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ciphertext = f.encrypt(plaintext)
    # Layout file: <salt_b64>\n<fernet_token>
    blob = base64.urlsafe_b64encode(salt) + b"\n" + ciphertext
    path = _file_for(domain)
    # Scrittura atomica (tmp + os.replace): un load() concorrente non deve mai
    # leggere un blob troncato (→ falso errore di decifratura). chmod sul tmp
    # PRIMA del replace così il file finale nasce gia' 0600.
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(blob)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def load(domain: str) -> dict | None:
    """Decifra al volo il payload. Ritorna None se il file non esiste."""
    path = _file_for(domain)
    if not path.exists():
        return None
    blob = path.read_bytes()
    try:
        salt_b64, ciphertext = blob.split(b"\n", 1)
        salt = base64.urlsafe_b64decode(salt_b64)
    except ValueError:
        raise ValueError(f"credential file malformed: {path}")
    key = _derive_fernet_key(salt)
    try:
        plaintext = Fernet(key).decrypt(ciphertext)
    except InvalidToken as ex:
        raise ValueError(f"credential decryption failed for {domain}: {ex}")
    return json.loads(plaintext.decode("utf-8"))


def list_domains() -> list[str]:
    """Ritorna la lista dei domini noti, ordinata. Niente lettura del payload."""
    if not CRED_DIR.exists():
        return []
    out = []
    for p in sorted(CRED_DIR.glob("*.json.age")):
        # Strippa il doppio suffisso .json.age
        stem = p.name[: -len(".json.age")]
        if stem:
            out.append(stem)
    return out


def remove(domain: str) -> bool:
    """Rimuove la credenziale. Ritorna True se esisteva ed e' stata rimossa."""
    path = _file_for(domain)
    if not path.exists():
        return False
    path.unlink()
    return True


def fingerprint(domain: str) -> str | None:
    """sha256[:16] della password salvata, per audit. None se assente o
    se il payload non contiene una password.

    Supporta entrambi i layout (ADR 0082 legacy form_data + ADR 0089 flat):
      - form: {"form_data": {"username": "...", "password": "..."}}
      - flat: {"username": "...", "password": "..."}
    """
    payload = load(domain)
    if payload is None:
        return None
    pwd = payload.get("password") or payload.get("pwd") or payload.get("passwd")
    if not pwd:
        form = payload.get("form_data") or {}
        pwd = form.get("password") or form.get("pwd") or form.get("passwd")
    if not pwd:
        return None
    return hashlib.sha256(pwd.encode("utf-8")).hexdigest()[:16]


# ── Form per TIPO di credenziale: INIETTATI DAL DOMINIO consumatore ─────────
# Ogni dominio conosce la propria semantica: il form dei suoi campi è
# dichiarato nella sezione `[credential_form]` del manifest FIRMATO di un suo
# executor (mail -> read_messages, sites -> login_sites, provider nella sua
# skill; il tipo generico `api` appartiene al dominio credenziali stesso, cioè
# a set_credentials). `set_credentials` resta universale: colleziona i form
# dal catalogo ammesso, quindi un dominio assente sparisce dal form da solo e
# uno nuovo lo porta con sé. Dialogo ADR 0090: i segreti usano il kind
# `credentials` (input password) e tornano all'executor via resume diretto,
# MAI dal contesto del planner.
_ALLOWED_INPUT_KINDS = frozenset({"text", "credentials", "number"})
_KIND_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _validate_form_spec(owner: str, spec: dict) -> str:
    slug = str(spec.get("kind") or "")
    if not _KIND_SLUG.match(slug):
        raise ValueError(
            f"credential_form di {owner!r}: kind invalido {slug!r}")
    if not str(spec.get("label_key") or ""):
        raise ValueError(
            f"credential_form di {owner!r}: label_key mancante")
    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError(f"credential_form di {owner!r}: fields mancanti")
    seen: set[str] = set()
    for field in fields:
        name = str(field.get("name") or "")
        if not name or name in seen:
            raise ValueError(f"credential_form di {owner!r}: campo "
                             f"invalido o duplicato ({name!r})")
        seen.add(name)
        if field.get("input") not in _ALLOWED_INPUT_KINDS:
            raise ValueError(
                f"credential_form di {owner!r}: input "
                f"{field.get('input')!r} non supportato ({slug}.{name})")
        if not str(field.get("prompt_key") or ""):
            raise ValueError(f"credential_form di {owner!r}: prompt_key "
                             f"mancante ({slug}.{name})")
    return slug


def credential_form_kinds() -> dict[str, dict]:
    """Colleziona i form dichiarati dai domini nel catalogo ammesso.

    Gira NEL PROCESSO SERVER (rev. 24/7 sera): la sandbox degli executor non
    monta le chiavi trusted né i manifest delle skill, quindi il runtime
    colleziona qui e INIETTA il risultato nell'arg runtime-owned
    `credential_forms` di `set_credentials` al choke-point di invocazione.
    Niente cache: la chiamata avviene solo quando si invoca il consumatore e
    il catalogo può cambiare a runtime (skill installate).

    Fail-loud: spec invalida o stesso kind dichiarato da due executor =
    errore che nomina i responsabili. Include i domini dormienti: è proprio
    per attivarli che servono le credenziali.
    """

    import tomllib
    from loader import load_catalog

    kinds: dict[str, dict] = {}
    owner: dict[str, str] = {}
    for executor in sorted(load_catalog(), key=lambda item: item.name):
        path = getattr(executor, "manifest_path", None)
        if not path:
            continue
        spec = (tomllib.loads(
            Path(path).read_text(encoding="utf-8"))).get("credential_form")
        if not isinstance(spec, dict):
            continue
        slug = _validate_form_spec(executor.name, spec)
        if slug in kinds:
            raise ValueError(
                f"credential_form {slug!r} dichiarato sia da "
                f"{owner[slug]!r} sia da {executor.name!r}")
        kinds[slug] = spec
        owner[slug] = executor.name
    if not kinds:
        raise ValueError(
            "nessun [credential_form] dichiarato nel catalogo ammesso")
    return kinds


def kind_for_binding(binding: str, kinds: dict | None = None) -> str | None:
    """`kinds` = mappa già collezionata (executor in sandbox: arg iniettato);
    None = collezione diretta (chiamanti nel processo server)."""
    low = str(binding or "").casefold()
    if kinds is None:
        kinds = credential_form_kinds()
    for slug, spec in sorted(kinds.items()):
        for prefix in (spec.get("detect_prefixes") or ()):
            if low.startswith(str(prefix).casefold()):
                return slug
    return None


def canonical_binding(kind: str, name: str, kinds: dict | None = None) -> str:
    if kinds is None:
        kinds = credential_form_kinds()
    spec = kinds.get(kind) or {}
    prefix = str(spec.get("binding_prefix") or "")
    clean = str(name or "").strip()
    if prefix and not clean.casefold().startswith(prefix.casefold()):
        return prefix + clean
    return clean


# Invariante `metnos:credentials_metadata_only` (ADR 0123 §2.2): i 3 executor
# find/set/delete_credentials non devono mai ritornare cleartext al PLANNER.
# Centralizzato qui (regola del 3 §7.2): prima duplicato in 3 file.
FORBIDDEN_KEYS = frozenset({
    "value", "values", "token", "secret", "secrets",
    "api_key", "password", "passwd", "client_secret",
    "cleartext", "raw",
})


def _is_empty_value(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (str, list, dict, tuple)) and len(v) == 0:
        return True
    return False


def assert_no_secrets_in_return(obj, _path: str = "$") -> None:
    """Valida ricorsivamente che nessun campo proibito porti valore non vuoto.
    Solleva ValueError se trova violazione (fail-loud §2.8). Match
    case-insensitive su nome di chiave EXACT (no substring) per evitare falsi
    positivi su `fingerprint`/`fields_present`.

    Unica esenzione STRUTTURALE: un elemento di `schema.choices` con sole
    chiavi {value, label} è un'opzione UI dei dialoghi (forma ADR 0127); il
    suo `value` è un identificatore di scelta, non un segreto."""
    if isinstance(obj, dict):
        option_dict = (
            ".schema.choices[" in _path and _path.endswith("]")
            and set(obj) <= {"value", "label"}
        )
        for k, v in obj.items():
            if (not option_dict
                    and str(k).lower() in FORBIDDEN_KEYS
                    and not _is_empty_value(v)):
                raise ValueError(
                    f"credentials_metadata_only violated at {_path}.{k}: "
                    f"forbidden key {k!r} carries non-empty value"
                )
            assert_no_secrets_in_return(v, f"{_path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            assert_no_secrets_in_return(item, f"{_path}[{i}]")

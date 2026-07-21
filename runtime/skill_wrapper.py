"""skill_wrapper - helper condivisi per executor importati da skill.

Estratto in modulo per regola del 3 (§7.2): i 5 helper canonici sono
identici nei 3 POC executor (read_events/set_events/delete_events) e
saranno replicati per ogni skill importata. Codegen Jinja (Task C) emette
codice che li importa invece di duplicarli.

Esporta:
- _skill_home(skill_name): radice della skill (env override METNOS_SKILL_HOME).
- _subprocess_runner(): hook test via METNOS_SUBPROCESS_FAKE.
- _run_api(script, argv, ...): subprocess wrapper deterministico.
- _classify_error(rc, stderr): mapping rc+stderr -> error_class.
- _needs_inputs_oauth_setup(skill_name, executor, args_base, ...) payload
  needs_inputs riusabile (ADR 0090 + ADR 0089).
- _check_credentials(binding): probe metadata (load via runtime/credentials.py).

Determinismo §7.9: nessun LLM. Nessuna mutazione di stato globale.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

# --- Normalizzazione flag booleani argparse (4/6/2026, §7.3 generale) --------
# Gli executor skill generati dall'importer costruiscono argv col pattern
# `argv.extend(["--flag", str(value)])` per OGNI arg. Per i flag argparse
# `action="store_true"` questo e' SBAGLIATO: vanno passati NUDI (`--flag`),
# altrimenti il valore diventa un positional extra → "usage: ... " (usage leak,
# §2.8). Fix centralizzato in _run_api: rileva i flag store_true del CLI e
# normalizza l'argv. Vale per QUALSIASI skill/CLI (github, google_workspace, ...),
# esistente e futuro, senza toccare i singoli executor.
_STORE_TRUE_CACHE: dict = {}  # str(script_path) -> (mtime, frozenset[flag])
_TRUTHY = {"1", "true", "yes", "si", "sì", "on", "y", "t", "vero"}
_FALSY = {"0", "false", "no", "off", "n", "f", "", "none", "null", "falso"}


def _store_true_flags(script_path: Path) -> frozenset:
    """Rileva i flag `--x` con `action="store_true"` di un CLI argparse
    (cache per mtime). Regex tollerante a definizioni multiriga."""
    try:
        mt = script_path.stat().st_mtime
    except OSError:
        return frozenset()
    key = str(script_path)
    hit = _STORE_TRUE_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        text = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset()
    # add_argument("--flag", ... action="store_true" ...) — `[^)]` matcha anche
    # i newline, quindi copre le definizioni su piu' righe.
    flags = frozenset(re.findall(
        r'add_argument\(\s*["\'](--[\w-]+)["\'][^)]*?store_true', text))
    _STORE_TRUE_CACHE[key] = (mt, flags)
    return flags


def _normalize_bool_flags(argv: Sequence[str], flags: frozenset) -> list:
    """Per ogni flag store_true presente in argv con un VALORE attaccato
    (`--flag val`): truthy → tieni il flag NUDO (scarta il valore); falsy →
    scarta flag+valore. Idempotente sui flag gia' nudi."""
    if not flags:
        return list(argv)
    argv = list(argv)
    out: list = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in flags and i + 1 < len(argv) and not str(argv[i + 1]).startswith("-"):
            v = str(argv[i + 1]).strip().lower()
            if v in _FALSY:
                i += 2          # falsy → droppa flag + valore
                continue
            out.append(tok)     # truthy → flag nudo, scarta il valore
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def _skill_home(skill_name: str) -> Path:
    """Radice dei file della skill copiati al momento dell'import.

    Override per test via METNOS_SKILL_HOME (path completo, gia' inclusivo
    della skill: niente concat di skill_name).

    Convenzione produzione: ${XDG_DATA_HOME:-~/.local/share}/metnos/skills/<skill>.
    """
    home_env = os.environ.get("METNOS_SKILL_HOME")
    if home_env:
        return Path(home_env)
    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError("skill_name must be a non-empty string")
    import config as _C  # §7.11
    return _C.PATH_USER_DATA / "skills" / skill_name


def _validate_skill_args(args, *, allowed: set[str] | frozenset[str],
                         required: tuple[str, ...] = ()) -> str | None:
    """Validate the common generated-wrapper boundary before provider CLI.

    Unknown keys never reach a third-party parser and missing required values
    never leak provider ``usage:`` text into a user-facing result.
    """
    if not isinstance(args, dict):
        return "args must be an object"
    unknown = sorted(set(args) - set(allowed))
    if unknown:
        return f"unknown arguments: {unknown}"
    missing = [
        name for name in required
        if name not in args or args.get(name) is None
        or (isinstance(args.get(name), str) and not args[name].strip())
        or (isinstance(args.get(name), list) and not args[name])
    ]
    if missing:
        return f"missing required arguments: {missing}"
    return None


def _subprocess_runner() -> Optional[Callable]:
    """Override hook per i test: METNOS_SUBPROCESS_FAKE=mod.fn dove
    fn(argv, env, timeout_s) -> (rc, stdout, stderr).

    Ritorna None se l'env non e' settato o il modulo non risolve.
    """
    fake = os.environ.get("METNOS_SUBPROCESS_FAKE")
    if not fake:
        return None
    mod_name, _, attr = fake.rpartition(".")
    if not mod_name or not attr:
        return None
    try:
        mod = __import__(mod_name, fromlist=[attr])
        return getattr(mod, attr, None)
    except Exception:
        return None


def _run_api(
    script_path: Path,
    argv: Sequence[str],
    *,
    skill_name: str,
    timeout_s: int = 30,
    extra_env: Optional[dict] = None,
) -> tuple[int, str, str]:
    """Esegue python <script_path> <argv>.

    DEVI: passare script_path come Path concreto e argv come lista.
    NON DEVI: usare shell=True.
    Inietta automaticamente HERMES_HOME=<_skill_home(skill_name)> +
    METNOS_SKILL_HOME (per script che leggono entrambi).

    Ritorna (returncode, stdout, stderr) sempre, anche su eccezione:
    - FileNotFoundError -> (127, "", "ModuleNotFoundError: <msg>")
    - TimeoutExpired -> (124, "", "timeout: <msg>")
    """
    fake = _subprocess_runner()
    env = dict(os.environ)
    home = _skill_home(skill_name)
    env.setdefault("HERMES_HOME", str(home))
    env.setdefault("METNOS_SKILL_HOME", str(home))
    if extra_env:
        env.update(extra_env)
    # github: se il token non e' nell'env (service senza METNOS_GITHUB_TOKEN),
    # risolvilo via SoT (cred-store / gh CLI) e iniettalo. Lo script generato
    # github_api.py lo legge da env: nessun edit allo script. Sistemico §7.3.
    if skill_name == "github" and not env.get("METNOS_GITHUB_TOKEN", "").strip():
        try:
            import skill_credentials as _sc
            _tok = _sc.resolve_github_token()
            if _tok:
                env["METNOS_GITHUB_TOKEN"] = _tok
        except Exception:
            pass

    # Normalizza i flag booleani store_true (§7.3 generale, vedi sopra). Robusto:
    # un errore di normalizzazione non deve mai impedire il run.
    try:
        argv = _normalize_bool_flags(argv, _store_true_flags(Path(script_path)))
    except Exception:
        pass

    if fake is not None:
        return fake(list(argv), env, timeout_s)

    if not Path(script_path).exists():
        return 127, "", f"ModuleNotFoundError: {script_path} not found"

    full = [sys.executable, str(script_path)] + list(argv)
    try:
        proc = subprocess.run(
            full, capture_output=True, text=True, env=env,
            timeout=timeout_s, check=False,
        )
    except FileNotFoundError as e:
        return 127, "", f"ModuleNotFoundError: {e}"
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# Tabella centralizzata per ADR 0101. Esposta come dato per il codegen
# e per i test (table coverage).
ERROR_CLASS_TABLE = (
    ("not_authenticated",   "auth_required"),
    ("not authenticated",   "auth_required"),
    ("run the setup script", "auth_required"),
    ("refresh_failed",      "auth_required"),
    ("invalid_grant",       "auth_required"),
    ("token expired",       "auth_required"),
    ("token has expired",   "auth_required"),
    ("missing credentials", "auth_required"),
    ("credentials not found", "auth_required"),
    # Provider capability absent for the authenticated account.  These are
    # stable API/protocol markers, not localized user-facing prose: retrying
    # with a different executor cannot enable the remote service.
    ("failedprecondition",    "capability_missing"),
    ("failed precondition",   "capability_missing"),
    ("service not enabled",   "capability_missing"),
    ("modulenotfounderror", "missing_dependency"),
    ("no module named",     "missing_dependency"),
    ("command not found",   "missing_dependency"),
    ("quota",               "rate_limited"),
    ("429",                 "rate_limited"),
    ("rate limit",          "rate_limited"),
    ("404",                 "not_found"),
    ("not found",           "not_found"),
    ("notfound",            "not_found"),
    ("timed out",           "network"),
    ("timeout",             "network"),
    ("name or service not known", "network"),
    ("connection refused",  "network"),
    ("connection reset",    "network"),
    ("500",                 "server_error"),
    ("503",                 "server_error"),
    ("internal server",     "server_error"),
)


ERROR_CODE_BY_CLASS = {
    "invalid_args": "ERR_ARG_INVALID",
    "auth_required": "ERR_AUTH_REQUIRED",
    "rate_limited": "ERR_RATE_LIMITED",
    "network": "ERR_NETWORK",
    "missing_dependency": "ERR_DEPENDENCY_MISSING",
    "not_found": "ERR_NOT_FOUND",
    "capability_missing": "ERR_CAPABILITY_MISSING",
    "server_error": "ERR_PROVIDER_SERVER",
    "unknown": "ERR_PROVIDER_UNKNOWN",
}


def _error_code_for_class(error_class: str) -> str:
    """Return a stable code for generated provider-wrapper failures."""
    return ERROR_CODE_BY_CLASS.get(str(error_class or ""), "ERR_PROVIDER_UNKNOWN")

# Combinazioni piu' specifiche (priorita' alta).
_SPECIAL_COMBOS = (
    (("403", "insufficient"),  "auth_required"),
    (("403", "access_denied"), "auth_required"),
    (("invalid", "argument"),  "invalid_args"),
    (("invalid", "value"),     "invalid_args"),
    (("invalid", "format"),    "invalid_args"),
)


def _classify_error(returncode: int, stderr: str) -> str:
    """Mappa (rc, stderr) -> error_class deterministica (§2.8 + ADR 0101).

    Classi possibili: auth_required, capability_missing, rate_limited,
    network, invalid_args, server_error, missing_dependency, not_found,
    unknown.

    DEVI: passare stderr come stringa (None tollerato -> "").
    OK: _classify_error(1, "NOT_AUTHENTICATED") -> "auth_required".
    ERRORE: _classify_error(0, "...") (rc=0 indica successo).
    """
    s = (stderr or "").lower()
    for keywords, klass in _SPECIAL_COMBOS:
        if all(k in s for k in keywords):
            return klass
    for needle, klass in ERROR_CLASS_TABLE:
        if needle in s:
            return klass
    if returncode == 127:
        return "missing_dependency"
    if returncode == 124:
        return "network"
    return "unknown"


def _get_skill_oauth_config(executor_file: str) -> dict:
    """Legge la sezione `[oauth_provider]` dal manifest.toml dell'executor.

    `executor_file` e' tipicamente `__file__` passato dall'executor.
    Ritorna dict con keys `scopes_options`, `mirror_paths`,
    `client_secret_install_path` (omessi quando vuoti). Vuoto se la sezione
    manca: il caller decide se erroreare o usare defaults.

    Cache module-level per evitare re-parse a ogni invocazione.
    """
    p = Path(executor_file).resolve().parent / "manifest.toml"
    key = str(p)
    cached = _OAUTH_CFG_CACHE.get(key)
    if cached is not None:
        return cached
    out: dict = {}
    if p.is_file():
        try:
            import tomllib  # type: ignore
            data = tomllib.loads(p.read_text(encoding="utf-8"))
            section = data.get("oauth_provider") or {}
            for field in ("scopes_options", "mirror_paths",
                          "client_secret_install_path"):
                if field in section and section[field]:
                    out[field] = section[field]
        except Exception:
            pass
    _OAUTH_CFG_CACHE[key] = out
    return out


_OAUTH_CFG_CACHE: dict = {}


def _get_oauth_provider_for_skill(skill_name: str) -> dict:
    """Lookup OAuth provider config in `runtime/skill_oauth_providers.json`
    by skill_name. Single source of truth, indipendente dal manifest
    dell'executor che invoca (fix 18/5/2026: backend non sa dove sta il
    manifest dell'executor caller).

    Ritorna dict con keys `scopes_options`, `mirror_paths`,
    `client_secret_install_path` (omessi quando vuoti). `{}` se la skill
    non e' nella tabella o il file manca.
    """
    cached = _OAUTH_CFG_CACHE.get(f"skill::{skill_name}")
    if cached is not None:
        return cached
    out: dict = {}
    providers_path = Path(__file__).resolve().parent / "skill_oauth_providers.json"
    if providers_path.is_file():
        try:
            import json as _json
            data = _json.loads(providers_path.read_text(encoding="utf-8"))
            entry = (data.get("providers") or {}).get(skill_name) or {}
            for field in ("scopes_options", "mirror_paths",
                          "client_secret_install_path"):
                if field in entry and entry[field]:
                    out[field] = entry[field]
        except Exception:
            pass
    _OAUTH_CFG_CACHE[f"skill::{skill_name}"] = out
    return out


def _needs_inputs_oauth_setup(
    *,
    skill_name: str,
    executor: str,
    args_base: dict,
    scopes_options: Optional[list] = None,
    services_options: Optional[list] = None,
    mirror_paths: Optional[list] = None,
    client_secret_install_path: Optional[str] = None,
    credential_kind: str = "oauth",
) -> dict:
    """Costruisce payload decision='needs_inputs' per OAuth setup mancante.

    Dialog 2-step: file_path (client_secret.json) + choice (scope set).
    Il completamento avvia un flow OAuth 2.0 Authorization Code con
    redirect HTTP callback (zero copy-paste manuale).

    `scopes_options`: lista di entry `{label, scopes}` dove `label` e' la
    voce mostrata all'utente e `scopes` la lista di URL scope per il
    provider. Se omesso, `services_options` (lista di label) viene
    interpretato come labels-only (nessun mapping scope -> il caller deve
    fornire scopes_options).

    `mirror_paths`: path filesystem dove duplicare il token in plain JSON
    (per skill legacy che leggono il token da posizione specifica).

    `client_secret_install_path`: path dove copiare il client_secret.json
    dell'utente (per skill legacy che lo leggono da posizione specifica).

    DEVI: passare args_base = dict(args) ricevuti da invoke().
    NON DEVI: includere credenziali cleartext in args_base.
    """
    # Risolve options esposte al form: priorita' scopes_options (con labels +
    # scopes interni). Backwards: services_options come fallback (labels only).
    if scopes_options:
        ui_choices = [str(o.get("label", "")) for o in scopes_options]
    elif services_options:
        ui_choices = list(services_options)
    else:
        ui_choices = []

    on_complete = {
        "type": "start_oauth_redirect_flow",
        "credential_kind": credential_kind,
        "binding": skill_name,
        "executor": executor,
        "args_base": dict(args_base),
    }
    if scopes_options:
        on_complete["scopes_options"] = list(scopes_options)
    if mirror_paths:
        on_complete["mirror_paths"] = list(mirror_paths)
    if client_secret_install_path:
        on_complete["client_secret_install_path"] = client_secret_install_path

    # Pre-populate file_path field default se il client_secret esiste gia'
    # nel mirror path canonico (caso "refresh after token revocation" 18/5/2026).
    client_secret_field = {
        "var": "client_secret_path",
        "prompt": "MSG_OAUTH_PROMPT_CLIENT_SECRET",
        "schema": {"kind": "file_path"},
    }
    if client_secret_install_path:
        try:
            existing = Path(client_secret_install_path).expanduser()
            if existing.is_file():
                client_secret_field["default"] = str(existing)
        except Exception:
            pass

    return {
        "title": "MSG_OAUTH_SETUP_NEEDED",
        "dialog": [
            client_secret_field,
            {
                "var": "services",
                "prompt": "MSG_OAUTH_PROMPT_SERVICES",
                "schema": {"kind": "choice", "choices": ui_choices},
            },
        ],
        "fmt": "auto",
        "on_complete": on_complete,
    }


def _needs_inputs_api_key_setup(
    *,
    skill_name: str,
    executor: str,
    args_base: dict,
    field_label: str = "API key",
) -> dict:
    """Variante 1-step per skill basate su API key singola (no OAuth)."""
    return {
        "title": "MSG_API_KEY_SETUP_NEEDED",
        "dialog": [
            {
                "var": "api_key",
                "prompt": f"{field_label} per {skill_name}",
                "schema": {"kind": "credentials"},
            },
        ],
        "fmt": "auto",
        "on_complete": {
            "type": "save_credentials_and_resume",
            "credential_kind": "api_key",
            "binding": skill_name,
            "executor": executor,
            "args_base": dict(args_base),
        },
    }


def _check_credentials(binding: str) -> tuple[bool, Optional[dict]]:
    """Probe metadata-only sullo storage credenziali.

    Ritorna (present, metadata):
    - present=True, metadata={'fields_present':...} se trovato.
    - present=False, metadata=None se mancante o non leggibile.

    NON tocca i VALORI cleartext: usa direttamente la presenza del file.
    Per il valore vero, l'executor consumer invoca credentials.load(binding)
    direttamente (in-process, fuori vista LLM).

    Fail-soft (§2.8 onesto): se runtime/credentials.py non esiste (test
    isolato), ritorna (False, None) senza eccezione.
    """
    try:
        runtime_dir = Path(__file__).resolve().parent
        if str(runtime_dir) not in sys.path:
            sys.path.insert(0, str(runtime_dir))
        import credentials as _cred  # type: ignore
    except Exception:
        return False, None

    try:
        path = _cred._file_for(binding)
    except Exception:
        return False, None
    if not path.exists():
        return False, None

    try:
        payload = _cred.load(binding)
    except Exception:
        return True, {"fields_present": [], "decrypt_failed": True}
    if not isinstance(payload, dict):
        return True, {"fields_present": []}
    fields_present = sorted(str(k) for k in payload.keys())
    return True, {"fields_present": fields_present}


# Mapping canonico di field name della skill -> field name Metnos.
# Estensibile: ogni skill puo' aggiungere il proprio mapping al caller.
FIELD_CANON_GENERIC = {
    "htmlLink":     "html_link",
    "webViewLink":  "web_view_link",
    "mimeType":     "mime_type",
    "modifiedTime": "modified_time",
    "createdTime":  "created_time",
    "fileName":     "file_name",
    "fileId":       "file_id",
    "userId":       "user_id",
    "threadId":     "thread_id",
}


def _normalize_record(raw: dict, *, kind: str, extra: Optional[dict] = None,
                      canon: Optional[dict] = None) -> dict:
    """Trasforma un dict skill (camelCase) in entry Metnos snake_case.

    DEVI: passare un dict (caller deve filtrare i tipi).
    Aggiunge sempre kind=<canonical> e merge di extra (es. calendar_id
    che la skill non emette ma serve per pipeable next-step).
    """
    if not isinstance(raw, dict):
        return {"kind": kind}
    mapping = dict(FIELD_CANON_GENERIC)
    if canon:
        mapping.update(canon)
    out: dict = {"kind": kind}
    for k, v in raw.items():
        key = mapping.get(k, k)
        out[key] = v
    if extra:
        for k, v in extra.items():
            if k not in out:
                out[k] = v
    return out

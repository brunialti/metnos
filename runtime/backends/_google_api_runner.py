"""runtime/backends/_google_api_runner.py — runner condiviso per i
backend Google Workspace (ADR 0130 effetto collaterale).

Wrappa `skill_wrapper._run_api` + classifier + retry deterministico §7.9
su error_class transienti (network/server_error/rate_limited) e su
errori TLS/SSL (SSL ASN1 lib, TLSV handshake) tipici di httplib2 sotto
carico.

Scope: usato SOLO dai 3 backend handwritten in
`runtime/backends/{events,files,messages}/google_workspace.py` (e da
`messages/gmail_google_workspace.py`). NON viene chiamato da executor
synth-generated.

Pattern callsite:
    from backends._google_api_runner import run_with_retry, SKILL_NAME

    def read(args):
        argv = ["calendar", "list", "--calendar", "primary", ...]
        data, err = run_with_retry(
            argv, executor="read_events", args_base=dict(args),
            auth_handler=_auth_needs_inputs, result_kind="entries",
        )
        if err is not None:
            ...
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import _classify_error, _run_api  # noqa: E402

SKILL_NAME = "google-workspace"

# Error_class che giustificano retry: condizioni temporanee, non
# permanenti (auth_required e invalid_args NON sono qui — fail-fast).
_TRANSIENT_ERROR_CLASSES = ("network", "server_error", "rate_limited")

# SSL handshake error pattern: httplib2 sotto carico, certificato
# riproposto. Non classificati come network/server_error dalla tabella
# `skill_wrapper.ERROR_CLASS_TABLE` — detect via substring nel stderr.
_SSL_PATTERNS = ("SSL", "ASN1", "TLSV", "ssl.SSLError")

# Tentativi totali = 1 (initial) + N (retry). 3 = 1+2 e' il default
# bilanciato fra latenza e copertura transient (oltre raddoppia attese).
DEFAULT_MAX_RETRIES = 2
DEFAULT_TIMEOUT_S = 60


def _is_ssl_error(stderr: str) -> bool:
    if not stderr:
        return False
    return any(p in stderr for p in _SSL_PATTERNS)


def run_with_retry(
    argv: list[str],
    *,
    executor: str,
    args_base: dict,
    auth_handler: Callable[[dict], dict] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    skill_name: str = SKILL_NAME,
) -> tuple[dict | list | None, dict | None]:
    """Esegue `python google_api.py <argv>` con retry §7.9.

    Args:
      argv: argomenti CLI google_api (es. `["calendar","create",...]`).
      executor: nome executor canonical (per OAuth needs_inputs payload).
      args_base: args originali dell'invoke (per replay post-OAuth).
      auth_handler: callback `(args_base) -> needs_inputs_dict` invocato
        quando `_classify_error == "auth_required"`. Se None, ritorna
        err_obj `{ok:False, error_class:"auth_required", ...}`.
      max_retries: tentativi addizionali oltre il primo (default 2 → 3 totali).
      timeout_s: timeout subprocess per tentativo.
      skill_name: override del default (`"google-workspace"`).

    Returns:
      (data, err): esattamente uno dei due e' None.
      - Success: (parsed_json | {}, None).
      - Auth: (None, auth_handler(args_base)) o err standardizzato.
      - Failure permanente: (None, {ok:False, error, error_class}).

    Determinismo §7.9: nessun LLM. Retry solo su transient OR SSL.
    """
    skill_root = _skill_root()
    api_script = skill_root / "scripts" / "google_api.py"
    last_err: dict | None = None

    for _attempt in range(max_retries + 1):
        rc, stdout, stderr = _run_api(api_script, argv,
                                       skill_name=skill_name,
                                       timeout_s=timeout_s)
        if rc == 0:
            if not stdout.strip():
                return {}, None
            try:
                return json.loads(stdout), None
            except json.JSONDecodeError as ex:
                last_err = {"ok": False,
                            "error": f"invalid JSON da google_api: {ex}",
                            "error_class": "server_error"}
                continue
        ec = _classify_error(rc, stderr)
        if ec == "auth_required":
            if auth_handler is not None:
                return None, auth_handler(args_base)
            return None, {"ok": False, "error_class": "auth_required",
                          "error": (stderr or "").strip() or f"rc={rc}"}
        last_err = {"ok": False,
                    "error": (stderr or "").strip() or f"rc={rc}",
                    "error_class": ec}
        # Retry solo su transient OR SSL handshake. Altri errori (e.g.
        # invalid_args, not_found) sono permanenti → fail-fast.
        if ec not in _TRANSIENT_ERROR_CLASSES and not _is_ssl_error(stderr):
            return None, last_err
    return None, last_err


def _skill_root() -> Path:
    """Lazy-import wrap per `skill_wrapper._skill_home` (evita import top-
    level se il helper viene usato in contesti senza skill_wrapper)."""
    from skill_wrapper import _skill_home
    return _skill_home(SKILL_NAME)

"""sudoer — verb-unique builtin executive (ADR 0070).

The sudoer receives a validated argv from `admin` and executes it. At
fire time it RE-VALIDATES against the deterministic safety tools
(forbidden + blacklist) to honour any blacklist edits Roberto may have
made between planning and firing. For delayed chains (scheduler in
between), it also runs the LLM-fast `sanity_check` per ADR 0070's
heuristic.

It accepts a one-time secret slot for the sudo password when the argv
starts with `sudo`. The slot is filled by the channel adapter (Telegram
DM, in production) and zeroed immediately after consumption.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_setup import get_logger
from safety.canonicalize import (
    Signature,
    compute_signature,
    has_sudo_wrapper,
    signature_matches,
)
from safety.sanity_check import compute_sanity_check, should_invoke
from safety.secret_slot import SecretSlot
from safety.storage import SafetyStore

log = get_logger(__name__)


# ── Manifest fingerprint (ADR 0069 enforcement) ───────────────────────
NOT_IN_VOCAB = True
EXPOSE_TO_PLANNER = False
AUTHORISED_CALLERS = ("runtime.dispatcher", "builtins.admin", "scheduler_v2")
VERB = "sudoer"


# ── Default execution timeout (per command) ───────────────────────────
DEFAULT_TIMEOUT_S = 60


@dataclass
class ExecResult:
    ok: bool
    status: str          # 'executed' | 'blocked_at_fire' | 'timeout' | 'error'
    argv: list[str]
    signature: str
    requires_sudo: bool
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    audit: dict = field(default_factory=dict)
    notify_user: Optional[str] = None  # message to forward to the user


def _re_validate(argv: list[str], signature: Signature) -> Optional[str]:
    """Re-run forbidden + blacklist checks. Return reason if blocked, else None."""
    # forbidden raw
    from system.admin import _check_forbidden_argv  # reuse, no duplication
    forbidden, forbidden_reason = _check_forbidden_argv(argv)
    if forbidden:
        return f"forbidden at fire: {forbidden_reason}"

    store = SafetyStore()
    try:
        for kind_tag in ("blacklist", "forbidden"):
            for row in store.find_by_kind(kind_tag):
                if signature_matches(signature, row.signature):
                    return (
                        f"blacklist at fire: {row.reason or row.signature}"
                    )
    finally:
        store.close()
    return None


def execute(
    *,
    argv: list[str],
    intent_text: str = "",
    scheduler_delay_minutes: int = 0,
    reversibility: str = "unknown",
    secret: Optional[SecretSlot] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    sanity_llm_call: Optional[Callable[[str, str], str]] = None,
) -> ExecResult:
    """Execute a validated argv.

    Re-validation happens here (defence in depth, ADR 0070). The optional
    `secret` is consumed exactly once; it must be filled by the caller
    when `argv` starts with sudo and `-S` was set.

    Args:
      argv:                    validated argv from `admin`.
      intent_text:             original NL intent (for sanity check + audit).
      scheduler_delay_minutes: how long ago `admin` validated this argv.
      reversibility:           reversibility class from admin's classifier.
      secret:                  one-time SecretSlot containing the sudo
                               password (None if no sudo required).
      timeout_s:               kill the subprocess after this many seconds.
      sanity_llm_call:         override for the sanity-check LLM bridge
                               (testing).

    Returns: ExecResult.
    """
    if not argv:
        return ExecResult(
            ok=False, status="error",
            argv=[], signature="", requires_sudo=False,
            stderr="empty argv",
        )

    sig = compute_signature(argv)
    requires_sudo = has_sudo_wrapper(argv)
    audit: dict = {
        "intent_text": intent_text,
        "argv": argv,
        "signature": str(sig),
        "requires_sudo": requires_sudo,
        "delay_min": scheduler_delay_minutes,
        "reversibility": reversibility,
    }

    # ── Re-validation at fire time ────────────────────────────────────
    block_reason = _re_validate(argv, sig)
    if block_reason:
        notify = (
            f"Avevi pianificato `{' '.join(argv)}`, ma ho trovato una "
            f"regola che lo blocca al momento dell'esecuzione: "
            f"{block_reason}. Non eseguito."
        )
        audit["block_reason"] = block_reason
        return ExecResult(
            ok=True, status="blocked_at_fire",
            argv=argv, signature=str(sig),
            requires_sudo=requires_sudo,
            audit=audit,
            notify_user=notify,
        )

    # ── Sanity check (ADR 0070 activation heuristic) ──────────────────
    if should_invoke(
        scheduler_delay_minutes=scheduler_delay_minutes,
        reversibility=reversibility,
    ):
        try:
            res = compute_sanity_check(
                intent_text=intent_text,
                argv=argv,
                scheduler_delay_minutes=scheduler_delay_minutes,
                reversibility=reversibility,
                system_state=None,
                llm_call=sanity_llm_call,
            )
            audit["sanity_smell"] = res.smell
            if res.smell == "urgent_review":
                notify = (
                    f"Stavo per eseguire `{' '.join(argv)}` ma vedo un "
                    f"problema contestuale: {res.reason or '(motivo non specificato)'}. "
                    "Non eseguito."
                )
                audit["block_reason"] = "sanity_urgent_review"
                return ExecResult(
                    ok=True, status="blocked_at_fire",
                    argv=argv, signature=str(sig),
                    requires_sudo=requires_sudo,
                    audit=audit,
                    notify_user=notify,
                )
            if res.smell == "suspicious":
                audit["sanity_warning"] = res.reason
        except Exception as e:  # noqa: BLE001
            # Sanity check is non-blocking by default on failure: it can
            # only ADD blocks, so a missing second opinion = continue.
            audit["sanity_error"] = f"{type(e).__name__}: {e}"

    # ── CIFS credentials placeholder substitution (ADR 0087) ──────────
    # Se il pianificatore ha emesso `credentials=${METNOS_CIFS_CREDS}` (vedi
    # admin.LLM_PROMPT_TEMPLATE), creiamo al volo il temp file dalle
    # credenziali cifrate e sostituiamo il placeholder. Cleanup garantito
    # dal context manager `cifs_helper.temp_credentials_file`.
    if _argv_has_cifs_placeholder(argv):
        return _spawn_with_cifs_credentials(
            argv=argv,
            secret=secret,
            timeout_s=timeout_s,
            signature=str(sig),
            requires_sudo=requires_sudo,
            audit=audit,
        )

    # ── Execute ───────────────────────────────────────────────────────
    return _spawn(
        argv=argv,
        secret=secret,
        timeout_s=timeout_s,
        signature=str(sig),
        requires_sudo=requires_sudo,
        audit=audit,
    )


# ── CIFS placeholder substitution (ADR 0087) ──────────────────────────

_CIFS_PLACEHOLDER = "${METNOS_CIFS_CREDS}"


def _argv_has_cifs_placeholder(argv: list[str]) -> bool:
    return any(_CIFS_PLACEHOLDER in tok for tok in argv)


def _derive_cifs_domain_from_argv(argv: list[str]) -> Optional[str]:
    """Estrae il nome host dallo source `//host/share` nell'argv per
    derivare la chiave di store (`cifs_<host>`). Ritorna None se non
    c'e' uno share CIFS riconoscibile.
    """
    import re as _re
    cifs_re = _re.compile(r"^//([^/]+)/.+$")
    for tok in argv:
        m = cifs_re.match(tok)
        if m:
            from cifs_helper import domain_for_server  # type: ignore
            return domain_for_server(m.group(1))
    return None


def _spawn_with_cifs_credentials(
    *,
    argv: list[str],
    secret: Optional[SecretSlot],
    timeout_s: int,
    signature: str,
    requires_sudo: bool,
    audit: dict,
) -> ExecResult:
    """Resolve the `${METNOS_CIFS_CREDS}` placeholder against a fresh
    temp credentials file derived from the share host, then delegate to
    `_spawn`. The temp file is destroyed on context exit, even on error.
    """
    domain = _derive_cifs_domain_from_argv(argv)
    if domain is None:
        audit["cifs_error"] = "no_share_source"
        return ExecResult(
            ok=False, status="error",
            argv=argv, signature=signature, requires_sudo=requires_sudo,
            stderr=(
                "placeholder ${METNOS_CIFS_CREDS} presente, ma nessuno "
                "share `//host/share` trovato nell'argv: impossibile "
                "derivare il dominio di store delle credenziali."
            ),
            audit=audit,
        )

    from cifs_helper import temp_credentials_file  # type: ignore

    with temp_credentials_file(domain) as (cred_path, err):
        if err is not None or cred_path is None:
            audit["cifs_error"] = err or "missing_credentials"
            audit["cifs_domain"] = domain
            return ExecResult(
                ok=False, status="error",
                argv=argv, signature=signature, requires_sudo=requires_sudo,
                stderr=(
                    f"credenziali CIFS mancanti per `{domain}`: {err}. "
                    "Salva prima username/password con "
                    "`cifs_helper.store_cifs_credentials`."
                ),
                audit=audit,
            )
        # Sostituzione placeholder → path concreto.
        substituted = [tok.replace(_CIFS_PLACEHOLDER, cred_path) for tok in argv]
        audit["cifs_domain"] = domain
        audit["cifs_creds_path"] = cred_path
        return _spawn(
            argv=substituted,
            secret=secret,
            timeout_s=timeout_s,
            signature=signature,
            requires_sudo=requires_sudo,
            audit=audit,
        )


def _spawn(
    *,
    argv: list[str],
    secret: Optional[SecretSlot],
    timeout_s: int,
    signature: str,
    requires_sudo: bool,
    audit: dict,
) -> ExecResult:
    """Lower-level spawn: subprocess.run with shell=False, optional sudo
    password via `-S` on stdin from the SecretSlot.
    """
    import time

    # If sudo is required, ensure the argv has the `-S` (read pwd from stdin)
    # flag so we can pipe the secret. Inject it after the sudo binary if
    # missing.
    if requires_sudo and argv[0] in ("sudo", "doas", "pkexec"):
        if "-S" not in argv:
            argv = [argv[0], "-S"] + argv[1:]

    # Prepare stdin payload
    stdin_data: Optional[bytes] = None
    started = time.monotonic()
    try:
        if secret is not None and requires_sudo:
            with secret.consume() as pwd:
                stdin_data = pwd + b"\n"
                proc = subprocess.run(
                    argv,
                    input=stdin_data,
                    capture_output=True,
                    timeout=timeout_s,
                    shell=False,
                )
        else:
            proc = subprocess.run(
                argv,
                capture_output=True,
                timeout=timeout_s,
                shell=False,
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecResult(
            ok=proc.returncode == 0,
            status="executed",
            argv=argv,
            signature=signature,
            requires_sudo=requires_sudo,
            exit_code=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace")[:5000],
            stderr=proc.stderr.decode("utf-8", errors="replace")[:5000],
            duration_ms=duration_ms,
            audit=audit,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - started) * 1000)
        audit["timeout"] = timeout_s
        return ExecResult(
            ok=False, status="timeout",
            argv=argv, signature=signature,
            requires_sudo=requires_sudo,
            duration_ms=duration_ms,
            stderr=f"timeout after {timeout_s}s",
            audit=audit,
        )
    except FileNotFoundError as e:
        return ExecResult(
            ok=False, status="error",
            argv=argv, signature=signature,
            requires_sudo=requires_sudo,
            stderr=f"binary not found: {e}",
            audit=audit,
        )
    except Exception as e:  # noqa: BLE001
        return ExecResult(
            ok=False, status="error",
            argv=argv, signature=signature,
            requires_sudo=requires_sudo,
            stderr=f"{type(e).__name__}: {e}",
            audit=audit,
        )
    finally:
        # Defence: even if SecretSlot wasn't consumed (e.g. exception
        # before consume), ensure the buffer is released.
        if secret is not None and not secret.is_consumed:
            try:
                with secret.consume() as _pwd:
                    pass  # immediate zeroing
            except Exception:
                pass

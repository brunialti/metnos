"""runtime.remote_exec — ponte fra il dispatch e gli executor remoti.

Quando il placement decide `device`, il runtime NON esegue `invoke_executor`
locale: accoda un'invocazione firmata (invocations.enqueue_invocation) e
attende il result consegnato dal client via POST /agent/result. Il result
ha la stessa shape §2.6 di un executor locale: il chiamante non distingue.

Errori onesti §2.8 (mai silenzio):
- device mai raggiunto entro la deadline -> ok:false + ERR_DEVICE_TIMEOUT
  (l'invocazione resta in coda finche' il device non ri-polla; il turno
  corrente pero' si chiude con l'errore, niente attese indefinite).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import invocations  # noqa: E402
from messages import get as _msg  # noqa: E402

from logging_setup import get_logger
log = get_logger(__name__)

# Margine oltre la deadline_ms dell'invocazione prima di dichiarare timeout
# lato server (il client ha bisogno di un giro di poll per prenderla).
WAIT_MARGIN_S = 15


def _register_i18n_keys() -> None:
    """Chiavi user-facing del sottosistema remoto (§11: mai stringhe
    hardcoded). Idempotente; il seed bundled le include (test gate)."""
    try:
        import i18n
        i18n.register_key_if_missing(
            "ERR_DEVICE_UNREACHABLE",
            "il dispositivo '{name}' non e' raggiungibile (nessun heartbeat recente)",
            "device '{name}' is not reachable (no recent heartbeat)",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_UNKNOWN",
            "il dispositivo '{name}' non risulta appaiato",
            "device '{name}' is not paired",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_AMBIGUOUS",
            "piu' dispositivi disponibili: specifica il nome del dispositivo",
            "multiple devices available: specify the device name",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_NONE_AVAILABLE",
            "nessun dispositivo raggiungibile per questa operazione",
            "no device is reachable for this operation",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_TIMEOUT",
            "il dispositivo '{name}' non ha risposto entro {seconds} secondi",
            "device '{name}' did not answer within {seconds} seconds",
            needs_translation=False)
    except Exception as _e:
        log.warning("registrazione chiavi i18n remote fallita: %s", _e)


_register_i18n_keys()


def invoke_remote(executor, args: dict, device_id: str, *,
                  timeout_s: int = 30,
                  turn_id: str | None = None,
                  reversibility: str | None = None,
                  env_injections: dict | None = None) -> dict:
    """Esegue `executor` sul device remoto e ritorna il result (shape §2.6).

    `executor` e' la dataclass loader.Executor (serve name + revertible).
    Bloccante come invoke_executor locale: il chiamante e' il runtime sync.
    """
    from devices import get_device
    dev = get_device(device_id)
    dev_name = dev.name if dev else device_id[:12]

    deadline_ms = int(timeout_s) * 1000
    invocation_id = invocations.enqueue_invocation(
        device_id,
        executor.name,
        args or {},
        turn_id=turn_id,
        scope="device",
        reversibility=reversibility or (
            "revertible" if getattr(executor, "revertible", False) else "read_only"),
        env_injections=env_injections,
        deadline_ms=deadline_ms,
    )

    wait_s = timeout_s + WAIT_MARGIN_S
    result = invocations.wait_result(invocation_id, wait_s)
    if result is None:
        log.warning("invocation %s senza result entro %ds (device %s)",
                    invocation_id, wait_s, device_id[:12])
        return {
            "ok": False,
            "error": _msg("ERR_DEVICE_TIMEOUT", name=dev_name, seconds=wait_s),
            "error_class": "remote_timeout",
            "invocation_id": invocation_id,
            "device_id": device_id,
        }

    # Il result e' gia' verificato (device_sig) da complete_invocation.
    # L'output COMPLETO dell'executor (§2.6: entries|results MA anche le chiavi
    # di dominio come total_lines/by_path/summary) viaggia in `payload`: il
    # runtime lo consuma ESATTAMENTE come un result locale. Senza questo il
    # round-trip perdeva tutto ciò che non era `entries` (bug live 3/7:
    # compute_files_loc → n_processed 5 ma entries [] e nessun dato LOC).
    # I metadati di trasporto (sandbox, elapsed_ms) vanno sotto `_remote`,
    # namespaced per non collidere con le chiavi dell'executor.
    payload = result.get("payload")
    if isinstance(payload, dict) and payload:
        merged = dict(payload)
        merged.setdefault("ok", bool(result.get("ok")))
        merged["_remote"] = {
            "device_id": device_id,
            "invocation_id": invocation_id,
            "sandbox": result.get("sandbox"),
            "elapsed_ms": result.get("elapsed_ms"),
        }
        return merged
    # Client pre-payload (0.2.5 e precedenti, non ancora reinstallato): thin
    # body coi soli campi §6.3. Fallback trasparente durante il rollout.
    result.setdefault("ok", False)
    return result
